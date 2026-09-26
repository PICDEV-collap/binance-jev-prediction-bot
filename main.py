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
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

# Ensure clean UTF-8 encoding on Windows console and streams to prevent garbled text
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import settings
from engine.binance_client import BinanceClient, OrderResult
from engine.jev_client import JevClient, JevEvaluationResult, MarketContext
from engine.risk_guard import RiskGuard, RiskEvaluationResult
from streams.ws_listener import BinanceWSListener, ConnectionState

# Setup structured logging (Console + Persistent bot.log for background execution)
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8", mode="a"),
    ]
)
logger = logging.getLogger("main_engine")


class ConfigUpdateRequest(BaseModel):
    # Risk & Martingale
    confidence_threshold: float | None = None
    default_order_contracts: int | None = None
    martingale_enabled: bool | None = None
    martingale_multiplier: float | None = None
    martingale_max_steps: int | None = None
    martingale_confidence_step: float | None = None
    martingale_max_confidence: float | None = None

    # Exposure & Circuit Breaker Limits
    max_position_size_usdt: float | None = None
    cooldown_seconds: int | None = None
    max_daily_loss_usdt: float | None = None
    max_concurrent_positions: int | None = None
    max_odds_cap: float | None = None
    min_odds_floor: float | None = None
    min_ev_edge: float | None = None
    min_time_left_seconds: int | None = None
    max_time_left_seconds: int | None = None
    slippage_bps: int | None = None

    # Target Market & Strategy
    target_symbol: str | None = None
    target_timeframe: str | None = None
    eval_interval_seconds: int | None = None

    # Operating Environment & Credentials
    paper_trading: bool | None = None
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    jev_ai_api_key: str | None = None
    jev_ai_model: str | None = None
    persist_to_env: bool | None = False


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

        # User-selected Target Pair & Timeframe for AI evaluation
        self.target_symbol: str = getattr(settings, "target_symbol", "BTCUSDT").upper()
        self.target_timeframe: str = getattr(settings, "target_timeframe", "15m").lower()
        self.eval_interval_seconds: int = getattr(settings, "eval_interval_seconds", 60)
        self.last_eval_time: Dict[str, float] = {}
        self.evaluated_rounds: Set[str] = set()
        self.traded_rounds: Set[str] = set()

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
            funding_source=getattr(settings, "funding_source", "CEX"),
            slippage_bps=getattr(settings, "slippage_bps", 200),
            max_odds_cap=getattr(settings, "max_odds_cap", 0.60),
            min_odds_floor=getattr(settings, "min_odds_floor", 0.20),
            enable_early_take_profit=getattr(settings, "enable_early_take_profit", True),
            take_profit_odds=getattr(settings, "take_profit_odds", 0.82),
        )

        self.risk_guard = RiskGuard(
            confidence_threshold=settings.confidence_threshold,
            max_position_size_usdt=settings.max_position_size_usdt,
            default_order_contracts=settings.default_order_contracts,
            cooldown_seconds=settings.cooldown_seconds,
            max_daily_loss_usdt=settings.max_daily_loss_usdt,
            max_concurrent_positions=settings.max_concurrent_positions,
            max_odds_cap=getattr(settings, "max_odds_cap", 0.60),
            min_odds_floor=getattr(settings, "min_odds_floor", 0.20),
            min_ev_edge=getattr(settings, "min_ev_edge", 0.05),
            min_time_left_seconds=getattr(settings, "min_time_left_seconds", 180),
            max_time_left_seconds=getattr(settings, "max_time_left_seconds", 850),
            martingale_enabled=getattr(settings, "martingale_enabled", True),
            martingale_multiplier=getattr(settings, "martingale_multiplier", 2.0),
            martingale_max_steps=getattr(settings, "martingale_max_steps", 4),
            martingale_confidence_step=getattr(settings, "martingale_confidence_step", 0.04),
            martingale_max_confidence=getattr(settings, "martingale_max_confidence", 0.95),
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
        self._background_tasks: Set[asyncio.Task] = set()
        self._eval_in_progress: Set[str] = set()

    def _spawn_task(self, coro, name: Optional[str] = None) -> asyncio.Task:
        """Spawn background task with strong reference to prevent premature garbage collection in Python 3.12+."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self) -> None:
        """Start trading core and all network sessions."""
        logger.info("=" * 70)
        logger.info("Initializing Binance Prediction Bot + Jev AI Decision Engine")
        logger.info(f"Mode: {'[PAPER TRADING]' if self.binance_client.paper_trading else '[LIVE CAPITAL TRADING]'}")
        logger.info(f"Confidence Threshold: {self.risk_guard.confidence_threshold * 100:.0f}%")
        logger.info(f"Max Position Size: ${self.risk_guard.max_position_size_usdt:.2f} USDT")
        logger.info(f"Cooldown Period: {self.risk_guard.cooldown_seconds}s")
        logger.info(
            f"Martingale Recovery: {'ENABLED' if self.risk_guard.martingale_enabled else 'DISABLED'} "
            f"(Multiplier: {self.risk_guard.martingale_multiplier}x, Max Steps: {self.risk_guard.martingale_max_steps}, "
            f"Confidence Step: +{self.risk_guard.martingale_confidence_step*100:.0f}%, "
            f"Max Conviction: {self.risk_guard.martingale_max_confidence*100:.0f}%)"
        )
        logger.info("=" * 70)

        await self.jev_client.start()
        await self.binance_client.start()
        await self.ws_listener.start()
        if not self.binance_client.paper_trading:
            self._spawn_task(self.binance_client.fetch_live_balance(), name="init_balance")
            self._spawn_task(self._init_live_catalog(), name="init_catalog")
        self._heartbeat_task = self._spawn_task(self._dashboard_heartbeat_loop(), name="heartbeat_loop")

    async def _init_live_catalog(self) -> None:
        """Fetch live prediction market catalog and sync supported symbols to RiskGuard."""
        try:
            await self.binance_client.fetch_prediction_market_topics(force_refresh=True)
            supported = self.binance_client.get_supported_prediction_symbols()
            self.risk_guard.set_supported_symbols(supported)
            logger.info(f"[CATALOG SYNC] Live Binance Prediction Pairs discovered: {', '.join(sorted(supported))}")
        except Exception as e:
            logger.debug(f"Catalog init notice: {e}")

    async def stop(self) -> None:
        """Stop all subsystems and clean up sessions."""
        logger.info("Stopping Trading Bot Coordinator...")
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Cancel and drain background tasks
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

        await self.ws_listener.stop()
        await self.binance_client.close()
        await self.jev_client.close()

    async def _dashboard_heartbeat_loop(self) -> None:
        """Periodic 1s heartbeat ensuring dashboard clock, status, and markets update continuously."""
        heartbeat_ticks = 0
        while True:
            try:
                await asyncio.sleep(1.0)
                heartbeat_ticks += 1
                markets = self.ws_listener.get_active_markets()

                # If live trading with API keys, poll Binance live balance every 5s or immediately if not loaded
                if not self.binance_client.paper_trading and (heartbeat_ticks % 5 == 0 or self.binance_client._live_balance_usdt <= 0.05):
                    self._spawn_task(self.binance_client.fetch_live_balance(), name="periodic_balance")

                # Periodic auto-claim sweep every 30s to claim any pending won contracts
                if not self.binance_client.paper_trading and (heartbeat_ticks % 30 == 0):
                    self._spawn_task(self.binance_client.claim_all_won_positions(), name="periodic_claim")

                # Periodic server time re-sync every 60s to continuously prevent clock drift (-1021)
                if not self.binance_client.paper_trading and (heartbeat_ticks % 60 == 0):
                    self._spawn_task(self.binance_client.sync_server_time(), name="periodic_sync_time")

                # Settle prediction positions when a round expires
                if markets:
                    active_market_ids = {m["market_id"] for m in markets}
                    current_prices = {m["symbol"]: m["underlying_price"] for m in markets}
                    # Update live price and unrealized PnL on active open positions
                    self.binance_client.update_positions_market_data(current_prices)

                    # Check and execute early take-profit if target odds reached (e.g. >= 0.82)
                    if getattr(settings, "enable_early_take_profit", True):
                        tp_records = await self.binance_client.check_and_execute_early_take_profits(markets)
                        for tp_rec in tp_records:
                            self.risk_guard.record_settlement_result(
                                won=True,
                                pnl=tp_rec["pnl"],
                                symbol=tp_rec["symbol"]
                            )

                    pnl, settled_records = self.binance_client.settle_expired_positions(active_market_ids, current_prices)
                    for rec in settled_records:
                        self.risk_guard.record_settlement_result(
                            won=rec["won"],
                            pnl=rec["pnl"],
                            symbol=rec["symbol"]
                        )
                    if any(rec.get("won") for rec in settled_records):
                        self._spawn_task(self.binance_client.claim_all_won_positions(), name="settle_claim")
                    # Prune expired rounds from memory sets
                    self.evaluated_rounds = {rid for rid in self.evaluated_rounds if rid in active_market_ids}
                    self.traded_rounds = {rid for rid in self.traded_rounds if rid in active_market_ids}
                    self.last_eval_time = {mid: t for mid, t in self.last_eval_time.items() if mid in active_market_ids}

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
                            await asyncio.wait_for(client.send_json(msg), timeout=0.8)
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

        # Supported Asset Filter: In live trading, only evaluate coins that Binance Prediction Markets actually supports
        if not self.binance_client.paper_trading:
            supported_syms = self.binance_client.get_supported_prediction_symbols()
            if market.symbol.upper() not in supported_syms:
                return

        # Token Filter 1: Check selected symbol
        if self.target_symbol != "ALL" and market.symbol.upper() != self.target_symbol.upper():
            return

        # Token Filter 2: Check selected timeframe
        if self.target_timeframe != "ALL" and market.timeframe.lower() != self.target_timeframe.lower():
            return

        # Timing Filter: Ensure round is within active trading window
        time_left = getattr(market, "time_left_seconds", 300)
        round_period = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}.get(market.timeframe.lower(), 900)

        # Skip late round entries (< min_time_left_seconds, e.g. < 60s) to prevent asymmetric expiry risk
        if time_left < self.risk_guard.min_time_left_seconds:
            return

        # Skip the first 10 seconds of a brand new round so the strike / spot baseline price stabilizes
        if time_left > (round_period - 10):
            return

        # Check if an order has already been executed on this round
        if market.market_id in self.traded_rounds:
            return

        # Check if we already hold an open position on this market round
        open_positions = self.binance_client.get_positions()
        if any(p.get("market_id") == market.market_id for p in open_positions):
            self.traded_rounds.add(market.market_id)
            return

        # Check if an evaluation or order dispatch is already in progress for this round
        if market.market_id in self._eval_in_progress:
            return

        # Cadence Filter: Evaluate every eval_interval_seconds (default 60s / 1 min)
        now = time.time()
        last_eval = self.last_eval_time.get(market.market_id, 0.0)
        if (now - last_eval) < self.eval_interval_seconds:
            return

        # Record this evaluation timestamp
        self.last_eval_time[market.market_id] = now
        self.evaluated_rounds.add(market.market_id)
        self._eval_in_progress.add(market.market_id)

        try:
            logger.info(
                f"[AI EVALUATION TRIGGERED: Every {self.eval_interval_seconds}s] Symbol: {market.symbol} | TF: {market.timeframe} | "
                f"Round: {market.market_id} | TimeLeft: {market.time_left_seconds}s | Spot: ${market.underlying_price:,.2f} | Beat: ${market.target_price:,.2f} | "
                f"DVR: {market.dvr_ratio:+.2f}sigma | OBI: {market.order_book_imbalance:+.2f} | Trend: {market.ema_trend} | RSI: {market.rsi_5m:.1f}"
            )

            # Step 0: Fetch historical win/loss performance feedback (Approach 3: Hybrid)
            recent_perf = self.binance_client.get_recent_performance(symbol=market.symbol, limit=5)
            market.recent_performance = recent_perf
            market.martingale_step = self.risk_guard.get_symbol_martingale_step(market.symbol)
            market.martingale_stage = self.risk_guard.get_stage_label(market.symbol)
            market.effective_hurdle = self.risk_guard.get_effective_confidence_threshold(market.symbol)
            logger.info(
                f"[FEEDBACK LOOP] {recent_perf['summary']} | "
                f"Stage: {market.martingale_stage} (Hurdle: {market.effective_hurdle*100:.0f}%)"
            )

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
                "atr_1m": getattr(market, "atr_1m", 0.0),
                "dvr_ratio": getattr(market, "dvr_ratio", 0.0),
                "rsi_1m": getattr(market, "rsi_1m", 50.0),
                "rsi_5m": getattr(market, "rsi_5m", 50.0),
                "ema_trend": getattr(market, "ema_trend", "NEUTRAL_CHOP"),
                "order_book_imbalance": getattr(market, "order_book_imbalance", 0.0),
                "market_regime": getattr(market, "market_regime", "RANGING"),
                "expiry_danger_flag": getattr(market, "expiry_danger_flag", False),
                "btc_correlation_dir": getattr(market, "btc_correlation_dir", "FLAT"),
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
                    f"on {market.market_id} ({market.timeframe}) [{risk_result.stage_label}] @ {risk_result.target_price:.3f}"
                )
                order_result: OrderResult = await self.binance_client.place_prediction_order(
                    market_id=market.market_id,
                    symbol=market.symbol,
                    side=clean_action,
                    contracts=risk_result.adjusted_contracts,
                    target_price=risk_result.target_price,
                    strike_price=market.target_price,
                    spot_price=market.underlying_price,
                    martingale_step=risk_result.martingale_step,
                    stage=risk_result.stage_label,
                    timeframe=market.timeframe,
                )
                record["order"] = order_result.model_dump()
                if order_result.status in ("FILLED", "SIMULATED", "NEW"):
                    self.traded_rounds.add(market.market_id)
                    self.risk_guard.record_market_traded(market.market_id)
                    logger.info(
                        f"[ORDER ENTERED] Position open on {market.market_id} ({market.symbol} {clean_action}). "
                        f"Round entry complete, pausing further evaluations for this round."
                    )
                else:
                    self.risk_guard.rollback_market_traded(market.market_id)
                    # If quote price was heavily over cap (>= 0.70 vs cap 0.60), back off evaluation on this round
                    # for 180s to avoid repeatedly hitting Binance quote API every 60s for a runaway round
                    if order_result.order_id == "REJECTED_QUOTE_CAP" and order_result.price >= 0.70:
                        self.last_eval_time[market.market_id] = now + 120.0
                        logger.info(
                            f"[QUOTE CAP BACKOFF] Market {market.market_id} is heavily overpriced (${order_result.price:.3f} >= $0.70). "
                            f"Applying 180s evaluation backoff on this round."
                        )

            # Cache last 50 decisions
            self.recent_decisions.append(record)
            if len(self.recent_decisions) > 50:
                self.recent_decisions.pop(0)

            # Broadcast update to connected dashboard WebSocket clients
            await self._broadcast_telemetry(record)
        finally:
            self._eval_in_progress.discard(market.market_id)

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
                await asyncio.wait_for(client.send_json(message), timeout=0.8)
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
                "evaluation_policy": f"every_{self.eval_interval_seconds}s",
                "eval_interval_seconds": self.eval_interval_seconds,
            },
            "network_health": {
                "state": self.ws_listener.metrics.get("network_state", "ONLINE"),
                "latency_ms": self.ws_listener.metrics.get("latency_ms", 0.0),
                "network_healthy": self.ws_listener.metrics.get("network_healthy", True),
                "last_packet_age_seconds": self.ws_listener.metrics.get("last_packet_age_seconds", 0.0),
                "is_stale": self.ws_listener.metrics.get("is_stale", False),
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


@app.post("/api/positions/clear")
async def clear_positions_endpoint() -> Dict[str, Any]:
    """Clear simulated paper positions from the active desk."""
    cleared = bot.binance_client.clear_paper_positions()
    return {"status": "success", "cleared_count": cleared}


def _persist_config_to_env(req: ConfigUpdateRequest) -> None:
    """Helper to update local .env file with non-empty configuration entries."""
    env_path = Path(".env")
    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    mapping: Dict[str, str] = {}
    if req.confidence_threshold is not None:
        mapping["CONFIDENCE_THRESHOLD"] = f"{req.confidence_threshold:.2f}"
    if req.default_order_contracts is not None:
        mapping["DEFAULT_ORDER_CONTRACTS"] = str(req.default_order_contracts)
    if req.max_position_size_usdt is not None:
        mapping["MAX_POSITION_SIZE_USDT"] = f"{req.max_position_size_usdt:.1f}"
    if req.cooldown_seconds is not None:
        mapping["COOLDOWN_SECONDS"] = str(req.cooldown_seconds)
    if req.max_daily_loss_usdt is not None:
        mapping["MAX_DAILY_LOSS_USDT"] = f"{req.max_daily_loss_usdt:.1f}"
    if req.max_concurrent_positions is not None:
        mapping["MAX_CONCURRENT_POSITIONS"] = str(req.max_concurrent_positions)
    if req.martingale_enabled is not None:
        mapping["MARTINGALE_ENABLED"] = "true" if req.martingale_enabled else "false"
    if req.martingale_multiplier is not None:
        mapping["MARTINGALE_MULTIPLIER"] = f"{req.martingale_multiplier:.1f}"
    if req.martingale_max_steps is not None:
        mapping["MARTINGALE_MAX_STEPS"] = str(req.martingale_max_steps)
    if req.martingale_confidence_step is not None:
        mapping["MARTINGALE_CONFIDENCE_STEP"] = f"{req.martingale_confidence_step:.2f}"
    if req.martingale_max_confidence is not None:
        mapping["MARTINGALE_MAX_CONFIDENCE"] = f"{req.martingale_max_confidence:.2f}"
    if req.max_odds_cap is not None:
        mapping["MAX_ODDS_CAP"] = f"{req.max_odds_cap:.2f}"
    if req.min_odds_floor is not None:
        mapping["MIN_ODDS_FLOOR"] = f"{req.min_odds_floor:.2f}"
    if req.min_ev_edge is not None:
        mapping["MIN_EV_EDGE"] = f"{req.min_ev_edge:.2f}"
    if req.min_time_left_seconds is not None:
        mapping["MIN_TIME_LEFT_SECONDS"] = str(req.min_time_left_seconds)
    if req.max_time_left_seconds is not None:
        mapping["MAX_TIME_LEFT_SECONDS"] = str(req.max_time_left_seconds)
    if req.slippage_bps is not None:
        mapping["SLIPPAGE_BPS"] = str(req.slippage_bps)
    if req.target_symbol is not None:
        mapping["TARGET_SYMBOL"] = req.target_symbol.upper()
    if req.target_timeframe is not None:
        mapping["TARGET_TIMEFRAME"] = req.target_timeframe.lower()
    if req.eval_interval_seconds is not None:
        mapping["EVAL_INTERVAL_SECONDS"] = str(req.eval_interval_seconds)
    if req.paper_trading is not None:
        mapping["PAPER_TRADING"] = "true" if req.paper_trading else "false"
    if req.binance_api_key is not None and req.binance_api_key.strip():
        mapping["BINANCE_API_KEY"] = req.binance_api_key.strip()
    if req.binance_api_secret is not None and req.binance_api_secret.strip():
        mapping["BINANCE_API_SECRET"] = req.binance_api_secret.strip()
    if req.jev_ai_api_key is not None and req.jev_ai_api_key.strip():
        mapping["JEV_AI_API_KEY"] = req.jev_ai_api_key.strip()
    if req.jev_ai_model is not None and req.jev_ai_model.strip():
        mapping["JEV_AI_MODEL"] = req.jev_ai_model.strip()

    if not mapping:
        return

    updated_keys = set()
    new_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in mapping:
                new_lines.append(f"{k}={mapping[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in mapping.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@app.get("/api/config")
async def get_config_endpoint() -> Dict[str, Any]:
    """Get complete active configuration and credential status."""
    rg = bot.risk_guard
    binance_key = bot.binance_client.api_key
    jev_key = bot.jev_client.api_key

    masked_binance = ""
    if binance_key:
        masked_binance = f"{binance_key[:4]}••••{binance_key[-4:]}" if len(binance_key) > 8 else "••••••••"

    masked_jev = ""
    if jev_key:
        masked_jev = f"{jev_key[:4]}••••{jev_key[-4:]}" if len(jev_key) > 8 else "••••••••"

    return {
        "status": "success",
        "config": {
            # Risk & Martingale
            "confidence_threshold": rg.confidence_threshold,
            "default_order_contracts": rg.default_order_contracts,
            "martingale_enabled": rg.martingale_enabled,
            "martingale_multiplier": rg.martingale_multiplier,
            "martingale_max_steps": rg.martingale_max_steps,
            "martingale_confidence_step": rg.martingale_confidence_step,
            "martingale_max_confidence": rg.martingale_max_confidence,
            # Exposure & Limits
            "max_position_size_usdt": rg.max_position_size_usdt,
            "cooldown_seconds": rg.cooldown_seconds,
            "max_daily_loss_usdt": rg.max_daily_loss_usdt,
            "max_concurrent_positions": rg.max_concurrent_positions,
            "max_odds_cap": rg.max_odds_cap,
            "min_odds_floor": rg.min_odds_floor,
            "min_ev_edge": rg.min_ev_edge,
            "min_time_left_seconds": rg.min_time_left_seconds,
            "max_time_left_seconds": rg.max_time_left_seconds,
            "slippage_bps": bot.binance_client.slippage_bps,
            # Strategy Targets
            "target_symbol": bot.target_symbol,
            "target_timeframe": bot.target_timeframe,
            "eval_interval_seconds": bot.eval_interval_seconds,
            # Mode & Credentials Status
            "paper_trading": bot.binance_client.paper_trading,
            "has_binance_key": bool(binance_key),
            "has_binance_secret": bool(bot.binance_client.api_secret),
            "binance_api_key_masked": masked_binance,
            "has_jev_key": bool(jev_key),
            "jev_ai_model": bot.jev_client.model,
            "jev_ai_key_masked": masked_jev,
        }
    }


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest) -> Dict[str, Any]:
    """Dynamically adjust risk thresholds, strategy targets, credentials, and operating mode from dashboard."""
    bot.risk_guard.update_thresholds(
        confidence_threshold=req.confidence_threshold,
        max_position_size_usdt=req.max_position_size_usdt,
        default_order_contracts=req.default_order_contracts,
        cooldown_seconds=req.cooldown_seconds,
        max_daily_loss_usdt=req.max_daily_loss_usdt,
        max_concurrent_positions=req.max_concurrent_positions,
        martingale_enabled=req.martingale_enabled,
        martingale_multiplier=req.martingale_multiplier,
        martingale_max_steps=req.martingale_max_steps,
        martingale_confidence_step=req.martingale_confidence_step,
        martingale_max_confidence=req.martingale_max_confidence,
        max_odds_cap=req.max_odds_cap,
        min_odds_floor=req.min_odds_floor,
        min_ev_edge=req.min_ev_edge,
        min_time_left_seconds=req.min_time_left_seconds,
        max_time_left_seconds=req.max_time_left_seconds,
    )

    if req.slippage_bps is not None:
        bot.binance_client.slippage_bps = req.slippage_bps
    if req.max_odds_cap is not None:
        bot.binance_client.max_odds_cap = req.max_odds_cap
    if req.min_odds_floor is not None:
        bot.binance_client.min_odds_floor = req.min_odds_floor

    if req.target_symbol is not None and req.target_symbol.strip():
        bot.target_symbol = req.target_symbol.upper().strip()
        logger.info(f"Target symbol updated to: {bot.target_symbol}")

    if req.target_timeframe is not None and req.target_timeframe.strip():
        bot.target_timeframe = req.target_timeframe.lower().strip()
        logger.info(f"Target timeframe updated to: {bot.target_timeframe}")

    if req.eval_interval_seconds is not None:
        bot.eval_interval_seconds = max(10, min(900, int(req.eval_interval_seconds)))
        logger.info(f"Evaluation interval updated to: {bot.eval_interval_seconds}s")

    if req.paper_trading is not None:
        prev_mode = bot.binance_client.paper_trading
        bot.binance_client.paper_trading = req.paper_trading
        if prev_mode and not req.paper_trading:
            bot.binance_client.clear_paper_positions()
            if bot.binance_client.api_key:
                bot._spawn_task(bot.binance_client.sync_server_time(), name="switch_live_sync_time")
                bot._spawn_task(bot.binance_client.fetch_live_balance(), name="switch_live_balance")
        logger.info(f"Updated Paper Trading mode to: {req.paper_trading}")

    if req.binance_api_key is not None and req.binance_api_key.strip():
        bot.binance_client.api_key = req.binance_api_key.strip()
        if bot.binance_client._session and not bot.binance_client._session.closed:
            bot.binance_client._session.headers["X-MBX-APIKEY"] = bot.binance_client.api_key
        if not bot.binance_client.paper_trading:
            bot._spawn_task(bot.binance_client.sync_server_time(), name="key_update_sync_time")
            bot._spawn_task(bot.binance_client.fetch_live_balance(), name="key_update_live_balance")
        logger.info("Updated Binance API Key")

    if req.binance_api_secret is not None and req.binance_api_secret.strip():
        bot.binance_client.api_secret = req.binance_api_secret.strip()
        logger.info("Updated Binance API Secret")

    if req.jev_ai_api_key is not None and req.jev_ai_api_key.strip():
        bot.jev_client.api_key = req.jev_ai_api_key.strip()
        if bot.jev_client._session and not bot.jev_client._session.closed:
            bot.jev_client._session.headers["Authorization"] = f"Bearer {bot.jev_client.api_key}"
        logger.info("Updated Jev AI API Key")

    if req.jev_ai_model is not None and req.jev_ai_model.strip():
        bot.jev_client.model = req.jev_ai_model.strip()
        logger.info(f"Updated Jev AI Model to: {bot.jev_client.model}")

    if req.persist_to_env:
        try:
            _persist_config_to_env(req)
            logger.info("Configuration successfully written to local .env file")
        except Exception as e:
            logger.error(f"Failed to persist configuration to .env: {e}")

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


@app.post("/api/trade/claim")
async def claim_winnings_endpoint() -> Dict[str, Any]:
    """Manually or programmatically trigger claim/batch-redeem for all won prediction contracts."""
    res = await bot.binance_client.claim_all_won_positions()
    await bot.binance_client.fetch_live_balance()
    return {
        "status": "success" if res.get("success", True) else "failed",
        "claim_result": res,
        "account": bot.binance_client.get_account_summary(),
        "closed_positions": bot.binance_client.get_closed_positions(),
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
    bot.traded_rounds.discard(market_id)
    bot.last_eval_time.pop(market_id, None)
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
