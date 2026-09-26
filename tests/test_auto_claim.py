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
    
    pnl, settled = client.settle_expired_positions(active_market_ids, current_prices)
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
    print("Auto-claim test passed!")
