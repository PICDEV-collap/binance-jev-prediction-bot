import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock
from engine.binance_client import BinanceClient, PositionInfo, OrderResult


def test_unknown_order_not_in_ongoing_rejected():
    """
    If order verification returns UNKNOWN and the token is NOT in Binance ongoing positions,
    the bot MUST NOT open a ghost position. It must return REJECTED.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        client._in_flight_orders["CLI_123"] = time.time()

        # Mock _verify_live_order_status returning UNKNOWN
        client._verify_live_order_status = AsyncMock(return_value={"status": "UNKNOWN", "order": None})
        # Mock fetch_ongoing_prediction_positions returning empty list
        client.fetch_ongoing_prediction_positions = AsyncMock(return_value=([], 0, "0"))

        result = await client._process_dispatched_live_order(
            order_id="BIN_999",
            client_order_id="CLI_123",
            market_id="BTCUSDT-5M-TEST",
            symbol="BTCUSDT",
            side="DOWN",
            clean_side="DOWN",
            contracts=23,
            target_price=0.439,
            strike_price=109000.0,
            spot_price=109000.0,
            timeframe="5m",
            martingale_step=1,
            stage="ไม้ 2 (Recovery)",
            token_id="TOKEN_999",
            elapsed_ms=100.0,
        )

        assert result.status == "REJECTED"
        assert "unconfirmed on Binance" in result.error_message
        assert len(client._live_positions) == 0
        assert "CLI_123" not in client._in_flight_orders

    asyncio.run(_run())


def test_failed_order_rejected_with_error():
    """
    If order verification returns FAILED with error message,
    the bot must record REJECTED, preserve the error message, and NOT open a position.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        client._in_flight_orders["CLI_FAILED"] = time.time()

        client._verify_live_order_status = AsyncMock(
            return_value={"status": "FAILED", "order": None, "errorMessage": "Failed to execute the market order"}
        )

        result = await client._process_dispatched_live_order(
            order_id="BIN_FAILED_1",
            client_order_id="CLI_FAILED",
            market_id="BTCUSDT-5M-TEST",
            symbol="BTCUSDT",
            side="DOWN",
            clean_side="DOWN",
            contracts=23,
            target_price=0.439,
            strike_price=109000.0,
            spot_price=109000.0,
            timeframe="5m",
            martingale_step=1,
            stage="ไม้ 2 (Recovery)",
            token_id="TOKEN_FAIL",
            elapsed_ms=120.0,
        )

        assert result.status == "REJECTED"
        assert "Failed to execute the market order" in result.error_message
        assert len(client._live_positions) == 0
        assert "CLI_FAILED" not in client._in_flight_orders

    asyncio.run(_run())


def test_filled_order_confirmed():
    """
    If order verification returns FILLED, the position is added to _live_positions.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        client._in_flight_orders["CLI_FILLED"] = time.time()

        client._verify_live_order_status = AsyncMock(
            return_value={"status": "FILLED", "order": {"filledShareQty": 26, "price": 0.41}}
        )

        result = await client._process_dispatched_live_order(
            order_id="BIN_FILLED_1",
            client_order_id="CLI_FILLED",
            market_id="BTCUSDT-5M-TEST",
            symbol="BTCUSDT",
            side="DOWN",
            clean_side="DOWN",
            contracts=26,
            target_price=0.41,
            strike_price=109000.0,
            spot_price=109000.0,
            timeframe="5m",
            martingale_step=0,
            stage="ไม้ 1 (Base)",
            token_id="TOKEN_SUCCESS",
            elapsed_ms=90.0,
        )

        assert result.status == "FILLED"
        assert len(client._live_positions) == 1
        pos = list(client._live_positions.values())[0]
        assert pos.contracts == 26
        assert pos.entry_price == 0.41
        assert "CLI_FILLED" not in client._in_flight_orders

    asyncio.run(_run())


def test_unknown_order_confirmed_via_ongoing_list():
    """
    If order history check returned UNKNOWN due to network lag,
    but fetch_ongoing_prediction_positions finds the token on Binance,
    it successfully admits the position.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        client._in_flight_orders["CLI_LAG"] = time.time()

        client._verify_live_order_status = AsyncMock(return_value={"status": "UNKNOWN", "order": None})
        client.fetch_ongoing_prediction_positions = AsyncMock(
            return_value=([{"tokenId": "TOKEN_REAL", "shares": "26", "avgPrice": "0.410"}], 1, "10.66")
        )

        result = await client._process_dispatched_live_order(
            order_id="BIN_LAG_1",
            client_order_id="CLI_LAG",
            market_id="BTCUSDT-5M-TEST",
            symbol="BTCUSDT",
            side="DOWN",
            clean_side="DOWN",
            contracts=26,
            target_price=0.41,
            strike_price=109000.0,
            spot_price=109000.0,
            timeframe="5m",
            martingale_step=0,
            stage="ไม้ 1 (Base)",
            token_id="TOKEN_REAL",
            elapsed_ms=150.0,
        )

        assert result.status == "FILLED"
        assert len(client._live_positions) == 1
        pos = list(client._live_positions.values())[0]
        assert pos.contracts == 26.0
        assert pos.entry_price == 0.410

    asyncio.run(_run())


