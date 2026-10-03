"""
Main entry point for Binance Prediction Markets Event-Driven Trading Bot
integrated with Jev AI Decision Engine.

Coordinates Market Ingress (WS), AI Evaluation (Jev AI),
Risk Guard (Threshold & Cooldown), and Execution (Binance REST API),
while serving a real-time Telemetry & Control API for the Web Dashboard.
"""

from __future__ import annotations
import asyncio
import hashlib
import hmac
import logging
from logging.handlers import RotatingFileHandler
import math
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Set, Optional, Literal

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
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import settings
from engine.binance_client import BinanceClient, OrderResult
from engine.jev_client import JevClient, JevEvaluationResult, MarketContext
from engine.risk_guard import RiskGuard, RiskEvaluationResult
from engine.performance import PerformanceMetrics, DashboardSender
from engine.symbols import DEFAULT_ACTIVE_SYMBOLS, normalize_active_symbols
from streams.ws_listener import BinanceWSListener, ConnectionState

# Setup structured logging (Console + Persistent bot.log for background execution)
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            "bot.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
    ]
)
logger = logging.getLogger("main_engine")


class ConfigUpdateRequest(BaseModel):
    # Risk & Martingale
    confidence_threshold: float | None = Field(default=None, ge=0.5, le=0.99, allow_inf_nan=False)
    default_order_contracts: int | None = Field(default=None, ge=1, le=500)
    martingale_enabled: bool | None = None
    martingale_mode: Literal["SMART_HYBRID", "FIXED_MULTIPLIER"] | None = None
    martingale_multiplier: float | None = Field(default=None, ge=1.1, le=4.0, allow_inf_nan=False)
    martingale_max_steps: int | None = Field(default=None, ge=1, le=6)
    martingale_confidence_step: float | None = Field(default=None, ge=0.01, le=0.10, allow_inf_nan=False)
    martingale_max_confidence: float | None = Field(default=None, ge=0.85, le=0.99, allow_inf_nan=False)

    # Exposure & Circuit Breaker Limits
    max_position_size_usdt: float | None = Field(default=None, ge=5.0, allow_inf_nan=False)
    cooldown_seconds: int | None = Field(default=None, ge=5, le=86400)
    max_daily_loss_usdt: float | None = Field(default=None, ge=10.0, allow_inf_nan=False)
    max_concurrent_positions: int | None = Field(default=None, ge=1, le=24)
    max_odds_cap: float | None = Field(default=None, ge=0.30, le=0.90, allow_inf_nan=False)
    min_odds_floor: float | None = Field(default=None, ge=0.01, le=0.50, allow_inf_nan=False)
    min_ev_edge: float | None = Field(default=None, ge=0.01, le=0.25, allow_inf_nan=False)
    min_time_left_seconds: int | None = Field(default=None, ge=30, le=86400)
    max_time_left_seconds: int | None = Field(default=None, ge=60, le=86400)
    slippage_bps: int | None = Field(default=None, ge=0, le=1000)

    # Target Market & Strategy
    target_symbol: str | None = Field(default=None, min_length=3, max_length=20)
    target_timeframe: Literal["ALL", "all", "5m", "15m", "1h", "1d"] | None = None
    active_symbols: List[str] | None = Field(default=None, min_length=1, max_length=24)
    eval_interval_seconds: int | None = Field(default=None, ge=10, le=900)

    # Operating Environment & Credentials
    paper_trading: bool | None = None
    binance_api_key: str | None = Field(default=None, max_length=256)
    binance_api_secret: str | None = Field(default=None, max_length=256)
    jev_ai_api_key: str | None = Field(default=None, max_length=512)
    jev_ai_model: str | None = Field(default=None, min_length=1, max_length=100)
    persist_to_env: bool | None = False


class TargetMarketRequest(BaseModel):
    target_symbol: str = Field(default="BTCUSDT", min_length=3, max_length=20)
    target_timeframe: Literal["ALL", "all", "5m", "15m", "1h", "1d"] = "15m"


class ManualTradeRequest(BaseModel):
    market_id: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=3, max_length=20)
    side: Literal["UP", "DOWN", "BUY_YES", "BUY_NO"]
    contracts: int = Field(default=10, ge=1, le=500)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


DASHBOARD_SESSION_TTL_SECONDS = 12 * 60 * 60
_dashboard_sessions: Dict[str, tuple[str, float]] = {}
_login_attempts: Dict[str, List[float]] = {}


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _authenticated_username(token: str) -> Optional[str]:
    if not token:
        return None
    return _username_for_digest(_token_digest(token))


def _username_for_digest(digest: str) -> Optional[str]:
    now = time.time()
    session = _dashboard_sessions.get(digest)
    if session is None:
        return None
    username, expires_at = session
    if expires_at <= now:
        _dashboard_sessions.pop(digest, None)
        return None
    return username


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    scheme, separator, credentials = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return credentials.strip()


