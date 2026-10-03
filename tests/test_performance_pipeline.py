import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.binance_client import BinanceClient, PreFlightVerificationResult
from engine.jev_client import JevEvaluationResult, MarketContext
from engine.performance import DashboardSender, PerformanceMetrics
from streams.ws_listener import BinanceWSListener
from main import TradingBotCoordinator


def context(identifier="round-1"):
    now = time.time()
    return MarketContext(
        market_id=identifier, symbol="BTCUSDT", question="Up or Down", timeframe="15m",
        odds_yes=.5, odds_no=.5, underlying_price=101, target_price=100,
        time_left_seconds=200, round_start_time_sec=now - 700,
        round_end_time_sec=now + 200, strike_confirmed=True,
        spot_source_timestamp_ms=int(now * 1000), spot_data_age_ms=0,
        indicator_data_ready=True, obi_available=True, momentum_available=True,
    )


def coordinator():
    bot = TradingBotCoordinator()
    bot.bot_status = "RUNNING"
    bot.is_paused = False
    bot.target_symbol = bot.target_timeframe = "ALL"
    bot.active_symbols = ["BTCUSDT"]
    bot.binance_client.paper_trading = True
    bot.max_ai_concurrency = 4
    bot.risk_guard.max_concurrent_positions = 20
    bot.risk_guard.min_time_left_seconds = 60
    bot.risk_guard.confidence_threshold = .8
    bot.binance_client.get_positions = lambda: []
    bot.binance_client.get_prediction_quote_snapshot = AsyncMock(return_value={
        "up_ask": .5, "down_ask": .5, "timestamp": time.time(), "source": "test",
    })
    return bot


def test_metrics_bound_memory_and_report_tail_latency():
    metrics = PerformanceMetrics(window=3)
    for _ in range(10):
        metrics.observe("ai", time.perf_counter() - .01)
    stats = metrics.snapshot()["stages"]["ai"]
    assert stats["samples"] == 3
    assert stats["p99_ms"] >= stats["p95_ms"] >= 10


def test_slow_socket_does_not_delay_fast_socket_and_heartbeats_merge():
    async def run():
        release = asyncio.Event()
        delivered = asyncio.Event()
        received = []

        async def slow_send(message):
            await release.wait()

        async def fast_send(message):
            received.append(message)
            delivered.set()

        slow = SimpleNamespace(send_json=slow_send, close=AsyncMock())
        fast = SimpleNamespace(send_json=fast_send, close=AsyncMock())
        metrics = PerformanceMetrics()
        senders = [DashboardSender(socket, lambda: True, lambda: None, metrics)
                   for socket in (slow, fast)]
        for sender in senders:
            sender.enqueue({"type": "HEARTBEAT", "value": 1, "closed_positions": [1]})
            sender.enqueue({"type": "HEARTBEAT", "value": 2})
        await asyncio.wait_for(delivered.wait(), .5)
        assert not release.is_set()
        assert received == [{"type": "HEARTBEAT", "value": 2, "closed_positions": [1]}]
        for sender in senders:
            sender.task.cancel()
        await asyncio.gather(*(sender.task for sender in senders))
        assert metrics.snapshot()["stages"]["dashboard_send"]["samples"] == 1

    asyncio.run(run())


def test_sender_preserves_event_order_and_disconnects_on_overflow():
    async def run():
        socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        disconnected = []
        metrics = PerformanceMetrics()
        sender = DashboardSender(socket, lambda: True, lambda: disconnected.append(True), metrics, capacity=2)
        for number in range(3):
            sender.enqueue({"type": "MARKET_EVALUATION", "number": number})
        await asyncio.wait_for(sender.task, .5)
        assert disconnected == [True]
        socket.close.assert_awaited_once()
        assert metrics.counters["dashboard_overflow"] == 1
        assert len(sender.events) <= 2
        sender = DashboardSender(socket, lambda: True, lambda: None, metrics)
        sender.enqueue({"type": "INITIAL_SNAPSHOT"})
        sender.enqueue({"type": "MARKET_EVALUATION"})
        for _ in range(10):
            await asyncio.sleep(0)
        assert [call.args[0]["type"] for call in socket.send_json.await_args_list] == [
            "INITIAL_SNAPSHOT", "MARKET_EVALUATION",
        ]
        sender.task.cancel()
        await sender.task

    asyncio.run(run())


def test_sender_checks_expired_authorization_before_delivery():
    async def run():
        socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        sender = DashboardSender(socket, lambda: False, lambda: None, PerformanceMetrics())
        sender.enqueue({"type": "HEARTBEAT"})
        await sender.task
        socket.send_json.assert_not_awaited()
        socket.close.assert_awaited_once()

    asyncio.run(run())


