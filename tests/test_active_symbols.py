import asyncio
import time

import pytest

from engine.binance_client import BinanceClient
from engine.symbols import normalize_active_symbols, normalize_symbol
from streams.ws_listener import BinanceWSListener


def test_normalize_active_symbols_cleans_and_deduplicates_pairs():
    assert normalize_symbol(" ada/usdt ") == "ADAUSDT"
    assert normalize_active_symbols(["adausdt", "ADA/USDT", "SOLUSDT"]) == (
        "ADAUSDT",
        "SOLUSDT",
    )


def test_normalize_active_symbols_rejects_empty_invalid_and_oversized_lists():
    with pytest.raises(ValueError, match="At least one"):
        normalize_active_symbols([])
    with pytest.raises(ValueError, match="Invalid trading pair"):
        normalize_active_symbols(["ADAUSD"])
    with pytest.raises(ValueError, match="maximum of 24"):
        normalize_active_symbols([f"X{i:02d}USDT" for i in range(25)])


def test_prediction_catalog_discovers_unlisted_crypto_pairs_and_fails_closed():
    client = BinanceClient(paper_trading=True)
    client._prediction_market_cache = {
        "marketTopics": [
            {
                "symbol": "ADA",
                "title": "Cardano Up or Down 5m",
                "chartType": "CRYPTO_UP_DOWN",
            },
            {
                "symbol": "SUIUSDT",
                "title": "Sui Up or Down 15m",
                "chartType": "CRYPTO_UP_DOWN",
            },
            {
                "symbol": "XRPUSDT",
                "title": "XRP price range 5m",
                "chartType": "OTHER",
            },
        ]
    }

    assert client.get_supported_prediction_symbols() == {"ADAUSDT", "SUIUSDT"}
    client._prediction_market_cache = {}
    assert client.get_supported_prediction_symbols() == set()


def test_prediction_oracle_strikes_resolve_new_pairs():
    now = time.time()
    client = BinanceClient(paper_trading=True)
    client._prediction_market_cache = {
        "marketTopics": [
            {
                "symbol": "ADAUSDT",
                "title": "Cardano Up or Down 5m",
                "chartType": "CRYPTO_UP_DOWN",
                "startDate": (now - 30) * 1000,
                "endDate": (now + 270) * 1000,
                "variantData": {"startPrice": "0.42"},
                "topicId": "topic-ada",
                "markets": [{"marketId": "market-ada"}],
            }
        ]
    }

    details = client.get_detailed_oracle_strikes(now_ts=now)
    assert details["ADAUSDT-5m"]["start_price"] == 0.42
    assert details["market-ada"]["symbol"] == "ADAUSDT"


def test_listener_accepts_new_pairs_and_reconfigures_streams():
    listener = BinanceWSListener(
        stream_url="wss://stream.binance.com:9443/stream?streams=btcusdt@ticker&key=value",
        active_symbols=["DOGEUSDT", "SOLUSDT"],
    )
    assert "dogeusdt@ticker/solusdt@ticker" in listener.stream_url
    assert "key=value" in listener.stream_url

    contexts = listener._normalize_market_data(
        {
            "s": "SOLUSDT",
            "c": "145.25",
            "q": "1200000",
            "E": int(time.time() * 1000),
            "B": "100",
            "A": "80",
        }
    )
    assert len(contexts) == 4
    assert {context.symbol for context in contexts} == {"SOLUSDT"}
    assert listener._normalize_market_data({"s": "XRPUSDT", "c": "0.6"}) == []

    asyncio.run(listener.configure_active_symbols(["ada/usdt", "SOLUSDT"]))
    assert listener.active_symbols == ["ADAUSDT", "SOLUSDT"]
    assert "adausdt@ticker/solusdt@ticker" in listener.stream_url
