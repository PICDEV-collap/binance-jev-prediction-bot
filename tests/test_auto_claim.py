import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
import pytest
from engine.binance_client import BinanceClient, ClosedPositionInfo, PositionInfo


def test_token_id_and_settlement_claim_flow():
    client = BinanceClient(paper_trading=True)
    
    # 1. Add active position with token_id
    pos_id = "POS_TEST_1"
    token_id = "TOKEN_999888777"
    client._paper_positions[pos_id] = PositionInfo(
        position_id=pos_id,
        market_id="BTCUSDT-5M-TEST",
        symbol="BTCUSDT",
        side="DOWN",
        contracts=1,
        entry_price=0.50,
        current_price=84000.0,
        target_price=84100.0,
        timeframe="5m",
        unrealized_pnl=0.0,
        token_id=token_id,
        entry_time=time.time() - 320,  # Expired
    )
    
    # 2. Settle expired positions
    active_market_ids = set()  # Market no longer active -> triggers settlement
    current_prices = {"BTCUSDT": 84050.0}  # Below 84100 -> DOWN wins!
    
    pnl, settled = asyncio.run(client.settle_expired_positions(active_market_ids, current_prices))
    assert len(settled) == 1
    assert settled[0]["won"] is True
    assert settled[0]["token_id"] == token_id
    
    # 3. Verify closed position stored token_id and is_claimed=False initially
    closed = client.get_closed_positions()
    assert len(closed) == 1
    assert closed[0]["token_id"] == token_id
    assert closed[0]["is_claimed"] is False
    
    # 4. Trigger claim_all_won_positions
    res = asyncio.run(client.claim_all_won_positions())
    assert res["success"] is True
    
    # 5. Check position marked claimed
    closed_after = client.get_closed_positions()
    assert closed_after[0]["is_claimed"] is True
    print("Paper claim simulation test passed!")


def test_autoclaim_disabled_in_live_trading():
    """Verify live trading does NOT trigger batch-redeem and leaves claiming to Binance."""
    from unittest.mock import AsyncMock
    client = BinanceClient(paper_trading=False, api_key="TEST_API_KEY", api_secret="TEST_SECRET")
    
    # Mock redeem_prediction_tokens and sync_historical_closed_positions
    client.redeem_prediction_tokens = AsyncMock()
    client.sync_historical_closed_positions = AsyncMock(return_value=1)
    
    res = asyncio.run(client.claim_all_won_positions())
    assert res["success"] is True
    assert "Binance" in res["message"]
    # redeem_prediction_tokens MUST NOT be called!
    assert client.redeem_prediction_tokens.call_count == 0
    # sync_historical_closed_positions MUST be called to synchronize official status
    assert client.sync_historical_closed_positions.call_count == 1


def test_subcent_pnl_preserves_precision():
    """Verify sub-cent fractional winning contracts (e.g. 0.0048 USDT) retain precision and don't round to 0.0."""
    from unittest.mock import AsyncMock
    client = BinanceClient(paper_trading=False, api_key="TEST_API_KEY", api_secret="TEST_SECRET")
    
    # Mock Binance SAPI ended position response with sub-cent win
    mock_ended = [{
        "tokenId": "TOK_SUBCENT_1",
        "positionId": "1001",
        "marketId": "123",
        "marketTopicTitle": "Bitcoin Price UP/DOWN 5m",
        "outcomeName": "UP",
        "shares": "0.02",
        "totalCost": "0.0152",
        "unrealizedPnl": "0.00481632",
        "isWinner": True,
        "positionStatus": "CLAIMED",
        "avgPrice": "0.76",
        "updatedTime": int(time.time() * 1000),
    }]
    client.fetch_ended_prediction_positions = AsyncMock(return_value=mock_ended)
    
    count = asyncio.run(client.sync_historical_closed_positions())
    assert count == 1
    closed = client.get_closed_positions()
    assert len(closed) == 1
    # PnL should be 0.0048, NOT 0.0!
    assert closed[0]["realized_pnl"] == 0.0048
    assert closed[0]["is_claimed"] is True

