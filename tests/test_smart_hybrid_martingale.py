"""
Unit tests for Martingale Option 3: Smart Hybrid PnL Recovery Sizing.
Tests loss accumulation, dynamic contract sizing with odds & PnL,
max position budget enforcement, winning reset, and switching modes.
"""

import pytest
from engine.risk_guard import RiskGuard
from engine.jev_client import JevEvaluationResult, MarketContext


def test_smart_hybrid_loss_accumulation_and_reset():
    guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="SMART_HYBRID",
        martingale_multiplier=2.0,
        martingale_max_steps=4,
        default_order_contracts=10,
        max_position_size_usdt=50.0,
    )

    # Initial state
    assert guard.get_symbol_martingale_step("ETHUSDT") == 0
    assert guard.get_symbol_accumulated_loss("ETHUSDT") == 0.0

    # First Loss: -$1.50
    guard.record_settlement_result(won=False, pnl=-1.50, symbol="ETHUSDT")
    assert guard.get_symbol_martingale_step("ETHUSDT") == 1
    assert guard.get_symbol_accumulated_loss("ETHUSDT") == 1.50
    assert "PnL: -$1.50" in guard.get_stage_label("ETHUSDT")

    # Second Loss: -$3.00
    guard.record_settlement_result(won=False, pnl=-3.00, symbol="ETHUSDT")
    assert guard.get_symbol_martingale_step("ETHUSDT") == 2
    assert guard.get_symbol_accumulated_loss("ETHUSDT") == 4.50
    assert "PnL: -$4.50" in guard.get_stage_label("ETHUSDT")

    # Win at Step 2: Recovers and resets
    guard.record_settlement_result(won=True, pnl=5.20, symbol="ETHUSDT")
    assert guard.get_symbol_martingale_step("ETHUSDT") == 0
    assert guard.get_symbol_accumulated_loss("ETHUSDT") == 0.0
    assert guard.get_stage_label("ETHUSDT") == "ไม้ 1 (Base)"


def test_smart_hybrid_vs_fixed_multiplier_sizing():
    # Setup Guard with SMART_HYBRID
    hybrid_guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="SMART_HYBRID",
        martingale_multiplier=2.0,
        martingale_max_steps=4,
        default_order_contracts=10,
        max_position_size_usdt=50.0,
        confidence_threshold=0.80,
    )

    # Setup Guard with FIXED_MULTIPLIER
    fixed_guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="FIXED_MULTIPLIER",
        martingale_multiplier=2.0,
        martingale_max_steps=4,
        default_order_contracts=10,
        max_position_size_usdt=50.0,
        confidence_threshold=0.80,
    )

    # Both lose round 1 with -$1.50
    hybrid_guard.record_settlement_result(won=False, pnl=-1.50, symbol="ETHUSDT")
    fixed_guard.record_settlement_result(won=False, pnl=-1.50, symbol="ETHUSDT")

    decision = JevEvaluationResult(action="BUY_YES", confidence=0.88, reasoning="Strong bounce")
    market = MarketContext(
        market_id="round_eth_002",
        symbol="ETHUSDT",
        question="Will Ethereum be Up or Down?",
        underlying_price=2750.0,
        target_price=0.40,
        odds_yes=0.40,
        odds_no=0.60,
        spread=0.01,
        time_left_seconds=300,
        min_payout_multiplier=1.0,
    )

    hybrid_res = hybrid_guard.validate_and_size_order(decision, market)
    fixed_res = fixed_guard.validate_and_size_order(decision, market)

    assert hybrid_res.approved is True
    assert fixed_res.approved is True

    # Under Fixed 2x: 10 * 2.0 = 20 contracts
    assert fixed_res.adjusted_contracts == 20

    # Under Smart Hybrid:
    # odds = 0.40 -> profit_per_contract = 0.60
    # base_profit = 10 * 0.60 = 6.00
    # accum_loss = 1.50
    # target_gain = 1.50 + 6.00 = 7.50
    # contracts = ceil(7.50 / 0.60) = 13 contracts
    # Cap = 20 contracts
    # Result = 13 contracts (saves capital!)
    assert hybrid_res.adjusted_contracts == 13
    assert hybrid_res.martingale_mode == "SMART_HYBRID"
    assert hybrid_res.accumulated_loss == 1.50


