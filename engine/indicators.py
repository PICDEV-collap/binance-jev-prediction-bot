"""
High-Performance Quantitative Indicator & Volatility Engine.
Pure Python, zero-external-dependency mathematical implementations for:
- ATR (Average True Range)
- RSI (Relative Strength Index)
- EMA (Exponential Moving Average) & Ribbon Trend Alignment
- DVR (Distance-to-Volatility Ratio / Sigma-Distance)
- Order Book Imbalance (OBI)
- Market Regime Classification
- Expiry Danger Zone Detection
- Rolling Candle Aggregator (tick to 1m/5m OHLC)
"""

from __future__ import annotations
import math
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional


@dataclass
class Candle:
    """Standard OHLCV representation."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def compute_ema(values: List[float], period: int) -> float:
    """Calculate Exponential Moving Average."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    
    # Initialize with SMA of first `period` items
    ema = sum(values[:period]) / period
    multiplier = 2.0 / (period + 1)
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def compute_rsi(closes: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index (RSI) using Wilder's smoothing.
    Returns float between 0.0 and 100.0 (defaults to 50.0 if insufficient data).
    """
    if len(closes) < period + 1:
        return 50.0

    gains: List[float] = []
    losses: List[float] = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))

    # Initial average gain/loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's smoothing for subsequent periods
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(max(0.0, min(100.0, rsi)), 2)


def compute_atr(candles: List[Candle], period: int = 14) -> float:
    """
    Calculate Average True Range (ATR) from OHLC candles.
    Returns estimated range in base currency units.
    """
    if len(candles) < 2:
        if candles:
            return max(0.01, candles[-1].high - candles[-1].low)
        return 1.0

    tr_list: List[float] = []
    for i in range(1, len(candles)):
        curr = candles[i]
        prev_close = candles[i - 1].close
        tr = max(
            curr.high - curr.low,
            abs(curr.high - prev_close),
            abs(curr.low - prev_close)
        )
        tr_list.append(tr)

    if not tr_list:
        return 1.0

    if len(tr_list) < period:
        return round(sum(tr_list) / len(tr_list), 4)

    # Wilder's smoothing
    atr = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period

    return round(max(0.0001, atr), 4)


def compute_dvr(
    spot: float,
    strike: float,
    atr_1m: float,
    time_left_seconds: int,
) -> float:
    """
    Distance-to-Volatility Ratio (DVR) / Sigma-Distance.
    Normalizes price distance to strike in units of standard expected price travel.
    
    Formula: (spot - strike) / (ATR_1m * sqrt(time_left_minutes))
    """
    if atr_1m <= 0.0:
        atr_1m = max(0.01, spot * 0.001)

    time_factor = math.sqrt(max(10, time_left_seconds) / 60.0)
    expected_travel = atr_1m * time_factor
    
    dvr = (spot - strike) / max(0.0001, expected_travel)
    return round(dvr, 3)


def determine_ema_trend(
    spot: float,
    ema_fast: float,
    ema_mid: float,
    ema_slow: float
) -> str:
    """
    Determine multi-EMA ribbon trend alignment.
    Returns: STRONG_UPTREND, WEAK_UPTREND, STRONG_DOWNTREND, WEAK_DOWNTREND, or NEUTRAL_CHOP
    """
    if ema_fast <= 0 or ema_mid <= 0 or ema_slow <= 0:
        return "NEUTRAL_CHOP"

    if spot > ema_fast > ema_mid > ema_slow:
        return "STRONG_UPTREND"
    elif spot < ema_fast < ema_mid < ema_slow:
        return "STRONG_DOWNTREND"
    elif spot > ema_mid and ema_fast >= ema_mid:
        return "WEAK_UPTREND"
    elif spot < ema_mid and ema_fast <= ema_mid:
        return "WEAK_DOWNTREND"
    else:
        return "NEUTRAL_CHOP"


def compute_order_book_imbalance(
    bid_qty: float,
    ask_qty: float,
) -> float:
    """
    Calculate Order Book Imbalance (OBI) from top bids/asks quantities.
    Returns value between -1.0 (heavy sell pressure) and +1.0 (heavy buy support).
    """
    total = bid_qty + ask_qty
    if total <= 0:
        return 0.0
    imbalance = (bid_qty - ask_qty) / total
    return round(max(-1.0, min(1.0, imbalance)), 3)


def check_expiry_danger(
    time_left_seconds: int,
    price_diff: float,
    atr_1m: float,
    danger_threshold_seconds: int = 60,
    proximity_ratio: float = 0.35,
) -> bool:
    """
    Detect Gamma Risk / Expiry Danger Zone:
    When expiration is imminent (< 60s) and price is dangerously close to strike (< 35% of 1m ATR).
    Entering during this window is highly probabilistic noise (coin-flip).
    """
    if time_left_seconds > danger_threshold_seconds:
        return False
    
    threshold_dist = max(0.01, atr_1m * proximity_ratio)
    return abs(price_diff) <= threshold_dist


def classify_market_regime(
    trend: str,
    rsi_5m: float,
    dvr_ratio: float,
    momentum_pct: float
) -> str:
    """
    Classify current market regime into actionable strategic states:
    - TREND_EXPANSION: Strong directional trend in progress
    - MEAN_REVERTING_RANGE: Oscillating in bounded range, overextended
    - HIGH_VOLATILITY_CHOP: Directionless turbulent chop
    """
    if "STRONG" in trend and abs(dvr_ratio) >= 1.2:
        return "TREND_EXPANSION"
    
    if (rsi_5m >= 72 and momentum_pct > 0) or (rsi_5m <= 28 and momentum_pct < 0):
        return "MEAN_REVERTING_RANGE"
    
    if trend == "NEUTRAL_CHOP" and abs(dvr_ratio) < 0.6:
        return "HIGH_VOLATILITY_CHOP"
    
    return "RANGING_COMPRESSION"


class RollingCandleAggregator:
    """
    High-speed, memory-efficient in-memory candle builder.
    Aggregates continuous tick stream into 1-minute and 5-minute candles.
    Retains up to max_history candles per symbol.
    """

    def __init__(self, max_history: int = 60) -> None:
        self.max_history = max_history
        self._current_1m: Dict[str, Candle] = {}
        self._history_1m: Dict[str, List[Candle]] = {}
        self._history_5m: Dict[str, List[Candle]] = {}
        self._current_5m: Dict[str, Candle] = {}

    def update_tick(self, symbol: str, price: float, volume: float = 0.0, timestamp: Optional[float] = None) -> None:
        """Process a price tick and update rolling 1m and 5m candles."""
        if price <= 0:
            return
        
        now = timestamp or time.time()
        bucket_1m = int(now // 60) * 60
        bucket_5m = int(now // 300) * 300

        # --- 1-Minute Candle Aggregation ---
        curr_1m = self._current_1m.get(symbol)
        if curr_1m is None or curr_1m.timestamp != bucket_1m:
            if curr_1m is not None:
                h_list = self._history_1m.setdefault(symbol, [])
                h_list.append(curr_1m)
                if len(h_list) > self.max_history:
                    h_list.pop(0)
            self._current_1m[symbol] = Candle(
                timestamp=bucket_1m,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
            )
        else:
            curr_1m.high = max(curr_1m.high, price)
            curr_1m.low = min(curr_1m.low, price)
            curr_1m.close = price
            curr_1m.volume += volume

        # --- 5-Minute Candle Aggregation ---
        curr_5m = self._current_5m.get(symbol)
        if curr_5m is None or curr_5m.timestamp != bucket_5m:
            if curr_5m is not None:
                h5_list = self._history_5m.setdefault(symbol, [])
                h5_list.append(curr_5m)
                if len(h5_list) > self.max_history:
                    h5_list.pop(0)
            self._current_5m[symbol] = Candle(
                timestamp=bucket_5m,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
            )
        else:
            curr_5m.high = max(curr_5m.high, price)
            curr_5m.low = min(curr_5m.low, price)
            curr_5m.close = price
            curr_5m.volume += volume

    def get_1m_candles(self, symbol: str) -> List[Candle]:
        """Return history + active 1m candle."""
        candles = list(self._history_1m.get(symbol, []))
        curr = self._current_1m.get(symbol)
        if curr:
            candles.append(curr)
        return candles

    def get_5m_candles(self, symbol: str) -> List[Candle]:
        """Return history + active 5m candle."""
        candles = list(self._history_5m.get(symbol, []))
        curr = self._current_5m.get(symbol)
        if curr:
            candles.append(curr)
        return candles

    def compute_all_metrics(
        self,
        symbol: str,
        spot_price: float,
        strike_price: float,
        time_left_seconds: int,
        bid_qty: float = 0.0,
        ask_qty: float = 0.0,
        btc_momentum_pct: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Compute full quantitative feature suite for a symbol in under 1 millisecond.
        """
        c1m = self.get_1m_candles(symbol)
        c5m = self.get_5m_candles(symbol)

        closes_1m = [c.close for c in c1m]
        closes_5m = [c.close for c in c5m]

        # ATR 1m
        atr_1m = compute_atr(c1m, period=14)
        if atr_1m <= 0.0001:
            atr_1m = max(0.01, spot_price * 0.0008)

        # Distance-to-Volatility Ratio (DVR)
        dvr = compute_dvr(spot_price, strike_price, atr_1m, time_left_seconds)

        # RSI 1m & 5m
        rsi_1m = compute_rsi(closes_1m, period=14) if len(closes_1m) >= 5 else 50.0
        rsi_5m = compute_rsi(closes_5m, period=14) if len(closes_5m) >= 5 else 50.0

        # EMA Ribbon (EMA 9, 21, 50 on 1m)
        ema_9 = compute_ema(closes_1m, 9) if closes_1m else spot_price
        ema_21 = compute_ema(closes_1m, 21) if closes_1m else spot_price
        ema_50 = compute_ema(closes_1m, 50) if closes_1m else spot_price
        ema_trend = determine_ema_trend(spot_price, ema_9, ema_21, ema_50)

        # Order Book Imbalance (OBI)
        obi = compute_order_book_imbalance(bid_qty, ask_qty)

        # Price diff
        price_diff = round(spot_price - strike_price, 4)

        # Expiry Danger Zone
        expiry_danger = check_expiry_danger(time_left_seconds, price_diff, atr_1m)

        # Market Momentum (recent 5m)
        momentum_pct = 0.0
        if len(closes_1m) >= 5 and closes_1m[-5] > 0:
            momentum_pct = round(((spot_price - closes_1m[-5]) / closes_1m[-5]) * 100.0, 3)

        # Market Regime
        regime = classify_market_regime(ema_trend, rsi_5m, dvr, momentum_pct)

        # BTC correlation direction
        btc_dir = "FLAT"
        if btc_momentum_pct > 0.05:
            btc_dir = "BULLISH"
        elif btc_momentum_pct < -0.05:
            btc_dir = "BEARISH"

        return {
            "atr_1m": atr_1m,
            "dvr_ratio": dvr,
            "rsi_1m": rsi_1m,
            "rsi_5m": rsi_5m,
            "ema_trend": ema_trend,
            "order_book_imbalance": obi,
            "market_regime": regime,
            "expiry_danger_flag": expiry_danger,
            "btc_correlation_dir": btc_dir,
            "ema_9": round(ema_9, 2),
            "ema_21": round(ema_21, 2),
            "ema_50": round(ema_50, 2),
        }
