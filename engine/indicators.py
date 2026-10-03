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
import datetime
import math
import time
from functools import lru_cache
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any, Set


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
    """Reuse the completed history and apply the active close without rounding."""
    if not values:
        return 0.0
    if len(values) <= period:
        return sum(values) / len(values)
    ema = _ema_history(tuple(values[:-1]), period)
    return (values[-1] - ema) * (2.0 / (period + 1)) + ema


@lru_cache(maxsize=256)
def _ema_history(values: Tuple[float, ...], period: int) -> float:
    ema = sum(values[:period]) / period
    multiplier = 2.0 / (period + 1)
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


@lru_cache(maxsize=256)
def _rsi_history(closes: Tuple[float, ...], period: int) -> Tuple[float, float]:
    gains, losses = [], []
    for previous, current in zip(closes, closes[1:]):
        diff = current - previous
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    return avg_gain, avg_loss


def compute_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    if len(closes) == period + 1:
        avg_gain, avg_loss = _rsi_history(tuple(closes), period)
    else:
        avg_gain, avg_loss = _rsi_history(tuple(closes[:-1]), period)
        diff = closes[-1] - closes[-2]
        avg_gain = (avg_gain * (period - 1) + max(0.0, diff)) / period
        avg_loss = (avg_loss * (period - 1) + max(0.0, -diff)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return round(max(0.0, min(100.0, 100.0 - 100.0 / (1.0 + avg_gain / avg_loss))), 2)


@lru_cache(maxsize=256)
def _atr_history(values: Tuple[Tuple[float, float, float], ...], period: int) -> float:
    ranges = [max(high - low, abs(high - previous[2]), abs(low - previous[2]))
              for previous, (high, low, close) in zip(values, values[1:])]
    if len(ranges) < period:
        return sum(ranges)
    atr = sum(ranges[:period]) / period
    for value in ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def compute_atr(candles: List[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return max(0.01, candles[-1].high - candles[-1].low) if candles else 1.0
    # Immutable completed OHLC keys automatically invalidate on seed or rollover.
    history = tuple((c.high, c.low, c.close) for c in candles[:-1])
    previous = _atr_history(history, period)
    current = candles[-1]
    previous_close = candles[-2].close
    last_range = max(current.high - current.low, abs(current.high - previous_close),
                     abs(current.low - previous_close))
    count = len(candles) - 1
    if count <= period:
        atr = (previous + last_range) / count
    else:
        atr = (previous * (period - 1) + last_range) / period
    return round(max(0.0001, atr), 4) if count >= period else round(atr, 4)


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
    proximity_ratio: float = 0.40,
    min_oracle_noise_buffer: float = 2.0,
) -> bool:
    """
    Detect Gamma Risk / Expiry Danger Zone & Chainlink Oracle Jitter:
    When expiration is imminent (< 60s) and price is dangerously close to strike (< 40% of 1m ATR or < min_oracle_noise_buffer).
    Entering during this window is highly probabilistic noise and prone to Oracle resolution jitter.
    """
    if time_left_seconds > danger_threshold_seconds:
        return False
    
    threshold_dist = max(min_oracle_noise_buffer, atr_1m * proximity_ratio)
    return abs(price_diff) <= threshold_dist


def check_unrealistic_velocity(
    action: str,
    spot_price: float,
    strike_price: float,
    time_left_seconds: int,
    atr_1m: float,
    max_velocity_multiplier: float = 1.8,
) -> tuple[bool, float, float]:
    """
    Check if the trade is betting on an underdog that requires an unrealistic price velocity
    to reach and cross the strike before time runs out.
    Returns (is_unrealistic, required_velocity_per_min, max_allowed_velocity_per_min).
    """
    if time_left_seconds <= 0 or time_left_seconds > 180:
        return False, 0.0, 0.0

    is_up = action in ("UP", "BUY_YES")
    is_down = action in ("DOWN", "BUY_NO")

    # Target distance needed to flip ITM
    if is_up and spot_price < strike_price:
        distance = strike_price - spot_price
    elif is_down and spot_price > strike_price:
        distance = spot_price - strike_price
    else:
        # Already ITM (in-the-money), doesn't need to chase
        return False, 0.0, 0.0

    required_speed_per_sec = distance / float(time_left_seconds)
    required_speed_per_min = required_speed_per_sec * 60.0
    effective_atr = max(0.5, atr_1m)
    max_allowed_speed = effective_atr * max_velocity_multiplier

    is_unrealistic = required_speed_per_min > max_allowed_speed
    return is_unrealistic, round(required_speed_per_min, 2), round(max_allowed_speed, 2)


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


def get_market_session(timestamp: Optional[float] = None) -> str:
    """
    Classify current global trading session by UTC hour.
    - ASIAN_HOURS: 00:00 - 07:00 UTC (Tokyo, Singapore, Hong Kong)
    - LONDON_ACTIVE: 07:00 - 12:00 UTC (European liquidity expansion)
    - US_EU_OVERLAP: 12:00 - 16:00 UTC (Peak global liquidity)
    - NEW_YORK_ACTIVE: 16:00 - 21:00 UTC (US session trend / close)
    - PACIFIC_TRANSITION: 21:00 - 24:00 UTC (Late US / early Asian handover)
    """
    t = timestamp or time.time()
    utc_dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    hour = utc_dt.hour
    if 0 <= hour < 7:
        return "ASIAN_HOURS"
    elif 7 <= hour < 12:
        return "LONDON_ACTIVE"
    elif 12 <= hour < 16:
        return "US_EU_OVERLAP"
    elif 16 <= hour < 21:
        return "NEW_YORK_ACTIVE"
    else:
        return "PACIFIC_TRANSITION"


def compute_strike_velocity(
    history: List[Tuple[float, float]],
    current_bps: float,
    now: float,
    window_seconds: float = 30.0,
) -> Tuple[float, str]:
    """
    Compute rate of change of distance to strike (basis points per second)
    and interpret directional expansion or contraction relative to strike position.
    
    Returns:
        (velocity_bps_s, description)
    """
    if not history:
        return 0.0, "STEADY_STAGNANT"
    
    # Filter within window
    cutoff = now - window_seconds
    valid_pts = [(t, bps) for t, bps in history if t >= cutoff]
    if not valid_pts or len(valid_pts) < 2:
        if history and (now - history[0][0]) >= 2.0:
            oldest_t, oldest_bps = history[0]
        else:
            return 0.0, "STEADY_STAGNANT"
    else:
        oldest_t, oldest_bps = valid_pts[0]

    dt = now - oldest_t
    if dt < 1.0:
        return 0.0, "STEADY_STAGNANT"
    
    velocity = round((current_bps - oldest_bps) / dt, 3)

    # Interpret state relative to current price position vs strike
    if current_bps > 0:
        # Currently above strike (Bullish territory)
        if velocity > 0.05:
            desc = "EXPANDING_BULL_BUFFER"
        elif velocity < -0.05:
            desc = "RETREATING_TO_STRIKE"
        else:
            desc = "STEADY_STAGNANT"
    elif current_bps < 0:
        # Currently below strike (Bearish territory)
        if velocity < -0.05:
            desc = "EXPANDING_BEAR_BUFFER"
        elif velocity > 0.05:
            desc = "RETREATING_TO_STRIKE"
        else:
            desc = "STEADY_STAGNANT"
    else:
        desc = "AT_STRIKE_PIVOT"

    return velocity, desc


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
        self._strike_history: Dict[str, List[Tuple[float, float]]] = {}

    def retain_symbols(self, symbols: Set[str]) -> None:
        """Drop accumulated market history for symbols outside the active universe."""
        for history in (
            self._current_1m,
            self._history_1m,
            self._history_5m,
            self._current_5m,
        ):
            for symbol in list(history):
                if symbol not in symbols:
                    history.pop(symbol, None)

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

    def seed_candles(
        self,
        symbol: str,
        candles_1m: List[Candle],
        candles_5m: Optional[List[Candle]] = None
    ) -> None:
        """
        Bootstrap historical candle history from REST API on startup.
        Eliminates the 30-minute cold-start indicator lag.
        """
        if candles_1m:
            sorted_1m = sorted(candles_1m, key=lambda c: c.timestamp)
            self._history_1m[symbol] = sorted_1m[-self.max_history:]
            self._current_1m.pop(symbol, None)

        if candles_5m:
            sorted_5m = sorted(candles_5m, key=lambda c: c.timestamp)
            self._history_5m[symbol] = sorted_5m[-self.max_history:]
            self._current_5m.pop(symbol, None)

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

    def compute_base_features(
        self,
        symbol: str,
        spot_price: float,
        bid_qty: float = 0.0,
        ask_qty: float = 0.0,
        btc_momentum_pct: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Compute symbol-level quantitative features (candles, ATR, RSI, EMA, OBI, momentum).
        Can be computed once per tick and reused across multiple timeframes.
        """
        c1m = self.get_1m_candles(symbol)
        c5m = self.get_5m_candles(symbol)

        closes_1m = [c.close for c in c1m]
        closes_5m = [c.close for c in c5m]

        # ATR 1m
        atr_1m = compute_atr(c1m, period=14)
        if atr_1m <= 0.0001:
            atr_1m = max(0.01, spot_price * 0.0008)

        # RSI 1m & 5m
        rsi_1m = compute_rsi(closes_1m, period=14) if len(closes_1m) >= 5 else 50.0
        rsi_5m = compute_rsi(closes_5m, period=14) if len(closes_5m) >= 5 else 50.0

        # EMA Ribbon (EMA 9, 21, 50 on 1m)
        ema_9 = compute_ema(closes_1m, 9) if closes_1m else spot_price
        ema_21 = compute_ema(closes_1m, 21) if closes_1m else spot_price
        ema_50 = compute_ema(closes_1m, 50) if closes_1m else spot_price
        ema_trend = determine_ema_trend(spot_price, ema_9, ema_21, ema_50)

        # Macro Trend (5m EMA trend alignment)
        macro_trend = "NEUTRAL"
        if len(closes_5m) >= 5:
            ema_5m_fast = compute_ema(closes_5m, 5)
            ema_5m_slow = compute_ema(closes_5m, 15)
            if spot_price > ema_5m_fast > ema_5m_slow:
                macro_trend = "BULLISH"
            elif spot_price < ema_5m_fast < ema_5m_slow:
                macro_trend = "BEARISH"
            else:
                macro_trend = "NEUTRAL"

        # Recent 3 completed 1m candles summary for price action trajectory
        recent_summary: List[str] = []
        hist_1m = self._history_1m.get(symbol, [])
        for c in hist_1m[-3:]:
            if c.open > 0:
                ret_pct = round(((c.close - c.open) / c.open) * 100.0, 2)
                direction = "Bull" if c.close >= c.open else "Bear"
                sign = "+" if ret_pct >= 0 else ""
                recent_summary.append(f"{sign}{ret_pct:.2f}% {direction}")
        recent_candles_summary = "[" + ", ".join(recent_summary) + "]" if recent_summary else "[]"

        # Order Book Imbalance (OBI)
        obi = compute_order_book_imbalance(bid_qty, ask_qty)

        # Market Momentum (recent 5m)
        momentum_pct = 0.0
        if len(closes_1m) >= 5 and closes_1m[-5] > 0:
            momentum_pct = round(((spot_price - closes_1m[-5]) / closes_1m[-5]) * 100.0, 3)

        # BTC correlation direction
        btc_dir = "FLAT"
        if btc_momentum_pct > 0.05:
            btc_dir = "BULLISH"
        elif btc_momentum_pct < -0.05:
            btc_dir = "BEARISH"

        return {
            "atr_1m": atr_1m,
            "rsi_1m": rsi_1m,
            "rsi_5m": rsi_5m,
            "ema_trend": ema_trend,
            "macro_trend_15m": macro_trend,
            "recent_candles_summary": recent_candles_summary,
            "order_book_imbalance": obi,
            "momentum_pct": momentum_pct,
            "btc_correlation_dir": btc_dir,
            "ema_9": round(ema_9, 2),
            "ema_21": round(ema_21, 2),
            "ema_50": round(ema_50, 2),
            "one_minute_sample_count": len(closes_1m),
            "five_minute_sample_count": len(closes_5m),
            # EMA(50) needs a full window and RSI needs at least 15 closes.
            "indicator_data_ready": len(closes_1m) >= 50 and len(closes_5m) >= 15,
            "momentum_available": len(closes_1m) >= 5,
        }

    def compute_round_metrics(
        self,
        base_features: Dict[str, Any],
        spot_price: float,
        strike_price: float,
        time_left_seconds: int,
        round_id: str = "",
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Combine base symbol features with timeframe-specific strike, velocity, session, and expiry metrics.
        Ultra-fast arithmetic only (~0.01ms).
        """
        atr_1m = base_features["atr_1m"]
        dvr = compute_dvr(spot_price, strike_price, atr_1m, time_left_seconds)
        price_diff = round(spot_price - strike_price, 4)

        now = timestamp or time.time()

        # Scale-invariant strike distance & velocity
        if strike_price > 0:
            strike_diff_pct = round(((spot_price - strike_price) / strike_price) * 100.0, 4)
            strike_diff_bps = round(((spot_price - strike_price) / strike_price) * 10000.0, 2)

            # Record strike history and compute velocity (bps / s)
            hist_key = round_id if round_id else f"{base_features.get('symbol', 'SYM')}_{strike_price:.2f}"
            hist = self._strike_history.setdefault(hist_key, [])
            hist.append((now, strike_diff_bps))
            if len(hist) > 40:
                cutoff_90 = now - 90.0
                hist[:] = [p for p in hist if p[0] >= cutoff_90]

            # Prune old round keys if map grows large
            if len(self._strike_history) > 60:
                cutoff_300 = now - 300.0
                stale_keys = [k for k, v in self._strike_history.items() if not v or v[-1][0] < cutoff_300]
                for sk in stale_keys:
                    self._strike_history.pop(sk, None)

            strike_velocity_bps_s, strike_velocity_desc = compute_strike_velocity(hist, strike_diff_bps, now)
        else:
            strike_diff_pct = 0.0
            strike_diff_bps = 0.0
            strike_velocity_bps_s = 0.0
            strike_velocity_desc = "STEADY_STAGNANT"

        expiry_danger = check_expiry_danger(time_left_seconds, price_diff, atr_1m)
        regime = classify_market_regime(
            base_features["ema_trend"],
            base_features["rsi_5m"],
            dvr,
            base_features["momentum_pct"],
        )
        market_session = get_market_session(now)

        return {
            "atr_1m": atr_1m,
            "dvr_ratio": dvr,
            "strike_diff_bps": strike_diff_bps,
            "strike_diff_pct": strike_diff_pct,
            "strike_velocity_bps_s": strike_velocity_bps_s,
            "strike_velocity_desc": strike_velocity_desc,
            "market_session": market_session,
            "recent_candles_summary": base_features.get("recent_candles_summary", "[]"),
            "macro_trend_15m": base_features.get("macro_trend_15m", "NEUTRAL"),
            "rsi_1m": base_features["rsi_1m"],
            "rsi_5m": base_features["rsi_5m"],
            "ema_trend": base_features["ema_trend"],
            "order_book_imbalance": base_features["order_book_imbalance"],
            "market_regime": regime,
            "expiry_danger_flag": expiry_danger,
            "btc_correlation_dir": base_features["btc_correlation_dir"],
            "ema_9": base_features["ema_9"],
            "ema_21": base_features["ema_21"],
            "ema_50": base_features["ema_50"],
        }

    def compute_all_metrics(
        self,
        symbol: str,
        spot_price: float,
        strike_price: float,
        time_left_seconds: int,
        bid_qty: float = 0.0,
        ask_qty: float = 0.0,
        btc_momentum_pct: float = 0.0,
        round_id: str = "",
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Compute full quantitative feature suite for a symbol in under 1 millisecond.
        """
        base = self.compute_base_features(
            symbol=symbol,
            spot_price=spot_price,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            btc_momentum_pct=btc_momentum_pct,
        )
        base["symbol"] = symbol
        return self.compute_round_metrics(
            base_features=base,
            spot_price=spot_price,
            strike_price=strike_price,
            time_left_seconds=time_left_seconds,
            round_id=round_id,
            timestamp=timestamp,
        )
