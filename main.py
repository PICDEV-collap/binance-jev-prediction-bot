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
from typing import Dict, Any, List, Set, Optional

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


class TargetMarketRequest(BaseModel):
    target_symbol: str = "BTCUSDT"
    target_timeframe: str = "15m"


class ManualTradeRequest(BaseModel):
    market_id: str
    symbol: str
    side: str  # "UP" or "DOWN"
    contracts: int = 10
    target_price: float
    strike_price: float | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TradingBotCoordinator:
    """
    Master coordinator orchestrating the event-driven trading lifecycle.
    Features token-efficient AI inference (1x per round) on target market only,
    and supports full dual-sided (UP and DOWN) trading.
    """

    def __init__(self) -> None:
        self.start_time = time.time()
        self.is_paused: bool = False
        self.bot_status: str = "RUNNING"  # "RUNNING" or "STOPPED"

        # User-selected Target Pair & Timeframe for AI evaluation (Token efficiency)
        self.target_symbol: str = getattr(settings, "target_symbol", "BTCUSDT").upper()
        self.target_timeframe: str = getattr(settings, "target_timeframe", "15m").lower()
        self.evaluated_rounds: Set[str] = set()

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
                markets = self.ws_listener.get_active_markets()

                # Settle paper trading positions when a round expires
                if markets:
                    active_market_ids = {m["market_id"] for m in markets}
                    current_prices = {m["symbol"]: m["underlying_price"] for m in markets}
                    # Update live price and unrealized PnL on active open positions
                    self.binance_client.update_positions_market_data(current_prices)
                    pnl = self.binance_client.settle_expired_positions(active_market_ids, current_prices)
                    if pnl != 0.0:
                        self.risk_guard.record_pnl(pnl)
                    # Prune expired rounds from memory set
                    self.evaluated_rounds = {rid for rid in self.evaluated_rounds if rid in active_market_ids}

                if self.ws_clients:
                    status = self.get_system_status()
                    open_pos = self.binance_client.get_positions()
                    closed_pos = self.binance_client.get_closed_positions()
                    msg = {
                        "type": "HEARTBEAT",
                        "system_status": status,
                        "active_markets": markets,
                        "open_positions": open_pos,
                        "closed_positions": closed_pos,
                        "positions": open_pos,
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
        Event-driven callback triggered on market tick from WebSocket.
        TOKEN-EFFICIENT POLICY:
        1. Only evaluates the user-selected pair (e.g. BTCUSDT) and timeframe (e.g. 15m).
        2. Evaluates EXACTLY 1 TIME PER ROUND to save API tokens (99.9% cost reduction).
        3. Supports dual-sided prediction orders (UP / DOWN).
        """
        if self.bot_status != "RUNNING" or self.is_paused:
            return

        # Token Filter 1: Check selected symbol
        if self.target_symbol != "ALL" and market.symbol.upper() != self.target_symbol.upper():
            return

        # Token Filter 2: Check selected timeframe
        if self.target_timeframe != "ALL" and market.timeframe.lower() != self.target_timeframe.lower():
            return

        # Token Filter 3: Check if already evaluated this round (1x per round)
        if market.market_id in self.evaluated_rounds:
            return

        # Mark round as evaluated immediately to guarantee strictly 1 AI call per round
        self.evaluated_rounds.add(market.market_id)

        logger.info(
            f"[AI EVALUATION TRIGGERED: 1x/Round] Symbol: {market.symbol} | TF: {market.timeframe} | "
            f"Round: {market.market_id} | Spot: ${market.underlying_price:,.2f} | Beat: ${market.target_price:,.2f}"
        )

        # Step 0: Fetch historical win/loss performance feedback (Approach 3: Hybrid)
        recent_perf = self.binance_client.get_recent_performance(symbol=market.symbol, limit=5)
        market.recent_performance = recent_perf
        logger.info(f"[FEEDBACK LOOP] {recent_perf['summary']}")

        # Step 1: AI Evaluation via Jev AI Decision Engine
        decision: JevEvaluationResult = await self.jev_client.evaluate_market(market)

        # Step 2: Risk & Execution Guard Validation (Dynamic Gate + Adaptive Sizing)
        open_positions = len(self.binance_client.get_positions())
        risk_result: RiskEvaluationResult = self.risk_guard.validate_and_size_order(
            decision=decision,
            market=market,
            current_open_positions_count=open_positions,
            recent_performance=recent_perf,
        )

        # Store rich telemetry record
        record = {
            "timestamp": time.time(),
            "market_id": market.market_id,
            "symbol": market.symbol,
            "question": market.question,
            "timeframe": market.timeframe,
            "odds_up": getattr(market, "odds_up", market.odds_yes),
            "odds_down": getattr(market, "odds_down", market.odds_no),
            "odds_yes": market.odds_yes,
            "odds_no": market.odds_no,
            "underlying_price": market.underlying_price,
            "target_price": market.target_price,
            "price_diff": market.price_diff,
            "momentum_pct": market.momentum_pct,
            "spread": market.spread,
            "volume_24h": market.volume_24h,
            "time_left_seconds": market.time_left_seconds,
            "decision": decision.model_dump(),
            "risk_validation": risk_result.model_dump(),
            "recent_performance": recent_perf,
            "order": None,
        }

        # Step 3: Order Execution (UP or DOWN if approved by Risk Guard)
        if risk_result.approved and risk_result.action in ("UP", "DOWN", "BUY_YES", "BUY_NO"):
            clean_action = "UP" if risk_result.action in ("UP", "BUY_YES") else "DOWN"
            logger.info(
                f">>> DISPATCHING PREDICTION ORDER: {clean_action} {risk_result.adjusted_contracts}x "
                f"on {market.market_id} ({market.timeframe}) @ {risk_result.target_price:.3f}"
            )
            order_result: OrderResult = await self.binance_client.place_prediction_order(
                market_id=market.market_id,
                symbol=market.symbol,
                side=clean_action,
                contracts=risk_result.adjusted_contracts,
                target_price=risk_result.target_price,
                strike_price=market.target_price,
                spot_price=market.underlying_price,
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

        open_pos = self.binance_client.get_positions()
        closed_pos = self.binance_client.get_closed_positions()
        message = {
            "type": "MARKET_EVALUATION",
            "data": event_data,
            "system_status": self.get_system_status(),
            "active_markets": self.ws_listener.get_active_markets(),
            "open_positions": open_pos,
            "closed_positions": closed_pos,
            "positions": open_pos,
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
            "target_market": {
                "target_symbol": self.target_symbol,
                "target_timeframe": self.target_timeframe,
                "evaluated_rounds_count": len(self.evaluated_rounds),
                "evaluation_policy": "1x_per_round",
            },
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
async def get_positions_endpoint() -> Dict[str, Any]:
    """Get currently active open positions and historical closed/settled positions."""
    return {
        "status": "success",
        "open_positions": bot.binance_client.get_positions(),
        "closed_positions": bot.binance_client.get_closed_positions(),
        "account": bot.binance_client.get_account_summary(),
    }


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


@app.get("/api/target")
async def get_target_endpoint() -> Dict[str, Any]:
    """Get currently targeted pair and timeframe for AI evaluation."""
    return {
        "target_symbol": bot.target_symbol,
        "target_timeframe": bot.target_timeframe,
        "evaluated_rounds_count": len(bot.evaluated_rounds),
        "policy": "1x_per_round",
    }


@app.post("/api/target")
async def set_target_endpoint(req: TargetMarketRequest) -> Dict[str, Any]:
    """Dynamically switch the targeted asset and timeframe for AI evaluation."""
    bot.target_symbol = req.target_symbol.upper()
    bot.target_timeframe = req.target_timeframe.lower()
    logger.info(f"[TARGET SWITCH] Target set to {bot.target_symbol} ({bot.target_timeframe})")
    return {
        "status": "success",
        "target_symbol": bot.target_symbol,
        "target_timeframe": bot.target_timeframe,
        "message": f"Target updated to {bot.target_symbol} ({bot.target_timeframe})",
        "system_status": bot.get_system_status()
    }


@app.post("/api/trade/manual")
async def manual_trade_endpoint(req: ManualTradeRequest) -> Dict[str, Any]:
    """Manually place an UP or DOWN prediction order without waiting for AI."""
    clean_side = "UP" if req.side.upper() in ("UP", "BUY_YES") else "DOWN"
    order_result = await bot.binance_client.place_prediction_order(
        market_id=req.market_id,
        symbol=req.symbol,
        side=clean_side,
        contracts=req.contracts,
        target_price=req.target_price,
        strike_price=req.strike_price if req.strike_price is not None else 0.0,
    )
    return {
        "status": "success",
        "order": order_result.model_dump(),
        "open_positions": bot.binance_client.get_positions(),
        "closed_positions": bot.binance_client.get_closed_positions(),
        "account": bot.binance_client.get_account_summary(),
    }


@app.post("/api/evaluate/force")
async def force_evaluate_endpoint(market_id: str) -> Dict[str, Any]:
    """Force an immediate single AI evaluation on specific market round."""
    markets = {m["market_id"]: m for m in bot.ws_listener.get_active_markets()}
    if market_id not in markets:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Market not found in active streams")

    m_info = markets[market_id]
    ctx = MarketContext(
        market_id=m_info["market_id"],
        symbol=m_info["symbol"],
        question=m_info["question"],
        timeframe=m_info.get("timeframe", "15m"),
        odds_yes=m_info["odds_yes"],
        odds_no=m_info["odds_no"],
        underlying_price=m_info["underlying_price"],
        target_price=m_info["target_price"],
        price_diff=m_info.get("price_diff", 0.0),
        momentum_pct=m_info.get("momentum_pct", 0.0),
        spread=m_info["spread"],
        volume_24h=m_info["volume_24h"],
        time_left_seconds=m_info["time_left_seconds"],
        bid=m_info.get("bid", 0.49),
        ask=m_info.get("ask", 0.51),
    )
    bot.evaluated_rounds.discard(market_id)
    await bot.on_market_tick(ctx)
    return {"status": "success", "message": f"Forced evaluation executed for {market_id}"}


@app.websocket("/ws/stream")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Real-time streaming WebSocket endpoint for Web Dashboard."""
    await websocket.accept()
    bot.ws_clients.add(websocket)
    try:
        # Send initial snapshot immediately upon connection
        open_pos = bot.binance_client.get_positions()
        closed_pos = bot.binance_client.get_closed_positions()
        snapshot = {
            "type": "INITIAL_SNAPSHOT",
            "system_status": bot.get_system_status(),
            "active_markets": bot.ws_listener.get_active_markets(),
            "recent_decisions": list(reversed(bot.recent_decisions[-20:])),
            "orders": bot.binance_client.get_order_history(limit=20),
            "open_positions": open_pos,
            "closed_positions": closed_pos,
            "positions": open_pos,
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