def persist_env_key(key: str, value: str) -> None:
    """Helper to update or append a key=value pair to the local .env file."""
    env_path = Path(".env")
    lines: List[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated = False
    new_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            if k.strip() == key:
                new_lines.append(f"{key}={value}")
                updated = True
                continue
        new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


class TradingBotCoordinator:
    """
    Master coordinator orchestrating the event-driven trading lifecycle.
    Features token-efficient AI inference (1x per round) on target market only,
    and supports full dual-sided (UP and DOWN) trading.
    """

    def __init__(self) -> None:
        self.start_time = time.time()
        persisted_status = getattr(settings, "bot_status", "STOPPED").upper()
        self.bot_status: str = "STOPPED" if persisted_status == "STOPPED" else "RUNNING"
        if not settings.paper_trading:
            self.bot_status = "STOPPED"
            logger.info("[BOOT] Live mode requires an explicit operator start after every process restart.")
        self.is_paused: bool = (self.bot_status == "STOPPED")
        if self.is_paused:
            logger.info("[BOOT] Restored bot state from disk: STOPPED (trading paused)")
        else:
            logger.info("[BOOT] Restored bot state from disk: RUNNING (trading active)")

        # User-selected Target Pair & Timeframe for AI evaluation
        configured_active_symbols = getattr(settings, "active_symbols", None)
        if not isinstance(configured_active_symbols, (str, list, tuple, set)):
            configured_active_symbols = None
        self.active_symbols: List[str] = list(normalize_active_symbols(
            configured_active_symbols or DEFAULT_ACTIVE_SYMBOLS
        ))
        self.target_symbol: str = getattr(settings, "target_symbol", "BTCUSDT").upper()
        self.target_timeframe: str = getattr(settings, "target_timeframe", "15m").lower()
        if self.target_symbol != "ALL" and self.target_symbol not in self.active_symbols:
            logger.warning(
                "Configured AI target %s is not in ACTIVE_SYMBOLS; using %s instead.",
                self.target_symbol,
                self.active_symbols[0],
            )
            self.target_symbol = self.active_symbols[0]
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
            martingale_mode=getattr(settings, "martingale_mode", "SMART_HYBRID"),
            martingale_multiplier=getattr(settings, "martingale_multiplier", 2.0),
            martingale_max_steps=getattr(settings, "martingale_max_steps", 4),
            martingale_confidence_step=getattr(settings, "martingale_confidence_step", 0.04),
            martingale_max_confidence=getattr(settings, "martingale_max_confidence", 0.95),
        )
        self.risk_guard.set_supported_symbols(set(self.active_symbols))

        # Mock prices are only used when explicitly requested; missing trade credentials
        # must never silently replace the configured market data source.
        use_mock_stream = settings.enable_mock_stream

        self.ws_listener = BinanceWSListener(
            stream_url=settings.binance_prediction_ws_url,
            on_market_event=self.on_market_tick,
            event_filter=self._should_evaluate_market,
            ping_interval=20,
            ping_timeout=10,
            enable_mock_stream=use_mock_stream,
            active_symbols=self.active_symbols,
        )

        # Telemetry storage
        self.recent_decisions: List[Dict[str, Any]] = []
        self.ws_clients: Set[WebSocket] = set()
        self.performance = PerformanceMetrics()
        self._dashboard_senders = {}
        self.max_ai_concurrency = settings.ai_max_concurrency
        self._ws_session_digests: Dict[WebSocket, str] = {}
        self._last_eval_time: Dict[str, float] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._background_tasks: Set[asyncio.Task] = set()
        self._eval_in_progress: Set[str] = set()
        self._order_execution_lock: asyncio.Lock = asyncio.Lock()
        self._last_oracle_log: Dict[str, float] = {}
        self._last_fast_oracle_sync: float = 0.0

    def _trigger_fast_oracle_sync(self) -> None:
        """Trigger immediate proactive refresh of Binance prediction topics to obtain startPrice."""
        now = time.time()
        if time.monotonic() < self.binance_client._prediction_retry_at:
            return
        if (now - getattr(self, "_last_fast_oracle_sync", 0.0)) < 2.0:
            return
        self._last_fast_oracle_sync = now
        async def _do_sync():
            try:
                await self.binance_client.fetch_prediction_market_topics(force_refresh=True)
                strike_map = self.binance_client.get_market_start_prices()
                detailed_strikes = self.binance_client.get_detailed_oracle_strikes()
                if strike_map or detailed_strikes:
                    self.ws_listener.update_official_strike_prices(strike_map, detailed_strikes)
            except Exception as ex:
                logger.debug(f"Fast oracle sync notice: {ex}")
        self._spawn_task(_do_sync(), name="fast_oracle_sync")

    def _should_evaluate_market(self, market: MarketContext) -> bool:
        """Fast non-allocating predicate to reject unselected symbols/timeframes before task spawning."""
        if self.bot_status != "RUNNING" or self.is_paused:
            return False
        if market.symbol.upper() not in self.active_symbols:
            return False
        if not self.binance_client.paper_trading:
            if self.ws_listener.enable_mock_stream:
                return False
            supported_syms = self.binance_client.get_supported_prediction_symbols()
            if market.symbol.upper() not in supported_syms:
                return False
        if self.target_symbol != "ALL" and market.symbol.upper() != self.target_symbol.upper():
            return False
        if self.target_timeframe != "ALL" and market.timeframe.lower() != self.target_timeframe.lower():
            return False
        return True

    def _spawn_task(self, coro, name: Optional[str] = None) -> Optional[asyncio.Task]:
        """Spawn background task with strong reference to prevent premature garbage collection in Python 3.12+."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if hasattr(coro, "close"):
                coro.close()
            return None
        task = loop.create_task(coro, name=name)
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
            f"(Mode: {self.risk_guard.martingale_mode}, Multiplier: {self.risk_guard.martingale_multiplier}x, "
            f"Max Steps: {self.risk_guard.martingale_max_steps}, "
            f"Confidence Step: +{self.risk_guard.martingale_confidence_step*100:.0f}%, "
            f"Max Conviction: {self.risk_guard.martingale_max_confidence*100:.0f}%)"
        )
        logger.info("=" * 70)

        await self.jev_client.start()
        await self.binance_client.start()
        await self.ws_listener.start()
        if not self.binance_client.paper_trading and self.binance_client.api_key and self.binance_client.api_secret:
            self._spawn_task(self.binance_client.fetch_live_balance(), name="init_balance")
            self._spawn_task(self._init_live_catalog(), name="init_catalog")
        elif not self.binance_client.paper_trading:
            logger.error("Live mode is configured without Binance credentials; trading remains stopped.")
        self._heartbeat_task = self._spawn_task(self._dashboard_heartbeat_loop(), name="heartbeat_loop")

    async def _init_live_catalog(self) -> None:
        """Fetch live prediction market catalog, sync supported symbols, Price to Beat strikes, and historical settled trades."""
        try:
            await self.binance_client.fetch_prediction_market_topics(force_refresh=True)
            supported = self.binance_client.get_supported_prediction_symbols()
            self.risk_guard.set_supported_symbols(supported)
            start_prices = self.binance_client.get_market_start_prices()
            detailed_strikes = self.binance_client.get_detailed_oracle_strikes()
            if start_prices or detailed_strikes:
                self.ws_listener.update_official_strike_prices(start_prices, detailed_strikes)
            # Reconcile closed positions history directly with Binance API
            await self.binance_client.sync_historical_closed_positions()
            self.risk_guard.reconcile_from_closed_positions(self.binance_client.get_closed_positions())
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

        senders = list(self._dashboard_senders.values())
        await asyncio.gather(*(sender.stop() for sender in senders), return_exceptions=True)
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
                if (
                    not self.binance_client.paper_trading
                    and self.binance_client.api_key
                    and self.binance_client.api_secret
                    and (heartbeat_ticks % 5 == 0 or self.binance_client._live_balance_usdt <= 0.05)
                ):
                    self._spawn_task(self.binance_client.fetch_live_balance(), name="periodic_balance")


                # Periodic server time re-sync every 60s to continuously prevent clock drift (-1021)
                if not self.binance_client.paper_trading and self.binance_client.api_key and self.binance_client.api_secret and (heartbeat_ticks % 60 == 0):
                    self._spawn_task(self.binance_client.sync_server_time(), name="periodic_sync_time")

                # Periodic reconciliation of historical settled positions directly from Binance every 60s
                if not self.binance_client.paper_trading and self.binance_client.api_key and self.binance_client.api_secret and (heartbeat_ticks % 60 == 0):
                    async def _reconcile_and_sync():
                        await self.binance_client.sync_historical_closed_positions()
                        self.risk_guard.reconcile_from_closed_positions(self.binance_client.get_closed_positions())
                    self._spawn_task(_reconcile_and_sync(), name="periodic_sync_closed")

                # Periodic / Proactive refresh of official Price to Beat (startPrice) from Binance topics
                needs_fast_oracle_sync = any(not m.get("strike_confirmed", False) for m in (markets or []))
                if (needs_fast_oracle_sync and heartbeat_ticks % 3 == 0) or (heartbeat_ticks % 15 == 0):
                    async def _sync_oracle_strikes():
                        try:
                            await self.binance_client.fetch_prediction_market_topics(force_refresh=needs_fast_oracle_sync)
                            strike_map = self.binance_client.get_market_start_prices()
                            detailed_strikes = self.binance_client.get_detailed_oracle_strikes()
                            if strike_map or detailed_strikes:
                                self.ws_listener.update_official_strike_prices(strike_map, detailed_strikes)
                        except Exception as ex:
                            logger.debug(f"Periodic strike sync notice: {ex}")
                    self._spawn_task(_sync_oracle_strikes(), name="periodic_oracle_strikes")

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
                                symbol=tp_rec["symbol"],
                                martingale_step=tp_rec.get("martingale_step"),
                            )

                    pnl, settled_records = await self.binance_client.settle_expired_positions(active_market_ids, current_prices)
                    for rec in settled_records:
                        self.risk_guard.record_settlement_result(
                            won=rec["won"],
                            pnl=rec["pnl"],
                            symbol=rec["symbol"],
                            martingale_step=rec.get("martingale_step"),
                        )
                    if settled_records:
                        self.risk_guard.reconcile_from_closed_positions(self.binance_client.get_closed_positions())
                    # Prune expired rounds from memory sets
                    self.evaluated_rounds = {rid for rid in self.evaluated_rounds if rid in active_market_ids}
                    self.traded_rounds = {rid for rid in self.traded_rounds if rid in active_market_ids}
                    self.last_eval_time = {mid: t for mid, t in self.last_eval_time.items() if mid in active_market_ids}

                if self.ws_clients:
                    status = self.get_system_status()
                    open_pos = self.binance_client.get_positions()
                    msg: Dict[str, Any] = {
                        "type": "HEARTBEAT",
                        "system_status": status,
                        "active_markets": markets,
                        "open_positions": open_pos,
                    }
                    msg.update(self.binance_client.get_closed_positions_page())
                    self._enqueue_dashboard(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Dashboard heartbeat notice: {e}")

    def start_bot(self) -> bool:
        """Start or resume trading execution."""
        if not self.binance_client.paper_trading and self.ws_listener.enable_mock_stream:
            logger.error("Refusing live trading while the synthetic market feed is enabled.")
            return False
        if not self.binance_client.paper_trading and not (
            self.binance_client.api_key.strip() and self.binance_client.api_secret.strip()
        ):
            logger.error("Refusing to start live trading: Binance API key and secret are required.")
            return False
        self.bot_status = "RUNNING"
        self.is_paused = False
        try:
            persist_env_key("BOT_STATUS", "RUNNING")
        except Exception as e:
            logger.warning(f"Failed to persist BOT_STATUS to .env: {e}")
        logger.info("[OPERATOR COMMAND] Bot status changed to: RUNNING")
        self._spawn_task(self.broadcast_status(), name="broadcast_start")
        return True

    def stop_bot(self) -> None:
        """Halt or freeze trading execution."""
        self.bot_status = "STOPPED"
        self.is_paused = True
        try:
            persist_env_key("BOT_STATUS", "STOPPED")
        except Exception as e:
            logger.warning(f"Failed to persist BOT_STATUS to .env: {e}")
        logger.info("[OPERATOR COMMAND] Bot status changed to: STOPPED")
        self._spawn_task(self.broadcast_status(), name="broadcast_stop")

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

        if market.symbol.upper() not in self.active_symbols:
            return

        # Reject stale or incomplete live inputs before spending an AI request or
        # performing a Binance pre-flight inquiry.
        if market.is_stale:
            return
        if not self.binance_client.paper_trading:
            if market.spot_data_age_ms is None or market.spot_data_age_ms > 5000:
                logger.warning("[AI SKIPPED] Spot event timestamp is missing or stale for %s.", market.market_id)
                return
            if not market.indicator_data_ready or not market.momentum_available or not market.obi_available:
                logger.warning("[AI SKIPPED] Indicator or spot top-of-book data is incomplete for %s.", market.market_id)
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
        round_period = (
            max(1, int(market.round_end_time_sec - market.round_start_time_sec))
            if market.round_start_time_sec is not None and market.round_end_time_sec is not None
            else {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}.get(market.timeframe.lower(), 900)
        )

        # Skip late round entries (< min_time_left_seconds, e.g. < 60s) to prevent asymmetric expiry risk
        if time_left < self.risk_guard.min_time_left_seconds:
            return

        # Skip the first 10 seconds of a brand new round so the strike / spot baseline price stabilizes
        if time_left > (round_period - 10):
            return

        # Price to Beat Oracle Gate: Ensure strike is officially confirmed by Binance for this round
        if not getattr(market, "strike_confirmed", False) or getattr(market, "target_price", 0.0) <= 0:
            now = time.time()
            oracle = self.binance_client.oracle_sync_status
            blocked = oracle["state"] in ("API_AUTH_ERROR", "RATE_LIMITED", "API_ERROR", "NETWORK_ERROR", "INVALID_RESPONSE")
            if (now - self._last_oracle_log.get(market.market_id, 0.0)) >= (60.0 if blocked else 10.0):
                self._last_oracle_log[market.market_id] = now
                if blocked:
                    logger.warning("[ORACLE ACCESS BLOCKED] Round %s: %s (HTTP=%s, code=%s)",
                                   market.market_id, oracle["reason"], oracle["http_status"], oracle["api_code"])
                else:
                    logger.warning("[WAITING FOR BINANCE ORACLE] Round %s (%s %s) has no confirmed startPrice for the current round",
                                   market.market_id, market.symbol, market.timeframe)
            self._trigger_fast_oracle_sync()
            return

        # Check if an order has already been executed on this round
        if market.market_id in self.traded_rounds:
            return

        # Check if we already hold an open position on this market round
        open_positions = self.binance_client.get_positions()
        in_flight_count = len(getattr(self.binance_client, "_in_flight_orders", {}))
        if any(p.get("market_id") == market.market_id for p in open_positions):
            self.traded_rounds.add(market.market_id)
            return

        # Pre-check: If max concurrent positions already reached, skip AI evaluation to conserve tokens & prevent churn
        if (len(open_positions) + in_flight_count) >= self.risk_guard.max_concurrent_positions:
            return

        # Check if an evaluation or order dispatch is already in progress for this round
        if market.market_id in self._eval_in_progress:
            return

        # Cadence Filter: Evaluate every eval_interval_seconds (default 60s / 1 min)
        now = time.time()
        last_eval = self.last_eval_time.get(market.market_id, 0.0)
        if (now - last_eval) < self.eval_interval_seconds:
            return

        if len(self._eval_in_progress) >= self.max_ai_concurrency:
            self.performance.counters["ai_capacity_skipped"] += 1
            return

        pipeline_started = time.perf_counter()
        # Record this evaluation timestamp
        self.last_eval_time[market.market_id] = now
        self.evaluated_rounds.add(market.market_id)
        self._eval_in_progress.add(market.market_id)

        try:
            # Supply the model with executable contract prices, independently from
            # the spot-derived probability estimate carried in odds_yes/odds_no.
            quote_cost = max(
                1.5,
                min(self.risk_guard.max_position_size_usdt,
                    self.risk_guard.default_order_contracts * 0.50),
            )
            quote_started = time.perf_counter()
            quote_snapshot = await self.binance_client.get_prediction_quote_snapshot(
                symbol=market.symbol,
                timeframe=market.timeframe,
                estimated_cost_usdt=quote_cost,
            )
            self.performance.observe("quote", quote_started)
            if not quote_snapshot:
                logger.warning(
                    "[AI SKIPPED] Could not obtain fresh UP and DOWN execution quotes for %s; no synthetic quote fallback is used.",
                    market.market_id,
                )
                return
            market.contract_up_ask = float(quote_snapshot["up_ask"])
            market.contract_down_ask = float(quote_snapshot["down_ask"])
            market.contract_quote_timestamp = float(quote_snapshot["timestamp"])
            market.contract_quote_source = str(quote_snapshot["source"])

            logger.info(
                f"[AI EVALUATION TRIGGERED: Every {self.eval_interval_seconds}s] Symbol: {market.symbol} | TF: {market.timeframe} | "
                f"Round: {market.market_id} | TimeLeft: {market.time_left_seconds}s | Spot: ${market.underlying_price:,.2f} | Beat: ${market.target_price:,.2f} | "
                f"DVR: {market.dvr_ratio:+.2f} expected-travel units | Spot top-of-book imbalance: {market.order_book_imbalance:+.2f} | Trend: {market.ema_trend} | RSI: {market.rsi_5m:.1f}"
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
            ai_started = time.perf_counter()
            decision: JevEvaluationResult = await self.jev_client.evaluate_market(market)
            self.performance.observe("ai", ai_started)
            fallback_blocked = decision.is_mock and not self.binance_client.paper_trading
            if fallback_blocked:
                logger.error(
                    "[LIVE EXECUTION BLOCKED] Jev AI fallback for %s (reason=%s); live orders require a valid remote AI response.",
                    market.market_id,
                    decision.fallback_reason or "unknown",
                )

            pre_flight = None
            pre_verified_quote = None

            order_result: Optional[OrderResult] = None

            # Step 2: Pre-Flight Binance Verification & Risk Guard Validation with Strict Mutual Exclusion Lock
            if (
                not fallback_blocked
                and decision.action in ("UP", "DOWN", "BUY_YES", "BUY_NO")
                and decision.confidence >= self.risk_guard.confidence_threshold
            ):
                lock_started = time.perf_counter()
                async with self._order_execution_lock:
                    self.performance.observe("execution_lock_wait", lock_started)
                    if self.bot_status != "RUNNING" or self.is_paused:
                        logger.info("[ORDER ABORTED] Bot was stopped before the order lock was acquired.")
                        return
                    refreshed = self._refresh_execution_market(market)
                    if refreshed is None:
                        self.performance.counters["stale_decision_rejected"] += 1
                        return
                    market = refreshed
                    if not self.binance_client.paper_trading:
                        refresh_started = time.perf_counter()
                        refreshed_quote = await self.binance_client.get_prediction_quote_snapshot(
                            symbol=market.symbol, timeframe=market.timeframe,
                            estimated_cost_usdt=quote_cost,
                        )
                        self.performance.observe("quote_refresh", refresh_started)
                        if (not refreshed_quote or not 0 <= time.time() - refreshed_quote["timestamp"] <= 5):
                            self.performance.counters["stale_quote_rejected"] += 1
                            return
                        market.contract_up_ask = float(refreshed_quote["up_ask"])
                        market.contract_down_ask = float(refreshed_quote["down_ask"])
                        market.contract_quote_timestamp = float(refreshed_quote["timestamp"])
                        market.contract_quote_source = str(refreshed_quote["source"])
                    # Concurrency Guard inside Mutex: Eliminate cross-asset TOCTOU race conditions
                    current_open = self.binance_client.get_positions()
                    in_flight = len(getattr(self.binance_client, "_in_flight_orders", {}))
                    if (len(current_open) + in_flight) >= self.risk_guard.max_concurrent_positions:
                        logger.info(
                            f"[CONCURRENCY LOCK] Max concurrent positions limit ({self.risk_guard.max_concurrent_positions}) "
                            f"reached (Open: {len(current_open)}, InFlight: {in_flight}). "
                            f"Order for {market.symbol} {decision.action} on {market.market_id} safely aborted to prevent simultaneous entries."
                        )
                        risk_result = RiskEvaluationResult(
                            approved=False,
                            reason=f"CONCURRENCY_LIMIT_REACHED: {len(current_open)} open + {in_flight} in-flight >= {self.risk_guard.max_concurrent_positions}",
                            adjusted_contracts=0,
                            confidence=decision.confidence,
                            market_id=market.market_id,
                            action=decision.action,
                            martingale_step=market.martingale_step,
                            stage_label=market.martingale_stage,
                            multiplier=1.0,
                            effective_threshold=market.effective_hurdle,
                        )
                    elif market.market_id in self.traded_rounds or any(p.get("market_id") == market.market_id for p in current_open):
                        self.traded_rounds.add(market.market_id)
                        logger.info(f"[ROUND ALREADY TRADED] Market {market.market_id} already has an entry. Skipping duplicate execution.")
                        return
                    else:
                        # Estimate preliminary cost for pre-flight quote inquiry based on Smart Hybrid recovery sizing
                        est_odds = (
                            market.contract_up_ask
                            if decision.action in ("UP", "BUY_YES")
                            else market.contract_down_ask
                        ) or 0.50
                        mult = self.risk_guard.martingale_multiplier ** market.martingale_step if (self.risk_guard.martingale_enabled and market.martingale_step > 0) else 1.0
                        if self.risk_guard.martingale_enabled and market.martingale_step > 0:
                            if self.risk_guard.martingale_mode == "SMART_HYBRID":
                                accum_loss = self.risk_guard.get_symbol_accumulated_loss(market.symbol)
                                profit_per_contract = max(0.05, 1.00 - est_odds)
                                target_gain = accum_loss + (self.risk_guard.default_order_contracts * profit_per_contract)
                                est_contracts = max(self.risk_guard.default_order_contracts, math.ceil(target_gain / profit_per_contract))
                            else:
                                est_contracts = max(1, math.ceil(self.risk_guard.default_order_contracts * mult))
                        else:
                            est_contracts = self.risk_guard.default_order_contracts

                        est_cost = max(1.5, min(self.risk_guard.max_position_size_usdt, float(est_contracts * est_odds)))

                        logger.info(
                            f"[PRE-FLIGHT GATE] Initiating Binance authoritative pre-order verification for "
                            f"{market.symbol} {decision.action} on {market.market_id} (Est. Cost: ${est_cost:.2f})..."
                        )
                        preflight_started = time.perf_counter()
                        pre_flight = await self.binance_client.verify_pre_flight_readiness(
                            symbol=market.symbol,
                            market_id=market.market_id,
                            side=decision.action,
                            timeframe=market.timeframe,
                            estimated_cost_usdt=est_cost,
                            strike_price=market.target_price,
                            spot_price=market.underlying_price,
                            max_concurrent_positions=self.risk_guard.max_concurrent_positions,
                        )

                        self.performance.observe("preflight", preflight_started)
                        refreshed = self._refresh_execution_market(market)
                        if refreshed is None or time.perf_counter() - preflight_started > 5.0:
                            pre_flight.verified = False
                            pre_flight.reason = "STALE_EXECUTION_INPUT: market or pre-flight quote expired"
                        else:
                            market = refreshed
                        # A stop request can arrive while the authoritative pre-flight call is in progress.
                        if self.bot_status != "RUNNING" or self.is_paused:
                            pre_flight.verified = False
                            pre_flight.reason = "BOT_STOPPED: Operator halted trading during pre-flight verification"

                        if not pre_flight.verified:
                            logger.warning(
                                f"[PRE-FLIGHT BLOCKED] Order NOT dispatched. Binance verification failed: {pre_flight.reason}. "
                                f"Capital preserved."
                            )
                            # If quote price was heavily over cap (>= 0.70 vs cap), back off evaluation on this round
                            # for 180s to avoid repeatedly hitting Binance quote API & Jev AI every 60s for a runaway round
                            if "exceeds max odds cap" in str(pre_flight.reason) and pre_flight.quoted_price >= 0.70:
                                self.last_eval_time[market.market_id] = now + 120.0
                                logger.info(
                                    f"[QUOTE CAP BACKOFF] Market {market.market_id} is heavily overpriced (${pre_flight.quoted_price:.3f} >= $0.70). "
                                    f"Applying 180s evaluation backoff on this round."
                                )
                            risk_result = RiskEvaluationResult(
                                approved=False,
                                reason=f"BINANCE_PRE_FLIGHT_FAILED: {pre_flight.reason}",
                                adjusted_contracts=0,
                                confidence=decision.confidence,
                                market_id=market.market_id,
                                action=decision.action,
                                martingale_step=market.martingale_step,
                                stage_label=market.martingale_stage,
                                multiplier=mult,
                                effective_threshold=market.effective_hurdle,
                            )
                        else:
                            # Update market target_price if Binance official oracle strike is available
                            if pre_flight.official_strike_price > 0:
                                market.target_price = pre_flight.official_strike_price
                            # Reconcile Martingale state from authoritative closed positions
                            self.risk_guard.reconcile_from_closed_positions(self.binance_client.get_closed_positions())

                            pre_verified_quote = {
                                "quote_id": pre_flight.quote_id,
                                "quoted_price": pre_flight.quoted_price,
                                "token_id": pre_flight.token_id,
                            }

                            # Execute RiskGuard sizing using 100% Binance verified balance & quote price
                            risk_started = time.perf_counter()
                            risk_result = self.risk_guard.validate_and_size_order(
                                decision=decision,
                                market=market,
                                current_open_positions_count=pre_flight.active_ongoing_count,
                                recent_performance=recent_perf,
                                verified_balance_usdt=pre_flight.live_balance_usdt,
                                confirmed_quote_price=pre_flight.quoted_price,
                            )

                            self.performance.observe("risk", risk_started)
                            # Step 3: Order Execution (Dispatched immediately inside lock to serialize fills)
                            if risk_result.approved and risk_result.action in ("UP", "DOWN", "BUY_YES", "BUY_NO"):
                                clean_action = "UP" if risk_result.action in ("UP", "BUY_YES") else "DOWN"
                                logger.info(
                                    f">>> DISPATCHING PREDICTION ORDER: {clean_action} {risk_result.adjusted_contracts}x "
                                    f"on {market.market_id} ({market.timeframe}) [{risk_result.stage_label}] @ {risk_result.target_price:.3f}"
                                )
                                execution_started = time.perf_counter()
                                order_result = await self.binance_client.place_prediction_order(
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
                                    pre_verified_quote=pre_verified_quote,
                                )
                                self.performance.observe("execution", execution_started)
                                if order_result.status in ("FILLED", "SIMULATED", "NEW"):
                                    self.traded_rounds.add(market.market_id)
                                    self.risk_guard.record_market_traded(market.market_id)
                                    logger.info(
                                        f"[ORDER ENTERED] Position open on {market.market_id} ({market.symbol} {clean_action}). "
                                        f"Round entry complete, pausing further evaluations for this round."
                                    )
                                else:
                                    self.risk_guard.rollback_market_traded(market.market_id)
                                    if order_result.order_id == "REJECTED_QUOTE_CAP" and order_result.price >= 0.70:
                                        self.last_eval_time[market.market_id] = now + 120.0
                                        logger.info(
                                            f"[QUOTE CAP BACKOFF] Market {market.market_id} is heavily overpriced (${order_result.price:.3f} >= $0.70). "
                                            f"Applying 180s evaluation backoff on this round."
                                        )
            elif fallback_blocked:
                risk_result = RiskEvaluationResult(
                    approved=False,
                    reason=(
                        "LIVE_EXECUTION_BLOCKED: Jev AI fallback "
                        f"({decision.fallback_reason or 'unknown'}); local heuristic fallback cannot place live orders"
                    ),
                    adjusted_contracts=0,
                    confidence=decision.confidence,
                    market_id=market.market_id,
                    action=decision.action,
                    martingale_step=market.martingale_step,
                    stage_label=market.martingale_stage,
                    multiplier=1.0,
                    effective_threshold=market.effective_hurdle,
                )
            else:
                open_positions = len(self.binance_client.get_positions())
                risk_result = self.risk_guard.validate_and_size_order(
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
                "obi_available": market.obi_available,
                "market_regime": getattr(market, "market_regime", "RANGING"),
                "expiry_danger_flag": getattr(market, "expiry_danger_flag", False),
                "btc_correlation_dir": getattr(market, "btc_correlation_dir", "FLAT"),
                "spread": market.spread,
                "volume_24h": market.volume_24h,
                "contract_up_ask": market.contract_up_ask,
                "contract_down_ask": market.contract_down_ask,
                "contract_quote_timestamp": market.contract_quote_timestamp,
                "contract_quote_source": market.contract_quote_source,
                "round_start_time_sec": market.round_start_time_sec,
                "round_end_time_sec": market.round_end_time_sec,
                "binance_topic_id": market.binance_topic_id,
                "binance_market_ids": market.binance_market_ids,
                "time_left_seconds": market.time_left_seconds,
                "decision": decision.model_dump(),
                "risk_validation": risk_result.model_dump(),
                "recent_performance": recent_perf,
                "pre_flight": pre_flight.model_dump() if pre_flight else None,
                "order": order_result.model_dump() if order_result else None,
            }

            # Cache last 50 decisions
            self.recent_decisions.append(record)
            if len(self.recent_decisions) > 50:
                self.recent_decisions.pop(0)

            # Broadcast update to connected dashboard WebSocket clients
            await self._broadcast_telemetry(record)
        finally:
            self.performance.observe("pipeline", pipeline_started)
            self._eval_in_progress.discard(market.market_id)

    async def _broadcast_telemetry(self, event_data: Dict[str, Any]) -> None:
        """Broadcast live tick and decision event to all connected dashboard websockets."""
        if not self.ws_clients:
            return

        open_pos = self.binance_client.get_positions()
        message = {
            "type": "MARKET_EVALUATION",
            "data": event_data,
            "system_status": self.get_system_status(),
            "active_markets": self.ws_listener.get_active_markets(),
            "open_positions": open_pos,
            "positions": open_pos,
        }

        message.update(self.binance_client.get_closed_positions_page())
        self._enqueue_dashboard(message)

    async def broadcast_status(self) -> None:
        """Broadcast updated system status immediately to all connected dashboard websockets."""
        if not self.ws_clients:
            return
        open_pos = self.binance_client.get_positions()
        msg = {
            "type": "HEARTBEAT",
            "system_status": self.get_system_status(),
            "active_markets": self.ws_listener.get_active_markets(),
            "open_positions": open_pos,
            "positions": open_pos,
        }
        msg.update(self.binance_client.get_closed_positions_page())
        self._enqueue_dashboard(msg)

    def _refresh_execution_market(self, market):
        # Live execution must use the current round and current market data.
        if self.binance_client.paper_trading:
            refreshed = market.model_copy(deep=True)
        else:
            refreshed = self.ws_listener.get_latest_market(market.symbol, market.timeframe)
            if refreshed is None or refreshed.market_id != market.market_id:
                return None
            age_ms = time.time() * 1000 - (refreshed.spot_source_timestamp_ms or 0)
            if (refreshed.is_stale or not 0 <= age_ms <= 5000
                    or not refreshed.strike_confirmed or refreshed.target_price <= 0
                    or not refreshed.indicator_data_ready or not refreshed.obi_available
                    or not refreshed.momentum_available
                    or not self._should_evaluate_market(refreshed)):
                return None
            for field in ("contract_up_ask", "contract_down_ask", "contract_quote_timestamp",
                          "contract_quote_source", "recent_performance", "martingale_step",
                          "martingale_stage", "effective_hurdle"):
                setattr(refreshed, field, getattr(market, field))
        if refreshed.round_end_time_sec is not None:
            refreshed.time_left_seconds = int(refreshed.round_end_time_sec - time.time())
        if refreshed.time_left_seconds < self.risk_guard.min_time_left_seconds:
            return None
        return refreshed

    def _enqueue_dashboard(self, message):
        for client in list(self.ws_clients):
            self._enqueue_dashboard_client(client, message)

    def _enqueue_dashboard_client(self, client, message):
        sender = self._dashboard_senders.get(client)
        if sender is None:
            def disconnected():
                self.ws_clients.discard(client)
                self._ws_session_digests.pop(client, None)
                self._dashboard_senders.pop(client, None)
            sender = DashboardSender(
                client,
                lambda: _username_for_digest(self._ws_session_digests.get(client, "")) is not None,
                disconnected, self.performance,
            )
            self._dashboard_senders[client] = sender
        sender.enqueue(message)

    def get_system_status(self) -> Dict[str, Any]:
        """Aggregate system telemetry for dashboard inspection."""
        uptime_seconds = int(time.time() - self.start_time)
        trading_mode = (
            "PAPER_TRADING" if self.binance_client.paper_trading
            else "LIVE_TRADING" if (
                self.binance_client.api_key.strip()
                and self.binance_client.api_secret.strip()
                and not self.ws_listener.enable_mock_stream
            )
            else "CONFIGURATION_ERROR"
        )
        account_summary = self.binance_client.get_account_summary()
        if trading_mode == "CONFIGURATION_ERROR":
            account_summary["mode"] = "CONFIGURATION_ERROR"
        return {
            "bot_status": self.bot_status,
            "is_paused": self.is_paused,
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s",
            "trading_mode": trading_mode,
            "target_market": {
                "target_symbol": self.target_symbol,
                "target_timeframe": self.target_timeframe,
                "active_symbols": list(self.active_symbols),
                "available_symbols": sorted(self.binance_client.get_supported_prediction_symbols()),
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
            "oracle_sync": self.binance_client.get_oracle_sync_status(),
            "performance": {**self.performance.snapshot(), "ingress": self.ws_listener.performance.snapshot(), "ai_in_progress": len(self._eval_in_progress), "ai_capacity": self.max_ai_concurrency, "dashboard_pending": sum(len(x.events) + (x.state is not None) for x in self._dashboard_senders.values())},
            "jev_ai": self.jev_client.stats,
            "risk_guard": self.risk_guard.metrics,
            "account": account_summary,
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

# Enable only explicitly configured dashboard origins.
dashboard_origins = [
    origin.strip().rstrip("/")
    for origin in settings.dashboard_allowed_origins.split(",")
    if origin.strip()
]
@app.middleware("http")
async def require_dashboard_session(request: Request, call_next):
    """Protect every HTTP API route by default; only credential login is public."""
    path = request.url.path
    if path.startswith("/api/") and path != "/api/auth/login" and request.method != "OPTIONS":
        username = _authenticated_username(_bearer_token(request.headers.get("Authorization")))
        if username is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        request.state.operator_username = username
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=dashboard_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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
async def get_orders(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Get order execution history log."""
    return bot.binance_client.get_order_history(limit=limit)


