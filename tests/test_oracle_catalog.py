import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from engine.binance_client import BinanceClient
from engine.oracle import round_window, start_price


def topic(identifier=123, duration=300, price=None):
    now = time.time()
    result = {
        "marketTopicId": identifier, "symbol": "BTCUSDT", "title": "Bitcoin Up or Down",
        "chartType": "CRYPTO_UP_DOWN", "startDate": (now - 20) * 1000,
        "endDate": (now - 20 + duration) * 1000, "markets": [{"marketId": 456}],
    }
    if price is not None:
        result["variantData"] = {"startPrice": price}
    return result


class Reply:
    def __init__(self, status, data, headers=None):
        self.status, self.data, self.headers = status, data, headers or {}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    async def json(self):
        return self.data


class Session:
    closed = False
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
    def get(self, url, **kwargs):
        self.requests.append(url)
        return next(self.replies)


def client(replies=()):
    instance = BinanceClient(api_key="test-key", api_secret="test-secret", paper_trading=True)
    instance._session = Session(replies)
    return instance


def test_list_without_variant_fetches_detail_and_unlocks_exact_round():
    topic_copy = topic()
    async def exact_run():
        listed = dict(topic_copy)
        detailed = {**listed, "variantData": {"startPrice": "84655.39"}}
        instance = client([
            Reply(200, {"marketTopics": [listed], "total": 1}), Reply(200, detailed),
            Reply(200, {"marketTopics": [dict(topic_copy)], "total": 1}),
        ])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance.get_market_start_prices()["BTCUSDT-5m"] == 84655.39
        assert "marketTopicId=123" in instance._session.requests[1]
        assert instance.get_oracle_sync_status()["state"] == "READY"
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert len(instance._session.requests) == 3
        assert instance.get_market_start_prices()["456"] == 84655.39
    asyncio.run(exact_run())


@pytest.mark.parametrize("status,code,state", [(400, -2015, "API_AUTH_ERROR"), (401, None, "API_AUTH_ERROR"), (429, -1003, "RATE_LIMITED"), (500, None, "API_ERROR")])
def test_api_failure_is_visible_throttled_and_does_not_leak_response(status, code, state, caplog):
    async def run():
        instance = client([Reply(status, {"code": code, "msg": "secret-response-must-not-be-logged"})])
        assert await instance.fetch_prediction_market_topics(force_refresh=True) == []
        assert await instance.fetch_prediction_market_topics(force_refresh=True) == []
        assert len(instance._session.requests) == 1
        assert instance.oracle_sync_status["state"] == state
        assert instance.oracle_sync_status["http_status"] == status
        assert instance.oracle_sync_status["api_code"] == code
        assert "secret-response" not in caplog.text
        assert "signature=" not in caplog.text
        assert "test-key" not in caplog.text
    asyncio.run(run())


def test_auth_recovery_after_backoff_fetches_current_round():
    async def run():
        current = topic(price="123.45")
        instance = client([
            Reply(400, {"code": -2015}), Reply(200, {"marketTopics": [current], "total": 1}),
        ])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        instance._prediction_retry_at = 0
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance.oracle_sync_status["state"] == "READY"
        assert instance.oracle_sync_status["api_code"] is None
        assert instance.get_market_start_prices()["BTCUSDT-5m"] == 123.45
    asyncio.run(run())


def test_clock_drift_retries_once_with_time_sync():
    async def run():
        instance = client([Reply(400, {"code": -1021}), Reply(200, {"marketTopics": [], "total": 0})])
        instance.sync_server_time = AsyncMock()
        await instance.fetch_prediction_market_topics(force_refresh=True)
        instance.sync_server_time.assert_awaited_once()
        assert len(instance._session.requests) == 2
        assert instance.oracle_sync_status["state"] == "WAITING_FOR_MARKET"
    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["id", "window", "symbol"])
def test_detail_must_match_requested_topic_round_and_symbol(mismatch):
    async def run():
        listed = topic()
        detailed = {**listed, "variantData": {"startPrice": "84655.39"}}
        if mismatch == "id":
            detailed["marketTopicId"] = 999
        elif mismatch == "window":
            detailed["startDate"] -= 300000
            detailed["endDate"] -= 300000
        else:
            detailed["symbol"] = "ETHUSDT"
        instance = client([Reply(200, {"marketTopics": [listed], "total": 1}), Reply(200, detailed)])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance.get_market_start_prices() == {}
        assert not instance._prediction_detail_cache
    asyncio.run(run())


