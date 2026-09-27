import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import time
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from streams.ws_listener import BinanceWSListener
from engine.binance_client import BinanceClient, PreFlightVerificationResult
from engine.jev_client import MarketContext


def test_ws_listener_unconfirmed_strike_in_live_mode():
    """
    Verify that in live streaming mode (enable_mock_stream=False),
    ws_listener marks strike_confirmed=False and target_price=0.0
    when Binance oracle startPrice has not yet been received.
    """
    listener = BinanceWSListener(enable_mock_stream=False)
    
    # Process a live tick for BTCUSDT
    now = time.time()
    tick_payload = {
        "s": "BTCUSDT",
        "c": "84500.00",
        "h": "85000.00",
        "l": "84000.00",
        "v": "1500.0",
        "P": "0.50",
        "B": "5.0",
        "A": "5.0",
        "E": int(now * 1000),
    }
    
    contexts = listener._normalize_market_data(tick_payload)
    assert len(contexts) > 0
    
    btc_5m = next(c for c in contexts if c.timeframe == "5m")
    # In live mode with no official strike, strike_confirmed must be False
    assert btc_5m.strike_confirmed is False
    assert btc_5m.target_price == 0.0


def test_ws_listener_official_strike_unlocks_confirmed_status():
    """
    Verify that when update_official_strike_prices is called with Binance startPrice,
    the active round immediately receives strike_confirmed=True and exact target_price.
    """
    listener = BinanceWSListener(enable_mock_stream=False)
    now = time.time()
    
    # Inject official strike from Binance topic
    official_strike = 84533.295
    listener.update_official_strike_prices({"BTCUSDT-5m": official_strike})
    
    tick_payload = {
        "s": "BTCUSDT",
        "c": "84550.00",
        "h": "85000.00",
        "l": "84000.00",
        "v": "1500.0",
        "P": "0.50",
        "B": "5.0",
        "A": "5.0",
        "E": int(now * 1000),
    }
    
    contexts = listener._normalize_market_data(tick_payload)
    btc_5m = next(c for c in contexts if c.timeframe == "5m")
    assert btc_5m.strike_confirmed is True
    assert btc_5m.target_price == official_strike
    assert btc_5m.price_diff == round(84550.00 - official_strike, 4)


def test_binance_client_rejects_expired_topics_for_start_prices():
    """
    Verify that get_market_start_prices and get_detailed_oracle_strikes
    strictly filter out expired topics from previous rounds (endDate <= now).
    """
    client = BinanceClient(api_key="mock", api_secret="mock", paper_trading=True)
    
    now = 1790516400.0  # Exact round rollover point
    
    # Topic from expired round (ended at now)
    expired_topic = {
        "title": "BNB Up or Down 5m",
        "symbol": "BNBUSDT",
        "startDate": int((now - 300) * 1000),
        "endDate": int(now * 1000),  # expired!
        "variantData": {"startPrice": "780.115"},
        "markets": [{"marketId": "12672900"}]
    }
    
    # Topic for current round
    active_topic = {
        "title": "BTC Up or Down 5m",
        "symbol": "BTCUSDT",
        "startDate": int(now * 1000),
        "endDate": int((now + 300) * 1000),  # active!
        "variantData": {"startPrice": "85004.615"},
        "markets": [{"marketId": "12672918"}]
    }
    
    client._prediction_market_cache = {"marketTopics": [expired_topic, active_topic]}
    
    # Query at now + 5 seconds into new round
    start_prices = client.get_market_start_prices(now_ts=now + 5.0)
    
    # Expired BNB topic must NOT be in start_prices
    assert "BNBUSDT-5m" not in start_prices
    assert "12672900" not in start_prices
    
    # Active BTC topic must be present
    assert start_prices.get("BTCUSDT-5m") == 85004.615
    assert start_prices.get("12672918") == 85004.615