def test_smart_hybrid_max_position_cap():
    guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="SMART_HYBRID",
        martingale_multiplier=2.0,
        martingale_max_steps=4,
        default_order_contracts=10,
        max_position_size_usdt=10.0,  # Strict $10.00 cap
        confidence_threshold=0.80,
    )

    # Accumulate a $8.00 loss
    guard.record_settlement_result(won=False, pnl=-8.00, symbol="BTCUSDT")

    decision = JevEvaluationResult(action="BUY_YES", confidence=0.90, reasoning="Extreme edge")
    market = MarketContext(
        market_id="round_btc_003",
        symbol="BTCUSDT",
        question="Will Bitcoin be Up or Down?",
        underlying_price=64000.0,
        target_price=0.50,
        odds_yes=0.50,
        odds_no=0.50,
        spread=0.01,
        time_left_seconds=300,
        min_payout_multiplier=1.0,
    )

    res = guard.validate_and_size_order(decision, market)
    assert res.approved is True
    # At odds 0.50, max contracts for $10 budget = int(10.0 / 0.50) = 20 contracts max
    assert res.adjusted_contracts <= 20
    assert (res.adjusted_contracts * market.odds_yes) <= 10.0


def test_dynamic_mode_switching():
    guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="SMART_HYBRID",
    )
    assert guard.martingale_mode == "SMART_HYBRID"

    guard.update_thresholds(martingale_mode="FIXED_MULTIPLIER")
    assert guard.martingale_mode == "FIXED_MULTIPLIER"
    assert guard.metrics["martingale"]["mode"] == "FIXED_MULTIPLIER"

    guard.update_thresholds(martingale_mode="SMART_HYBRID")
    assert guard.martingale_mode == "SMART_HYBRID"
    assert guard.metrics["martingale"]["mode"] == "SMART_HYBRID"


def test_smart_hybrid_pnl_covers_accumulated_loss_at_prediction_odds():
    """
    Test real-world scenario from user report:
    BNBUSDT accumulated loss = -$2.89 at Step 2, odds = 0.569.
    Verify that calculated contracts yield net profit >= accumulated loss upon winning.
    """
    guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="SMART_HYBRID",
        martingale_multiplier=2.0,
        martingale_max_steps=6,
        default_order_contracts=1,
        max_position_size_usdt=20.0,
        confidence_threshold=0.80,
    )

    # Simulate 2 consecutive losses totaling $2.89
    guard.record_settlement_result(won=False, pnl=-1.39, symbol="BNBUSDT")
    guard.record_settlement_result(won=False, pnl=-1.50, symbol="BNBUSDT")

    assert guard.get_symbol_martingale_step("BNBUSDT") == 2
    assert guard.get_symbol_accumulated_loss("BNBUSDT") == 2.89

    decision = JevEvaluationResult(action="DOWN", confidence=0.92, reasoning="Strong bear continuation")
    market = MarketContext(
        market_id="BNBUSDT-5M-TEST",
        symbol="BNBUSDT",
        question="Will BNB be Up or Down?",
        underlying_price=778.0,
        target_price=0.569,
        odds_yes=0.431,
        odds_no=0.569,
        spread=0.01,
        time_left_seconds=250,
        min_payout_multiplier=1.0,
    )

    res = guard.validate_and_size_order(decision, market)
    assert res.approved is True

    # Mathematical verification:
    # Profit per contract = 1.00 - 0.569 = 0.431
    # Expected profit on win = contracts * 0.431
    # Expected profit MUST be strictly greater than accumulated loss ($2.89)
    expected_profit_on_win = res.adjusted_contracts * (1.00 - market.target_price)
    assert expected_profit_on_win > guard.get_symbol_accumulated_loss("BNBUSDT")
    assert res.adjusted_contracts >= 7
    # Exposure must respect max_position_size_usdt
    assert (res.adjusted_contracts * market.target_price) <= 20.0