@pytest.mark.parametrize("duration,timeframe", [(300, "5m"), (900, "15m"), (3600, "1h"), (86400, "1d")])
def test_timeframe_uses_dated_round_when_title_omits_duration(duration, timeframe):
    instance = client()
    instance._prediction_market_cache = {"marketTopics": [topic(duration=duration, price="100")]}
    assert f"BTCUSDT-{timeframe}" in instance.get_market_start_prices()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "bad", None])
def test_invalid_oracle_prices_never_confirm(value):
    assert start_price(topic(price=value)) is None


def test_json_variant_and_string_dates_are_supported_without_guessing_strike():
    current = topic()
    current["variantData"] = '{"startPrice":"84655.39"}'
    current["startDate"] = str(current["startDate"])
    current["endDate"] = str(current["endDate"])
    instance = client()
    instance._prediction_market_cache = {"marketTopics": [current]}
    assert instance.get_market_start_prices()["BTCUSDT-5m"] == 84655.39
    assert round_window({"startDate": "NaN", "endDate": 100}) is None


def test_expired_or_future_topics_are_not_hydrated():
    async def run():
        expired, future = topic(), topic(identifier=124)
        expired["startDate"] -= 300000
        expired["endDate"] -= 300000
        future["startDate"] += 300000
        future["endDate"] += 300000
        instance = client([Reply(200, {"marketTopics": [expired, future], "total": 2})])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert len(instance._session.requests) == 1
        assert instance.get_market_start_prices() == {}
    asyncio.run(run())


def test_concurrent_sync_calls_share_one_fetch():
    async def run():
        instance = client()
        entered, release = asyncio.Event(), asyncio.Event()
        async def page(*_):
            entered.set()
            await release.wait()
            return [topic(price="100")], 1, False
        instance._fetch_market_page = AsyncMock(side_effect=page)
        first = asyncio.create_task(instance.fetch_prediction_market_topics(force_refresh=True))
        await entered.wait()
        second = asyncio.create_task(instance.fetch_prediction_market_topics(force_refresh=True))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        instance._fetch_market_page.assert_awaited_once()
    asyncio.run(run())


def test_rate_limit_respects_retry_after():
    async def run():
        instance = client([Reply(429, {"code": -1003}, {"Retry-After": "120"})])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance._prediction_retry_at - time.monotonic() > 119
    asyncio.run(run())


def test_missing_credentials_reports_access_failure_without_network_request():
    async def run():
        instance = client()
        instance.api_key = ""
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance.oracle_sync_status["state"] == "API_AUTH_ERROR"
        assert not instance._session.requests
    asyncio.run(run())


def test_nested_list_and_detail_payloads_are_unwrapped():
    async def run():
        listed = topic()
        detailed = {**listed, "variantData": {"startPrice": "84655.39"}}
        instance = client([
            Reply(200, {"code": "000000", "data": {"marketTopics": [listed], "total": 1}}),
            Reply(200, {"code": "000000", "data": detailed}),
        ])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance.get_market_start_prices()["BTCUSDT-5m"] == 84655.39
    asyncio.run(run())


def test_unrecognized_round_interval_is_not_guessed_as_five_minutes():
    instance = client()
    instance._prediction_market_cache = {"marketTopics": [topic(duration=2000, price="100")]}
    assert instance.get_market_start_prices() == {}


def test_invalid_schema_is_visible_and_old_rounds_do_not_count_as_confirmed():
    async def run():
        instance = client([Reply(200, {"unexpected": []})])
        await instance.fetch_prediction_market_topics(force_refresh=True)
        assert instance.oracle_sync_status["state"] == "INVALID_RESPONSE"
        expired = topic(price="100")
        expired["startDate"] -= 300000
        expired["endDate"] -= 300000
        instance._prediction_market_cache = {"marketTopics": [expired]}
        instance.oracle_sync_status["state"] = "READY"
        assert instance.get_oracle_sync_status()["state"] == "WAITING_FOR_PRICE"
        assert instance.get_oracle_sync_status()["confirmed_rounds"] == 0
    asyncio.run(run())
