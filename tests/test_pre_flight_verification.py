import asyncio
import time
import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from engine.binance_client import BinanceClient, PreFlightVerificationResult
from engine.jev_client import JevEvaluationResult, MarketContext
from engine.risk_guard import RiskGuard


def test_pre_flight_blocks_duplicate_ongoing_position():
    """
    Verify that if Binance SAPI tab=ONGOING reports an open position
    for this market, pre-flight verification immediately blocks the order.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        
        # Mock fetch_ongoing_prediction_positions returning active position on market
        async def mock_ongoing(limit=20):
            return (
                [{"marketId": "BTC-5M-101", "positionId": 12345, "outcomeName": "Up"}],
                {"walletBalance": "71.89"},
                {"ongoingCount": 1}
            )
        client.fetch_ongoing_prediction_positions = mock_ongoing

        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="BTC-5M-101",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
        )

        assert res.verified is False
        assert res.has_duplicate_position is True
        assert "Active position already open" in res.reason

    asyncio.run(_run())


def test_pre_flight_blocks_insufficient_balance():
    """
    Verify that if Binance SAPI reports available balance < minimum or < estimated cost,
    pre-flight verification immediately blocks the order.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        
        # Mock walletBalance = 0.80 USDT (< 1.50 minimum)
        async def mock_ongoing(limit=20):
            return ([], {"walletBalance": "0.80"}, {"ongoingCount": 0})
        client.fetch_ongoing_prediction_positions = mock_ongoing

        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="BTC-5M-NEW",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
        )

        assert res.verified is False
        assert "Insufficient Binance live balance" in res.reason

    asyncio.run(_run())


def test_pre_flight_blocks_max_concurrent_positions():
    """
    Verify that if Binance reports ongoingCount >= max_concurrent_positions,
    pre-flight verification blocks the order to enforce exposure limits.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        
        async def mock_ongoing(limit=20):
            return (
                [{"marketId": f"M_{i}"} for i in range(5)],
                {"walletBalance": "100.00"},
                {"ongoingCount": 5}
            )
        client.fetch_ongoing_prediction_positions = mock_ongoing

        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="BTC-5M-NEW",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
            max_concurrent_positions=5,
        )

        assert res.verified is False
        assert "Max concurrent positions limit reached" in res.reason

    asyncio.run(_run())


def test_pre_flight_blocks_locked_market_round():
    """
    Verify that if Binance topic reports market round tradingStatus != OPEN,
    pre-flight verification blocks the order.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        
        async def mock_ongoing(limit=20):
            return ([], {"walletBalance": "100.00"}, {"ongoingCount": 0})
        client.fetch_ongoing_prediction_positions = mock_ongoing

        # Mock topic where market is LOCKED
        mock_topic = [{
            "symbol": "BTCUSDT",
            "title": "Bitcoin Up or Down 5m",
            "variantData": {"startPrice": "84500.00"},
            "markets": [{
                "marketId": "BTC-5M-LOCKED",
                "tradingStatus": "LOCKED",
                "outcomes": [
                    {"name": "Up", "tokenId": "TOK_UP_1"},
                    {"name": "Down", "tokenId": "TOK_DOWN_1"}
                ]
            }]
        }]
        async def mock_topics(force_refresh=False):
            return mock_topic
        client.fetch_prediction_market_topics = mock_topics

        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="BTC-5M-LOCKED",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
        )

        assert res.verified is False
        assert "not OPEN" in res.reason

    asyncio.run(_run())


