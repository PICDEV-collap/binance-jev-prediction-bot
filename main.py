"""
Main entry point for Binance Prediction Markets Event-Driven Trading Bot
integrated with Jev AI Decision Engine.

Coordinates Market Ingress (WS), AI Evaluation (Jev AI),
Risk Guard (Threshold & Cooldown), and Execution (Binance REST API),
while serving a real-time Telemetry & Control API for the Web Dashboard.
"""

from __future__ import annotations
import asyncio
import logging
import signal
import sys
import time
from typing import Dict, Any, List, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import settings
from engine.binance_client import BinanceClient, OrderResult
from engine.jev_client import JevClient, JevEvaluationResult, MarketContext
from engine.risk_guard import RiskGuard, RiskEvaluationResult
from streams.ws_listener import BinanceWSListener, ConnectionState

# Setup structured logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main_engine")


class ConfigUpdateRequest(BaseModel):
    confidence_threshold: float | None = None
    max_position_size_usdt: float | None = None
    cooldown_seconds: int | None = None
    paper_trading: bool | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TradingBotCoordinator:
    """
    Master coordinator orchestrating the event-driven trading lifecycle.
    """

    def __init__(self) -> None:
        self.start_time = time.time()
        self.is_paused: bool = False
        self.bot_status: str = "RUNNING"  # "RUNNING" or "STOPPED"

        # Initialize core components
        self.jev_client = JevClient(
            api_key=settings.jev_ai_api_key,
            endpoint=settings.jev_ai_endpoint,
            model=settings.jev_ai_model,
            timeout_seconds=settings.jev_ai_timeout_seconds,
        )

        self.binance_client = BinanceClient(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            base_url=settings.binance_prediction_base_url,
            recv_window=settings.binance_recv_window,
            paper_trading=settings.paper_trading,
            slippage_tolerance=settings.slippage_tolerance,
        )

        self.risk_guard = RiskGuard(
            confidence_threshold=settings.confidence_threshold,
            max_position_size_usdt=settings.max_position_size_usdt,
            default_order_contracts=settings.default_order_contracts,
            cooldown_seconds=settings.cooldown_seconds,
            max_daily_loss_usdt=settings.max_daily_loss_usdt,
            max_concurrent_positions=settings.max_concurrent_positions,
        )

        # Decide whether to use mock stream if no API keys or explicitly enabled
        use_mock_stream = settings.enable_mock_stream or not bool(settings.binance_api_key)

        self.ws_listener = BinanceWSListener(
            stream_url=settings.binance_prediction_ws_url,
            on_market_event=self.on_market_tick,
            ping_interval=20,
            ping_timeout=10,
            enable_mock_stream=use_mock_stream,
        )

        # Telemetry storage
        self.recent_decisions: List[Dict[str, Any]] = []
        self.ws_clients: Set[WebSocket] = set()
        self._last_eval_time: Dict[str, float] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start trading core and all network sessions."""
        logger.info("=" * 70)
        logger.info("Initializing Binance Prediction Bot + Jev AI Decision Engine")
        logger.info(f"Mode: {'[PAPER TRADING]' if self.binance_client.paper_trading else '[LIVE CAPITAL TRADING]'}")
        logger.info(f"Confidence Threshold: {self.risk_guard.confidence_threshold * 100:.0f}%")
        logger.info(f"Max Position Size: ${self.risk_guard.max_position_size_usdt:.2f} USDT")
        logger.info(f"Cooldown Period: {self.risk_guard.cooldown_seconds}s")
        logger.info("=" * 70)

        await self.jev_client.start()
        await self.binance_client.start()
        await self.ws_listener.start()
        self._heartbeat_task = asyncio.create_task(self._dashboard_heartbeat_loop())

    async def stop(self) -> None:
        """Stop all subsystems and clean up sessions."""
        logger.info("Stopping Trading Bot Coordinator...")
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self.ws_listener.stop()
        await self.binance_client.close()
        await self.jev_client.close()

    async def _dashboard_heartbeat_loop(self) -> None:
        """Periodic 1s heartbeat ensuring dashboard clock, status, and markets update continuously."""
        while True:
            try:
                await asyncio.sleep(1.0)
                if self.ws_clients:
                    status = self.get_system_status()
                    markets = self.ws_listener.get_active_markets()
                    msg = {
                        "type": "HEARTBEAT",
                        "system_status": status,
                        "active_markets": markets,
                    }
                    disconnected: List[WebSocket] = []
                    for client in list(self.ws_clients):
                        try:
                            await client.send_json(msg)
                        except Exception:
                            disconnected.append(client)
                    for dead in disconnected:
                        self.ws_clients.discard(dead)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Dashboard heartbeat notice: {e}")

    def start_bot(self) -> None:
        """Start or resume trading execution."""
        self.bot_status = "RUNNING"
        self.is_paused = False
        logger.info("[OPERATOR COMMAND] Bot status changed to: RUNNING")

    def stop_bot(self) -> None:
        """Halt or freeze trading execution."""
        self.bot_status = "STOPPED"
        self.is_paused = True
        logger.info("[OPERATOR COMMAND] Bot status changed to: STOPPED")

    async def on_market_tick(self, market: MarketContext) -> None:
        """
        Event-driven callback triggered on every market tick from WebSocket.
        Executes non-blocking inference, risk gating, and order dispatch.
        """
        if self.bot_status != "RUNNING" or self.is_paused:
            return

        # Throttle evaluation per symbol to once every 2.5s to avoid API rate limits,
        # but always evaluate on the first tick for that symbol
        now = time.time()
        last_eval = self._last_eval_time.get(market.symbol, 0.0)
        if now - last_eval < 2.5:
            return
        self._last_eval_time[market.symbol] = now

        # Step 1: AI Evaluation via Jev AI Decision Engine
        decision: JevEvaluationResult = await self.jev_client.evaluate_market(market)

        # Step 2: Risk & Execution Guard Validation
        open_positions = len(self.binance_client.get_positions())
        risk_result: RiskEvaluationResult = self.risk_guard.validate_and_size_order(
            decision=decision,
            market=market,
            current_open_positions_count=open_positions
        )

        # Store rich telemetry record
        record = {
            "timestamp": time.time(),
            "market_id": market.market_id,
            "symbol": market.symbol,
            "question": market.question,
            "odds_yes": market.odds_yes,
            "odds_no": market.odds_no,
            "underlying_price": market.underlying_price,
            "target_price": market.target_price,
            "momentum_pct": market.momentum_pct,
            "spread": market.spread,
            "volume_24h": market.volume_24h,
            "time_left_seconds": market.time_left_seconds,
            "decision": decision.model_dump(),
            "risk_validation": risk_result.model_dump(),
            "order": None,
        }

        # Step 3: Order Execution (if approved by Risk Guard)
        if risk_result.approved:
            logger.info(
                f">>> DISPATCHING ORDER: {risk_result.action} {risk_result.adjusted_contracts}x "
                f"on {market.market_id} @ {risk_result.target_price:.3f}"
            )
            order_result: OrderResult = await self.binance_client.place_prediction_order(
                market_id=market.market_id,
                symbol=market.symbol,
                side=risk_result.action,
                contracts=risk_result.adjusted_contracts,
                target_price=risk_result.target_price,
            )
            record["order"] = order_result.model_dump()

        # Cache last 50 decisions
        self.recent_decisions.append(record)
        if len(self.recent_decisions) > 50:
            self.recent_decisions.pop(0)

        # Broadcast update to connected dashboard WebSocket clients
        await self._broadcast_telemetry(record)

    async def _broadcast_telemetry(self, event_data: Dict[str, Any]) -> None:
        """Broadcast live tick and decision event to all connected dashboard websockets."""
        if not self.ws_clients:
            return

        message = {
            "type": "MARKET_EVALUATION",
            "data": event_data,
            "system_status": self.get_system_status(),
            "active_markets": self.ws_listener.get_active_markets(),
        }

        disconnected: List[WebSocket] = []
        for client in list(self.ws_clients):
            try:
                await client.send_json(message)
            except Exception:
                disconnected.append(client)

        for dead_client in disconnected:
            self.ws_clients.discard(dead_client)

    def get_system_status(self) -> Dict[str, Any]:
        """Aggregate system telemetry for dashboard inspection."""
        uptime_seconds = int(time.time() - self.start_time)
        return {
            "bot_status": self.bot_status,
            "is_paused": self.is_paused,
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s",
            "trading_mode": "PAPER_TRADING" if self.binance_client.paper_trading else "LIVE_TRADING",
            "ws_stream": self.ws_listener.metrics,
            "jev_ai": self.jev_client.stats,
            "risk_guard": self.risk_guard.metrics,
            "account": self.binance_client.get_account_summary(),
        }


# ==============================================================================
# FastAPI Telemetry & Control Server
# ==============================================================================

bot = TradingBotCoordinator()
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    # Startup
    await bot.start()
    yield
    # Shutdown
    await bot.stop()

app = FastAPI(
    title="Binance Prediction Markets + Jev AI Trading Core",
    version="1.0.0",
    description="Event-driven quantitative prediction trading bot with Jev AI integration",
    lifespan=lifespan
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    """Get high-level status of trading bot, connection state, and risk metrics."""
    return bot.get_system_status()


@app.get("/api/markets")
async def get_markets() -> List[Dict[str, Any]]:
    """Get active prediction markets with latest Yes/No odds."""
    return bot.ws_listener.get_active_markets()


@app.get("/api/decisions")
async def get_decisions() -> List[Dict[str, Any]]:
    """Get recent Jev AI evaluations with confidence scores and reasoning."""
    return list(reversed(bot.recent_decisions))


@app.get("/api/orders")
async def get_orders(limit: int = 50) -> List[Dict[str, Any]]:
    """Get order execution history log."""
    return bot.binance_client.get_order_history(limit=limit)


@app.get("/api/positions")
async def get_positions() -> List[Dict[str, Any]]:
    """Get currently active positions."""
    return bot.binance_client.get_positions()


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest) -> Dict[str, Any]:
    """Dynamically adjust risk thresholds and operating mode from dashboard."""
    bot.risk_guard.update_thresholds(
        confidence_threshold=req.confidence_threshold,
        max_position_size_usdt=req.max_position_size_usdt,
        cooldown_seconds=req.cooldown_seconds,
    )
    if req.paper_trading is not None:
        bot.binance_client.paper_trading = req.paper_trading
        logger.info(f"Updated Paper Trading mode to: {req.paper_trading}")

    return {
        "status": "success",
        "message": "Configuration updated successfully",
        "current_status": bot.get_system_status()
    }


@app.post("/api/auth/login")
async def login_endpoint(req: LoginRequest) -> Dict[str, Any]:
    """Authenticate dashboard operator identity."""
    if req.username == settings.dashboard_username and req.password == settings.dashboard_password:
        return {
            "status": "success",
            "token": settings.dashboard_auth_token,
            "username": req.username,
            "message": "Authentication successful"
        }
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.post("/api/bot/start")
async def start_bot_endpoint() -> Dict[str, Any]:
    """Web command to start/activate bot trading."""
    bot.start_bot()
    return {
        "bot_status": bot.bot_status,
        "message": "Trading bot activated successfully",
        "system_status": bot.get_system_status()
    }


@app.post("/api/bot/stop")
async def stop_bot_endpoint() -> Dict[str, Any]:
    """Web command to stop/freeze bot trading."""
    bot.stop_bot()
    return {
        "bot_status": bot.bot_status,
        "message": "Trading bot halted successfully",
        "system_status": bot.get_system_status()
    }


@app.post("/api/pause")
async def toggle_pause() -> Dict[str, Any]:
    """Pause or resume trading execution."""
    bot.is_paused = not bot.is_paused
    bot.bot_status = "STOPPED" if bot.is_paused else "RUNNING"
    logger.info(f"Trading bot state changed: {bot.bot_status} (paused={bot.is_paused})")
    return {"bot_status": bot.bot_status, "is_paused": bot.is_paused}


@app.post("/api/circuit-breaker/reset")
async def reset_circuit_breaker() -> Dict[str, Any]:
    """Reset daily loss circuit breaker."""
    bot.risk_guard.reset_circuit_breaker()
    return {"status": "reset", "message": "Circuit breaker reset to active"}


@app.websocket("/ws/stream")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Real-time streaming WebSocket endpoint for Web Dashboard."""
    await websocket.accept()
    bot.ws_clients.add(websocket)
    try:
        # Send initial snapshot immediately upon connection
        snapshot = {
            "type": "INITIAL_SNAPSHOT",
            "system_status": bot.get_system_status(),
            "active_markets": bot.ws_listener.get_active_markets(),
            "recent_decisions": list(reversed(bot.recent_decisions[-20:])),
            "orders": bot.binance_client.get_order_history(limit=20),
            "positions": bot.binance_client.get_positions(),
        }
        await websocket.send_json(snapshot)

        # Keep socket open and handle any incoming messages/pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        bot.ws_clients.discard(websocket)
    except Exception:
        bot.ws_clients.discard(websocket)


def run_main() -> None:
    """Launch trading bot with uvicorn server."""
    uvicorn.run(
        "main:app",
        host=settings.telemetry_host,
        port=settings.telemetry_port,
        log_level=settings.log_level.lower(),
        reload=False
    )


if __name__ == "__main__":
    run_main()
