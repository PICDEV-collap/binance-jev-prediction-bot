import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
from unittest.mock import AsyncMock
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


def test_live_order_history_uses_the_filled_positions_timeframe():
    """The live order log must not fall back to OrderResult's 15m default."""
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        client._verify_live_order_status = AsyncMock(return_value={
            "status": "FILLED",
            "order": {"filledShareQty": "2", "price": "0.58"},
        })

        result = await client._process_dispatched_live_order(
            order_id="ORDER_5M",
            client_order_id="CLIENT_5M",
            market_id="ETHUSDT-5M-R1790694600000",
            symbol="ETHUSDT",
            side="DOWN",
            clean_side="DOWN",
            contracts=2,
            target_price=0.58,
            strike_price=2716.02,
            spot_price=2715.25,
            timeframe="5m",
            martingale_step=0,
            stage="Base",
            token_id="TOKEN_5M",
            elapsed_ms=25.0,
        )

        assert result.status == "FILLED"
        assert result.timeframe == "5m"
        position = next(iter(client._live_positions.values()))
        assert position.timeframe == result.timeframe
        assert position.market_id == result.market_id

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


def test_unscoped_oracle_strike_is_not_cached_or_applied():
    """A flat symbol/timeframe strike must not leak into an unidentified live round."""
    listener = BinanceWSListener(enable_mock_stream=False)
    now = time.time()

    listener.update_official_strike_prices({"BTCUSDT-5m": 84533.295})
    contexts = listener._normalize_market_data({
        "s": "BTCUSDT",
        "c": "84550.00",
        "B": "5.0",
        "A": "5.0",
        "E": int(now * 1000),
    })

    btc_5m = next(context for context in contexts if context.timeframe == "5m")
    assert btc_5m.strike_confirmed is False
    assert btc_5m.target_price == 0.0


def test_early_take_profit_reconciliation_parity():
    """
    Verify that an early take-profit position with partial remainder shares
    prioritizes realizedPnl (+$15.15), aggregates bought shares (27x),
    and tags outcome as TAKE_PROFIT.
    """
    async def _run():
        from unittest.mock import AsyncMock
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)

        token_id = "TOKEN_TP_WIN_840232"
        market_id = 12757205

        mock_ended = [{
            "positionId": 16161249,
            "tokenId": token_id,
            "marketId": market_id,
            "marketTopicTitle": "Bitcoin Up or Down - September 28, 1:30AM-1:35AM ET",
            "outcomeName": "Down",
            "shares": "0.96",  # Unsold remainder
            "avgPrice": "0.41836735",
            "totalCost": "0.4024489",
            "positionStatus": "CLAIMED",
            "isWinner": True,
            "finalOutcome": "Down",
            "unrealizedPnl": "0.5575511",  # PnL of remaining 0.96 shares
            "realizedPnl": "15.15155122",  # TOTAL realized profit of the round
            "updatedTime": int((time.time() - 100) * 1000),
        }]

        mock_orders = [
            {
                "orderId": "26092800001921651740",
                "marketId": market_id,
                "side": "BUY",
                "status": "FILLED",
                "filledShareQty": "26.96",
                "price": "0.41",
            },
            {
                "orderId": "26092800001921655655",
                "marketId": market_id,
                "side": "SELL",
                "status": "FILLED",
                "filledShareQty": "26.0",
                "price": "0.98",
            }
        ]

        client.fetch_ended_prediction_positions = AsyncMock(return_value=mock_ended)
        client.fetch_prediction_order_history = AsyncMock(return_value=mock_orders)

        count = await client.sync_historical_closed_positions(limit=10)
        assert count == 1

        closed = client.get_closed_positions()
        assert len(closed) == 1
        pos = closed[0]

        # Must have full profit $15.15, not the partial remainder $0.56!
        assert pos["realized_pnl"] == 15.15
        # Must reflect the 27 bought contracts, not 1x!
        assert pos["contracts"] == 27
        # Must be tagged as TAKE_PROFIT
        assert pos["result"] == "TAKE_PROFIT"
        assert pos["is_claimed"] is True

    asyncio.run(_run())