def test_tick_burst_keeps_only_latest_pending_event():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        observed = []

        async def callback(market):
            observed.append(market.underlying_price)
            entered.set()
            await release.wait()

        listener = BinanceWSListener(on_market_event=callback)
        listener._schedule_market_event(context())
        await entered.wait()
        for price in range(1000):
            market = context()
            market.underlying_price = price
            listener._schedule_market_event(market)
        assert len(listener._dispatch_tasks) == len(listener._pending_events) == 1
        tasks = list(listener._dispatch_tasks.values())
        release.set()
        await asyncio.gather(*tasks)
        assert observed == [101, 999]
        assert not listener._pending_events and not listener._dispatch_tasks

    asyncio.run(run())


def test_ai_capacity_skips_without_consuming_cadence_and_recovers():
    async def run():
        bot = coordinator()
        release = asyncio.Event()

        async def evaluate(market):
            await release.wait()
            return JevEvaluationResult(action="UP", confidence=.1, probability_up=.5, reasoning="test")

        bot.jev_client.evaluate_market = AsyncMock(side_effect=evaluate)
        tasks = [asyncio.create_task(bot.on_market_tick(context(f"round-{i}"))) for i in range(6)]
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(bot._eval_in_progress) == 4
        assert bot.jev_client.evaluate_market.await_count == 4
        assert "round-4" not in bot.last_eval_time
        release.set()
        await asyncio.gather(*tasks)
        assert not bot._eval_in_progress
        await bot.on_market_tick(context("round-4"))
        assert bot.jev_client.evaluate_market.await_count == 5
        assert bot.performance.counters["ai_capacity_skipped"] == 2

    asyncio.run(run())


def test_cancelled_ai_releases_capacity():
    async def run():
        bot = coordinator()
        bot.jev_client.evaluate_market = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await bot.on_market_tick(context())
        assert not bot._eval_in_progress
        assert bot.performance.snapshot()["stages"]["pipeline"]["samples"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["stale", "rollover", "expiry", "missing"])
def test_live_decision_is_rejected_if_market_changes_during_ai(change):
    async def run():
        bot = coordinator()
        bot.binance_client.paper_trading = False
        bot.ws_listener.enable_mock_stream = False
        bot.binance_client.get_supported_prediction_symbols = lambda: {"BTCUSDT"}
        market = context()
        bot.ws_listener._active_markets["BTCUSDT_15m"] = market

        async def evaluate(_):
            latest = market.model_copy(deep=True)
            if change == "stale":
                latest.spot_source_timestamp_ms -= 10000
            elif change == "rollover":
                latest.market_id = "next-round"
            elif change == "expiry":
                latest.round_end_time_sec = time.time() + 5
            if change == "missing":
                bot.ws_listener._active_markets.clear()
            else:
                bot.ws_listener._active_markets["BTCUSDT_15m"] = latest
            return JevEvaluationResult(action="UP", confidence=.99, probability_up=.9, reasoning="test")

        bot.jev_client.evaluate_market = AsyncMock(side_effect=evaluate)
        bot.binance_client.verify_pre_flight_readiness = AsyncMock()
        bot.binance_client.place_prediction_order = AsyncMock()
        await bot.on_market_tick(market)
        bot.binance_client.verify_pre_flight_readiness.assert_not_awaited()
        bot.binance_client.place_prediction_order.assert_not_awaited()
        assert bot.performance.counters["stale_decision_rejected"] == 1

    asyncio.run(run())


def test_live_refresh_uses_latest_price_without_losing_quote_or_recovery_context():
    bot = coordinator()
    bot.binance_client.paper_trading = False
    bot.ws_listener.enable_mock_stream = False
    bot.binance_client.get_supported_prediction_symbols = lambda: {"BTCUSDT"}
    original = context()
    original.contract_up_ask = .51
    original.martingale_step = 2
    latest = original.model_copy(update={"underlying_price": 105, "contract_up_ask": None})
    bot.ws_listener._active_markets["BTCUSDT_15m"] = latest
    refreshed = bot._refresh_execution_market(original)
    assert refreshed.underlying_price == 105
    assert refreshed.contract_up_ask == .51
    assert refreshed.martingale_step == 2
    assert refreshed is not latest


def test_history_pagination_is_newest_first_and_serializes_only_page():
    client = BinanceClient(paper_trading=True)
    rows = [SimpleNamespace(model_dump=lambda i=i: {"position_id": str(i)}) for i in range(125)]
    client._closed_positions = rows
    first = client.get_closed_positions_page()
    second = client.get_closed_positions_page(offset=50)
    last = client.get_closed_positions_page(offset=100)
    assert first["closed_positions"][0]["position_id"] == "124"
    assert second["closed_positions"][0]["position_id"] == "74"
    assert len(last["closed_positions"]) == 25
    assert first["closed_positions_total"] == 125
    assert client.get_closed_positions_page(offset=200)["closed_positions"] == []


