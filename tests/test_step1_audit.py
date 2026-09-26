"""
Step 1 Audit Test Suite:
Validating Risk Guard, Martingale integrity, and Binance Client execution/claim safety.
"""
import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from engine.binance_client import BinanceClient, PositionInfo, ClosedPositionInfo
from engine.risk_guard import RiskGuard
from engine.jev_client import MarketContext, JevEvaluationResult


def test_risk_guard_sizing_underflow_rejected():
    """
    Finding #3 Validation: When max_position_size_usdt is smaller than target_price,
    RiskGuard MUST REJECT the order with POSITION_SIZE_EXCEEDED instead of forcing 1 contract!
    """
    guard = RiskGuard(
        confidence_threshold=0.80,
        max_position_size_usdt=0.30,  # Budget is only $0.30
        default_order_contracts=10,
        min_odds_floor=0.10,
        max_odds_cap=0.90,
    )
    ctx = MarketContext(
        market_id="BTCUSDT-15M-TEST",
        symbol="BTCUSDT",
        question="BTC Up or Down",
        odds_yes=0.50,  # 1 contract costs $0.50 (> $0.30 budget)
        odds_no=0.50,
        underlying_price=84000.0,
        target_price=84000.0,
        time_left_seconds=400,
    )
    decision = JevEvaluationResult(action="UP", confidence=0.85, reasoning="Bullish")

    res = guard.validate_and_size_order(decision, ctx)
    assert res.approved is False
    assert "Position size budget" in res.reason
    assert res.adjusted_contracts == 0
    assert guard._rejection_counts["POSITION_SIZE_EXCEEDED"] == 1


def test_risk_guard_zero_or_negative_pricing_rejected():
    """Ensure target_price <= 0 is rejected with INVALID_PRICING."""
    guard = RiskGuard(min_odds_floor=0.0)
    ctx = MarketContext(
        market_id="BTCUSDT-15M-TEST",
        symbol="BTCUSDT",
        question="BTC Up or Down",
        odds_yes=0.0,
        odds_no=0.0,
        underlying_price=84000.0,
        target_price=84000.0,
        time_left_seconds=400,
    )
    decision = JevEvaluationResult(action="UP", confidence=0.85, reasoning="Bullish")
    res = guard.validate_and_size_order(decision, ctx)
    assert res.approved is False
    assert "Invalid pricing" in res.reason or "minimum odds floor" in res.reason


def test_auto_claim_transient_error_preserves_unclaimed_status():
    """
    Finding #1 Validation: When batch-redeem API fails with HTTP 500/502/429,
    closed positions must RETAIN is_claimed=False so they can be retried!
    """
    client = BinanceClient(paper_trading=False, api_key="TEST_KEY", api_secret="TEST_SEC")
    pos = ClosedPositionInfo(
        position_id="POS_1",
        market_id="MKT_1",
        symbol="BTCUSDT",
        side="UP",
        contracts=10,
        entry_price=0.50,
        target_price=84000.0,
        settlement_price=84100.0,
        result="WIN",
        realized_pnl=5.0,
        token_id="TOKEN_12345",
        is_claimed=False,
        entry_time=time.time() - 1000,
    )
    client._closed_positions.append(pos)

    # Mock HTTP 502 Bad Gateway response
    mock_resp = AsyncMock()
    mock_resp.status = 502
    mock_resp.json = AsyncMock(return_value={"code": -1000, "msg": "Bad Gateway / Cloudflare error"})

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp
    client._session = mock_session

    res = asyncio.run(client.redeem_prediction_tokens(["TOKEN_12345"]))
    assert res["success"] is False
    # Crucial check: pos.is_claimed MUST REMAIN False!
    assert pos.is_claimed is False, "Transient server error should NOT mark position as claimed!"


def test_auto_claim_terminal_error_marks_claimed():
    """
    Terminal rejection (e.g. 'already claimed') SHOULD mark is_claimed=True to prevent endless retries.
    """
    client = BinanceClient(paper_trading=False, api_key="TEST_KEY", api_secret="TEST_SEC")
    pos = ClosedPositionInfo(
        position_id="POS_2",
        market_id="MKT_2",
        symbol="BTCUSDT",
        side="UP",
        contracts=10,
        entry_price=0.50,
        target_price=84000.0,
        settlement_price=84100.0,
        result="WIN",
        realized_pnl=5.0,
        token_id="TOKEN_ALREADY_DONE",
        is_claimed=False,
        entry_time=time.time() - 1000,
    )
    client._closed_positions.append(pos)

    # Mock terminal error: "Token already claimed"
    mock_resp = AsyncMock()
    mock_resp.status = 400
    mock_resp.json = AsyncMock(return_value={"code": -9000, "msg": "Token already claimed by user"})

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.post.return_value.__aenter__.return_value = mock_resp
    client._session = mock_session

    res = asyncio.run(client.redeem_prediction_tokens(["TOKEN_ALREADY_DONE"]))
    assert res["success"] is False
    # Terminal error marks claimed
    assert pos.is_claimed is True


def test_cooldown_rollback_on_failed_dispatch():
    """
    Finding #4 Validation: If RiskGuard approves an order but dispatch fails,
    rollback_market_traded removes the cooldown lock.
    """
    guard = RiskGuard(cooldown_seconds=45)
    market_id = "BTCUSDT-15M-COOLDOWN_TEST"
    guard.record_market_traded(market_id)
    assert market_id in guard._market_last_traded

    # Rollback
    guard.rollback_market_traded(market_id)
    assert market_id not in guard._market_last_traded


def test_claim_all_won_positions_grace_period():
    """
    Finding #2 Validation: A newly closed position (< 5m old) NOT YET in Binance's
    claimable list should NOT be marked is_claimed=True prematurely!
    """
    client = BinanceClient(paper_trading=False, api_key="TEST_KEY", api_secret="TEST_SEC")
    recent_pos = ClosedPositionInfo(
        position_id="POS_RECENT",
        market_id="MKT_RECENT",
        symbol="BTCUSDT",
        side="UP",
        contracts=5,
        entry_price=0.50,
        target_price=84000.0,
        settlement_price=84100.0,
        result="WIN",
        realized_pnl=2.5,
        token_id="TOKEN_FRESH_WIN",
        is_claimed=False,
        entry_time=time.time() - 30,  # 30 seconds ago
        settled_at=time.time() - 10,   # Settled only 10 seconds ago
    )
    client._closed_positions.append(recent_pos)

    # Mock fetch_claimable_positions returning empty list (Binance hasn't processed settlement yet)
    client.fetch_claimable_positions = AsyncMock(return_value=[])

    res = asyncio.run(client.claim_all_won_positions())
    assert recent_pos.is_claimed is False, "Freshly settled position must not be marked claimed prematurely!"