def test_history_sync_keeps_net_pnl_and_corrects_existing_market_metadata():
    """A losing outcome can still have positive net PnL after a partial sale."""
    async def _run():
        for official_pnl in (0.43, 0.0):
            client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
            token_id = f"TOKEN_PARTIAL_LOSS_{official_pnl}"
            existing = ClosedPositionInfo(
                position_id="POS_OLD_ETH",
                market_id="ETHUSDT-1H-B12931620",
                symbol="ETHUSDT",
                side="DOWN",
                contracts=3,
                entry_price=0.52,
                timeframe="1h",
                result="LOSS",
                realized_pnl=-0.46,
                token_id=token_id,
                entry_time=time.time() - 600,
                settled_at=time.time() - 300,
            )
            client._closed_positions.append(existing)
            client.fetch_ended_prediction_positions = AsyncMock(return_value=[{
                "positionId": 12931620,
                "marketId": 12931620,
                "tokenId": token_id,
                "marketTopicTitle": "Ethereum Up or Down - September 29, 10:35AM-10:40AM ET",
                "outcomeName": "Down",
                "shares": "0.88",
                "avgPrice": "0.52",
                "totalCost": "1.50",
                "isWinner": False,
                "unrealizedPnl": "-0.46",
                "realizedPnl": str(official_pnl),
                "updatedTime": int((time.time() - 10) * 1000),
            }])
            client.fetch_prediction_order_history = AsyncMock(return_value=[
                {
                    "marketId": 12931620,
                    "side": "BUY",
                    "status": "FILLED",
                    "filledShareQty": "2.88",
                    "filledUsdtAmount": "1.50",
                },
                {
                    "marketId": 12931620,
                    "side": "SELL",
                    "status": "FILLED",
                    "filledShareQty": "2.00",
                    "filledUsdtAmount": "1.93",
                },
            ])
            client.fetch_ongoing_prediction_positions = AsyncMock(return_value=([], 0, 0))

            assert await client.sync_historical_closed_positions(limit=10) == 1
            synced = client.get_closed_positions()[0]
            assert synced["result"] == "LOSS"
            assert synced["realized_pnl"] == 0.43
            assert synced["timeframe"] == "5m"
            assert synced["market_id"] == "ETHUSDT-5M-B12931620"
            assert synced["contracts"] == 3

    asyncio.run(_run())


def test_early_take_profit_reconstructed_from_orders_alone():
    """
    Verify that when Binance tab=ENDED omits a 100% early-sold position (0 shares remaining at expiry),
    sync_historical_closed_positions accurately reconstructs the closed TAKE_PROFIT position
    from filled sell orders in order/history, and RiskGuard resets Martingale to Step 0.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        guard = RiskGuard(martingale_enabled=True)
        # Set ETH in a simulated loss state first
        guard._symbol_martingale_step["ETHUSDT"] = 1
        guard._symbol_accumulated_loss["ETHUSDT"] = 0.51
        guard._symbol_consecutive_losses["ETHUSDT"] = 1
        guard._symbol_last_result["ETHUSDT"] = "LOSS"

        market_id = 12846549

        # tab=ENDED is EMPTY because position was 100% sold early before expiration
        client.fetch_ended_prediction_positions = AsyncMock(return_value=[])

        # order/history has the complete BUY and SELL execution history
        mock_orders = [
            {
                "orderId": "26092800001923606296",
                "marketId": market_id,
                "marketTopicId": 6164109,
                "marketTopicTitle": "Ethereum Up or Down - September 28, 7PM ET",
                "slug": "ethereum-up-or-down-september-28-2026-7pm-et",
                "outcome": "Up",
                "side": "SELL",
                "status": "FILLED",
                "filledShareQty": "0.15",
                "price": "0.96",
                "realizedPnl": "0.05413266",
                "terminalTime": 1790639962874,
            },
            {
                "orderId": "26092800001923585863",
                "marketId": market_id,
                "marketTopicId": 6164109,
                "marketTopicTitle": "Ethereum Up or Down - September 28, 7PM ET",
                "slug": "ethereum-up-or-down-september-28-2026-7pm-et",
                "outcome": "Up",
                "side": "SELL",
                "status": "FILLED",
                "filledShareQty": "11.0",
                "price": "0.92",
                "realizedPnl": "3.52092873",
                "terminalTime": 1790638853933,
            },
            {
                "orderId": "26092800001923565534",
                "marketId": market_id,
                "marketTopicId": 6164109,
                "marketTopicTitle": "Ethereum Up or Down - September 28, 7PM ET",
                "slug": "ethereum-up-or-down-september-28-2026-7pm-et",
                "outcome": "Up",
                "side": "BUY",
                "status": "FILLED",
                "filledShareQty": "11.15",
                "price": "0.59",
                "createTime": 1790637874134,
            },
        ]
        client.fetch_prediction_order_history = AsyncMock(return_value=mock_orders)

        count = await client.sync_historical_closed_positions(limit=10)
        assert count == 1

        closed = client.get_closed_positions()
        assert len(closed) == 1
        pos = closed[0]

        assert pos["symbol"] == "ETHUSDT"
        assert pos["timeframe"] == "1h"
        assert pos["result"] == "TAKE_PROFIT"
        assert pos["realized_pnl"] == 3.58
        assert pos["contracts"] == 11
        assert pos["is_claimed"] is True
        assert pos["settled_at"] == 1790639962.874

        # RiskGuard must reconcile and reset ETHUSDT to Martingale Step 0 with $0.00 loss
        guard.reconcile_from_closed_positions(client.get_closed_positions())
        assert guard.get_symbol_martingale_step("ETHUSDT") == 0
        assert guard.get_symbol_accumulated_loss("ETHUSDT") == 0.0
        assert guard._symbol_last_result["ETHUSDT"] == "WIN"
        assert "ไม้ 1 (Base)" in guard.get_stage_label("ETHUSDT")

    asyncio.run(_run())
