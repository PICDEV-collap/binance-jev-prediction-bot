"""
High-Performance Binance Prediction Markets WebSocket Listener.
Features auto-reconnection with exponential backoff & jitter,
heartbeat/ping-pong management (ping_interval=20),
and non-blocking event distribution using asyncio.create_task().
"""

from __future__ import annotations
import asyncio
import json
import logging
import random
import time
from enum import Enum
from typing import Callable, Coroutine, Any, Optional, Dict, List

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from engine.jev_client import MarketContext

logger = logging.getLogger("ws_listener")


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
        stream_url: str = "wss://fstream.binance.com/ws",
        on_market_event: Optional[MarketEventCallback] = None,
        ping_interval: int = 20,
        ping_timeout: int = 10,
        enable_mock_stream: bool = False,
    ) -> None:
        self.stream_url = stream_url
        self.on_market_event = on_market_event
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.enable_mock_stream = enable_mock_stream

        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self._running: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._mock_task: Optional[asyncio.Task] = None

        # Reconnect parameters
        self._base_backoff: float = 1.0
        self._backoff_factor: float = 1.8
        self._max_backoff: float = 30.0
        self._reconnect_attempts: int = 0

        # Telemetry stats
        self._messages_received: int = 0
        self._events_dispatched: int = 0
        self._last_heartbeat_time: float = 0.0
        self._last_event_time: float = 0.0
        self._active_markets: Dict[str, MarketContext] = {}
        self._price_history: Dict[str, List[tuple[float, float]]] = {}

    async def start(self) -> None:
        """Start the WebSocket listener loop."""
        if self._running:
            logger.warning("WebSocket listener already running.")
            return

        self._running = True
        logger.info(f"Starting Binance WebSocket Listener (Mock Mode: {self.enable_mock_stream})...")

        if self.enable_mock_stream:
            self._mock_task = asyncio.create_task(self._run_mock_stream_generator())
        else:
            self._listener_task = asyncio.create_task(self._connection_supervisor())

    async def stop(self) -> None:
        """Gracefully stop the listener and close sockets."""
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

        if self._mock_task and not self._mock_task.done():
            self._mock_task.cancel()
            try:
                await self._mock_task
            except asyncio.CancelledError:
                pass

        logger.info("Binance WebSocket Listener terminated cleanly.")

    async def _connection_supervisor(self) -> None:
        """
        Supervisor loop that manages connection lifecycle and auto-reconnection
        with exponential backoff and jitter.
        """
        while self._running:
            try:
                self.state = (
                    ConnectionState.CONNECTING
                    if self._reconnect_attempts == 0
                    else ConnectionState.RECONNECTING
                )
                logger.info(
                    f"Connecting to Binance Stream: {self.stream_url} "
                    f"(Attempt: {self._reconnect_attempts + 1})"
                )

                async with websockets.connect(
                    self.stream_url,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    close_timeout=5,
                    max_size=2**20,  # 1MB buffer
                ) as ws:
                    self._ws = ws
                    self.state = ConnectionState.CONNECTED
                    self._reconnect_attempts = 0
                    logger.info("Successfully connected to Binance WebSocket Stream.")

                    # Subscribe to prediction market ticker streams if required
                    await self._send_subscriptions(ws)

                    # Message ingress loop
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        self._handle_raw_message(raw_msg)

            except (ConnectionClosed, WebSocketException) as ws_err:
                self.state = ConnectionState.RECONNECTING
                logger.warning(f"Binance WebSocket disconnected: {ws_err}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.state = ConnectionState.RECONNECTING
                logger.error(f"Unexpected WebSocket error: {exc}", exc_info=True)

            if self._running:
                # Compute backoff with full jitter: uniform(0, min(max_backoff, base * factor^attempt))
                self._reconnect_attempts += 1
                backoff_cap = min(
                    self._max_backoff,
                    self._base_backoff * (self._backoff_factor ** self._reconnect_attempts)
                )
                jittered_delay = random.uniform(0.5, backoff_cap)
                logger.info(f"Reconnecting in {jittered_delay:.2f} seconds...")
                await asyncio.sleep(jittered_delay)

    async def _send_subscriptions(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Send subscription payload for prediction market streams."""
        subscribe_payload = {
            "method": "SUBSCRIBE",
            "params": [
                "!miniTicker@arr",
                "btcusdt@markPrice@1s",
                "ethusdt@markPrice@1s",
            ],
            "id": int(time.time()),
        }
        await ws.send(json.dumps(subscribe_payload))
        logger.info("Sent WebSocket stream subscriptions.")

    def _handle_raw_message(self, raw_data: str | bytes) -> None:
        """
        Handle raw incoming message without blocking the WebSocket loop.
        Dispatches processing asynchronously via asyncio.create_task.
        """
        self._messages_received += 1
        self._last_heartbeat_time = time.time()

        try:
            payload = json.loads(raw_data)
        except Exception as e:
            logger.warning(f"Failed to decode WebSocket JSON: {e}")
            return

        # Normalize incoming stream data into a MarketContext
        market_context = self._normalize_market_data(payload)
        if market_context:
            self._active_markets[market_context.market_id] = market_context
            self._last_event_time = time.time()

            # Non-blocking distribution to trading core
            if self.on_market_event:
                self._events_dispatched += 1
                asyncio.create_task(self._safe_dispatch_event(market_context))

    async def _safe_dispatch_event(self, context: MarketContext) -> None:
        """Execute callback inside a protected non-blocking task."""
        try:
            if self.on_market_event:
                await self.on_market_event(context)
        except Exception as e:
            logger.error(f"Error in on_market_event handler task: {e}", exc_info=True)

    def _normalize_market_data(self, payload: Dict[str, Any]) -> Optional[MarketContext]:
        """Convert Binance raw stream tick into standardized MarketContext."""
        # Handle Binance markPrice stream
        if payload.get("e") == "markPriceUpdate":
            symbol = payload.get("s", "BTCUSDT")
            mark_price = float(payload.get("p", 64000.0))

            # Synthesize prediction round based on current 15m mark
            round_id = f"{symbol}-15M-{int(time.time() // 900)}"
            strike = round(mark_price * 1.002, 2)
            time_left = 900 - int(time.time() % 900)

            # Synthetic odds calculation from distance to strike
            diff_ratio = (mark_price - strike) / mark_price
            odds_yes = round(max(0.05, min(0.95, 0.50 + (diff_ratio * 25.0))), 3)
            odds_no = round(1.0 - odds_yes, 3)

            # Calculate real rolling 5m momentum from live price history
            now = time.time()
            history = self._price_history.setdefault(symbol, [])
            history.append((now, mark_price))
            cutoff = now - 300.0
            while len(history) > 1 and history[0][0] < cutoff:
                history.pop(0)

            if len(history) > 1 and history[0][1] > 0:
                momentum_pct = round(((mark_price - history[0][1]) / history[0][1]) * 100.0, 3)
            else:
                momentum_pct = 0.0

            return MarketContext(
                market_id=round_id,
                symbol=symbol,
                question=f"Will {symbol} close >= ${strike:,.2f} this 15M round?",
                odds_yes=odds_yes,
                odds_no=odds_no,
                spread=0.015,
                volume_24h=1845000.0,
                time_left_seconds=time_left,
                underlying_price=mark_price,
                target_price=strike,
                momentum_pct=momentum_pct
            )

        # Handle direct prediction market ticker format if present
        if "marketId" in payload or "oddsYes" in payload:
            return MarketContext(
                market_id=payload.get("marketId", "MARKET-001"),
                symbol=payload.get("symbol", "BTCUSDT"),
                question=payload.get("question", "Binary Event Settlement"),
                odds_yes=float(payload.get("oddsYes", 0.50)),
                odds_no=float(payload.get("oddsNo", 0.50)),
                spread=float(payload.get("spread", 0.02)),
                volume_24h=float(payload.get("volume24h", 100000.0)),
                time_left_seconds=int(payload.get("timeLeft", 300)),
                underlying_price=float(payload.get("underlyingPrice", 0.0)),
                target_price=float(payload.get("targetPrice", 0.0)),
                momentum_pct=float(payload.get("momentumPct", 0.0))
            )

        return None

    async def _run_mock_stream_generator(self) -> None:
        """
        High-fidelity simulated market feed generator for testing and demonstration.
        Emits realistic market ticks for BTC, ETH, and SOL prediction markets.
        """
        self.state = ConnectionState.CONNECTED
        logger.info("Mock Stream Generator activated: emitting synthetic prediction market ticks.")

        symbols = [
            ("BTCUSDT", 65420.0, "Will BTC close >= $65,500 at 15m expiration?"),
            ("ETHUSDT", 3480.0, "Will ETH settle >= $3,500 at 15m expiration?"),
            ("SOLUSDT", 152.5, "Will SOL hold >= $153.00 at 15m expiration?"),
        ]

        while self._running:
            for symbol, base_price, question in symbols:
                if not self._running:
                    break

                # Apply micro Brownian motion to spot price
                pct_change = random.gauss(0.0001, 0.0015)
                current_price = round(base_price * (1.0 + pct_change), 2)
                target = round(base_price * 1.0015, 2)

                time_left = 900 - int(time.time() % 900)
                diff = (current_price - target) / target
                odds_yes = round(max(0.08, min(0.92, 0.50 + (diff * 35.0) + random.uniform(-0.03, 0.03))), 3)
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
                self._active_markets[market.market_id] = market

                if self.on_market_event:
                    self._events_dispatched += 1
                    asyncio.create_task(self._safe_dispatch_event(market))

                await asyncio.sleep(2.5)  # 2.5s between simulated market updates

    def get_active_markets(self) -> List[Dict[str, Any]]:
        """Return snapshot of currently active prediction markets."""
        return [m.model_dump() for m in self._active_markets.values()]

    @property
    def metrics(self) -> Dict[str, Any]:
        """WebSocket connection telemetry."""
        return {
            "state": self.state.value,
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
