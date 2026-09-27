import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
from engine.binance_client import BinanceClient, ClosedPositionInfo, PositionInfo
from engine.risk_guard import RiskGuard
from streams.ws_listener import BinanceWSListener


def test_live_settlement_reconciliation_loss():
    """
    Verify that when Binance API tab=ENDED reports isWinner: False,
    the bot records LOSS, negative PnL, is_claimed: False, and RiskGuard escalates Martingale.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        guard = RiskGuard(martingale_enabled=True, martingale_multiplier=2.0)
        
        token_id = "TOKEN_LOSS_123"
        pos_id = "POS_LIVE_ETH_1"
        
        # Simulate an open live position
        client._live_positions[pos_id] = PositionInfo(
            position_id=pos_id,
            market_id="ETHUSDT-5M-TEST",
            symbol="ETHUSDT",
            side="UP",
            contracts=1,
            entry_price=0.53,
            current_price=2695.0,
            target_price=2696.0,
            timeframe="5m",
            unrealized_pnl=0.0,
            martingale_step=0,
            stage="ไม้ 1 (Base)",
            token_id=token_id,
            entry_time=time.time() - 310,  # 5m passed
        )

        # Mock fetch_ended_prediction_positions returning official Binance loss record
        mock_ended = [{
            "positionId": 999111,
            "tokenId": token_id,
            "marketTopicTitle": "Ethereum Up or Down - September 27, 1:25AM-1:30AM ET",
            "outcomeName": "Up",
            "shares": "2.77",
            "avgPrice": "0.54",
            "totalCost": "1.5",
            "positionStatus": "CLAIMED",
            "isWinner": False,  # Binance officially ruled LOST
            "finalOutcome": "Down",
            "unrealizedPnl": "-1.5",
            "updatedTime": int((time.time() - 10) * 1000),
        }]
        
        async def mock_fetch(limit=30):
            return mock_ended
        client.fetch_ended_prediction_positions = mock_fetch

        # Run settlement
        pnl, events = await client.settle_expired_positions(active_market_ids={"NEW_ROUND"}, current_prices={"ETHUSDT": 2697.0})
        
        assert len(events) == 1
        event = events[0]
        assert event["won"] is False
        assert event["pnl"] == -1.50
        assert event["position_id"] == pos_id
        
        # Check closed positions history
        closed = client.get_closed_positions()
        assert len(closed) == 1
        assert closed[0]["result"] == "LOSS"
        assert closed[0]["realized_pnl"] == -1.50
        assert closed[0]["is_claimed"] is False  # Never marked claimed for a loss!
        
        # Check RiskGuard recorded the real loss and escalated Martingale
        guard.record_settlement_result(won=event["won"], pnl=event["pnl"], symbol=event["symbol"])
        assert guard.consecutive_losses == 1
        assert guard.get_symbol_martingale_step("ETHUSDT") == 1
        assert "ไม้แก้ 1" in guard.get_stage_label("ETHUSDT")
        assert guard.get_symbol_accumulated_loss("ETHUSDT") == 1.50

    asyncio.run(_run())


def test_reconcile_existing_misreported_trade():
    """
    Verify that sync_historical_closed_positions corrects a past misreported trade
    from WIN (+0.45, claimed=True) to LOSS (-1.50, claimed=False).
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        
        token_id = "TOKEN_MISREPORTED_888"
        
        # Previously misreported item in bot memory
        misreported = ClosedPositionInfo(
            position_id="POS_OLD_WRONG",
            market_id="ETHUSDT-5M-R5968289",
            symbol="ETHUSDT",
            side="UP",
            contracts=1,
            entry_price=0.548,
            target_price=2696.19,
            settlement_price=2697.01,
            timeframe="5m",
            result="WIN",  # Incorrectly recorded WIN
            realized_pnl=0.45,
            token_id=token_id,
            is_claimed=True,  # Incorrectly assumed claimed
            entry_time=time.time() - 600,
            settled_at=time.time() - 300,
        )
        client._closed_positions.append(misreported)

        # Official Binance response: It actually lost -$1.50!
        mock_ended = [{
            "positionId": 123456,
            "tokenId": token_id,
            "marketTopicTitle": "Ethereum Up or Down - September 27, 12:25AM-12:30AM ET",
            "outcomeName": "Up",
            "shares": "2.94",
            "avgPrice": "0.51",
            "totalCost": "1.5",
            "positionStatus": "CLAIMED",
            "isWinner": False,  # LOST
            "finalOutcome": "Down",
            "unrealizedPnl": "-1.5",
            "updatedTime": int((time.time() - 300) * 1000),
        }]
        
        async def mock_fetch(limit=50):
            return mock_ended
        client.fetch_ended_prediction_positions = mock_fetch

        # Run history reconciliation
        updated = await client.sync_historical_closed_positions(limit=50)
        assert updated == 1
        
        # Verify corrected state
        assert misreported.result == "LOSS"
        assert misreported.realized_pnl == -1.50
        assert misreported.is_claimed is False

    asyncio.run(_run())


def test_oracle_strike_price_update():
    """Verify that ws_listener uses official oracle strike prices when provided."""
    listener = BinanceWSListener(enable_mock_stream=True)
    
    # Inject official strike price from Binance topic
    official_strikes = {"BTCUSDT-5m": 84533.295}
    listener.update_official_strike_prices(official_strikes)
    
    assert listener._official_strike_prices["BTCUSDT-5m"] == 84533.295