def test_pre_flight_blocks_overpriced_quote():
    """
    Verify that if Binance quote price > max_odds_cap (e.g. 0.72 vs 0.60),
    pre-flight blocks order to protect risk/reward ratio.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False, max_odds_cap=0.60)
        
        async def mock_ongoing(limit=20):
            return ([], {"walletBalance": "100.00"}, {"ongoingCount": 0})
        client.fetch_ongoing_prediction_positions = mock_ongoing

        mock_topic = [{
            "symbol": "BTCUSDT",
            "title": "Bitcoin Up or Down 5m",
            "variantData": {"startPrice": "84500.00"},
            "markets": [{
                "marketId": "BTC-5M-VALID",
                "tradingStatus": "OPEN",
                "outcomes": [
                    {"name": "Up", "tokenId": "TOK_UP_1"},
                    {"name": "Down", "tokenId": "TOK_DOWN_1"}
                ]
            }]
        }]
        async def mock_topics(force_refresh=False):
            return mock_topic
        client.fetch_prediction_market_topics = mock_topics

        # Mock quote returning 0.75 (> 0.60 cap)
        async def mock_quote(**kwargs):
            return {"quoteId": "Q_OVERPRICED", "price": "0.75"}
        client.get_prediction_quote = mock_quote

        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="BTC-5M-VALID",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
        )

        assert res.verified is False
        assert "exceeds max odds cap" in res.reason

    asyncio.run(_run())


def test_pre_flight_success_and_risk_guard_integration():
    """
    Verify that when all Binance conditions are verified,
    pre-flight returns verified=True, authoritative balance & quote,
    and RiskGuard accurately computes sizing using Binance-confirmed data.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False, max_odds_cap=0.60)
        guard = RiskGuard(
            confidence_threshold=0.80,
            max_position_size_usdt=20.0,
            default_order_contracts=2,
            martingale_enabled=True,
            martingale_multiplier=2.0,
        )
        
        async def mock_ongoing(limit=20):
            return ([], {"walletBalance": "71.89"}, {"ongoingCount": 1})
        client.fetch_ongoing_prediction_positions = mock_ongoing

        mock_topic = [{
            "symbol": "BTCUSDT",
            "title": "Bitcoin Up or Down 5m",
            "variantData": {"startPrice": "84748.00"},
            "markets": [{
                "marketId": "BTC-5M-APPROVED",
                "tradingStatus": "OPEN",
                "outcomes": [
                    {"name": "Up", "tokenId": "TOK_UP_VALID"},
                    {"name": "Down", "tokenId": "TOK_DOWN_VALID"}
                ]
            }]
        }]
        async def mock_topics(force_refresh=False):
            return mock_topic
        client.fetch_prediction_market_topics = mock_topics

        # Mock quote returning favorable 0.45 odds
        async def mock_quote(**kwargs):
            return {"quoteId": "QUOTE_BINANCE_CONFIRMED_123", "price": "0.45"}
        client.get_prediction_quote = mock_quote

        # 1. Run Pre-Flight Verification
        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="BTCUSDT",
            market_id="BTC-5M-APPROVED",
            side="UP",
            timeframe="5m",
            estimated_cost_usdt=2.0,
        )

        assert res.verified is True
        assert res.live_balance_usdt == 71.89
        assert res.official_strike_price == 84748.00
        assert res.quoted_price == 0.45
        assert res.quote_id == "QUOTE_BINANCE_CONFIRMED_123"

        # 2. Integrate into RiskGuard Sizing
        decision = JevEvaluationResult(
            action="UP",
            confidence=0.88,
            horizon_minutes=5,
            edge_estimate=0.15,
            reasoning="Strong momentum confirmation"
        )
        market = MarketContext(
            market_id="BTC-5M-APPROVED",
            symbol="BTCUSDT",
            question="Will Bitcoin be above 84748 at expiration?",
            timeframe="5m",
            underlying_price=84760.0,
            target_price=res.official_strike_price,
            odds_yes=0.50,
            odds_no=0.50,
            spread=0.01,
            time_left_seconds=200,
        )

        risk_res = guard.validate_and_size_order(
            decision=decision,
            market=market,
            current_open_positions_count=res.active_ongoing_count,
            verified_balance_usdt=res.live_balance_usdt,
            confirmed_quote_price=res.quoted_price,
        )

        assert risk_res.approved is True
        assert risk_res.target_price == 0.45  # Confirmed Binance Quote price used!
        assert risk_res.adjusted_contracts >= 1
        # Total cost must be within verified balance
        assert (risk_res.adjusted_contracts * risk_res.target_price) <= res.live_balance_usdt

    asyncio.run(_run())


def test_pre_flight_blocks_with_in_flight_orders():
    """
    Verify that in-flight orders are counted towards max_concurrent_positions
    so racing async tasks cannot exceed the concurrency limit.
    """
    async def _run():
        client = BinanceClient(api_key="test_key", api_secret="test_secret", paper_trading=False)
        
        async def mock_ongoing(limit=20):
            # API reports 0 ongoing, but we simulate 1 order currently in-flight
            return ([], {"walletBalance": "100.00"}, {"ongoingCount": 0})
        client.fetch_ongoing_prediction_positions = mock_ongoing

        # Simulate an order currently in-flight
        client._in_flight_orders["BTC-5M-INFLIGHT"] = {"created_at": 123456789}

        res: PreFlightVerificationResult = await client.verify_pre_flight_readiness(
            symbol="ETHUSDT",
            market_id="ETH-5M-RACE",
            side="DOWN",
            timeframe="5m",
            estimated_cost_usdt=2.0,
            max_concurrent_positions=1,
        )

        assert res.verified is False
        assert "Max concurrent positions limit reached" in res.reason
        assert res.active_ongoing_count == 1

    asyncio.run(_run())


def test_trading_bot_coordinator_order_execution_lock_exists():
    """
    Verify TradingBotCoordinator has _order_execution_lock initialized as an asyncio.Lock.
    """
    from main import TradingBotCoordinator
    bot = TradingBotCoordinator()
    assert hasattr(bot, "_order_execution_lock")
    assert isinstance(bot._order_execution_lock, asyncio.Lock)