def test_sender_shutdown_before_first_turn_cleans_up():
    async def run():
        disconnected = []
        socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        sender = DashboardSender(socket, lambda: True, lambda: disconnected.append(True), PerformanceMetrics())
        await sender.stop()
        assert disconnected == [True]
        socket.close.assert_awaited_once()
    asyncio.run(run())


def test_history_delta_is_computed_after_heartbeat_coalescing_and_handles_removal():
    async def run():
        sent = asyncio.Queue()
        async def send(message):
            await sent.put(message)
        socket = SimpleNamespace(send_json=send, close=AsyncMock())
        sender = DashboardSender(socket, lambda: True, lambda: None, PerformanceMetrics())
        sender.enqueue({"type": "INITIAL_SNAPSHOT", "closed_positions": [
            {"position_id": "a", "pnl": 0}, {"position_id": "b", "pnl": 0},
        ]})
        await sent.get()
        # Both changes must survive coalescing before the next send.
        sender.enqueue({"type": "HEARTBEAT", "closed_positions": [
            {"position_id": "a", "pnl": 1}, {"position_id": "b", "pnl": 0},
        ]})
        sender.enqueue({"type": "HEARTBEAT", "closed_positions": [
            {"position_id": "a", "pnl": 1}, {"position_id": "b", "pnl": 2},
        ]})
        message = await sent.get()
        assert message["closed_positions_delta"]["upserts"] == [
            {"position_id": "a", "pnl": 1}, {"position_id": "b", "pnl": 2},
        ]
        sender.enqueue({"type": "HEARTBEAT", "closed_positions": []})
        message = await sent.get()
        assert message["closed_positions_delta"] == {"upserts": [], "order": []}
        await sender.stop()
    asyncio.run(run())


def test_history_deduplication_still_sends_edits_below_first_row(monkeypatch):
    async def run():
        monkeypatch.setattr("main._username_for_digest", lambda _: "tester")
        bot = coordinator()
        socket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        # SimpleNamespace is unhashable; use a regular fake socket for the registry.
        class Socket:
            send_json = socket.send_json
            close = socket.close
        client = Socket()
        history = [{"position_id": "new", "pnl": 1}, {"position_id": "old", "pnl": 0}]
        bot._enqueue_dashboard_client(client, {"type": "INITIAL_SNAPSHOT", "closed_positions": history})
        bot._enqueue_dashboard_client(client, {"type": "MARKET_EVALUATION", "closed_positions": history})
        changed = [history[0], {"position_id": "old", "pnl": 2}]
        bot._enqueue_dashboard_client(client, {"type": "MARKET_EVALUATION", "closed_positions": changed})
        for _ in range(15):
            await asyncio.sleep(0)
        messages = [call.args[0] for call in socket.send_json.await_args_list]
        assert "closed_positions" in messages[0]
        assert "closed_positions" not in messages[1]
        assert messages[2]["closed_positions_delta"] == {
            "upserts": [{"position_id": "old", "pnl": 2}], "order": ["new", "old"],
        }
        await bot._dashboard_senders[client].stop()
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["quote", "preflight"])
def test_live_pipeline_rejects_stale_refreshed_quote_or_post_preflight_market(stage):
    async def run():
        bot = coordinator()
        bot.binance_client.paper_trading = False
        bot.ws_listener.enable_mock_stream = False
        bot.binance_client.get_supported_prediction_symbols = lambda: {"BTCUSDT"}
        market = context()
        bot.ws_listener._active_markets["BTCUSDT_15m"] = market
        bot.jev_client.evaluate_market = AsyncMock(return_value=JevEvaluationResult(
            action="UP", confidence=.99, probability_up=.9, reasoning="test"))
        if stage == "quote":
            bot.binance_client.get_prediction_quote_snapshot.side_effect = [
                {"up_ask": .5, "down_ask": .5, "timestamp": time.time(), "source": "test"},
                {"up_ask": .5, "down_ask": .5, "timestamp": time.time()-10, "source": "test"},
            ]

        async def verify(**kwargs):
            bot.ws_listener._active_markets["BTCUSDT_15m"].round_end_time_sec = time.time()+5
            return PreFlightVerificationResult(verified=True, quoted_price=.5, live_balance_usdt=100)

        bot.binance_client.verify_pre_flight_readiness = AsyncMock(side_effect=verify)
        bot.binance_client.place_prediction_order = AsyncMock()
        await bot.on_market_tick(market)
        bot.binance_client.place_prediction_order.assert_not_awaited()
        if stage == "quote":
            bot.binance_client.verify_pre_flight_readiness.assert_not_awaited()
            assert bot.performance.counters["stale_quote_rejected"] == 1
        else:
            bot.binance_client.verify_pre_flight_readiness.assert_awaited_once()
            assert "STALE_EXECUTION_INPUT" in bot.recent_decisions[-1]["pre_flight"]["reason"]
    asyncio.run(run())
