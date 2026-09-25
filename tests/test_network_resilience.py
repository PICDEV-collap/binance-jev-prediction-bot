"""
Unit tests for Binance Official Prediction Markets alignment
and Network Disconnection & Fault Tolerance Protection System.
"""

import unittest
import time
from unittest.mock import MagicMock, AsyncMock, patch

from engine.jev_client import MarketContext, JevEvaluationResult
from engine.risk_guard import RiskGuard, RiskEvaluationResult
from engine.binance_client import BinanceClient, OrderResult
from streams.ws_listener import BinanceWSListener, ConnectionState


class TestNetworkResilienceAndBinanceOfficial(unittest.TestCase):
    def setUp(self):
        self.risk_guard = RiskGuard(
            confidence_threshold=0.80,
            max_position_size_usdt=50.0,
            default_order_contracts=10,
            cooldown_seconds=45,
            max_daily_loss_usdt=300.0,
            max_concurrent_positions=5,
            martingale_enabled=True,
            martingale_multiplier=2.0,
            martingale_max_steps=4,
        )

        self.fresh_market = MarketContext(
            market_id="BTCUSDT-5M-R1000",
            symbol="BTCUSDT",
            question="Will BTC close >= $85,000 at 5m expiration?",
            timeframe="5m",
            odds_yes=0.60,
            odds_no=0.40,
            spread=0.01,
            volume_24h=1500000.0,
            time_left_seconds=240,
            underlying_price=85050.0,
            target_price=85000.0,
            price_diff=50.0,
            momentum_pct=0.08,
            is_stale=False,
        )

        self.strong_bull_decision = JevEvaluationResult(
            action="UP",
            confidence=0.88,
            reasoning="Strong upward momentum across 5m and 1m indicators.",
            model="jev-latest",
            latency_ms=120.0,
        )

    def test_gate_0a_rejects_stale_market_data(self):
        """Verify that Gate 0A halts orders when market data is marked stale (>5s delay)."""
        stale_market = self.fresh_market.model_copy(update={"is_stale": True})
        
        result: RiskEvaluationResult = self.risk_guard.validate_and_size_order(
            decision=self.strong_bull_decision,
            market=stale_market,
            current_open_positions_count=0
        )

        self.assertFalse(result.approved)
        self.assertIn("NETWORK_OFFLINE", result.reason)
        self.assertEqual(result.adjusted_contracts, 0)
        self.assertGreaterEqual(self.risk_guard.metrics["rejections_breakdown"].get("NETWORK_OFFLINE", 0), 1)

    def test_gate_0a_allows_fresh_data_when_conviction_meets_hurdle(self):
        """Verify that when market data is fresh, Gate 0A passes and order is approved."""
        result: RiskEvaluationResult = self.risk_guard.validate_and_size_order(
            decision=self.strong_bull_decision,
            market=self.fresh_market,
            current_open_positions_count=0
        )

        self.assertTrue(result.approved)
        self.assertEqual(result.action, "UP")
        self.assertEqual(result.adjusted_contracts, 10)

    def test_ws_listener_network_state_transitions(self):
        """Verify tri-state logic of BinanceWSListener: ONLINE, DEGRADED, OFFLINE."""
        listener = BinanceWSListener(enable_mock_stream=False)
        listener._running = True

        # Case 1: Fresh event + connected + healthy -> ONLINE
        listener.state = ConnectionState.CONNECTED
        listener._network_healthy = True
        listener._last_event_time = time.time()
        self.assertEqual(listener.network_state, "ONLINE")

        # Case 2: WS reconnecting, but recent event (< 5s) -> DEGRADED
        listener.state = ConnectionState.RECONNECTING
        listener._last_event_time = time.time() - 2.0
        self.assertEqual(listener.network_state, "DEGRADED")

        # Case 3: Stale event (> 5.0s silence) -> OFFLINE
        listener._last_event_time = time.time() - 6.5
        self.assertEqual(listener.network_state, "OFFLINE")

        # Case 4: Network probe unhealthy -> OFFLINE
        listener._last_event_time = time.time()
        listener._network_healthy = False
        self.assertEqual(listener.network_state, "OFFLINE")

    def test_binance_client_official_prediction_parameters(self):
        """Verify BinanceClient defaults and official endpoint payload structure."""
        client = BinanceClient(
            api_key="test_key",
            api_secret="test_secret",
            base_url="https://api.binance.com",
            paper_trading=True,
            funding_source="CEX",
            slippage_bps=1000,
        )

        self.assertEqual(client.base_url, "https://api.binance.com")
        self.assertEqual(client.funding_source, "CEX")
        self.assertEqual(client.slippage_bps, 1000)

        # In paper mode, simulated order succeeds with binary $1.00 settlement tracking
        async def run_paper():
            res = await client.place_prediction_order(
                market_id="BTCUSDT-5M-R1000",
                symbol="BTCUSDT",
                side="UP",
                contracts=10,
                target_price=0.55,
                strike_price=85000.0,
                spot_price=85050.0,
            )
            return res

        import asyncio
        result = asyncio.run(run_paper())
        self.assertEqual(result.status, "SIMULATED")
        self.assertEqual(result.side, "UP")
        self.assertEqual(result.contracts, 10)
        self.assertTrue(any(p.market_id == "BTCUSDT-5M-R1000" for p in client._paper_positions.values()))

    def test_50_50_tie_resolution(self):
        """Verify official Binance 50-50 tie resolution when settlement spot equals strike price."""
        import asyncio
        client = BinanceClient(paper_trading=True)

        async def run_settle():
            await client.place_prediction_order(
                market_id="BTCUSDT-5M-TIE-ROUND",
                symbol="BTCUSDT",
                side="UP",
                contracts=10,
                target_price=0.50,
                strike_price=84000.0,
                spot_price=84000.0,
            )
            # Simulate round ending where spot is exactly equal to strike (84000.0)
            pnl, events = client.settle_expired_positions(
                active_market_ids={"NEW_ROUND"},
                current_prices={"BTCUSDT": 84000.0}
            )
            return pnl, events

        net_pnl, events = asyncio.run(run_settle())
        self.assertEqual(len(events), 1)
        # Cost was 10 * 0.50 = 5.0. Payout at 50-50 is 10 * 0.50 = 5.0 -> Net PnL = 0.0
        self.assertEqual(net_pnl, 0.0)
        self.assertEqual(events[0]["won"], True)
        self.assertEqual(client._closed_positions[-1].result, "TIE (50-50)")

    def test_negative_ev_rejection_and_positive_ev_approval(self):
        """Verify Expected Value (EV) Gate rejects overpriced bets and approves positive edge bets."""
        # Case 1: Overpriced bet (Market price 0.85, Confidence 0.82 -> EV = 0.82 - 0.85 = -0.03)
        overpriced_market = self.fresh_market.model_copy(update={"odds_yes": 0.85})
        decision = JevEvaluationResult(
            action="UP",
            confidence=0.82,
            reasoning="Moderate edge, but price in market is very expensive.",
            model="jev-latest",
            latency_ms=100.0,
        )
        res_neg = self.risk_guard.validate_and_size_order(
            decision=decision,
            market=overpriced_market,
            current_open_positions_count=0
        )
        self.assertFalse(res_neg.approved)
        self.assertIn("Expected Value", res_neg.reason)
        self.assertGreater(self.risk_guard._rejection_counts["NEGATIVE_EV_RISK"], 0)

        # Case 2: Positive edge bet (Market price 0.55, Confidence 0.85 -> EV = 0.85 - 0.55 = +0.30 >= 0.02)
        cheap_market = self.fresh_market.model_copy(update={"odds_yes": 0.55})
        res_pos = self.risk_guard.validate_and_size_order(
            decision=decision,
            market=cheap_market,
            current_open_positions_count=0
        )
        self.assertTrue(res_pos.approved)

    def test_unrealistic_velocity_rejection(self):
        """Verify that chasing an underdog with impossible speed needed is rejected."""
        # Price is $166 below strike ($83,938 vs $84,104) with only 120s left and 1m ATR = $30
        underdog_market = self.fresh_market.model_copy(update={
            "underlying_price": 83938.0,
            "target_price": 84104.0,
            "time_left_seconds": 120,
            "atr_1m": 30.0,
            "odds_yes": 0.04,
        })
        chase_decision = JevEvaluationResult(
            action="UP",
            confidence=0.85,
            reasoning="AI hallucinating reversal while remaining time is only 2 minutes.",
            model="jev-latest",
            latency_ms=100.0,
        )
        res = self.risk_guard.validate_and_size_order(
            decision=chase_decision,
            market=underdog_market,
            current_open_positions_count=0
        )
        self.assertFalse(res.approved)
        self.assertIn("Unrealistic Velocity", res.reason)
        self.assertGreater(self.risk_guard._rejection_counts["UNREALISTIC_VELOCITY_RISK"], 0)


if __name__ == "__main__":
    unittest.main()

