"""
Automated unit & regression tests for Reinforced AI Decision Features.
Tests:
- Technical Indicators: ATR, RSI, EMA Ribbon
- Volatility Normalization: DVR (Distance-to-Volatility Ratio)
- Order Flow: Order Book Imbalance (OBI)
- Market Regime Classification
- Expiry Danger Zone detection
- Rolling Candle Aggregation
- Jev AI Heuristic & Multi-factor evaluation
- RiskGuard quantitative gates (Gate 2.5 & Gate 2.6)
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import asyncio
import time
from engine.indicators import (
    compute_atr,
    compute_rsi,
    compute_ema,
    compute_dvr,
    determine_ema_trend,
    compute_order_book_imbalance,
    check_expiry_danger,
    classify_market_regime,
    RollingCandleAggregator,
    Candle,
)
from engine.jev_client import MarketContext, JevClient, JevEvaluationResult
from engine.risk_guard import RiskGuard


def test_indicator_calculations():
    print("[1/5] Testing mathematical indicator computations...")
    # EMA test
    prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
    ema_9 = compute_ema(prices, 9)
    assert ema_9 > 14.0, f"Expected EMA > 14.0, got {ema_9}"

    # RSI test
    rsi = compute_rsi(prices, 14)
    assert rsi >= 50.0, f"Expected bullish RSI >= 50, got {rsi}"

    # ATR test
    candles = [
        Candle(timestamp=i * 60, open=100 + i, high=103 + i, low=99 + i, close=101 + i, volume=50)
        for i in range(25)
    ]
    atr = compute_atr(candles, 14)
    assert atr > 0.0, f"Expected positive ATR, got {atr}"

    # DVR test
    dvr = compute_dvr(spot=84200.0, strike=84100.0, atr_1m=20.0, time_left_seconds=300)
    assert dvr > 1.0, f"Expected positive sigma DVR, got {dvr}"

    # OBI test
    obi_bull = compute_order_book_imbalance(bid_qty=120.0, ask_qty=40.0)
    assert obi_bull == 0.5, f"Expected +0.50 OBI, got {obi_bull}"
    obi_bear = compute_order_book_imbalance(bid_qty=30.0, ask_qty=90.0)
    assert obi_bear == -0.5, f"Expected -0.50 OBI, got {obi_bear}"

    # Expiry danger test
    danger_active = check_expiry_danger(time_left_seconds=40, price_diff=2.0, atr_1m=25.0)
    assert danger_active is True, "Expected danger active when <60s and close to strike"
    danger_inactive = check_expiry_danger(time_left_seconds=300, price_diff=2.0, atr_1m=25.0)
    assert danger_inactive is False, "Expected danger inactive when time > 60s"
    print("      -> Indicators math verified successfully.")


def test_rolling_candle_aggregator():
    print("[2/5] Testing RollingCandleAggregator tick processing...")
    agg = RollingCandleAggregator(max_history=50)
    start_time = time.time() - 1800
    base_price = 84000.0

    # Feed 30 ticks
    for i in range(30):
        t = start_time + i * 60
        price = base_price + i * 5.0
        agg.update_tick("BTCUSDT", price, volume=25.0, timestamp=t)

    metrics = agg.compute_all_metrics(
        symbol="BTCUSDT",
        spot_price=base_price + 30 * 5.0,
        strike_price=base_price + 100.0,
        time_left_seconds=450,
        bid_qty=75.0,
        ask_qty=25.0,
        btc_momentum_pct=0.20,
    )

    assert metrics["atr_1m"] > 0
    assert metrics["ema_trend"] == "STRONG_UPTREND"
    assert metrics["order_book_imbalance"] > 0
    assert metrics["btc_correlation_dir"] == "BULLISH"
    print("      -> RollingCandleAggregator verified successfully.")


def test_jev_client_decision_engine():
    print("[3/5] Testing JevClient evaluation with enriched telemetry...")
    async def _run():
        client = JevClient(api_key="")  # Offline heuristic mode
        ctx = MarketContext(
            market_id="BTCUSDT-15M-TEST",
            symbol="BTCUSDT",
            question="BTC Up or Down 15m",
            odds_yes=0.51,
            odds_no=0.49,
            underlying_price=84250.0,
            target_price=84100.0,
            price_diff=+150.0,
            momentum_pct=0.40,
            atr_1m=18.0,
            dvr_ratio=+2.10,
            rsi_1m=64.0,
            rsi_5m=66.0,
            ema_trend="STRONG_UPTREND",
            order_book_imbalance=+0.55,
            market_regime="TREND_EXPANSION",
            expiry_danger_flag=False,
        )

        t0 = time.perf_counter()
        decision = await client.evaluate_market(ctx)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        assert decision.action == "UP"
        assert decision.confidence >= 0.85
        assert "DVR" in decision.reasoning
        assert "Trend" in decision.reasoning
        print(f"      -> JevClient decision: {decision.action} ({decision.confidence*100:.1f}%) in {latency_ms:.2f}ms.")
    asyncio.run(_run())


def test_risk_guard_gates():
    print("[4/5] Testing RiskGuard validation & new quantitative gates...")
    guard = RiskGuard(confidence_threshold=0.80)

    ctx_base = MarketContext(
        market_id="BTCUSDT-15M-R1",
        symbol="BTCUSDT",
        question="BTC Up or Down",
        odds_yes=0.55,
        odds_no=0.45,
        underlying_price=84250.0,
        target_price=84100.0,
        price_diff=150.0,
        expiry_danger_flag=False,
        ema_trend="STRONG_UPTREND",
        order_book_imbalance=0.30,
    )
    decision = JevEvaluationResult(action="UP", confidence=0.88, reasoning="Strong bullish signal")

    # Test approved base trade
    res_base = guard.validate_and_size_order(decision, ctx_base)
    assert res_base.approved is True, f"Base trade should be approved: {res_base.reason}"

    # Test Gate 2.5: Expiry danger
    ctx_danger = ctx_base.model_copy(update={"expiry_danger_flag": True, "time_left_seconds": 25})
    res_danger = guard.validate_and_size_order(decision, ctx_danger)
    assert res_danger.approved is False
    assert "Expiry Danger Zone Filter" in res_danger.reason

    # Test Gate 2.6: Microstructure contradiction
    ctx_contra = ctx_base.model_copy(update={
        "expiry_danger_flag": False,
        "ema_trend": "STRONG_DOWNTREND",
        "order_book_imbalance": -0.65,
    })
    res_contra = guard.validate_and_size_order(decision, ctx_contra)
    assert res_contra.approved is False
    assert "Microstructure Contradiction Filter" in res_contra.reason

    # Test Circuit Breaker: Net Profit should NEVER trip the circuit breaker!
    guard.max_daily_loss_usdt = 200.0
    guard.reset_circuit_breaker()
    # Simulate a profitable run with mixed wins and losses (+255 net profit)
    for _ in range(5):
        guard.record_settlement_result(won=True, pnl=70.0, symbol="BTCUSDT")   # +350 gross win
        guard.record_settlement_result(won=False, pnl=-19.0, symbol="BTCUSDT") # -95 gross loss
    # Net PnL = +255.0 USDT!
    assert guard._daily_net_pnl == 255.0, f"Expected +255 net PnL, got {guard._daily_net_pnl}"
    assert guard._daily_realized_loss == 0.0, f"Expected 0 daily loss on profit, got {guard._daily_realized_loss}"
    assert guard._circuit_breaker_active is False, "Circuit breaker MUST NOT trip when account is in profit (+255 USDT)!"

    # Simulate true drawdown exceeding 200 limit
    guard.record_settlement_result(won=False, pnl=-500.0, symbol="BTCUSDT")
    assert guard._daily_net_pnl < -200.0
    assert guard._circuit_breaker_active is True, "Circuit breaker MUST trip when net daily loss exceeds limit!"
    
    # Test reset
    guard.reset_circuit_breaker()
    assert guard._circuit_breaker_active is False
    assert guard._daily_realized_loss == 0.0

    print("      -> RiskGuard gates & Net PnL Circuit Breaker verified successfully.")


def test_app_import_sanity():
    print("[5/5] Testing main application & websocket listener imports...")
    import main
    from streams.ws_listener import BinanceWSListener
    from engine.binance_client import BinanceClient
    assert main.TradingBotCoordinator is not None
    assert BinanceWSListener is not None
    assert BinanceClient is not None
    print("      -> Core bot modules imported cleanly.")


if __name__ == "__main__":
    test_indicator_calculations()
    test_rolling_candle_aggregator()
    asyncio.run(test_jev_client_decision_engine())
    test_risk_guard_gates()
    test_app_import_sanity()
    print("\n========================================================")
    print(" [OK] ALL 5/5 TEST SUITES PASSED! READY FOR GIT & RESTART.")
    print("========================================================")
