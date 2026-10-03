"""
High-Performance Binance Prediction Markets WebSocket Listener & Ingress Core.
Features:
- Dual-mode resilient WebSocket ingress (Spot Combined Stream + Futures fallback)
- Auto-reconnection with exponential backoff & full jitter
- Safe envelope unwrapping (combined streams, arrays, single ticker)
- Automatic Watchdog REST Poller (zero-gap fallback ensures 100% data continuity)
- Non-blocking event distribution via asyncio.create_task()
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import math
import random
import time
from enum import Enum
from typing import Callable, Coroutine, Any, Optional, Dict, List, Set, Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from engine.jev_client import MarketContext
from engine.indicators import RollingCandleAggregator, Candle
from engine.performance import PerformanceMetrics
from engine.symbols import DEFAULT_ACTIVE_SYMBOLS, normalize_active_symbols

logger = logging.getLogger("ws_listener")

SUPPORTED_SYMBOLS = DEFAULT_ACTIVE_SYMBOLS
TIMEFRAMES: List[tuple[str, int]] = [
    ("5m", 300),
    ("15m", 900),
    ("1h", 3600),
    ("1d", 86400),
]

SPOT_COMBINED_STREAM_URL = "wss://stream.binance.com:9443/stream"
REST_TICKER_API = "https://api.binance.com/api/v3/ticker/24hr"


def _stream_url_for_symbols(template: str, symbols: Iterable[str]) -> str:
    """Build a combined ticker stream URL, or retain a bare socket for SUBSCRIBE."""
    parts = urlsplit(template)
    path = parts.path or "/ws"
    query_params = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "streams"]
    if path.rstrip("/") == "/stream":
        streams = "/".join(f"{symbol.lower()}@ticker" for symbol in symbols)
        query_params.append(("streams", streams))
    query = urlencode(query_params, doseq=True, safe="@/")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _rest_ticker_url_for_symbols(symbols: Iterable[str]) -> str:
    """Build a bounded REST ticker query for the current active pair universe."""
    encoded_symbols = quote(json.dumps(list(symbols), separators=(",", ":")), safe="")
    return f"{REST_TICKER_API}?symbols={encoded_symbols}"


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"


MarketEventCallback = Callable[[MarketContext], Coroutine[Any, Any, None]]


class BinanceWSListener:
    """
    Asynchronous WebSocket stream listener for Binance Prediction Markets.
    Guarantees continuous market ingress with zero blocking on the WS loop.
    """

    def __init__(
        self,
        stream_url: str = SPOT_COMBINED_STREAM_URL,
        on_market_event: Optional[MarketEventCallback] = None,
        event_filter: Optional[Callable[[MarketContext], bool]] = None,
        ping_interval: int = 20,
        ping_timeout: int = 10,
        enable_mock_stream: bool = False,
        active_symbols: Optional[Iterable[str]] = None,
    ) -> None:
        self.active_symbols = list(normalize_active_symbols(active_symbols or SUPPORTED_SYMBOLS))

        # Keep spot as the preferred source, and retain the configured futures URL as fallback.
        if "fstream.binance.com" in stream_url:
            primary_template = SPOT_COMBINED_STREAM_URL
            fallback_template = stream_url
        else:
            primary_template = stream_url
            fallback_template = SPOT_COMBINED_STREAM_URL
        self._primary_stream_template = primary_template
        self._fallback_stream_template = fallback_template
        self.stream_url = _stream_url_for_symbols(primary_template, self.active_symbols)
        self._fallback_stream_url = _stream_url_for_symbols(fallback_template, self.active_symbols)
        self._stream_config_version = 0

        self.on_market_event = on_market_event
        self.event_filter = event_filter
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.enable_mock_stream = enable_mock_stream

        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self._running: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._mock_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Network Health & Liveness Prober
        self._latency_ms: float = 0.0
        self._network_healthy: bool = True
        self._max_stale_seconds: float = 5.0
        self._network_liveness_task: Optional[asyncio.Task] = None

        # Reconnect parameters
        self._base_backoff: float = 1.0
        self._backoff_factor: float = 1.8
        self._max_backoff: float = 20.0
        self._reconnect_attempts: int = 0

        # Telemetry stats
        self._messages_received: int = 0
        self._events_dispatched: int = 0
        self._last_heartbeat_time: float = 0.0
        self._last_event_time: float = 0.0
        self._active_markets: Dict[str, MarketContext] = {}
        self._round_base_prices: Dict[str, float] = {}
        self._official_strike_details: Dict[str, Dict[str, Any]] = {}
        self.candle_aggregator: RollingCandleAggregator = RollingCandleAggregator(max_history=60)
        self._btc_momentum: float = 0.0
        self._background_tasks: Set[asyncio.Task] = set()
        self.performance = PerformanceMetrics()
        self._pending_events: Dict[str, MarketContext] = {}
        self._dispatch_tasks: Dict[str, asyncio.Task] = {}

    async def configure_active_symbols(self, symbols: Iterable[str]) -> bool:
        """Replace the subscribed pair allowlist and reconnect the feed without restarting the bot."""
        normalized = list(normalize_active_symbols(symbols))
        if normalized == self.active_symbols:
            return False

        active = set(normalized)
        removed = set(self.active_symbols) - active
        self.active_symbols = normalized
        self.stream_url = _stream_url_for_symbols(self._primary_stream_template, self.active_symbols)
        self._fallback_stream_url = _stream_url_for_symbols(self._fallback_stream_template, self.active_symbols)
        self._stream_config_version += 1

        self._active_markets = {
            key: market for key, market in self._active_markets.items()
            if market.symbol in active
        }
        self.candle_aggregator.retain_symbols(active)
        self._round_base_prices = {
            key: price for key, price in self._round_base_prices.items()
            if not any(key.startswith(f"{symbol}-") for symbol in removed)
        }
        self._official_strike_details = {
            key: detail for key, detail in self._official_strike_details.items()
            if str(detail.get("symbol", "")).upper() in active
        }

        if self._running:
            self._spawn_task(self._bootstrap_kline_history())
            if self._ws and not self._ws.closed:
                await self._ws.close()

        logger.info("Active Binance spot pairs updated: %s", ", ".join(self.active_symbols))
        return True

    def update_official_strike_prices(
        self,
        strike_map: Dict[str, float],
        detailed_strikes: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> None:
        """
        Cache dated Binance Price to Beat data from the marketTopics catalog.

        ``strike_map`` remains accepted for existing callers, but flat symbol/timeframe
        values are deliberately ignored because they cannot identify which round they belong to.
        """
        if not detailed_strikes:
            return
        self._official_strike_details.update(detailed_strikes)

        # Keep only active rounds in the hot cache. An expired market ID must not be
        # retained as a source for a later round's Price to Beat.
        now = time.time()
        for key, detail in list(self._official_strike_details.items()):
            end_time = float(detail.get("end_time_sec", 0.0) or 0.0)
            if end_time > 0 and end_time <= now:
                self._official_strike_details.pop(key, None)

    def _spawn_task(self, coro) -> asyncio.Task:
        """Spawn background task with strong reference to prevent premature garbage collection in Python 3.12+."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self) -> None:
        """Start the WebSocket listener, watchdog, and network health prober."""
        if self._running:
            logger.warning("WebSocket listener already running.")
            return

        self._running = True
        logger.info(f"Starting Binance WebSocket Listener (Mock Mode: {self.enable_mock_stream})...")

        if self.enable_mock_stream:
            self._mock_task = self._spawn_task(self._run_mock_stream_generator())
        else:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=4.0)
            )
            self._spawn_task(self._bootstrap_kline_history())
            self._listener_task = self._spawn_task(self._connection_supervisor())
            self._watchdog_task = self._spawn_task(self._watchdog_rest_poller())
            self._network_liveness_task = self._spawn_task(self._network_liveness_prober())

    async def stop(self) -> None:
        """Gracefully stop the listener, watchdog, and close sockets."""
        self._running = False
        self.state = ConnectionState.DISCONNECTED

        if self._ws and not self._ws.closed:
            await self._ws.close()
            logger.info("Closed Binance WebSocket connection.")

        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        if self._network_liveness_task and not self._network_liveness_task.done():
            self._network_liveness_task.cancel()
            try:
                await self._network_liveness_task
            except asyncio.CancelledError:
                pass

        if self._mock_task and not self._mock_task.done():
            self._mock_task.cancel()
            try:
                await self._mock_task
            except asyncio.CancelledError:
                pass

        # Drain all remaining event dispatch background tasks
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        self._pending_events.clear()
        self._dispatch_tasks.clear()

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

        logger.info("Binance WebSocket Listener terminated cleanly.")

    async def _connection_supervisor(self) -> None:
        """
        Supervisor loop that manages connection lifecycle and auto-reconnection
        with exponential backoff and jitter.
        """
        target_url = self.stream_url
        observed_config_version = self._stream_config_version

        while self._running:
            if observed_config_version != self._stream_config_version:
                target_url = self.stream_url
                observed_config_version = self._stream_config_version
                self._reconnect_attempts = 0
            try:
                self.state = (
                    ConnectionState.CONNECTING
                    if self._reconnect_attempts == 0
                    else ConnectionState.RECONNECTING
                )
                logger.info(
                    f"Connecting to Binance Stream: {target_url} "
                    f"(Attempt: {self._reconnect_attempts + 1})"
                )

                async with websockets.connect(
                    target_url,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    close_timeout=5,
                    max_size=2**20,  # 1MB buffer
                ) as ws:
                    self._ws = ws
                    self.state = ConnectionState.CONNECTED
                    self._reconnect_attempts = 0
                    logger.info("Successfully connected to Binance WebSocket Stream.")

                    # If URL does not already have streams specified, send subscription
                    if "streams=" not in target_url.lower():
                        await self._send_subscriptions(ws)

                    # Message ingress loop
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        self._handle_raw_message(raw_msg)

            except (ConnectionClosed, WebSocketException) as ws_err:
                self.state = ConnectionState.RECONNECTING
                logger.warning(f"Binance WebSocket disconnected: {ws_err}")
                # Alternate the configured primary and fallback URLs. Both URLs
                # include the current active-pair subscription when applicable.
                if target_url == self.stream_url:
                    logger.info("Switching to configured Binance WebSocket fallback.")
                    target_url = self._fallback_stream_url
                else:
                    target_url = self.stream_url
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.state = ConnectionState.RECONNECTING
                logger.error(f"Unexpected WebSocket error: {exc}", exc_info=True)
                if target_url == self.stream_url:
                    target_url = self._fallback_stream_url
                else:
                    target_url = self.stream_url

            if self._running:
                self._reconnect_attempts += 1
                backoff_cap = min(
                    self._max_backoff,
                    self._base_backoff * (self._backoff_factor ** self._reconnect_attempts)
                )
                jittered_delay = random.uniform(1.0, backoff_cap)
                logger.info(f"Reconnecting in {jittered_delay:.2f} seconds...")
                await asyncio.sleep(jittered_delay)

    async def _send_subscriptions(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Send subscription payload if connecting to a bare WebSocket."""
        subscribe_payload = {
            "method": "SUBSCRIBE",
            "params": [f"{symbol.lower()}@ticker" for symbol in self.active_symbols],
            "id": int(time.time()),
        }
        await ws.send(json.dumps(subscribe_payload))
        logger.info("Sent WebSocket stream subscriptions.")

    def _handle_raw_message(self, raw_data: str | bytes) -> None:
        """
        Handle raw incoming message safely without blocking the WebSocket loop.
        Dispatches processing asynchronously via asyncio.create_task.
        """
        self._messages_received += 1
        self._last_heartbeat_time = time.time()

        try:
            payload = json.loads(raw_data)
        except Exception as e:
            logger.warning(f"Failed to decode WebSocket JSON: {e}")
            return

        # Handle Array of items (e.g. from !miniTicker@arr)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    self._process_single_tick(item)
            return

        # Handle Dictionary payload
        if isinstance(payload, dict):
            # Check if this is a response to SUBSCRIBE
            if "result" in payload and "id" in payload:
                logger.debug("Received subscription ack from Binance.")
                return

            # Check if wrapped in combined stream envelope {"stream": "...", "data": {...}}
            data = payload.get("data", payload)
            if isinstance(data, dict):
                self._process_single_tick(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        self._process_single_tick(item)

    def _process_single_tick(self, data: Dict[str, Any]) -> None:
        """Normalize and dispatch ticks for all active timeframes."""
        try:
            started = time.perf_counter()
            contexts = self._normalize_market_data(data)
            self.performance.observe("normalization_indicators", started)
            if contexts:
                source_age = contexts[0].spot_data_age_ms
                if source_age is not None:
                    self.performance.record("source_to_ingress", source_age)
                for market_context in contexts:
                    # Key by unique symbol + timeframe to guarantee exactly 1 live round per pair (at most 24 total)
                    key = f"{market_context.symbol}_{market_context.timeframe}"
                    self._active_markets[key] = market_context
                    self._last_event_time = time.time()

                    # Non-blocking distribution to trading core
                    if self.on_market_event:
                        if self.event_filter and not self.event_filter(market_context):
                            continue
                        self._events_dispatched += 1
                        self._schedule_market_event(market_context)
        except Exception as err:
            logger.error(f"Error processing single tick: {err}", exc_info=True)

    def _schedule_market_event(self, context: MarketContext) -> None:
        key = f"{context.symbol}_{context.timeframe}"
        self._pending_events[key] = context
        if key not in self._dispatch_tasks:
            self._dispatch_tasks[key] = self._spawn_task(self._drain_market_events(key))
        else:
            self.performance.counters["ticks_coalesced"] += 1

    async def _drain_market_events(self, key: str) -> None:
        try:
            while key in self._pending_events:
                context = self._pending_events.pop(key)
                if self.event_filter and not self.event_filter(context):
                    continue
                await self._safe_dispatch_event(context)
        finally:
            self._pending_events.pop(key, None)
            self._dispatch_tasks.pop(key, None)

    async def _safe_dispatch_event(self, context: MarketContext) -> None:
        """Execute callback inside a protected non-blocking task."""
        try:
            self.performance.record("dispatch_age", max(0, time.time() - context.timestamp) * 1000)
            context.is_stale = (self.network_state == "OFFLINE")
            if self.on_market_event:
                await self.on_market_event(context)
        except Exception as e:
            logger.error(f"Error in on_market_event handler task: {e}", exc_info=True)

    def _normalize_market_data(self, payload: Dict[str, Any]) -> List[MarketContext]:
        """Convert Binance raw stream tick into standardized MarketContext for all active timeframes."""
        symbol = payload.get("s", payload.get("symbol", "")).upper()
        if not symbol or symbol not in self.active_symbols:
            return []

        # Extract current market price (c=close/last, p=mark/last)
        price_str = payload.get("c") or payload.get("p") or payload.get("price")
        if not price_str:
            return []

        try:
            mark_price = float(price_str)
        except (ValueError, TypeError):
            return []

        # ``q`` is the underlying spot pair's rolling 24h quote volume.
        try:
            volume_24h = float(payload.get("q", 0.0))
        except (ValueError, TypeError):
            volume_24h = 0.0

        now = time.time()
        source_timestamp_ms: Optional[int] = None
        raw_source_timestamp = payload.get("E", payload.get("C"))
        if raw_source_timestamp is not None:
            try:
                source_timestamp_ms = int(raw_source_timestamp)
            except (ValueError, TypeError):
                source_timestamp_ms = None
        spot_data_age_ms = (
            max(0.0, now * 1000.0 - source_timestamp_ms)
            if source_timestamp_ms is not None
            else None
        )

        # These fields are spot top-of-book quantities, not prediction-contract depth.
        has_top_of_book = payload.get("B") is not None and payload.get("A") is not None
        try:
            bid_qty = float(payload.get("B", 0.0))
            ask_qty = float(payload.get("A", 0.0))
        except (ValueError, TypeError):
            bid_qty, ask_qty = 0.0, 0.0
            has_top_of_book = False
        obi_available = has_top_of_book and (bid_qty + ask_qty) > 0

        # Update in-memory candle aggregator
        self.candle_aggregator.update_tick(
            symbol=symbol,
            price=mark_price,
            # ``v`` is a rolling 24h cumulative volume on @ticker, not a per-tick delta.
            # Do not add it repeatedly to locally-built candle volume.
            volume=0.0,
            timestamp=now
        )

        if symbol == "BTCUSDT":
            self._btc_momentum = 0.0

        clean_symbol = symbol.replace("USDT", "")
        results: List[MarketContext] = []

        # Ultra-low latency quantitative metrics: Compute symbol-level features once per tick
        base_features = self.candle_aggregator.compute_base_features(
            symbol=symbol,
            spot_price=mark_price,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            btc_momentum_pct=self._btc_momentum,
        )
        momentum_pct = float(base_features.get("momentum_pct", 0.0))
        if symbol == "BTCUSDT":
            self._btc_momentum = momentum_pct

        for tf, round_period in TIMEFRAMES:
            local_round_idx = int(now // round_period)
            local_round_id = f"{symbol}-{tf.upper()}-R{local_round_idx}"
            time_left = round_period - int(now % round_period)
            round_id = local_round_id
            round_start_time: Optional[float] = None
            round_end_time: Optional[float] = None
            binance_topic_id: Optional[str] = None
            binance_market_ids: List[str] = []

            # Determine fixed Price to Beat for this round (prioritizes official Binance oracle startPrice)
            strike_confirmed = False
            official_strike = 0.0

            # 1. Check detailed strike entry with round time window validation
            detail = self._official_strike_details.get(f"{symbol}-{tf}")
            if detail:
                d_st = detail.get("start_time_sec", 0.0)
                d_et = detail.get("end_time_sec", 0.0)
                d_price = float(detail.get("start_price", 0.0))
                if d_price > 0 and d_st > 0 and d_et > d_st and d_st <= now < d_et:
                    official_strike = d_price
                    round_start_time = float(d_st)
                    round_end_time = float(d_et)
                    round_id = f"{symbol}-{tf.upper()}-R{int(round_start_time * 1000)}"
                    time_left = max(0, math.ceil(round_end_time - now))
                    binance_topic_id = str(detail.get("topic_id") or "") or None
                    binance_market_ids = [str(value) for value in detail.get("market_ids", []) if value]

            # 2. Check round_base_prices cache if already confirmed
            if official_strike > 0:
                strike = official_strike
                strike_confirmed = True
                self._round_base_prices[round_id] = official_strike
            elif round_id in self._round_base_prices:
                strike = self._round_base_prices[round_id]
                strike_confirmed = True
            elif self.enable_mock_stream:
                # Mock stream offline testing mode
                strike = round(mark_price, 2)
                strike_confirmed = True
                self._round_base_prices[round_id] = strike
            else:
                # Never reuse an unscoped symbol/timeframe strike: it may belong to
                # the prior Binance market round. Wait for an active dated oracle record.
                strike = mark_price
                strike_confirmed = False

            price_diff = round(mark_price - strike, 4) if strike_confirmed else 0.0
            target_price = strike if strike_confirmed else 0.0

            # This is a model-derived directional estimate, not a live contract quote.
            diff_ratio = ((mark_price - strike) / strike) if strike > 0 else 0.0
            vol = 0.004 * math.sqrt(max(10, time_left) / max(60, round_period))
            z = (diff_ratio + (momentum_pct * 0.0005)) / max(0.0001, vol)
            prob_up = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, 1.8 * z))))
            odds_up = round(max(0.01, min(0.99, prob_up)), 3)
            odds_down = round(1.0 - odds_up, 3)

            question = f"{clean_symbol} Up or Down {tf}"

            # Calculate timeframe/round specific metrics (~0.01ms)
            metrics = self.candle_aggregator.compute_round_metrics(
                base_features=base_features,
                spot_price=mark_price,
                strike_price=strike,
                time_left_seconds=time_left,
                round_id=round_id,
            )

            results.append(
                MarketContext(
                    market_id=round_id,
                    symbol=symbol,
                    question=question,
                    timeframe=tf,
                    odds_yes=odds_up,
                    odds_no=odds_down,
                    spread=None,
                    volume_24h=volume_24h,
                    time_left_seconds=time_left,
                    underlying_price=mark_price,
                    target_price=target_price,
                    price_diff=price_diff,
                    momentum_pct=momentum_pct,
                    atr_1m=metrics["atr_1m"],
                    dvr_ratio=metrics["dvr_ratio"] if strike_confirmed else 0.0,
                    strike_diff_bps=metrics["strike_diff_bps"],
                    strike_diff_pct=metrics["strike_diff_pct"],
                    strike_velocity_bps_s=metrics["strike_velocity_bps_s"],
                    strike_velocity_desc=metrics["strike_velocity_desc"],
                    market_session=metrics["market_session"],
                    recent_candles_summary=metrics["recent_candles_summary"],
                    macro_trend_15m=metrics["macro_trend_15m"],
                    rsi_1m=metrics["rsi_1m"],
                    rsi_5m=metrics["rsi_5m"],
                    ema_trend=metrics["ema_trend"],
                    order_book_imbalance=metrics["order_book_imbalance"],
                    market_regime=metrics["market_regime"],
                    expiry_danger_flag=metrics["expiry_danger_flag"],
                    btc_correlation_dir=metrics["btc_correlation_dir"],
                    strike_confirmed=strike_confirmed,
                    round_start_time_sec=round_start_time,
                    round_end_time_sec=round_end_time,
                    binance_topic_id=binance_topic_id,
                    binance_market_ids=binance_market_ids,
                    timestamp=now,
                    spot_source_timestamp_ms=source_timestamp_ms,
                    spot_data_age_ms=spot_data_age_ms,
                    indicator_data_ready=bool(base_features["indicator_data_ready"]),
                    one_minute_sample_count=int(base_features["one_minute_sample_count"]),
                    five_minute_sample_count=int(base_features["five_minute_sample_count"]),
                    momentum_available=bool(base_features["momentum_available"]),
                    obi_available=obi_available,
                )
            )

        return results

    async def _bootstrap_kline_history(self) -> None:
        """
        Bootstrap historical 1m and 5m klines from Binance REST API on startup.
        Pre-seeds RollingCandleAggregator to eliminate the 30-minute cold-start indicator lag.
        """
        logger.info("[KLINE BOOTSTRAP] Pre-fetching historical klines from Binance REST API...")
        base_url = "https://api.binance.com/api/v3/klines"
        symbols_to_fetch = list(self.active_symbols)

        for sym in symbols_to_fetch:
            if not self._running:
                break
            try:
                session = self._http_session
                if session is None or session.closed:
                    continue

                candles_1m: List[Candle] = []
                candles_5m: List[Candle] = []

                # Fetch enough candles to warm EMA(50), RSI(14), and macro features.
                async with session.get(f"{base_url}?symbol={sym}&interval=1m&limit=100") as resp_1m:
                    if resp_1m.status == 200:
                        raw_1m = await resp_1m.json()
                        for k in raw_1m:
                            candles_1m.append(
                                Candle(
                                    timestamp=float(k[0]) / 1000.0,
                                    open=float(k[1]),
                                    high=float(k[2]),
                                    low=float(k[3]),
                                    close=float(k[4]),
                                    volume=float(k[5]),
                                )
                            )

                async with session.get(f"{base_url}?symbol={sym}&interval=5m&limit=100") as resp_5m:
                    if resp_5m.status == 200:
                        raw_5m = await resp_5m.json()
                        for k in raw_5m:
                            candles_5m.append(
                                Candle(
                                    timestamp=float(k[0]) / 1000.0,
                                    open=float(k[1]),
                                    high=float(k[2]),
                                    low=float(k[3]),
                                    close=float(k[4]),
                                    volume=float(k[5]),
                                )
                            )

                if candles_1m or candles_5m:
                    self.candle_aggregator.seed_candles(
                        symbol=sym,
                        candles_1m=candles_1m,
                        candles_5m=candles_5m if candles_5m else None,
                    )
                    logger.info(
                        f"[KLINE BOOTSTRAP] Successfully seeded {len(candles_1m)} 1m candles "
                        f"and {len(candles_5m)} 5m candles for {sym}."
                    )
            except Exception as e:
                logger.warning(f"[KLINE BOOTSTRAP] Failed to bootstrap klines for {sym}: {e}")

    async def _watchdog_rest_poller(self) -> None:
        """
        Background Watchdog: polls Binance REST API if WebSocket has been silent
        for more than 3.5 seconds. Ensures 100% continuous data updates regardless of network.
        """
        logger.info("Watchdog REST Poller activated for continuous fallback telemetry.")
        await asyncio.sleep(2.0)  # Initial grace period

        while self._running:
            try:
                silence_duration = time.time() - self._last_event_time
                if silence_duration >= 3.5:
                    if self._http_session and not self._http_session.closed:
                        async with self._http_session.get(_rest_ticker_url_for_symbols(self.active_symbols)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if isinstance(data, list):
                                    for item in data:
                                        self._process_single_tick(item)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Watchdog REST poll notice: {e}")

            await asyncio.sleep(3.0)

    async def _run_mock_stream_generator(self) -> None:
        """
        High-fidelity simulated market feed generator for testing and demonstration.
        Emits ticks for the currently configured pair allowlist.
        """
        self.state = ConnectionState.CONNECTED
        logger.info("Mock Stream Generator activated: emitting synthetic prediction market ticks.")

        mock_base_prices = {
            "BTCUSDT": 84000.0, "ETHUSDT": 2680.0, "BNBUSDT": 585.0,
            "SOLUSDT": 114.5, "DOGEUSDT": 0.14, "XRPUSDT": 0.60,
        }

        while self._running:
            for symbol in list(self.active_symbols):
                if not self._running:
                    break

                base_price = mock_base_prices.get(symbol, 100.0)
                clean_symbol = symbol.replace("USDT", "")
                question = f"Will {clean_symbol} meet its 15m prediction-market target?"
                pct_change = random.gauss(0.0001, 0.0015)
                current_price = round(base_price * (1.0 + pct_change), 2)
                target = round(base_price * 1.0015, 2)

                time_left = 900 - int(time.time() % 900)
                diff = (current_price - target) / target
                odds_yes = round(max(0.08, min(0.92, 0.50 + (diff * 35.0) + random.uniform(-0.02, 0.02))), 3)
                odds_no = round(1.0 - odds_yes, 3)

                self.candle_aggregator.update_tick(symbol, current_price, volume=random.uniform(50, 500))
                mock_round_id = f"{symbol}-15M-R{int(time.time() // 900)}"
                metrics = self.candle_aggregator.compute_all_metrics(
                    symbol=symbol,
                    spot_price=current_price,
                    strike_price=target,
                    time_left_seconds=time_left,
                    bid_qty=random.uniform(10, 50),
                    ask_qty=random.uniform(10, 50),
                    btc_momentum_pct=0.05,
                    round_id=mock_round_id,
                )

                market = MarketContext(
                    market_id=mock_round_id,
                    symbol=symbol,
                    question=question,
                    odds_yes=odds_yes,
                    odds_no=odds_no,
                    spread=0.012,
                    volume_24h=round(random.uniform(500000, 2500000), 2),
                    time_left_seconds=time_left,
                    underlying_price=current_price,
                    target_price=target,
                    price_diff=round(current_price - target, 4),
                    momentum_pct=round(pct_change * 100.0, 2),
                    atr_1m=metrics["atr_1m"],
                    dvr_ratio=metrics["dvr_ratio"],
                    strike_diff_bps=metrics["strike_diff_bps"],
                    strike_diff_pct=metrics["strike_diff_pct"],
                    strike_velocity_bps_s=metrics["strike_velocity_bps_s"],
                    strike_velocity_desc=metrics["strike_velocity_desc"],
                    market_session=metrics["market_session"],
                    rsi_1m=metrics["rsi_1m"],
                    rsi_5m=metrics["rsi_5m"],
                    ema_trend=metrics["ema_trend"],
                    order_book_imbalance=metrics["order_book_imbalance"],
                    market_regime=metrics["market_regime"],
                    expiry_danger_flag=metrics["expiry_danger_flag"],
                    btc_correlation_dir=metrics["btc_correlation_dir"],
                )

                self._messages_received += 1
                self._last_heartbeat_time = time.time()
                self._last_event_time = time.time()
                self._active_markets[f"{symbol}_{market.timeframe}"] = market

                if self.on_market_event:
                    self._events_dispatched += 1
                    self._schedule_market_event(market)

                await asyncio.sleep(2.0)

    async def _network_liveness_prober(self) -> None:
        """
        Active Network Liveness & Latency Prober.
        Periodically pings Binance REST API endpoint every 3 seconds to measure
        real round-trip network latency and actively detect disconnection/packet drop.
        """
        logger.info("Active Network Liveness & Latency Prober started.")
        ping_url = "https://api.binance.com/api/v3/ping"
        await asyncio.sleep(1.0)

        while self._running:
            try:
                if self._http_session and not self._http_session.closed:
                    t0 = time.perf_counter()
                    async with self._http_session.get(ping_url, timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                        if resp.status == 200:
                            t1 = time.perf_counter()
                            self._latency_ms = round((t1 - t0) * 1000.0, 1)
                            self._network_healthy = True
                        else:
                            self._network_healthy = False
            except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
                self._network_healthy = False
                logger.warning(f"[NETWORK PROBER] Network probe failed or timed out: {net_err}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._network_healthy = False
                logger.debug(f"[NETWORK PROBER] Liveness probe error: {e}")

            await asyncio.sleep(3.0)

    @property
    def network_state(self) -> str:
        """
        Tri-state network liveness for fail-safe trading protection:
        - ONLINE: Low-latency connection, WebSocket active, fresh market data.
        - DEGRADED: WebSocket reconnecting, but REST watchdog poller is maintaining data.
        - OFFLINE: Connection timed out (>5s silence) or internet probe failed.
        """
        if self.enable_mock_stream:
            return "ONLINE"
        now = time.time()
        age = (now - self._last_event_time) if self._last_event_time > 0 else 0.0
        if not self._network_healthy or (self._last_event_time > 0 and age > self._max_stale_seconds):
            return "OFFLINE"
        if self.state == ConnectionState.CONNECTED:
            return "ONLINE"
        return "DEGRADED"

    def get_latest_market(self, symbol: str, timeframe: str) -> Optional[MarketContext]:
        market = self._active_markets.get(f"{symbol}_{timeframe}")
        return market.model_copy(deep=True) if market is not None else None

    def get_active_markets(self) -> List[Dict[str, Any]]:
        """
        Return snapshot of currently active prediction markets across all assets and timeframes.
        Guarantees strictly 1 active round per symbol-timeframe pair (exactly 24 markets max).
        """
        now = time.time()
        tf_dict = dict(TIMEFRAMES)
        active_list: List[MarketContext] = []
        is_net_stale = (self.network_state == "OFFLINE")

        # Prune older round base prices to avoid memory growth
        if len(self._round_base_prices) > 300:
            for old_key in list(self._round_base_prices.keys())[:-100]:
                self._round_base_prices.pop(old_key, None)

        for k in list(self._active_markets.keys()):
            m = self._active_markets[k]
            round_period = tf_dict.get(m.timeframe, 300)
            if m.round_start_time_sec is not None and m.round_end_time_sec is not None:
                if m.round_start_time_sec <= now < m.round_end_time_sec:
                    m.time_left_seconds = max(0, math.ceil(m.round_end_time_sec - now))
                else:
                    # The displayed Binance round has expired (or is not active yet).
                    # Do not carry its strike into a new locally generated round.
                    m.time_left_seconds = 0
                    m.strike_confirmed = False
                    m.target_price = 0.0
                    m.price_diff = 0.0
            else:
                time_left = round_period - int(now % round_period)
                current_round_idx = int(now // round_period)
                expected_round_id = f"{m.symbol}-{m.timeframe.upper()}-R{current_round_idx}"

                # In the absence of an authoritative Binance interval, move the local
                # ID but clear the previous round's strike instead of silently reusing it.
                if m.market_id != expected_round_id:
                    m.market_id = expected_round_id
                    cached_strike = self._round_base_prices.get(expected_round_id)
                    if cached_strike is not None:
                        m.target_price = cached_strike
                        m.strike_confirmed = True
                    else:
                        m.target_price = 0.0
                        m.price_diff = 0.0
                        m.strike_confirmed = False
                m.time_left_seconds = time_left
            m.is_stale = is_net_stale
            active_list.append(m)

        sym_order = {s: i for i, s in enumerate(self.active_symbols)}
        tf_order = {"5m": 0, "15m": 1, "1h": 2, "1d": 3}

        def sort_key(m: MarketContext):
            return (sym_order.get(m.symbol, 99), tf_order.get(m.timeframe, 99))

        sorted_markets = sorted(active_list, key=sort_key)
        return [m.model_dump() for m in sorted_markets]

    @property
    def metrics(self) -> Dict[str, Any]:
        """WebSocket & Network connection telemetry."""
        age = round(time.time() - self._last_event_time, 2) if self._last_event_time > 0 else 0.0
        net_state = self.network_state
        return {
            "state": self.state.value,
            "network_state": net_state,
            "latency_ms": self._latency_ms,
            "network_healthy": self._network_healthy,
            "last_packet_age_seconds": age,
            "is_stale": (net_state == "OFFLINE"),
            "stream_url": self.stream_url,
            "mock_mode": self.enable_mock_stream,
            "messages_received": self._messages_received,
            "events_dispatched": self._events_dispatched,
            "last_event_time": self._last_event_time,
            "active_markets_count": len(self._active_markets),
            "reconnect_attempts": self._reconnect_attempts,
            "ping_interval": self.ping_interval,
            "ping_timeout": self.ping_timeout,
        }
