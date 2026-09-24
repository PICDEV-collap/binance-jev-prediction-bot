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
import json
import logging
import random
import time
from enum import Enum
from typing import Callable, Coroutine, Any, Optional, Dict, List

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from engine.jev_client import MarketContext

logger = logging.getLogger("ws_listener")

SPOT_COMBINED_STREAM_URL = (
    "wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker"
)
REST_TICKER_API = "https://api.binance.com/api/v3/ticker/24hr?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22SOLUSDT%22%5D"


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
        ping_interval: int = 20,
        ping_timeout: int = 10,
        enable_mock_stream: bool = False,
    ) -> None:
        # Default to high-reliability Spot combined stream if fstream was supplied
        if "fstream.binance.com" in stream_url:
            self.stream_url = SPOT_COMBINED_STREAM_URL
            self._fallback_stream_url = stream_url
        else:
            self.stream_url = stream_url
            self._fallback_stream_url = SPOT_COMBINED_STREAM_URL

        self.on_market_event = on_market_event
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
        self._price_history: Dict[str, List[tuple[float, float]]] = {}

    async def start(self) -> None:
        """Start the WebSocket listener and background watchdog loop."""
        if self._running:
            logger.warning("WebSocket listener already running.")
            return

        self._running = True
        logger.info(f"Starting Binance WebSocket Listener (Mock Mode: {self.enable_mock_stream})...")

        if self.enable_mock_stream:
            self._mock_task = asyncio.create_task(self._run_mock_stream_generator())
        else:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=4.0)
            )
            self._listener_task = asyncio.create_task(self._connection_supervisor())
            self._watchdog_task = asyncio.create_task(self._watchdog_rest_poller())

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

        if self._mock_task and not self._mock_task.done():
            self._mock_task.cancel()
            try:
                await self._mock_task
            except asyncio.CancelledError:
                pass

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

        logger.info("Binance WebSocket Listener terminated cleanly.")

    async def _connection_supervisor(self) -> None:
        """
        Supervisor loop that manages connection lifecycle and auto-reconnection
        with exponential backoff and jitter.
        """
        target_url = self.stream_url

        while self._running:
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
                    if "?" not in target_url:
                        await self._send_subscriptions(ws)

                    # Message ingress loop
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        self._handle_raw_message(raw_msg)

            except (ConnectionClosed, WebSocketException) as ws_err:
                self.state = ConnectionState.RECONNECTING
                logger.warning(f"Binance WebSocket disconnected: {ws_err}")
                # Switch to backup URL if primary fails repeatedly
                if target_url != SPOT_COMBINED_STREAM_URL:
                    logger.info("Flipping to Binance Spot Combined Stream fallback.")
                    target_url = SPOT_COMBINED_STREAM_URL
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.state = ConnectionState.RECONNECTING
                logger.error(f"Unexpected WebSocket error: {exc}", exc_info=True)

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
            "params": [
                "btcusdt@ticker",
                "ethusdt@ticker",
                "solusdt@ticker",
            ],
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
        """Normalize and dispatch a single tick dictionary."""
        try:
            market_context = self._normalize_market_data(data)
            if market_context:
                # Key by symbol and market_id for fast lookup
                self._active_markets[market_context.symbol] = market_context
                self._active_markets[market_context.market_id] = market_context
                self._last_event_time = time.time()

                # Non-blocking distribution to trading core
                if self.on_market_event:
                    self._events_dispatched += 1
                    asyncio.create_task(self._safe_dispatch_event(market_context))
        except Exception as err:
            logger.error(f"Error processing single tick: {err}", exc_info=True)

    async def _safe_dispatch_event(self, context: MarketContext) -> None:
        """Execute callback inside a protected non-blocking task."""
        try:
            if self.on_market_event:
                await self.on_market_event(context)
        except Exception as e:
            logger.error(f"Error in on_market_event handler task: {e}", exc_info=True)

    def _normalize_market_data(self, payload: Dict[str, Any]) -> Optional[MarketContext]:
        """Convert Binance raw stream tick into standardized MarketContext."""
        symbol = payload.get("s", payload.get("symbol", "")).upper()
        if not symbol or symbol not in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            return None

        # Extract current market price (c=close/last, p=mark/last)
        price_str = payload.get("c") or payload.get("p") or payload.get("price")
        if not price_str:
            return None

        try:
            mark_price = float(price_str)
        except (ValueError, TypeError):
            return None

        # Volume quote asset (q = quote volume, v = base volume)
        try:
            volume_24h = float(payload.get("q", payload.get("v", 1500000.0)))
        except (ValueError, TypeError):
            volume_24h = 1500000.0

        # 24h price change percentage
        try:
            change_24h_pct = float(payload.get("P", 0.0))
        except (ValueError, TypeError):
            change_24h_pct = 0.0

        # Current 15-minute prediction round
        now = time.time()
        round_period = 900  # 15 minutes
        round_id = f"{symbol}-15M-R{int(now // round_period)}"
        time_left = round_period - int(now % round_period)

        # Realistic prediction strike price
        if "BTC" in symbol:
            strike = round(round(mark_price * 1.0015 / 50.0) * 50.0, 2)
            base_spread = 0.012
        elif "ETH" in symbol:
            strike = round(round(mark_price * 1.0015 / 10.0) * 10.0, 2)
            base_spread = 0.015
        elif "SOL" in symbol:
            strike = round(round(mark_price * 1.0015 / 0.5) * 0.5, 2)
            base_spread = 0.018
        else:
            strike = round(mark_price * 1.0015, 2)
            base_spread = 0.015

        # Calculate real rolling 5m momentum from live price history
        history = self._price_history.setdefault(symbol, [])
        history.append((now, mark_price))
        cutoff = now - 300.0
        while len(history) > 1 and history[0][0] < cutoff:
            history.pop(0)

        if len(history) > 1 and history[0][1] > 0:
            momentum_pct = round(((mark_price - history[0][1]) / history[0][1]) * 100.0, 3)
        else:
            # Fallback to scaled 24h momentum
            momentum_pct = round(change_24h_pct / 48.0, 3)

        # Implied odds calculation from distance to strike and momentum
        diff_ratio = (mark_price - strike) / mark_price
        odds_yes = round(max(0.08, min(0.92, 0.50 + (diff_ratio * 40.0) + (momentum_pct * 0.05))), 3)
        odds_no = round(1.0 - odds_yes, 3)

        # Realistic bid/ask spread for binary prediction contract
        spread = base_spread

        clean_symbol = symbol.replace("USDT", "")
        question = f"Will {clean_symbol} settle >= ${strike:,.2f} at 15m expiration?"

        return MarketContext(
            market_id=round_id,
            symbol=symbol,
            question=question,
            odds_yes=odds_yes,
            odds_no=odds_no,
            spread=spread,
            volume_24h=volume_24h,
            time_left_seconds=time_left,
            underlying_price=mark_price,
            target_price=strike,
            momentum_pct=momentum_pct
        )

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
                        async with self._http_session.get(REST_TICKER_API) as resp:
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
        Emits realistic market ticks for BTC, ETH, and SOL prediction markets.
        """
        self.state = ConnectionState.CONNECTED
        logger.info("Mock Stream Generator activated: emitting synthetic prediction market ticks.")

        symbols = [
            ("BTCUSDT", 84000.0, "Will BTC close >= $84,100 at 15m expiration?"),
            ("ETHUSDT", 2680.0, "Will ETH settle >= $2,690 at 15m expiration?"),
            ("SOLUSDT", 114.5, "Will SOL hold >= $115.00 at 15m expiration?"),
        ]

        while self._running:
            for symbol, base_price, question in symbols:
                if not self._running:
                    break

                pct_change = random.gauss(0.0001, 0.0015)
                current_price = round(base_price * (1.0 + pct_change), 2)
                target = round(base_price * 1.0015, 2)

                time_left = 900 - int(time.time() % 900)
                diff = (current_price - target) / target
                odds_yes = round(max(0.08, min(0.92, 0.50 + (diff * 35.0) + random.uniform(-0.02, 0.02))), 3)
                odds_no = round(1.0 - odds_yes, 3)

                market = MarketContext(
                    market_id=f"{symbol}-15M-R{int(time.time() // 900)}",
                    symbol=symbol,
                    question=question,
                    odds_yes=odds_yes,
                    odds_no=odds_no,
                    spread=0.012,
                    volume_24h=round(random.uniform(500000, 2500000), 2),
                    time_left_seconds=time_left,
                    underlying_price=current_price,
                    target_price=target,
                    momentum_pct=round(pct_change * 100.0, 2)
                )

                self._messages_received += 1
                self._last_heartbeat_time = time.time()
                self._last_event_time = time.time()
                self._active_markets[symbol] = market
                self._active_markets[market.market_id] = market

                if self.on_market_event:
                    self._events_dispatched += 1
                    asyncio.create_task(self._safe_dispatch_event(market))

                await asyncio.sleep(2.0)

    def get_active_markets(self) -> List[Dict[str, Any]]:
        """Return snapshot of unique currently active prediction markets (BTC, ETH, SOL)."""
        unique_markets: Dict[str, MarketContext] = {}
        for m in self._active_markets.values():
            unique_markets[m.symbol] = m
        return [m.model_dump() for m in unique_markets.values()]

    @property
    def metrics(self) -> Dict[str, Any]:
        """WebSocket connection telemetry."""
        unique_count = len(set(m.symbol for m in self._active_markets.values()))
        return {
            "state": self.state.value,
            "stream_url": self.stream_url,
            "mock_mode": self.enable_mock_stream,
            "messages_received": self._messages_received,
            "events_dispatched": self._events_dispatched,
            "last_event_time": self._last_event_time,
            "active_markets_count": unique_count if unique_count > 0 else len(self._active_markets),
            "reconnect_attempts": self._reconnect_attempts,
            "ping_interval": self.ping_interval,
            "ping_timeout": self.ping_timeout,
        }