def test_take_profit_code_minus_9000_purges_phantom():
    """
    If early take-profit gets HTTP error code -9000 ("You have exceeded your available shares"),
    it must purge the phantom position immediately from active desk.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        client.enable_early_take_profit = True
        client.take_profit_odds = 0.82
        client._session = AsyncMock()

        phantom_pos = PositionInfo(
            position_id="POS_PHANTOM_1",
            market_id="BTCUSDT-5M-PHANTOM",
            symbol="BTCUSDT",
            side="DOWN",
            contracts=23,
            entry_price=0.439,
            current_price=109000.0,
            target_price=109000.0,
            timeframe="5m",
            unrealized_pnl=11.76,
            martingale_step=1,
            stage="ไม้ 2 (Recovery)",
            token_id="TOKEN_PHANTOM",
            entry_time=time.time() - 60,
        )
        client._live_positions["POS_PHANTOM_1"] = phantom_pos

        # Mock get_prediction_quote returning code -9000
        client.get_prediction_quote = AsyncMock(
            return_value={"code": -9000, "msg": "You have exceeded your available shares"}
        )

        # Run check_and_execute_early_take_profits with market odds meeting take_profit_odds (0.85 >= 0.82)
        markets = [{"market_id": "BTCUSDT-5M-PHANTOM", "odds_yes": 0.15, "odds_no": 0.85}]
        await client.check_and_execute_early_take_profits(markets=markets)

        # Position must have been purged
        assert "POS_PHANTOM_1" not in client._live_positions
        assert len(client._live_positions) == 0

    asyncio.run(_run())


def test_cross_reconciliation_purges_ghost_position():
    """
    When sync_historical_closed_positions runs, an unconfirmed position older than 25s
    that is neither in ongoing positions nor ended positions must be purged.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)

        ghost_pos = PositionInfo(
            position_id="POS_GHOST_OLD",
            market_id="BTCUSDT-5M-GHOST",
            symbol="BTCUSDT",
            side="DOWN",
            contracts=23,
            entry_price=0.439,
            current_price=109000.0,
            target_price=109000.0,
            timeframe="5m",
            unrealized_pnl=11.76,
            martingale_step=1,
            stage="ไม้ 2 (Recovery)",
            token_id="TOKEN_GHOST",
            entry_time=time.time() - 35,  # 35 seconds ago (> 25s grace period)
        )
        client._live_positions["POS_GHOST_OLD"] = ghost_pos

        # Both ongoing and ended positions are empty (ghost never existed on Binance)
        client.fetch_ended_prediction_positions = AsyncMock(return_value=[])
        client.fetch_ongoing_prediction_positions = AsyncMock(return_value=([], 0, "0"))

        await client.sync_historical_closed_positions(limit=10)

        # Ghost position must be purged
        assert "POS_GHOST_OLD" not in client._live_positions
        assert len(client._live_positions) == 0

    asyncio.run(_run())