def test_oracle_gate_blocks_ai_evaluation_when_unconfirmed():
    """
    Verify that main.py on_market_tick blocks evaluation and order dispatch
    if strike_confirmed is False or target_price <= 0.
    """
    from main import TradingBotCoordinator
    
    bot = TradingBotCoordinator()
    bot.bot_status = "RUNNING"
    bot.is_paused = False
    bot.target_symbol = "BTCUSDT"
    bot.target_timeframe = "5m"
    bot.binance_client.paper_trading = True
    
    # Mock Jev client evaluate_market to detect if evaluation is triggered
    bot.jev_client.evaluate_market = AsyncMock()
    
    # Create market context with strike_confirmed = False
    market = MarketContext(
        market_id="BTCUSDT-5M-R1000",
        symbol="BTCUSDT",
        question="BTC Up or Down 5m",
        timeframe="5m",
        odds_yes=0.50,
        odds_no=0.50,
        time_left_seconds=200,
        underlying_price=85000.0,
        target_price=0.0,  # Unconfirmed
        strike_confirmed=False
    )
    
    # Trigger tick
    asyncio.run(bot.on_market_tick(market))
    
    # Ensure Jev AI was NOT evaluated
    bot.jev_client.evaluate_market.assert_not_called()
    assert "BTCUSDT-5M-R1000" not in bot.evaluated_rounds


def test_oracle_gate_permits_evaluation_when_strike_confirmed():
    """
    Verify that main.py on_market_tick permits evaluation once strike_confirmed=True
    and target_price > 0.
    """
    from main import TradingBotCoordinator
    from engine.jev_client import JevEvaluationResult
    
    bot = TradingBotCoordinator()
    bot.bot_status = "RUNNING"
    bot.is_paused = False
    bot.target_symbol = "BTCUSDT"
    bot.target_timeframe = "5m"
    bot.binance_client.paper_trading = True
    
    # Mock Jev evaluation return
    mock_decision = JevEvaluationResult(
        action="UP",
        confidence=0.50,
        reasoning="Market chop",
        model="jev-mock",
        latency_ms=10.0,
        is_mock=True
    )
    bot.jev_client.evaluate_market = AsyncMock(return_value=mock_decision)
    
    # Create market context with strike_confirmed = True
    market = MarketContext(
        market_id="BTCUSDT-5M-R2000",
        symbol="BTCUSDT",
        question="BTC Up or Down 5m",
        timeframe="5m",
        odds_yes=0.50,
        odds_no=0.50,
        time_left_seconds=200,
        underlying_price=85000.0,
        target_price=84950.0,
        strike_confirmed=True
    )
    
    asyncio.run(bot.on_market_tick(market))
    
    # Ensure Jev AI WAS evaluated
    bot.jev_client.evaluate_market.assert_called_once()
    assert "BTCUSDT-5M-R2000" in bot.evaluated_rounds


def test_pre_flight_blocks_when_oracle_strike_not_confirmed():
    """
    Verify that verify_pre_flight_readiness returns verified=False
    if official_strike <= 0.
    """
    client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
    
    async def mock_ongoing(limit=20):
        return ([], {"walletBalance": "100.00"}, {"ongoingCount": 0})
    client.fetch_ongoing_prediction_positions = mock_ongoing
    
    async def mock_topics(force_refresh=False):
        return [{
            "symbol": "BTCUSDT",
            "title": "Bitcoin Up or Down 5m",
            "variantData": {"startPrice": "0"},  # Unconfirmed or 0
            "markets": [{
                "marketId": "12345",
                "tradingStatus": "OPEN",
                "outcomes": [{"name": "Up", "tokenId": "TOK_1"}]
            }]
        }]
    client.fetch_prediction_market_topics = mock_topics
    
    res: PreFlightVerificationResult = asyncio.run(
        client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="12345",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
            strike_price=0.0
        )
    )
    
    assert res.verified is False
    assert "not yet confirmed" in res.reason or "not yet officially published" in res.reason
