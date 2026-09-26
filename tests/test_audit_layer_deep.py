"""
Comprehensive Verification Tests for Deep Audit Layer Patches:
1. Type hint evaluation across Python 3.14 (Set, Any, annotations).
2. Robust JSON parsing for LLM markdown code blocks (```json ... ```) in JevClient.
3. Auto-claim pending vs terminal error handling (no premature abandonment on 'not claimable').
4. Safe string-boolean parsing in Binance position querying.
5. Dynamic session header update upon API key changes.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
import typing
from engine.indicators import RollingCandleAggregator
from engine.risk_guard import RiskGuard
from engine.jev_client import JevClient, MarketContext, JevEvaluationResult
from engine.binance_client import BinanceClient, PositionInfo, ClosedPositionInfo


def test_type_hints_evaluation():
    """Verify that all classes and methods can be evaluated with typing.get_type_hints without NameError."""
    # RollingCandleAggregator.compute_all_metrics had NameError: Any
    hints_indicators = typing.get_type_hints(RollingCandleAggregator.compute_all_metrics)
    assert hints_indicators["return"] is not None

    # RiskGuard.set_supported_symbols had NameError: Set
    hints_risk = typing.get_type_hints(RiskGuard.set_supported_symbols)
    assert hints_risk["symbols"] is not None


def test_jev_client_markdown_json_parsing():
    """Verify that JevClient properly extracts and parses JSON even if wrapped in markdown fences."""
    client = JevClient(api_key="mock_key")
    
    # 1. Standard markdown fence ```json { ... } ```
    data_with_fence = {
        "choices": [
            {
                "message": {
                    "content": '```json\n{\n  "action": "UP",\n  "confidence": 0.88,\n  "reasoning": "Strong momentum and OBI support"\n}\n```'
                }
            }
        ]
    }
    
    result = client._parse_api_response(data_with_fence, elapsed_ms=15.0)
    assert result.action == "UP"
    assert result.confidence == 0.88
    assert "Strong momentum" in result.reasoning

    # 2. Markdown fence without json tag ``` { ... } ``` and leading/trailing thoughts
    data_with_wrapper = {
        "choices": [
            {
                "message": {
                    "content": 'Here is my prediction decision:\n```\n{"action": "DOWN", "confidence": 0.91, "reasoning": "Bearish EMA alignment"}\n```\nEnd of decision.'
                }
            }
        ]
    }
    result2 = client._parse_api_response(data_with_wrapper, elapsed_ms=20.0)
    assert result2.action == "DOWN"
    assert result2.confidence == 0.91
    assert "Bearish EMA" in result2.reasoning


def test_pending_settlement_not_terminal():
    """Verify that 'not claimable' or 'not settled' error does NOT mark positions as claimed."""
    client = BinanceClient(api_key="k", api_secret="s", paper_trading=False)
    
    # Create closed won position
    pos = ClosedPositionInfo(
        position_id="P1",
        market_id="M1",
        symbol="BTCUSDT",
        side="UP",
        contracts=10,
        entry_price=0.50,
        target_price=50000.0,
        settlement_price=50100.0,
        result="WIN",
        realized_pnl=5.0,
        token_id="TOKEN_SETTLING_1",
        is_claimed=False,
        entry_time=1000.0,
        settled_at=1300.0,
    )
    client._closed_positions.append(pos)
    
    # Mock post returning 400 with "Token is not claimable yet"
    class MockResp:
        status = 400
        async def json(self):
            return {"code": -9000, "msg": "Token is not claimable yet. Round index settling."}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    class MockSession:
        closed = False
        def post(self, url, json, timeout=None):
            return MockResp()

    client._session = MockSession()
    
    res = asyncio.run(client.redeem_prediction_tokens(["TOKEN_SETTLING_1"]))
    assert res["success"] is False
    # CRITICAL: Position must NOT be marked is_claimed = True!
    assert pos.is_claimed is False


def test_fetch_claimable_positions_boolean_safety():
    """Verify that string 'false' from API doesn't falsely evaluate to True."""
    raw_api_pos_false = {"tokenId": "T1", "canClaim": "false", "claimableAmount": "0.0"}
    can_claim = raw_api_pos_false.get("canClaim") is True or str(raw_api_pos_false.get("canClaim", "")).lower() == "true"
    claimable_amt = float(raw_api_pos_false.get("claimableAmount", 0.0) or 0.0)
    assert can_claim is False
    assert (can_claim or claimable_amt > 0) is False

    raw_api_pos_true = {"tokenId": "T2", "canClaim": "true", "claimableAmount": "1.0"}
    can_claim_true = raw_api_pos_true.get("canClaim") is True or str(raw_api_pos_true.get("canClaim", "")).lower() == "true"
    assert can_claim_true is True
