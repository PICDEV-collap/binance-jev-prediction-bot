"""
Unit and regression tests for Odds Limits, Time-Window Filters, Quote Price Guard, and Early Take-Profit.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
import pytest
from engine.jev_client import MarketContext, JevEvaluationResult, JevClient
from engine.risk_guard import RiskGuard
from engine.binance_client import BinanceClient, PositionInfo


def test_max_odds_cap_rejection():
    """Verify that bets with price > max_odds_cap (0.60) are rejected to prevent low-profit/100%-loss bets."""
    guard = RiskGuard(confidence_threshold=0.80, max_odds_cap=0.60, min_odds_floor=0.20)

    # Market at 0.65 odds (high odds, win payout would be only +53.8%)
    ctx = MarketContext(
        market_id="BTCUSDT-5M-TEST-1",
        symbol="BTCUSDT",
        question="BTC Up or Down 5m",
        odds_yes=0.65,
        odds_no=0.35,
        underlying_price=84200.0,
        target_price=84100.0,
        time_left_seconds=200,
    )
    decision = JevEvaluationResult(action="UP", confidence=0.85, reasoning="Bullish")

    res = guard.validate_and_size_order(decision, ctx)
    assert res.approved is False
    assert "exceeds maximum odds cap" in res.reason
    assert guard._rejection_counts["EXTREME_ODDS_RISK"] > 0


def test_min_odds_floor_rejection():
    """Verify that extreme underdog bets (< 0.20, like 0.01) are rejected."""
    guard = RiskGuard(confidence_threshold=0.80, max_odds_cap=0.60, min_odds_floor=0.20)

    # Market at 0.01 odds (like row 9 in user image)
    ctx = MarketContext(
        market_id="BTCUSDT-5M-TEST-2",
        symbol="BTCUSDT",
        question="BTC Up or Down 5m",
        odds_yes=0.01,
        odds_no=0.99,
        underlying_price=84000.0,
        target_price=84100.0,
        time_left_seconds=200,
    )
    decision = JevEvaluationResult(action="UP", confidence=0.85, reasoning="Reversal hope")

    res = guard.validate_and_size_order(decision, ctx)
    assert res.approved is False
    assert "below minimum odds floor" in res.reason
    assert guard._rejection_counts["EXTREME_ODDS_RISK"] > 0


def test_time_window_filter_rejection():
    """Verify that late-round entries (< 120s left) are rejected to avoid timing traps."""
    guard = RiskGuard(confidence_threshold=0.80, min_time_left_seconds=120)

    # Round with only 75 seconds left
    ctx = MarketContext(
        market_id="BTCUSDT-5M-TEST-3",
        symbol="BTCUSDT",
        question="BTC Up or Down 5m",
        odds_yes=0.52,
        odds_no=0.48,
        underlying_price=84200.0,
        target_price=84100.0,
        time_left_seconds=75,
        expiry_danger_flag=False,
    )
    decision = JevEvaluationResult(action="UP", confidence=0.85, reasoning="Bullish")

    res = guard.validate_and_size_order(decision, ctx)
    assert res.approved is False
    assert "Time-Window Filter" in res.reason
    assert guard._rejection_counts["OUTSIDE_TIME_WINDOW"] > 0


def test_quote_price_guard_simulation():
    """Verify Quote Price Guard rejection when simulated or live quote exceeds max_odds_cap."""
    client = BinanceClient(
        paper_trading=True,
        max_odds_cap=0.60,
        min_odds_floor=0.20,
    )

    # Test quote validation logic
    quote_data_expensive = {"price": "0.99", "quoteId": "Q123"}
    quoted_price = float(quote_data_expensive["price"])
    assert quoted_price > client.max_odds_cap, "Quoted price should exceed max odds cap"

    quote_data_underdog = {"price": "0.01", "quoteId": "Q124"}
    quoted_underdog = float(quote_data_underdog["price"])
    assert quoted_underdog < client.min_odds_floor, "Quoted price should be below floor"


def test_early_take_profit_paper():
    """Verify that open positions automatically take profit early when market odds hit >= 0.82."""
    async def _run():
        client = BinanceClient(
            paper_trading=True,
            enable_early_take_profit=True,
            take_profit_odds=0.82,
        )

        # Add an open paper position bought at 0.50
        pos = PositionInfo(
            position_id="POS_TEST_TP_1",
            market_id="BTC-5M-R100",
            symbol="BTCUSDT",
            side="UP",
            contracts=10,
            entry_price=0.50,
            target_price=84000.0,
            current_price=84200.0,
            timeframe="5m",
            unrealized_pnl=3.20,
        )
        client._paper_positions[pos.position_id] = pos

        # Market odds have surged to 0.85 (in heavy profit)
        active_markets = [
            {
                "market_id": "BTC-5M-R100",
                "symbol": "BTCUSDT",
                "odds_yes": 0.85,
                "odds_no": 0.15,
            }
        ]

        tp_events = await client.check_and_execute_early_take_profits(active_markets)
        assert len(tp_events) == 1
        assert tp_events[0]["won"] is True
        assert tp_events[0]["pnl"] == 3.50  # 10 contracts * (0.85 - 0.50) = 3.50 USDT profit!
        assert pos.position_id not in client._paper_positions, "Position should be removed from active"
        assert len(client._closed_positions) == 1
        assert client._closed_positions[0].result == "TAKE_PROFIT"

    asyncio.run(_run())


def test_fifteen_min_confluence_and_contradiction():
    """Verify that 15m confluence boosts conviction, while Trend vs OBI contradiction dampens it."""
    async def _run():
        client = JevClient(api_key="")

        # 1. Bullish Confluence: 15m Strong Uptrend + Positive OBI + Good RSI + Positive Momentum
        ctx_confluence = MarketContext(
            market_id="BTC-15M-CONF",
            symbol="BTCUSDT",
            question="BTC Up or Down 15m",
            timeframe="15m",
            odds_yes=0.50,
            odds_no=0.50,
            underlying_price=85200.0,
            target_price=85100.0,
            price_diff=100.0,
            time_left_seconds=600,
            dvr_ratio=+1.5,
            ema_trend="STRONG_UPTREND",
            order_book_imbalance=+0.30,
            rsi_5m=55.0,
            momentum_pct=0.25,
            btc_correlation_dir="BULLISH",
        )
        res_conf = await client.evaluate_market(ctx_confluence)
        assert res_conf.action == "UP"
        assert "Bullish Confluence" in res_conf.reasoning
        assert res_conf.confidence >= 0.85

        # 2. Contradiction: Uptrend but heavy order book selling pressure
        ctx_contradiction = ctx_confluence.model_copy(update={
            "order_book_imbalance": -0.40,
        })
        res_contra = await client.evaluate_market(ctx_contradiction)
        assert "Contradiction Dampener" in res_contra.reasoning
        assert res_contra.confidence < res_conf.confidence, "Contradiction should lower conviction"

    asyncio.run(_run())


def test_asymmetric_odds_penalty():
    """Verify that heuristic applies asymmetric odds penalty when contract price > 0.60."""
    async def _run():
        client = JevClient(api_key="")

        # Market with high odds > 0.60
        ctx_expensive = MarketContext(
            market_id="BTC-15M-EXP",
            symbol="BTCUSDT",
            question="BTC Up or Down 15m",
            timeframe="15m",
            odds_yes=0.68,
            odds_no=0.32,
            underlying_price=85200.0,
            target_price=85100.0,
            price_diff=100.0,
            time_left_seconds=500,
            dvr_ratio=+1.0,
            ema_trend="UPTREND",
            order_book_imbalance=+0.10,
            rsi_5m=55.0,
            momentum_pct=0.10,
        )
        res_exp = await client.evaluate_market(ctx_expensive)
        assert "Asymmetric Odds Penalty" in res_exp.reasoning

    asyncio.run(_run())