@app.get("/api/positions")
async def get_positions_endpoint(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)) -> Dict[str, Any]:
    """Return live positions and a bounded newest-first history page."""
    return {
        "status": "success",
        "open_positions": bot.binance_client.get_positions(),
        **bot.binance_client.get_closed_positions_page(offset=offset, limit=limit),
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
    if req.martingale_mode is not None:
        mapping["MARTINGALE_MODE"] = req.martingale_mode.upper()
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
    if req.active_symbols is not None:
        mapping["ACTIVE_SYMBOLS"] = ",".join(normalize_active_symbols(req.active_symbols))
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
            "martingale_mode": getattr(rg, "martingale_mode", "SMART_HYBRID"),
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
            "active_symbols": list(bot.active_symbols),
            "available_symbols": sorted(bot.binance_client.get_supported_prediction_symbols()),
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
    next_active_symbols = list(bot.active_symbols)
    available_prediction_symbols = bot.binance_client.get_supported_prediction_symbols()
    if req.active_symbols is not None:
        try:
            next_active_symbols = list(normalize_active_symbols(req.active_symbols))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        added_symbols = set(next_active_symbols) - set(bot.active_symbols)
        if added_symbols:
            try:
                await bot.binance_client.fetch_prediction_market_topics(force_refresh=True)
            except Exception as exc:
                logger.warning("Could not refresh Binance pair catalog while updating active pairs: %s", exc)
            available_prediction_symbols = bot.binance_client.get_supported_prediction_symbols()
            pending_symbols = sorted(added_symbols - available_prediction_symbols)
            if pending_symbols:
                logger.info(
                    "Configured pair(s) without a current Binance Prediction Market; live execution remains blocked: %s",
                    ", ".join(pending_symbols),
                )

        removed_symbols = set(bot.active_symbols) - set(next_active_symbols)
        held_symbols = {
            str(position.get("symbol", "")).upper()
            for position in bot.binance_client.get_positions()
        }
        positions_at_risk = sorted(removed_symbols & held_symbols)
        if positions_at_risk:
            raise HTTPException(
                status_code=409,
                detail=f"Close or settle open positions before removing: {', '.join(positions_at_risk)}",
            )

    next_target_symbol = (
        req.target_symbol.strip().upper()
        if req.target_symbol is not None and req.target_symbol.strip()
        else bot.target_symbol
    )
    if next_target_symbol == "ALL":
        next_target_symbol = "ALL"
    elif next_target_symbol not in next_active_symbols:
        if req.target_symbol is not None:
            raise HTTPException(
                status_code=422,
                detail="The AI target pair must be selected in Active Trading Pairs first.",
            )
        next_target_symbol = next_active_symbols[0]
        req.target_symbol = next_target_symbol

    next_max_odds = req.max_odds_cap if req.max_odds_cap is not None else bot.risk_guard.max_odds_cap
    next_min_odds = req.min_odds_floor if req.min_odds_floor is not None else bot.risk_guard.min_odds_floor
    next_min_time = req.min_time_left_seconds if req.min_time_left_seconds is not None else bot.risk_guard.min_time_left_seconds
    next_max_time = req.max_time_left_seconds if req.max_time_left_seconds is not None else bot.risk_guard.max_time_left_seconds
    if next_min_odds >= next_max_odds:
        raise HTTPException(status_code=422, detail="Minimum odds must be lower than maximum odds")
    if next_min_time > next_max_time:
        raise HTTPException(status_code=422, detail="Minimum time left must not exceed maximum time left")

    effective_api_key = (req.binance_api_key or bot.binance_client.api_key).strip()
    effective_api_secret = (req.binance_api_secret or bot.binance_client.api_secret).strip()
    if req.paper_trading is False and not (effective_api_key and effective_api_secret):
        raise HTTPException(status_code=422, detail="Live trading requires both Binance API credentials")
    if req.paper_trading is False and bot.ws_listener.enable_mock_stream:
        raise HTTPException(status_code=422, detail="Disable ENABLE_MOCK_STREAM before enabling live trading")
    if req.paper_trading is True and not bot.binance_client.paper_trading and bot.binance_client.get_positions():
        raise HTTPException(status_code=409, detail="Settle or close live positions before switching to paper mode")
    credentials_changed = any(
        value is not None and bool(value.strip())
        for value in (req.binance_api_key, req.binance_api_secret)
    )
    if (req.paper_trading is not None or credentials_changed) and bot._order_execution_lock.locked():
        raise HTTPException(status_code=409, detail="Wait for the in-flight order to finish before changing live mode or credentials")

    bot.risk_guard.update_thresholds(
        confidence_threshold=req.confidence_threshold,
        max_position_size_usdt=req.max_position_size_usdt,
        default_order_contracts=req.default_order_contracts,
        cooldown_seconds=req.cooldown_seconds,
        max_daily_loss_usdt=req.max_daily_loss_usdt,
        max_concurrent_positions=req.max_concurrent_positions,
        martingale_enabled=req.martingale_enabled,
        martingale_mode=req.martingale_mode,
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
        bot.target_symbol = next_target_symbol
        logger.info(f"Target symbol updated to: {bot.target_symbol}")

    if req.target_timeframe is not None and req.target_timeframe.strip():
        bot.target_timeframe = req.target_timeframe.lower().strip()
        logger.info(f"Target timeframe updated to: {bot.target_timeframe}")

    if req.active_symbols is not None:
        bot.active_symbols = next_active_symbols
        settings.active_symbols = ",".join(next_active_symbols)
        await bot.ws_listener.configure_active_symbols(next_active_symbols)
        bot.risk_guard.set_supported_symbols(available_prediction_symbols | set(next_active_symbols))
        logger.info("Trading pair allowlist updated: %s", ", ".join(next_active_symbols))

    if req.eval_interval_seconds is not None:
        bot.eval_interval_seconds = max(10, min(900, int(req.eval_interval_seconds)))
        logger.info(f"Evaluation interval updated to: {bot.eval_interval_seconds}s")

    if req.paper_trading is not None:
        prev_mode = bot.binance_client.paper_trading
        bot.binance_client.paper_trading = req.paper_trading
        if prev_mode and not req.paper_trading:
            bot.binance_client.clear_paper_positions()
            bot.stop_bot()
        logger.info(f"Updated Paper Trading mode to: {req.paper_trading}")

    if req.binance_api_key is not None and req.binance_api_key.strip():
        bot.binance_client.api_key = req.binance_api_key.strip()
        if bot.binance_client._session and not bot.binance_client._session.closed:
            bot.binance_client._session.headers["X-MBX-APIKEY"] = bot.binance_client.api_key
        logger.info("Updated Binance API Key")

    if req.binance_api_secret is not None and req.binance_api_secret.strip():
        bot.binance_client.api_secret = req.binance_api_secret.strip()
        logger.info("Updated Binance API Secret")

    if (
        not bot.binance_client.paper_trading
        and bot.binance_client.api_key.strip()
        and bot.binance_client.api_secret.strip()
        and (
            req.paper_trading is False
            or (req.binance_api_key is not None and bool(req.binance_api_key.strip()))
            or (req.binance_api_secret is not None and bool(req.binance_api_secret.strip()))
        )
    ):
        bot._spawn_task(bot.binance_client.sync_server_time(), name="live_config_sync_time")
        bot._spawn_task(bot.binance_client.fetch_live_balance(), name="live_config_balance")

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
async def login_endpoint(req: LoginRequest, request: Request, response: Response):
    """Authenticate an operator and issue a short-lived, revocable bearer token."""
    now = time.time()
    client_ip = request.client.host if request.client else "unknown"
    for ip in list(_login_attempts):
        recent = [stamp for stamp in _login_attempts[ip] if now - stamp < 60.0]
        if recent:
            _login_attempts[ip] = recent
        else:
            _login_attempts.pop(ip, None)
    attempts = _login_attempts.get(client_ip, [])
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Too many login attempts; try again in one minute")

    expected_username = settings.dashboard_username.strip()
    expected_password = settings.dashboard_password
    if not expected_username or not expected_password:
        raise HTTPException(status_code=503, detail="Dashboard credentials are not configured")

    username_ok = hmac.compare_digest(req.username.encode("utf-8"), expected_username.encode("utf-8"))
    password_ok = hmac.compare_digest(req.password.encode("utf-8"), expected_password.encode("utf-8"))
    if not (username_ok and password_ok):
        _login_attempts.setdefault(client_ip, []).append(now)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _login_attempts.pop(client_ip, None)
    for digest, (_, expires_at) in list(_dashboard_sessions.items()):
        if expires_at <= now:
            _dashboard_sessions.pop(digest, None)
    token = secrets.token_urlsafe(32)
    _dashboard_sessions[_token_digest(token)] = (expected_username, now + DASHBOARD_SESSION_TTL_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "status": "success",
        "token": token,
        "username": expected_username,
        "expires_in": DASHBOARD_SESSION_TTL_SECONDS,
        "message": "Authentication successful",
    }


@app.get("/api/auth/session")
async def session_endpoint(request: Request) -> Dict[str, Any]:
    return {"status": "success", "username": request.state.operator_username}


@app.post("/api/auth/logout")
async def logout_endpoint(request: Request) -> Dict[str, str]:
    token = _bearer_token(request.headers.get("Authorization"))
    if token:
        _dashboard_sessions.pop(_token_digest(token), None)
    return {"status": "success", "message": "Session closed"}


@app.post("/api/bot/start")
async def start_bot_endpoint() -> Dict[str, Any]:
    """Web command to start/activate bot trading."""
    if not bot.start_bot():
        raise HTTPException(status_code=409, detail="Live trading is not ready; check credentials and disable the mock feed")
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
    if bot.is_paused:
        if not bot.start_bot():
            raise HTTPException(status_code=409, detail="Live trading is not ready; check credentials and disable the mock feed")
    else:
        bot.stop_bot()
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
        "active_symbols": list(bot.active_symbols),
        "available_symbols": sorted(bot.binance_client.get_supported_prediction_symbols()),
        "evaluated_rounds_count": len(bot.evaluated_rounds),
        "policy": "1x_per_round",
    }


@app.post("/api/target")
async def set_target_endpoint(req: TargetMarketRequest) -> Dict[str, Any]:
    """Dynamically switch the targeted asset and timeframe for AI evaluation."""
    requested_symbol = req.target_symbol.upper().strip()
    if requested_symbol != "ALL" and requested_symbol not in bot.active_symbols:
        raise HTTPException(
            status_code=422,
            detail="The AI target pair must be selected in Active Trading Pairs first.",
        )
    bot.target_symbol = requested_symbol
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
    if bot.bot_status != "RUNNING" or bot.is_paused:
        raise HTTPException(status_code=409, detail="Trading is stopped; resume the bot before placing a manual order")

    active_markets = {m["market_id"]: m for m in bot.ws_listener.get_active_markets()}
    market = active_markets.get(req.market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market is not active")
    symbol = req.symbol.upper().strip()
    if symbol != str(market.get("symbol", "")).upper():
        raise HTTPException(status_code=422, detail="Market symbol does not match the active market")
    strike_price = float(market.get("target_price", 0.0))
    spot_price = float(market.get("underlying_price", 0.0))
    if not bot.binance_client.paper_trading:
        if bot.ws_listener.enable_mock_stream:
            raise HTTPException(status_code=409, detail="Manual live orders are disabled while the synthetic market feed is enabled")
        if not (bot.binance_client.api_key.strip() and bot.binance_client.api_secret.strip()):
            raise HTTPException(status_code=409, detail="Live trading requires both Binance API credentials")
        if bot.ws_listener.network_state == "OFFLINE":
            raise HTTPException(status_code=503, detail="Live market data is offline")
        if not market.get("strike_confirmed") or not math.isfinite(strike_price) or strike_price <= 0:
            raise HTTPException(status_code=409, detail="Waiting for Binance's official strike price")
        if not math.isfinite(spot_price) or spot_price <= 0:
            raise HTTPException(status_code=409, detail="The active market has invalid spot data")

    clean_side = "UP" if req.side in ("UP", "BUY_YES") else "DOWN"
    time_left = int(market.get("time_left_seconds", 0))
    timeframe = str(market.get("timeframe", "15m")).lower()
    quote_snapshot = await bot.binance_client.get_prediction_quote_snapshot(
        symbol=symbol,
        timeframe=timeframe,
        estimated_cost_usdt=max(1.5, req.contracts * 0.50),
    )
    if not quote_snapshot:
        raise HTTPException(status_code=503, detail="Binance could not provide fresh UP/DOWN execution quotes")
    current_price = float(quote_snapshot["up_ask"] if clean_side == "UP" else quote_snapshot["down_ask"])
    if not math.isfinite(current_price) or not (0.0 < current_price < 1.0):
        raise HTTPException(status_code=409, detail="Binance returned an invalid execution quote")
    if not (bot.risk_guard.min_odds_floor <= current_price <= bot.risk_guard.max_odds_cap):
        raise HTTPException(status_code=409, detail="The active market odds are outside the configured risk limits")
    spread_value = market.get("spread")
    spread = float(spread_value) if spread_value is not None else None
    if spread is not None and (not math.isfinite(spread) or spread < 0.0 or spread > 0.06):
        raise HTTPException(status_code=409, detail="The active market spread exceeds the configured safety limit")
    effective_min_time = (
        max(bot.risk_guard.min_time_left_seconds, 120)
        if timeframe in ("15m", "1h", "1d")
        else bot.risk_guard.min_time_left_seconds
    )
    if not (effective_min_time <= time_left <= bot.risk_guard.max_time_left_seconds):
        raise HTTPException(status_code=409, detail="The market is outside the configured trading window")
    bot.risk_guard._check_and_reset_daily_window()
    if bot.risk_guard._circuit_breaker_active or bot.risk_guard._daily_realized_loss >= bot.risk_guard.max_daily_loss_usdt:
        raise HTTPException(status_code=409, detail="Daily loss circuit breaker is active")
    if req.contracts * current_price > bot.risk_guard.max_position_size_usdt:
        raise HTTPException(status_code=422, detail="Manual order exceeds the configured position size limit")

    async with bot._order_execution_lock:
        if bot.bot_status != "RUNNING" or bot.is_paused:
            raise HTTPException(status_code=409, detail="Trading was stopped before pre-flight verification")
        open_positions = bot.binance_client.get_positions()
        in_flight = list(getattr(bot.binance_client, "_in_flight_orders", {}).values())
        if req.market_id in bot.traded_rounds or any(p.get("market_id") == req.market_id for p in open_positions):
            raise HTTPException(status_code=409, detail="A position already exists for this market round")
        if any(order.get("market_id") == req.market_id for order in in_flight):
            raise HTTPException(status_code=409, detail="An order is already in flight for this market round")
        if len(open_positions) + len(in_flight) >= bot.risk_guard.max_concurrent_positions:
            raise HTTPException(status_code=409, detail="Maximum concurrent positions reached")

        pre_flight = await bot.binance_client.verify_pre_flight_readiness(
            symbol=symbol,
            market_id=req.market_id,
            side=clean_side,
            timeframe=timeframe,
            estimated_cost_usdt=req.contracts * current_price,
            strike_price=strike_price,
            spot_price=spot_price,
            max_concurrent_positions=bot.risk_guard.max_concurrent_positions,
        )
        if not pre_flight.verified:
            raise HTTPException(status_code=409, detail=f"Pre-flight verification failed: {pre_flight.reason}")
        if bot.bot_status != "RUNNING" or bot.is_paused:
            raise HTTPException(status_code=409, detail="Trading was stopped during pre-flight verification")

        execution_price = current_price
        verified_quote = None
        if not bot.binance_client.paper_trading:
            if not pre_flight.quote_id or pre_flight.quoted_price is None or not pre_flight.token_id:
                raise HTTPException(status_code=503, detail="Binance did not return a complete executable quote")
            execution_price = float(pre_flight.quoted_price)
            if not (0.0 < execution_price < 1.0):
                raise HTTPException(status_code=409, detail="Binance returned an invalid execution price")
            if not (bot.risk_guard.min_odds_floor <= execution_price <= bot.risk_guard.max_odds_cap):
                raise HTTPException(status_code=409, detail="Binance quote is outside the configured odds limits")
            if req.contracts * execution_price > bot.risk_guard.max_position_size_usdt:
                raise HTTPException(status_code=422, detail="Quoted manual order exceeds the configured position size limit")
            if pre_flight.live_balance_usdt < req.contracts * execution_price:
                raise HTTPException(status_code=409, detail="Insufficient Binance balance for the quoted order")
            verified_quote = {
                "quote_id": pre_flight.quote_id,
                "quoted_price": execution_price,
                "token_id": pre_flight.token_id,
            }

        order_result = await bot.binance_client.place_prediction_order(
            market_id=req.market_id,
            symbol=symbol,
            side=clean_side,
            contracts=req.contracts,
            target_price=execution_price,
            strike_price=pre_flight.official_strike_price or strike_price,
            spot_price=spot_price,
            timeframe=timeframe,
            pre_verified_quote=verified_quote,
        )
        if order_result.status in ("FILLED", "SIMULATED", "NEW"):
            bot.traded_rounds.add(req.market_id)
            bot.risk_guard.record_market_traded(req.market_id)
        else:
            raise HTTPException(status_code=409, detail=order_result.error_message or "Binance rejected the order")

    return {
        "status": "success",
        "order": order_result.model_dump(),
        "open_positions": bot.binance_client.get_positions(),
        "closed_positions": bot.binance_client.get_closed_positions(),
        "account": bot.binance_client.get_account_summary(),
    }


@app.post("/api/trade/claim")
async def claim_winnings_endpoint() -> Dict[str, Any]:
    """Claiming is managed natively by Binance Prediction Markets platform or Binance UI.
    Auto-claim in the bot has been disabled to prevent synchronization and duplicate redeem conflicts."""
    await bot.binance_client.sync_historical_closed_positions()
    await bot.binance_client.fetch_live_balance()
    return {
        "status": "info",
        "message": "Autoclaim disabled. Settlements and redemptions are handled natively by Binance.",
        "account": bot.binance_client.get_account_summary(),
        "closed_positions": bot.binance_client.get_closed_positions(),
    }


@app.post("/api/evaluate/force")
async def force_evaluate_endpoint(
    market_id: str = Query(..., min_length=1, max_length=128),
) -> Dict[str, Any]:
    """Force an immediate single AI evaluation on specific market round."""
    if bot.bot_status != "RUNNING" or bot.is_paused:
        raise HTTPException(status_code=409, detail="Trading is stopped")
    markets = {m["market_id"]: m for m in bot.ws_listener.get_active_markets()}
    if market_id not in markets:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Market not found in active streams")

    if market_id in bot.traded_rounds or market_id in bot._eval_in_progress:
        raise HTTPException(status_code=409, detail="This market round is already traded or being evaluated")
    open_positions = bot.binance_client.get_positions()
    in_flight_orders = getattr(bot.binance_client, "_in_flight_orders", {}).values()
    if any(position.get("market_id") == market_id for position in open_positions) or any(
        order.get("market_id") == market_id for order in in_flight_orders
    ):
        raise HTTPException(status_code=409, detail="An open position or order already exists for this market round")

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
    bot.last_eval_time.pop(market_id, None)
    await bot.on_market_tick(ctx)
    return {"status": "success", "message": f"Forced evaluation executed for {market_id}"}


@app.websocket("/ws/stream")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Authenticate the dashboard before sending any telemetry over WebSocket."""
    await websocket.accept()
    token = ""
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=8.0)
        if not isinstance(auth_message, dict) or auth_message.get("type") != "AUTH":
            await websocket.close(code=4401)
            return
        token = str(auth_message.get("token", ""))
        if _authenticated_username(token) is None:
            await websocket.close(code=4401)
            return

        session_digest = _token_digest(token)
        bot.ws_clients.add(websocket)
        bot._ws_session_digests[websocket] = session_digest

        # Send initial snapshot through the same writer as subsequent updates
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
        snapshot.update(bot.binance_client.get_closed_positions_page())
        bot._enqueue_dashboard_client(websocket, snapshot)

        # Keep socket open and handle any incoming messages/pings
        while True:
            data = await websocket.receive_text()
            if _username_for_digest(session_digest) is None:
                await websocket.close(code=4401)
                break
            if data == "ping":
                bot._enqueue_dashboard_client(websocket, {"type": "PONG"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Dashboard WebSocket closed after a connection error")
    finally:
        sender = bot._dashboard_senders.pop(websocket, None)
        if sender is not None:
            await sender.stop()
        bot.ws_clients.discard(websocket)
        bot._ws_session_digests.pop(websocket, None)


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
