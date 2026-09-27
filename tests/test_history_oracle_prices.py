import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
import pytest
from unittest.mock import AsyncMock
from engine.binance_client import BinanceClient, ClosedPositionInfo


def test_sync_closed_positions_populates_strike_and_spot():
    """Verify that sync_historical_closed_positions populates target_price and settlement_price from market oracle detail."""
    client = BinanceClient(paper_trading=False, api_key="TEST_API_KEY", api_secret="TEST_SECRET")
    
    mock_ended = [{
        "tokenId": "TOK_123456",
        "positionId": "1001",
        "marketId": "12683356",
        "marketTopicId": "6120185",
        "marketTopicTitle": "Bitcoin Up or Down 5m",
        "outcomeName": "DOWN",
        "shares": "2.5",
        "totalCost": "1.5",
        "unrealizedPnl": "1.0",
        "isWinner": True,
        "positionStatus": "CLAIMED",
        "avgPrice": "0.60",
        "updatedTime": int(time.time() * 1000),
    }]
    client.fetch_ended_prediction_positions = AsyncMock(return_value=mock_ended)
    # Pre-cache or mock fetch_market_oracle_prices
    client._market_oracle_cache[12683356] = (84758.005, 84735.445)
    
    count = asyncio.run(client.sync_historical_closed_positions())
    assert count == 1
    closed = client.get_closed_positions()
    assert len(closed) == 1
    assert closed[0]["target_price"] == 84758.005
    assert closed[0]["settlement_price"] == 84735.445
    assert closed[0]["result"] == "WIN"


def test_market_oracle_cache_avoids_duplicate_requests():
    """Verify that fetch_market_oracle_prices returns cached values without HTTP requests."""
    client = BinanceClient(paper_trading=False, api_key="TEST_API_KEY", api_secret="TEST_SECRET")
    client._market_oracle_cache[12683367] = (778.705, 777.395)
    
    # Session is None or dummy - should not raise exception because it hits cache!
    sp, ep = asyncio.run(client.fetch_market_oracle_prices(6120195, 12683367))
    assert sp == 778.705
    assert ep == 777.395


def test_orig_position_updates_when_previously_zero():
    """Verify that existing closed positions with 0.0 strike or spot get updated to real values upon sync."""
    client = BinanceClient(paper_trading=False, api_key="TEST_API_KEY", api_secret="TEST_SECRET")
    
    # Pre-existing position with target_price=0.0 and settlement_price=0.0
    orig = ClosedPositionInfo(
        position_id="POS_OLD_001",
        market_id="BTCUSDT-5M-TEST",
        symbol="BTCUSDT",
        side="DOWN",
        contracts=1,
        entry_price=0.50,
        target_price=0.0,
        settlement_price=0.0,
        result="WIN",
        realized_pnl=0.5,
        token_id="TOK_EXISTING_1",
        is_claimed=True,
    )
    client._closed_positions.append(orig)
    
    mock_ended = [{
        "tokenId": "TOK_EXISTING_1",
        "positionId": "2001",
        "marketId": "999888",
        "marketTopicId": "777666",
        "marketTopicTitle": "Bitcoin Up or Down 5m",
        "outcomeName": "DOWN",
        "shares": "1.0",
        "totalCost": "0.5",
        "unrealizedPnl": "0.5",
        "isWinner": True,
        "positionStatus": "CLAIMED",
        "avgPrice": "0.50",
        "updatedTime": int(time.time() * 1000),
    }]
    client.fetch_ended_prediction_positions = AsyncMock(return_value=mock_ended)
    client._market_oracle_cache[999888] = (84800.50, 84750.25)
    
    count = asyncio.run(client.sync_historical_closed_positions())
    assert count == 1
    closed = client.get_closed_positions()
    assert len(closed) == 1
    # Both target_price and settlement_price should be updated from 0.0 to real values!
    assert closed[0]["target_price"] == 84800.50
    assert closed[0]["settlement_price"] == 84750.25
