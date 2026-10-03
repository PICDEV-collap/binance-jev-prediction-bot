"""
Binance Prediction Markets REST API Client.
Features HMAC-SHA256 request signing, server time synchronization,
persistent connection pooling, and high-fidelity paper trading simulation.
"""

from __future__ import annotations
import asyncio
from collections import deque
import hashlib
import hmac
import logging
import math
import re
import time
import uuid
from typing import Optional, Dict, Any, List, Set, Tuple
from urllib.parse import urlencode
from engine.symbols import normalize_symbol
from engine.oracle import round_window, start_price, topic_id

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger("binance_client")


def _is_crypto_up_down_topic(topic: Dict[str, Any]) -> bool:
    title = str(topic.get("title", "")).upper()
    chart_type = str(topic.get("chartType", "")).upper()
    return "UP OR DOWN" in title or "UP/DOWN" in title or chart_type == "CRYPTO_UP_DOWN"


def _prediction_topic_symbol(topic: Dict[str, Any]) -> Optional[str]:
    """Resolve a USDT pair from a Binance prediction topic without a fixed coin list."""
    raw_symbol = str(topic.get("symbol", "")).strip().upper()
    title = str(topic.get("title", "")).strip().upper()
    candidates = [raw_symbol]
    if raw_symbol and not raw_symbol.endswith("USDT"):
        candidates.append(f"{raw_symbol}USDT")

    direct_pair = re.search(r"\b([A-Z0-9]{2,20}USDT)\b", title)
    if direct_pair:
        candidates.append(direct_pair.group(1))

    base_match = re.search(
        r"\b([A-Z0-9]{2,20})(?:\s+PRICE)?\s+(?:UP\s+OR\s+DOWN|UP/DOWN)\b",
        title,
    )
    if base_match:
        base = base_match.group(1)
        aliases = {
            "BITCOIN": "BTC", "ETHEREUM": "ETH", "ETHER": "ETH",
            "BINANCECOIN": "BNB", "SOLANA": "SOL", "DOGECOIN": "DOGE",
            "RIPPLE": "XRP",
        }
        candidates.append(f"{aliases.get(base, base)}USDT")

    for candidate in candidates:
        try:
            return normalize_symbol(candidate)
        except ValueError:
            continue
    return None


def _prediction_position_symbol(position: Dict[str, Any]) -> Optional[str]:
    """Resolve the underlying pair from a Binance position or order-history record."""
    title = str(position.get("marketTopicTitle") or position.get("marketTitle") or position.get("title") or "")
    slug = str(position.get("slug", "")).replace("-", " ").replace("_", " ")
    title = f"{title} {slug}".strip()
    raw_symbol = (
        position.get("symbol")
        or position.get("marketSymbol")
        or position.get("underlyingSymbol")
        or ""
    )
    return _prediction_topic_symbol({"symbol": raw_symbol, "title": title})


def _prediction_timeframe(position: Dict[str, Any], default: str = "5m") -> str:
    """Infer a prediction market interval from Binance metadata and its time window."""
    title = str(
        position.get("marketTopicTitle")
        or position.get("marketTitle")
        or position.get("title")
        or ""
    )
    slug = str(position.get("slug") or "").replace("-", " ").replace("_", " ")
    text = f"{title} {slug} {position.get('timeframe', '')}".lower()

    explicit = re.search(r"\b(5m|15m|1h|1d)\b", text, re.IGNORECASE)
    if explicit:
        return explicit.group(1).lower()

    # Binance titles for short markets commonly carry the exact ET interval,
    # e.g. "10:35AM-10:40AM ET". Do not mistake those rows for hourly markets.
    time_range = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*[-–]\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s+et\b",
        text,
        re.IGNORECASE,
    )
    if time_range:
        start_hour, start_minute, start_period, end_hour, end_minute, end_period = time_range.groups()

        def _minutes(hour: str, minute: Optional[str], period: str) -> int:
            hour_24 = int(hour) % 12
            if period.lower() == "pm":
                hour_24 += 12
            return hour_24 * 60 + int(minute or 0)

        duration = _minutes(end_hour, end_minute, end_period) - _minutes(start_hour, start_minute, start_period)
        if duration <= 0:
            duration += 24 * 60
        if 4 <= duration <= 6:
            return "5m"
        if 14 <= duration <= 16:
            return "15m"
        if 55 <= duration <= 65:
            return "1h"
        if 23 * 60 <= duration <= 25 * 60:
            return "1d"

    if "hourly" in text or re.search(r"\b\d{1,2}(?:am|pm)\s+et\b", text, re.IGNORECASE):
        return "1h"
    return default


class OrderResult(BaseModel):
    """Execution feedback returned after dispatching an order."""
    order_id: str
    client_order_id: str
    market_id: str
    symbol: str
    side: str  # "UP" or "DOWN"
    contracts: int
    price: float
    status: str  # "FILLED", "REJECTED", "SIMULATED", "NEW"
    latency_ms: float
    timeframe: str = "15m"
    price_to_beat: float = 0.0
    martingale_step: int = 0
    stage: str = "ไม้ 1 (Base)"
    error_message: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)


class PositionInfo(BaseModel):
    """Representation of an active prediction contract position."""
    position_id: str
    market_id: str
    symbol: str
    side: str  # "UP" or "DOWN"
    contracts: int
    entry_price: float
    current_price: float
    target_price: float = 0.0  # Price to Beat
    timeframe: str = "15m"
    unrealized_pnl: float
    martingale_step: int = 0
    stage: str = "ไม้ 1 (Base)"
    token_id: str = ""
    is_settling: bool = False
    entry_time: float = Field(default_factory=time.time)


class ClosedPositionInfo(BaseModel):
    """Historical record of a settled prediction contract position."""
    position_id: str
    market_id: str
    symbol: str
    side: str  # "UP" or "DOWN"
    contracts: int
    entry_price: float
    target_price: float = 0.0  # Price to Beat (Strike)
    settlement_price: float = 0.0  # Final spot price
    timeframe: str = "15m"
    result: str  # "WIN" or "LOSS"
    realized_pnl: float
    martingale_step: int = 0
    stage: str = "ไม้ 1 (Base)"
    token_id: str = ""
    is_claimed: bool = False
    entry_time: float = Field(default_factory=time.time)
    settled_at: float = Field(default_factory=time.time)


class PreFlightVerificationResult(BaseModel):
    """
    Direct authoritative verification result from Binance SAPI before opening an order.
    Ensures 100% data fidelity for position sizing, EV calculation, and duplicate prevention.
    """
    verified: bool = False
    reason: str = ""
    live_balance_usdt: float = 0.0
    active_ongoing_count: int = 0
    has_duplicate_position: bool = False
    official_strike_price: float = 0.0
    token_id: str = ""
    quote_id: Optional[str] = None
    quoted_price: Optional[float] = None
    latency_ms: float = 0.0


class BinanceClient:
    """
    High-performance asynchronous client for Binance Prediction API.
    Supports live execution via signed requests and zero-risk paper trading mode.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: str = "https://api.binance.com",
        recv_window: int = 5000,
        paper_trading: bool = True,
        slippage_tolerance: float = 0.03,
        funding_source: str = "CEX",
        slippage_bps: int = 200,
        max_odds_cap: float = 0.60,
        min_odds_floor: float = 0.20,
        enable_early_take_profit: bool = True,
        take_profit_odds: float = 0.82,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        # Ensure SAPI/W3W endpoints always target api.binance.com rather than fapi.binance.com
        if "fapi.binance.com" in base_url:
            self.base_url = "https://api.binance.com"
        else:
            self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.paper_trading = paper_trading
        self.slippage_tolerance = slippage_tolerance
        self.funding_source = funding_source
        self.slippage_bps = max(0, min(1000, int(slippage_bps)))
        self.max_odds_cap = max_odds_cap
        self.min_odds_floor = min_odds_floor
        self.enable_early_take_profit = enable_early_take_profit
        self.take_profit_odds = take_profit_odds

        self._session: Optional[aiohttp.ClientSession] = None
        self._time_offset_ms: int = 0
        self._total_orders_dispatched: int = 0
        self._total_fills: int = 0
        self._in_flight_orders: Dict[str, Dict[str, Any]] = {}

        # Simulated Paper Trading State
        self._paper_balance_usdt: float = 1000.0  # Starting mock wallet
        self._paper_positions: Dict[str, PositionInfo] = {}
        self._closed_positions: List[ClosedPositionInfo] = []
        self._order_history: deque[OrderResult] = deque(maxlen=1000)

        # Live Binance Account Cache
        self._live_balance_usdt: float = 0.0
        self._live_available_usdt: float = 0.0
        self._live_unrealized_usdt: float = 0.0
        self._live_initial_capital: float = 0.0
        self._live_positions: Dict[str, PositionInfo] = {}
        self._wallet_address: str = "0x8bd02cfadc1065dba4db288997ea24d26a788936"
        self._wallet_id: str = "a34494abf3d7403796af191a0accdd86"
        self._active_account_type: str = "CeDeFi"
        self._prediction_market_cache: Dict[str, Any] = {}
        self._prediction_cache_timestamp: float = 0.0
        self._prediction_catalog_lock = asyncio.Lock()
        self._prediction_retry_at: float = 0.0
        self._prediction_detail_cache: Dict[str, Dict[str, Any]] = {}
        self.oracle_sync_status: Dict[str, Any] = {
            "state": "STARTING", "reason": "Waiting for Binance market data",
            "http_status": None, "api_code": None, "confirmed_rounds": 0,
            "last_success_at": None, "retry_at": None,
        }
        # Permanent in-memory cache for resolved market oracle strike and settlement prices
        self._market_oracle_cache: Dict[int, Tuple[float, float]] = {}
        self._background_tasks: Set[asyncio.Task] = set()

    def _spawn_task(self, coro) -> Optional[asyncio.Task]:
        """Spawn background task with strong reference to prevent premature garbage collection in Python 3.12+."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return task
        except RuntimeError:
            return None

    async def start(self) -> None:
        """Initialize session with persistent connection pooling and sync time."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=100,
                keepalive_timeout=75.0,
                enable_cleanup_closed=True
            )
            headers = {
                "Accept": "application/json",
                "User-Agent": "Binance-Jev-Prediction-Bot/1.0",
            }
            if self.api_key:
                headers["X-MBX-APIKEY"] = self.api_key

            self._session = aiohttp.ClientSession(
                connector=connector,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=4.0, connect=1.0)
            )
            logger.info("Binance Client HTTP session initialized.")

            if not self.paper_trading and self.api_key:
                await self.sync_server_time()

    async def close(self) -> None:
        """Clean up HTTP session and background tasks."""
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

        if self._session and not self._session.closed:
            await self._session.close()
            await asyncio.sleep(0.05)
            logger.info("Binance Client HTTP session closed.")

    async def sync_server_time(self) -> None:
        """Synchronize client with Binance server time using median ping to prevent timestamp drift."""
        if self._session is None or self._session.closed:
            await self.start()
        try:
            assert self._session is not None
            url = f"{self.base_url}/api/v3/time"
            offsets = []
            for _ in range(3):
                t0 = int(time.time() * 1000)
                try:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            t1 = int(time.time() * 1000)
                            server_time = data.get("serverTime", t1)
                            one_way_latency = max(0, (t1 - t0) // 2)
                            offset = server_time - (t1 - one_way_latency)
                            offsets.append(offset)
                except Exception:
                    pass
                await asyncio.sleep(0.05)

            if offsets:
                offsets.sort()
                self._time_offset_ms = offsets[len(offsets) // 2]
                logger.info(f"Binance server time synced via /api/v3/time (median of {len(offsets)}). Offset: {self._time_offset_ms}ms")
                return

            # Fallback to fapi time if spot /api/v3/time unavailable
            fallback_url = f"{self.base_url}/fapi/v1/time"
            t0 = int(time.time() * 1000)
            async with self._session.get(fallback_url, timeout=aiohttp.ClientTimeout(total=2.0)) as fresp:
                if fresp.status == 200:
                    fdata = await fresp.json()
                    t1 = int(time.time() * 1000)
                    server_time = fdata.get("serverTime", t1)
                    one_way_latency = max(0, (t1 - t0) // 2)
                    self._time_offset_ms = server_time - (t1 - one_way_latency)
                    logger.info(f"Binance server time synced via /fapi/v1/time. Offset: {self._time_offset_ms}ms")
                    return
        except Exception as e:
            logger.warning(f"Could not sync Binance server time ({e}). Using existing offset {self._time_offset_ms}ms.")

    def _get_timestamp(self) -> int:
        """
        Get calibrated timestamp in milliseconds with safety buffer.
        Binance permits: serverTime - recvWindow <= timestamp <= serverTime + 1000ms.
        Subtracting a 500ms safety buffer guarantees timestamp is NEVER ahead of server time,
        while safely residing well within standard recvWindow.
        """
        return int(time.time() * 1000) + self._time_offset_ms - 500

    def _sign_payload(self, params: Dict[str, Any]) -> str:
        """Generate HMAC-SHA256 signature for Binance request parameters."""
        query_str = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{query_str}&signature={signature}"

    def _market_data_failure(self, state, reason, http_status=None, api_code=None, delay=5):
        self._prediction_retry_at = time.monotonic() + delay
        self.oracle_sync_status.update(
            state=state, reason=reason, http_status=http_status, api_code=api_code,
            retry_at=time.time() + delay,
        )
        # Never log signed URLs, API keys, or an untrusted response message.
        logger.warning("[ORACLE SYNC] %s (HTTP=%s, code=%s); retry in %ss",
                       reason, http_status, api_code, delay)

    async def _request_prediction_market(self, path, parameters):
        if time.monotonic() < self._prediction_retry_at:
            return None
        if not self.api_key or not self.api_secret:
            self._market_data_failure("API_AUTH_ERROR", "Binance market data requires a configured API key and secret", delay=60)
            return None
        if self._session is None or self._session.closed:
            await self.start()
        host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        for attempt in range(2):
            query = {**parameters, "recvWindow": self.recv_window, "timestamp": self._get_timestamp()}
            url = f"{host}{path}?{self._sign_payload(query)}"
            try:
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=6.0)) as response:
                    status = response.status
                    retry_after = response.headers.get("Retry-After", "60")
                    try:
                        data = await response.json()
                    except (ValueError, aiohttp.ContentTypeError):
                        data = None
                code = data.get("code") if isinstance(data, dict) else None
                if code == -1021 and attempt == 0:
                    await self.sync_server_time()
                    continue
                if status == 200 and isinstance(data, dict) and code in (None, 0, "000000", "0"):
                    payload = data.get("data", data)
                    if isinstance(payload, dict):
                        if time.monotonic() >= self._prediction_retry_at:
                            self.oracle_sync_status.update(state="SYNCING", reason="Fetching current Binance oracle rounds",
                                                          http_status=200, api_code=None, retry_at=None)
                        return payload
                if status in (401, 403) or code in (-2014, -2015, -1022):
                    self._market_data_failure("API_AUTH_ERROR", "Binance rejected market data access; check API key, permissions and IP allowlist", status, code, 60)
                elif status in (418, 429):
                    try:
                        delay = max(60, min(86400, int(retry_after)))
                    except (TypeError, ValueError):
                        delay = 60
                    self._market_data_failure("RATE_LIMITED", "Binance market data rate limit reached", status, code, delay)
                else:
                    self._market_data_failure("API_ERROR", "Binance market data request failed or returned an invalid response", status, code)
                return None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                self._market_data_failure("NETWORK_ERROR", "Could not reach Binance prediction market data")
                return None
        return None

    async def _fetch_market_page(self, offset: int = 0, limit: int = 100) -> Tuple[List[Dict[str, Any]], int, bool]:
        data = await self._request_prediction_market(
            "/sapi/v1/w3w/wallet/prediction/market/list",
            {"offset": offset, "limit": limit, "l1Category": "crypto", "l2Category": "up-down"},
        )
        if data is None:
            return [], 0, False
        topics = data.get("marketTopics")
        if not isinstance(topics, list):
            self._market_data_failure("INVALID_RESPONSE", "Binance market list is missing marketTopics", 200)
            return [], 0, False
        try:
            total = max(0, int(data.get("total", len(topics))))
        except (TypeError, ValueError):
            self._market_data_failure("INVALID_RESPONSE", "Binance market list has invalid pagination", 200)
            return [], 0, False
        return [topic for topic in topics if isinstance(topic, dict)], total, bool(data.get("hasMore", False))

    async def _hydrate_oracle_details(self, topics):
        now_ms = time.time() * 1000
        # Fixed oracle values are reusable only within the same dated topic.
        self._prediction_detail_cache = {
            identifier: detail for identifier, detail in self._prediction_detail_cache.items()
            if (window := round_window(detail)) and window[0] <= now_ms < window[1]
        }
        candidates = {}
        for topic in topics:
            window = round_window(topic)
            if not _is_crypto_up_down_topic(topic) or not window or not window[0] <= now_ms < window[1]:
                continue
            identifier = topic_id(topic)
            if not identifier.isdigit() or int(identifier) <= 0 or start_price(topic) is not None:
                continue
            symbol = _prediction_topic_symbol(topic)
            if not symbol:
                continue
            timeframe = self._oracle_timeframe(topic)
            if not timeframe:
                continue
            key = (symbol, timeframe)
            previous = candidates.get(key)
            if previous is None or window[0] > round_window(previous)[0]:
                candidates[key] = topic
        # Bound both requests in flight and the active detail universe.
        semaphore = asyncio.Semaphore(4)
        async def hydrate(topic):
            identifier = topic_id(topic)
            async with semaphore:
                detail = self._prediction_detail_cache.get(identifier)
                if detail is None:
                    detail = await self._request_prediction_market(
                        "/sapi/v1/w3w/wallet/prediction/market/detail", {"marketTopicId": int(identifier)},
                    )
                if not detail:
                    return
                if topic_id(detail) != identifier or round_window(detail) != round_window(topic):
                    logger.warning("[ORACLE SYNC] Discarded a market detail with mismatched topic or round")
                    return
                if _prediction_topic_symbol(detail) not in (None, _prediction_topic_symbol(topic)):
                    logger.warning("[ORACLE SYNC] Discarded a market detail with mismatched symbol")
                    return
                if start_price(detail) is None:
                    return
                self._prediction_detail_cache[identifier] = detail
                topic.update({key: value for key, value in detail.items() if value is not None})
        await asyncio.gather(*(hydrate(topic) for topic in list(candidates.values())[:96]))

    @staticmethod
    def _oracle_timeframe(topic):
        window = round_window(topic)
        if window:
            seconds = (window[1] - window[0]) / 1000
            for timeframe, period in (("5m", 300), ("15m", 900), ("1h", 3600), ("1d", 86400)):
                if abs(seconds - period) <= 1:
                    return timeframe
        return _prediction_timeframe(topic, default="")

    async def fetch_prediction_market_topics(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        # Coalesce simultaneous heartbeat and tick-triggered syncs into one fetch.
        request_started = time.monotonic()
        async with self._prediction_catalog_lock:
            now = time.time()
            cached = self._prediction_market_cache.get("marketTopics", [])
            if time.monotonic() < self._prediction_retry_at:
                return cached
            if getattr(self, "_last_catalog_fetch_completed", 0) > request_started:
                return cached
            has_expired = any(
                _is_crypto_up_down_topic(topic) and (window := round_window(topic)) and window[1] <= now * 1000
                for topic in cached
            )
            if not force_refresh and cached and not has_expired and now - self._prediction_cache_timestamp < 30:
                return cached
            first_topics, total, has_more = await self._fetch_market_page(0, 100)
            all_topics = list(first_topics)
            if has_more and total > 100:
                # Serial pages respect backoff immediately if authorization or rate limits fail.
                for offset in range(100, min(total, 2500), 100):
                    topics, _, more = await self._fetch_market_page(offset, 100)
                    all_topics.extend(topics)
                    if not more:
                        break
            if all_topics:
                await self._hydrate_oracle_details(all_topics)
                self._prediction_market_cache = {"marketTopics": all_topics}
                self._prediction_cache_timestamp = now
                confirmed = len({(detail["symbol"], detail["timeframe"]) for detail in self.get_detailed_oracle_strikes().values()})
                self.oracle_sync_status["confirmed_rounds"] = confirmed
                if time.monotonic() >= self._prediction_retry_at:
                    self.oracle_sync_status.update(
                        state="READY" if confirmed else "WAITING_FOR_PRICE",
                        reason="Current Binance oracle prices received" if confirmed else "No confirmed startPrice for current rounds",
                        last_success_at=time.time(), retry_at=None,
                    )
            elif self.oracle_sync_status["state"] == "SYNCING":
                self.oracle_sync_status.update(state="WAITING_FOR_MARKET", reason="Binance returned no crypto market topics", confirmed_rounds=0)
            self._last_catalog_fetch_completed = time.monotonic()
            return self._prediction_market_cache.get("marketTopics", [])

    def get_supported_prediction_symbols(self) -> Set[str]:
        """
        Return pairs discovered in Binance's crypto Up/Down market catalog.
        An empty catalog yields an empty set so live trading fails closed.
        """
        symbols: Set[str] = set()
        topics = self._prediction_market_cache.get("marketTopics", []) if self._prediction_market_cache else []
        for t in topics:
            if not _is_crypto_up_down_topic(t):
                continue
            symbol = _prediction_topic_symbol(t)
            if symbol:
                symbols.add(symbol)
        return symbols

    def get_oracle_sync_status(self) -> Dict[str, Any]:
        status = dict(self.oracle_sync_status)
        status["confirmed_rounds"] = len({
            (detail["symbol"], detail["timeframe"])
            for detail in self.get_detailed_oracle_strikes().values()
        })
        if status["state"] == "READY" and not status["confirmed_rounds"]:
            status.update(state="WAITING_FOR_PRICE", reason="Waiting for confirmed prices for the new round")
        return status

    def get_market_start_prices(self, now_ts: Optional[float] = None) -> Dict[str, float]:
        """
        Extract official Chainlink Price to Beat (startPrice) for each active binary prediction market
        from the cached marketTopics catalog.
        Excludes expired rounds to ensure stale strikes from previous rounds are never reused.
        """
        prices: Dict[str, float] = {}
        details = self.get_detailed_oracle_strikes(now_ts=now_ts)
        for key, detail in details.items():
            start_price = float(detail.get("start_price", 0.0) or 0.0)
            if start_price <= 0:
                continue
            if key == f"{detail.get('symbol')}-{detail.get('timeframe')}":
                prices[key] = start_price
            elif key in detail.get("market_ids", []):
                prices[key] = start_price
        return prices

    def get_detailed_oracle_strikes(self, now_ts: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        """
        Extract detailed official Chainlink Price to Beat (startPrice) with round timing boundaries
        (startDate, endDate) for active prediction markets.
        Keyed by f"{symbol}-{tf}" and marketId.
        """
        details: Dict[str, Dict[str, Any]] = {}
        symbol_rounds: Dict[str, Dict[str, Any]] = {}
        topics = self._prediction_market_cache.get("marketTopics", []) if self._prediction_market_cache else []
        now_ms = (time.time() if now_ts is None else now_ts) * 1000.0

        for t in topics:
            if not _is_crypto_up_down_topic(t):
                continue
            window = round_window(t)
            if window is None or not window[0] <= now_ms < window[1]:
                continue
            start_date, end_date = window
            start_price_value = start_price(t)
            if start_price_value is None:
                continue

            sym = _prediction_topic_symbol(t)
            if not sym:
                continue

            tf = self._oracle_timeframe(t)
            if not tf:
                continue

            markets = t.get("markets")
            if not isinstance(markets, list):
                markets = []
            market_ids = [str(m.get("marketId")) for m in markets if isinstance(m, dict) and m.get("marketId")]
            info = {
                "symbol": sym,
                "timeframe": tf,
                "start_price": start_price_value,
                "start_time_sec": (start_date / 1000.0) if start_date else 0.0,
                "end_time_sec": (end_date / 1000.0) if end_date else 0.0,
                "market_ids": market_ids,
                "topic_id": topic_id(t),
            }
            if sym:
                key = f"{sym}-{tf}"
                previous = symbol_rounds.get(key)
                if previous is None or info["start_time_sec"] > previous["start_time_sec"]:
                    symbol_rounds[key] = info
                for m_id in market_ids:
                    details[m_id] = info
        details.update(symbol_rounds)
        return details

    async def get_prediction_quote(
        self,
        token_id: str,
        amount_wei: str,
        side: str = "BUY",
        order_type: str = "MARKET",
        slippage_bps: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """Get execution quote from Binance Prediction Trading API."""
        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "amountIn": amount_wei,
            "orderType": order_type,
            "side": side,
            "slippageBps": slippage_bps,
            "tokenId": token_id,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        if self._wallet_address:
            query_params["walletAddress"] = self._wallet_address
        if self._wallet_id:
            query_params["walletId"] = self._wallet_id

        signed_query = self._sign_payload(query_params)
        url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/trade/get-quote?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.post(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data
                elif data.get("code") == -1021 or "ahead of the server's time" in str(data.get("msg", "")).lower():
                    logger.warning("Detected time drift (-1021) in get_prediction_quote. Re-syncing Binance time and retrying quote...")
                    await self.sync_server_time()
                    query_params["timestamp"] = self._get_timestamp()
                    signed_query = self._sign_payload(query_params)
                    retry_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/trade/get-quote?{signed_query}"
                    async with self._session.post(retry_url, timeout=aiohttp.ClientTimeout(total=5.0)) as r_resp:
                        return await r_resp.json()
                else:
                    logger.warning(f"Failed to get prediction quote: HTTP {resp.status} - {data}")
                    return data
        except Exception as e:
            logger.error(f"Exception requesting prediction quote: {e}")
            return None

    @staticmethod
    def _extract_prediction_quote_price(quote_data: Optional[Dict[str, Any]]) -> Optional[float]:
        """Normalize Binance's direct price or amountIn/amountOut quote representation."""
        if not quote_data:
            return None
        try:
            if quote_data.get("price") is not None:
                price = float(quote_data["price"])
            elif quote_data.get("amountIn") is not None and quote_data.get("amountOut") is not None:
                amount_in = float(quote_data["amountIn"])
                amount_out = float(quote_data["amountOut"])
                price = amount_in / amount_out if amount_out > 0 else math.nan
            else:
                return None
        except (ValueError, TypeError, ZeroDivisionError):
            return None
        return price if math.isfinite(price) and 0.0 < price <= 1.0 else None

    async def get_prediction_quote_snapshot(
        self,
        symbol: str,
        timeframe: str,
        estimated_cost_usdt: float = 1.5,
    ) -> Optional[Dict[str, Any]]:
        """Fetch fresh, executable BUY quotes for both outcomes of the active market.

        These are per-outcome execution prices, not order-book midpoints. Live callers
        must fail closed if either side cannot be quoted. Paper mode returns an explicitly
        marked simulation price and never represents it as exchange market data.
        """
        if self.paper_trading:
            return {
                "up_ask": 0.50,
                "down_ask": 0.50,
                "timestamp": time.time(),
                "source": "paper_simulation",
                "quote_ids": {},
            }
        if not (self.api_key.strip() and self.api_secret.strip()):
            return None

        topics = await self.fetch_prediction_market_topics()
        now_ms = time.time() * 1000.0
        clean_symbol = symbol.upper().strip()
        clean_tf = timeframe.lower().strip()

        candidates: List[Dict[str, Any]] = []
        for topic in topics:
            if not _is_crypto_up_down_topic(topic):
                continue
            title = str(topic.get("title", ""))
            if _prediction_topic_symbol(topic) != clean_symbol:
                continue
            if not re.search(rf"\b{re.escape(clean_tf)}\b", title, re.IGNORECASE):
                continue
            try:
                start_ms = float(topic.get("startDate", 0) or 0)
                end_ms = float(topic.get("endDate", 0) or 0)
            except (ValueError, TypeError):
                continue
            if (start_ms and now_ms < start_ms) or (end_ms and now_ms >= end_ms):
                continue
            candidates.append(topic)

        # Prefer the most recently started active round if the catalog briefly overlaps.
        candidates.sort(key=lambda item: float(item.get("startDate", 0) or 0), reverse=True)
        for topic in candidates:
            token_ids: Dict[str, str] = {}
            for market in topic.get("markets", []):
                if str(market.get("tradingStatus", "OPEN")).upper() not in {"OPEN", "REGISTERED"}:
                    continue
                for outcome in market.get("outcomes", []):
                    name = str(outcome.get("name", "")).strip().lower()
                    token_id = str(outcome.get("tokenId", "")).strip()
                    if token_id and name in {"up", "yes"}:
                        token_ids["up"] = token_id
                    elif token_id and name in {"down", "no"}:
                        token_ids["down"] = token_id
                if "up" in token_ids and "down" in token_ids:
                    break
            if not {"up", "down"}.issubset(token_ids):
                continue

            amount_wei = str(int(max(1.5, estimated_cost_usdt) * 10**18))
            up_task = self.get_prediction_quote(
                token_id=token_ids["up"], amount_wei=amount_wei, side="BUY",
                order_type="MARKET", slippage_bps=self.slippage_bps,
            )
            down_task = self.get_prediction_quote(
                token_id=token_ids["down"], amount_wei=amount_wei, side="BUY",
                order_type="MARKET", slippage_bps=self.slippage_bps,
            )
            up_data, down_data = await asyncio.gather(up_task, down_task, return_exceptions=False)
            up_price = self._extract_prediction_quote_price(up_data)
            down_price = self._extract_prediction_quote_price(down_data)
            if (
                up_price is None or down_price is None
                or not up_data or not down_data
                or not up_data.get("quoteId") or not down_data.get("quoteId")
            ):
                continue
            return {
                "up_ask": up_price,
                "down_ask": down_price,
                "timestamp": time.time(),
                "source": "binance_prediction_buy_quote",
                "quote_ids": {
                    "up": str(up_data["quoteId"]),
                    "down": str(down_data["quoteId"]),
                },
                "estimated_cost_usdt": max(1.5, estimated_cost_usdt),
                "market_topic_id": str(topic.get("topicId", "")),
            }
        return None

    async def _verify_live_order_status(self, order_id: str, timeout_seconds: float = 3.5) -> Dict[str, Any]:
        """
        Poll Binance Prediction order history to verify if an order was actually FILLED or FAILED.
        Prevents ghost positions when FOK orders fail during matching engine processing.
        """
        if self.paper_trading or not order_id:
            return {"status": "FILLED", "order": None}

        deadline = time.time() + max(timeout_seconds, 8.0)
        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url

        while time.time() < deadline:
            params = {
                "recvWindow": self.recv_window,
                "timestamp": self._get_timestamp(),
                "limit": 10,
            }
            if self._wallet_address:
                params["walletAddress"] = self._wallet_address
            if self._wallet_id:
                params["walletId"] = self._wallet_id
            sq = self._sign_payload(params)
            url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/order/history?{sq}"
            try:
                assert self._session is not None
                async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                    if resp.status == 200:
                        d = await resp.json()
                        for o in d.get("orders", []):
                            if str(o.get("orderId")) == str(order_id):
                                st = str(o.get("status", "")).upper()
                                if st in ("FILLED", "FAILED", "CANCELED", "REJECTED"):
                                    return {"status": st, "order": o, "errorMessage": o.get("errorMessage", "")}
            except Exception as e:
                logger.debug(f"Exception verifying order status {order_id}: {e}")
            await asyncio.sleep(0.6)

        return {"status": "UNKNOWN", "order": None}

    async def _process_dispatched_live_order(
        self,
        order_id: str,
        client_order_id: str,
        market_id: str,
        symbol: str,
        side: str,
        clean_side: str,
        contracts: int,
        target_price: float,
        strike_price: float,
        spot_price: float,
        timeframe: str,
        martingale_step: int,
        stage: str,
        token_id: Optional[str],
        elapsed_ms: float,
    ) -> OrderResult:
        """
        Verify order execution status on Binance matching engine before opening position.
        Prevents ghost positions when Binance FOK orders are killed/rejected.
        """
        order_check = await self._verify_live_order_status(order_id)
        check_status = order_check.get("status")

        if check_status in ("FAILED", "CANCELED", "REJECTED"):
            err_msg = order_check.get("errorMessage") or f"Order failed on Binance matching engine ({check_status})"
            logger.error(
                f"[BINANCE LIVE REJECTION] Order {order_id} ({client_order_id}) on {market_id} FAILED: {err_msg}. "
                f"Position NOT opened."
            )
            result = OrderResult(
                order_id=order_id,
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                price=target_price,
                status="REJECTED",
                latency_ms=round(elapsed_ms, 2),
                timeframe=timeframe,
                martingale_step=martingale_step,
                stage=stage,
                error_message=err_msg,
            )
            self._total_orders_dispatched += 1
            self._order_history.append(result)
            self._in_flight_orders.pop(client_order_id, None)
            return result

        # Strict FILLED-only validation: If status is not explicitly FILLED, verify against ongoing positions
        if check_status != "FILLED":
            ongoing_positions, _, _ = await self.fetch_ongoing_prediction_positions(limit=10)
            matched_ongoing = None
            if token_id:
                for op in ongoing_positions:
                    if str(op.get("tokenId", "")).strip() == str(token_id).strip():
                        matched_ongoing = op
                        break

            if not matched_ongoing:
                err_msg = order_check.get("errorMessage") or f"Order {order_id} unconfirmed on Binance matching engine (status: {check_status})"
                logger.error(
                    f"[BINANCE UNCONFIRMED ORDER] Order {order_id} ({client_order_id}) on {market_id} was not confirmed as FILLED on Binance. "
                    f"Position NOT opened. Ghost position prevented."
                )
                result = OrderResult(
                    order_id=order_id,
                    client_order_id=client_order_id,
                    market_id=market_id,
                    symbol=symbol,
                    side=side,
                    contracts=contracts,
                    price=target_price,
                    status="REJECTED",
                    latency_ms=round(elapsed_ms, 2),
                    timeframe=timeframe,
                    martingale_step=martingale_step,
                    stage=stage,
                    error_message=err_msg,
                )
                self._total_orders_dispatched += 1
                self._order_history.append(result)
                self._in_flight_orders.pop(client_order_id, None)
                return result

            # Confirmed present in Binance ongoing positions!
            o_data = matched_ongoing
            filled_shares = float(matched_ongoing.get("shares", 0.0) or contracts)
            actual_price = float(matched_ongoing.get("avgPrice", 0.0) or target_price)
        else:
            o_data = order_check.get("order") or {}
            filled_shares = float(o_data.get("filledShareQty", 0.0) or contracts)
            actual_price = float(o_data.get("price", 0.0) or target_price)

        result = OrderResult(
            order_id=order_id,
            client_order_id=client_order_id,
            market_id=market_id,
            symbol=symbol,
            side=side,
            contracts=int(filled_shares) if filled_shares >= 1 else contracts,
            price=actual_price,
            status="FILLED",
            latency_ms=round(elapsed_ms, 2),
            timeframe=timeframe,
            martingale_step=martingale_step,
            stage=stage,
        )
        self._total_orders_dispatched += 1
        self._total_fills += 1
        self._order_history.append(result)
        self._in_flight_orders.pop(client_order_id, None)

        pos_id = f"POS_LIVE_{market_id}_{clean_side}_{uuid.uuid4().hex[:4]}"
        self._live_positions[pos_id] = PositionInfo(
            position_id=pos_id,
            market_id=market_id,
            symbol=symbol,
            side=clean_side,
            contracts=int(filled_shares) if filled_shares >= 1 else contracts,
            entry_price=actual_price,
            current_price=spot_price if spot_price > 0 else actual_price,
            target_price=strike_price if strike_price > 0 else actual_price,
            timeframe=timeframe,
            unrealized_pnl=0.0,
            martingale_step=martingale_step,
            stage=stage,
            token_id=str(token_id or ""),
        )

        logger.info(
            f"LIVE PREDICTION ORDER CONFIRMED FILLED: {side} {result.contracts}x on {market_id} [{stage}] "
            f"@ {actual_price:.3f} (Latency: {elapsed_ms:.1f}ms)"
        )
        return result

    async def place_prediction_order(
        self,
        market_id: str,
        symbol: str,
        side: str,  # "BUY_YES" or "BUY_NO" / "UP" or "DOWN"
        contracts: int,
        target_price: float,
        strike_price: float = 0.0,
        spot_price: float = 0.0,
        martingale_step: int = 0,
        stage: str = "ไม้ 1 (Base)",
        timeframe: str = "5m",
        pre_verified_quote: Optional[Dict[str, Any]] = None,
    ) -> OrderResult:
        """
        Execute an order on Binance Prediction Markets.
        Paper simulation is available only when explicitly enabled. A live-mode client
        without credentials fails closed instead of silently simulating an order.
        """
        start_time = time.perf_counter()
        client_order_id = f"JEV_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"

        # --- Paper Trading Simulation Mode ---
        if self.paper_trading:
            return await self._simulate_order_execution(
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                target_price=target_price,
                strike_price=strike_price,
                spot_price=spot_price,
                client_order_id=client_order_id,
                start_time=start_time,
                martingale_step=martingale_step,
                stage=stage,
            )

        if not (self.api_key.strip() and self.api_secret.strip()):
            result = OrderResult(
                order_id="REJECTED",
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                price=target_price,
                status="REJECTED",
                latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                timeframe=timeframe,
                martingale_step=martingale_step,
                stage=stage,
                error_message="Live trading requires both Binance API credentials",
            )
            self._total_orders_dispatched += 1
            self._order_history.append(result)
            return result

        # --- Live Execution Mode ---
        if self._session is None or self._session.closed:
            await self.start()

        # Map side to Binance prediction contract outcome
        prediction_choice = "UP" if ("UP" in side.upper() or "YES" in side.upper()) else "DOWN"
        clean_side = "UP" if prediction_choice == "UP" else "DOWN"

        # 1. Resolve active prediction market topic and outcome token ID
        topics = await self.fetch_prediction_market_topics()
        token_id: Optional[str] = None
        clean_tf = str(timeframe).lower().strip()
        if not clean_tf or clean_tf == "5m":
            for candidate in ["15m", "15M", "1h", "1H", "1d", "1D", "5m", "5M"]:
                if f"-{candidate.upper()}-" in market_id.upper() or f"-{candidate.lower()}-" in market_id.lower():
                    clean_tf = candidate.lower()
                    break

        clean_sym = symbol.upper().strip()

        # Validate that symbol is offered on Binance Prediction Markets in live mode
        supported_syms = self.get_supported_prediction_symbols()
        if not self.paper_trading and clean_sym not in supported_syms:
            err_msg = (
                f"Asset {symbol} has no binary prediction contracts on Binance. "
                f"Actively supported prediction pairs: {', '.join(sorted(supported_syms))}."
            )
            logger.warning(f"[BINANCE LIVE REJECTION] {err_msg}")
            result = OrderResult(
                order_id="FAILED",
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                price=target_price,
                status="REJECTED",
                latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                timeframe=clean_tf,
                martingale_step=martingale_step,
                stage=stage,
                error_message=err_msg
            )
            self._total_orders_dispatched += 1
            self._order_history.append(result)
            return result

        quote_id = None
        quoted_price: Optional[float] = None
        quote_data = None

        # Check if pre-verified quote from pre-flight gate is provided
        if pre_verified_quote and pre_verified_quote.get("quote_id"):
            quote_id = pre_verified_quote["quote_id"]
            quoted_price = pre_verified_quote.get("quoted_price")
            token_id = pre_verified_quote.get("token_id")
            quote_data = {"quoteId": quote_id, "price": quoted_price}
        else:
            def _find_token(topic_list: List[Dict[str, Any]]) -> Optional[str]:
                import re
                tf_pattern = rf"\b{clean_tf}\b"
                candidates = []

                for t in topic_list:
                    if not _is_crypto_up_down_topic(t):
                        continue
                    t_title = str(t.get("title", ""))

                    # Resolve exact pair identity so similarly named tokens cannot collide.
                    if _prediction_topic_symbol(t) != clean_sym:
                        continue

                    # Match timeframe precisely (e.g. 5m, 15m, 1h, 1d)
                    tf_match = bool(re.search(tf_pattern, t_title, re.IGNORECASE))
                    if not tf_match:
                        continue

                    for m in t.get("markets", []):
                        for out in m.get("outcomes", []):
                            out_name = str(out.get("name", "")).strip().lower()
                            is_side_match = (
                                (prediction_choice == "UP" and out_name in ("up", "yes")) or
                                (prediction_choice == "DOWN" and out_name in ("down", "no"))
                            )
                            tid = str(out.get("tokenId", "")).strip()
                            if is_side_match and tid:
                                candidates.append((m, tid))

                if candidates:
                    for m, tid in candidates:
                        if m.get("status") in ("REGISTERED", "OPEN"):
                            return tid
                    return candidates[0][1]
                return None

            token_id = _find_token(topics)
            if not token_id:
                topics = await self.fetch_prediction_market_topics(force_refresh=True)
                token_id = _find_token(topics)

            # 2. Inquire Quote: Binance Market orders require >= ~1.5 USDT in wei (18 decimals)
            order_cost_usdt = max(1.5, float(contracts * target_price))
            amount_wei = str(int(order_cost_usdt * 10**18))

            if token_id:
                quote_data = await self.get_prediction_quote(
                    token_id=token_id,
                    amount_wei=amount_wei,
                    side="BUY",
                    order_type="MARKET",
                    slippage_bps=self.slippage_bps,
                )

            quote_id = quote_data.get("quoteId") if quote_data else None

            # Auto-retry with smaller amount if Binance reports liquidity threshold issue
            if not quote_id and quote_data and quote_data.get("code") == -9000 and "smaller amount" in str(quote_data.get("msg", "")).lower():
                logger.info("Retrying quote with smaller amount (1.0 USDT) due to thin order book liquidity...")
                quote_data = await self.get_prediction_quote(
                    token_id=token_id,
                    amount_wei=str(int(1.0 * 10**18)),
                    side="BUY",
                    order_type="MARKET",
                    slippage_bps=self.slippage_bps,
                )
                quote_id = quote_data.get("quoteId") if quote_data else None

        if not quote_id:
            err_msg = quote_data.get("msg") if quote_data else f"Token not found for {symbol} {prediction_choice}"
            logger.error(f"[BINANCE LIVE REJECTION] Order {client_order_id} on {market_id} failed quote inquiry: {err_msg}")
            result = OrderResult(
                order_id="FAILED",
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                price=target_price,
                status="REJECTED",
                latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                timeframe=clean_tf,
                martingale_step=martingale_step,
                stage=stage,
                error_message=f"Quote error: {err_msg}"
            )
            self._total_orders_dispatched += 1
            self._order_history.append(result)
            return result
        # 2.5. Quote Price Guard: Inspect actual execution price offered in the Binance quote
        quoted_price: Optional[float] = None
        if quote_data:
            if "price" in quote_data:
                try:
                    quoted_price = float(quote_data["price"])
                except (ValueError, TypeError):
                    pass
            elif "amountOut" in quote_data and "amountIn" in quote_data:
                try:
                    amt_in = float(quote_data["amountIn"]) / 10**18
                    amt_out = float(quote_data["amountOut"]) / 10**18
                    if amt_out > 0:
                        quoted_price = amt_in / amt_out
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        if quoted_price is not None:
            if quoted_price > self.max_odds_cap:
                payout_pct = ((1.0 - quoted_price) / max(0.001, quoted_price)) * 100.0
                logger.warning(
                    f"[BINANCE LIVE REJECTION] Order {client_order_id} on {market_id} BLOCKED BY QUOTE GUARD: "
                    f"Quoted price ${quoted_price:.3f} > max cap ${self.max_odds_cap:.2f} "
                    f"(Win profit would only be +{payout_pct:.1f}% vs -100% loss risk). Aborting order to protect capital!"
                )
                result = OrderResult(
                    order_id="REJECTED_QUOTE_CAP",
                    client_order_id=client_order_id,
                    market_id=market_id,
                    symbol=symbol,
                    side=side,
                    contracts=contracts,
                    price=quoted_price,
                    status="REJECTED",
                    latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                    timeframe=clean_tf,
                    martingale_step=martingale_step,
                    stage=stage,
                    error_message=f"Quoted price ${quoted_price:.3f} exceeds max cap ${self.max_odds_cap:.2f} (Potential win: +{payout_pct:.1f}%)"
                )
                self._total_orders_dispatched += 1
                self._order_history.append(result)
                return result

            if quoted_price < self.min_odds_floor:
                logger.warning(
                    f"[BINANCE LIVE REJECTION] Order {client_order_id} on {market_id} BLOCKED BY QUOTE GUARD: "
                    f"Quoted price ${quoted_price:.3f} < min floor ${self.min_odds_floor:.2f}. Aborting underdog order!"
                )
                result = OrderResult(
                    order_id="REJECTED_QUOTE_FLOOR",
                    client_order_id=client_order_id,
                    market_id=market_id,
                    symbol=symbol,
                    side=side,
                    contracts=contracts,
                    price=quoted_price,
                    status="REJECTED",
                    latency_ms=round((time.perf_counter() - start_time) * 1000.0, 2),
                    timeframe=clean_tf,
                    martingale_step=martingale_step,
                    stage=stage,
                    error_message=f"Quoted price ${quoted_price:.3f} below floor ${self.min_odds_floor:.2f}"
                )
                self._total_orders_dispatched += 1
                self._order_history.append(result)
                return result

        query_params = {
            "accountType": "SPOT",
            "fundingSource": self.funding_source or "MPC",
            "orderType": "MARKET",
            "quoteId": quote_id,
            "slippageBps": self.slippage_bps,
            "timeInForce": "FOK",
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        if self._wallet_address:
            query_params["walletAddress"] = self._wallet_address
        if self._wallet_id:
            query_params["walletId"] = self._wallet_id

        signed_query = self._sign_payload(query_params)
        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        endpoint_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle?{signed_query}"

        # Register in-flight order for idempotency and network reconciliation
        self._in_flight_orders[client_order_id] = {
            "market_id": market_id,
            "symbol": symbol,
            "side": side,
            "contracts": contracts,
            "price": target_price,
            "dispatched_at": time.time(),
            "status": "DISPATCHING",
        }

        try:
            assert self._session is not None
            async with self._session.post(endpoint_url, json={}) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                try:
                    response_data = await resp.json()
                except Exception:
                    raw_text = await resp.text()
                    response_data = {"msg": f"HTTP {resp.status}: {raw_text[:200]}"}

                if resp.status in (200, 201):
                    order_id = str(response_data.get("orderId", client_order_id))
                    return await self._process_dispatched_live_order(
                        order_id=order_id,
                        client_order_id=client_order_id,
                        market_id=market_id,
                        symbol=symbol,
                        side=side,
                        clean_side=clean_side,
                        contracts=contracts,
                        target_price=target_price,
                        strike_price=strike_price,
                        spot_price=spot_price,
                        timeframe=timeframe,
                        martingale_step=martingale_step,
                        stage=stage,
                        token_id=token_id,
                        elapsed_ms=elapsed_ms,
                    )
                else:
                    err_code = response_data.get("code")
                    err_msg = response_data.get("msg", f"HTTP {resp.status}")
                    if err_code == -1021 or "ahead of the server's time" in str(err_msg).lower():
                        logger.warning(f"[BINANCE LIVE RETRY] Detected time drift (-1021) on order {client_order_id}. Re-syncing time and retrying...")
                        await self.sync_server_time()
                        query_params["timestamp"] = self._get_timestamp()
                        signed_query = self._sign_payload(query_params)
                        retry_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle?{signed_query}"
                        async with self._session.post(retry_url, json={}) as r_resp:
                            r_elapsed = (time.perf_counter() - start_time) * 1000.0
                            try:
                                r_data = await r_resp.json()
                            except Exception:
                                r_data = {}
                            if r_resp.status in (200, 201):
                                order_id = str(r_data.get("orderId", client_order_id))
                                return await self._process_dispatched_live_order(
                                    order_id=order_id,
                                    client_order_id=client_order_id,
                                    market_id=market_id,
                                    symbol=symbol,
                                    side=side,
                                    clean_side=clean_side,
                                    contracts=contracts,
                                    target_price=target_price,
                                    strike_price=strike_price,
                                    spot_price=spot_price,
                                    timeframe=timeframe,
                                    martingale_step=martingale_step,
                                    stage=stage,
                                    token_id=token_id,
                                    elapsed_ms=r_elapsed,
                                )
                            else:
                                response_data = r_data
                                err_code = response_data.get("code")
                                err_msg = response_data.get("msg", f"HTTP {r_resp.status}")

                    if err_code == -31003:
                        full_err = "[Code -31003] SAS authorization required: กรุณาเปิดใช้งาน Secure Auto Sign (SAS) ในแอป Binance เพื่ออนุญาตให้บอทส่งคำสั่งเทรดได้อัตโนมัติ"
                    else:
                        full_err = f"[Code {err_code}] {err_msg}" if err_code is not None else str(err_msg)
                    logger.error(f"[BINANCE LIVE REJECTION] Order {client_order_id} on {market_id} rejected: {full_err}")
                    result = OrderResult(
                        order_id="FAILED",
                        client_order_id=client_order_id,
                        market_id=market_id,
                        symbol=symbol,
                        side=side,
                        contracts=contracts,
                        price=target_price,
                        status="REJECTED",
                        latency_ms=round(elapsed_ms, 2),
                        timeframe=clean_tf,
                        martingale_step=martingale_step,
                        stage=stage,
                        error_message=full_err
                    )
                    self._total_orders_dispatched += 1
                    self._order_history.append(result)
                    self._in_flight_orders.pop(client_order_id, None)
                    return result

        except (aiohttp.ClientError, asyncio.TimeoutError) as net_err:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                f"[NETWORK DISCONNECT/TIMEOUT] Order {client_order_id} failed during network dispatch: {net_err}. "
                f"Tagging order as TIMED_OUT_UNVERIFIED to avoid duplicate placement."
            )
            if client_order_id in self._in_flight_orders:
                self._in_flight_orders[client_order_id]["status"] = "TIMED_OUT"
            result = OrderResult(
                order_id="TIMEOUT",
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                price=target_price,
                status="REJECTED",
                latency_ms=round(elapsed_ms, 2),
                timeframe=clean_tf,
                martingale_step=martingale_step,
                stage=stage,
                error_message=f"Network Disconnection/Timeout: {net_err}"
            )
            self._order_history.append(result)
            return result

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Binance order dispatch exception: {e}")
            self._in_flight_orders.pop(client_order_id, None)
            result = OrderResult(
                order_id="ERROR",
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                price=target_price,
                status="REJECTED",
                latency_ms=round(elapsed_ms, 2),
                timeframe=clean_tf,
                martingale_step=martingale_step,
                stage=stage,
                error_message=str(e)
            )
            self._order_history.append(result)
            return result

    async def _simulate_order_execution(
        self,
        market_id: str,
        symbol: str,
        side: str,
        contracts: int,
        target_price: float,
        strike_price: float,
        spot_price: float,
        client_order_id: str,
        start_time: float,
        martingale_step: int = 0,
        stage: str = "ไม้ 1 (Base)",
    ) -> OrderResult:
        """Ultra-fast simulated execution engine for paper trading."""
        # Realistic network round-trip simulation (15ms - 40ms)
        await asyncio.sleep(0.02)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        clean_side = "UP" if side in ("UP", "BUY_YES") else "DOWN" if side in ("DOWN", "BUY_NO") else side

        # Extract timeframe from market_id (e.g. BTCUSDT-5M-R... -> 5m)
        tf = "15m"
        for candidate in ["5M", "15M", "1H", "1D"]:
            if f"-{candidate}-" in market_id:
                tf = candidate.lower()
                break

        # Small simulated fill slippage
        executed_price = round(target_price, 4)
        order_cost = executed_price * contracts

        # Strike price is the price to beat; spot is current underlying spot price
        beat_price = strike_price if strike_price > 0 else (spot_price if spot_price > 0 else target_price)
        current_spot = spot_price if spot_price > 0 else beat_price

        # Check mock balance
        if order_cost > self._paper_balance_usdt:
            result = OrderResult(
                order_id=f"SIM_REJ_{uuid.uuid4().hex[:6]}",
                client_order_id=client_order_id,
                market_id=market_id,
                symbol=symbol,
                side=clean_side,
                contracts=contracts,
                price=executed_price,
                status="REJECTED",
                latency_ms=round(elapsed_ms, 2),
                timeframe=tf,
                price_to_beat=beat_price,
                martingale_step=martingale_step,
                stage=stage,
                error_message="Insufficient paper balance"
            )
            self._order_history.append(result)
            return result

        # Deduct balance and open position
        self._paper_balance_usdt -= order_cost
        position_id = f"POS_{market_id}_{clean_side}_{uuid.uuid4().hex[:4]}"

        self._paper_positions[position_id] = PositionInfo(
            position_id=position_id,
            market_id=market_id,
            symbol=symbol,
            side=clean_side,
            contracts=contracts,
            entry_price=executed_price,
            current_price=current_spot,
            target_price=beat_price,
            timeframe=tf,
            unrealized_pnl=0.0,
            martingale_step=martingale_step,
            stage=stage,
            token_id=f"SIM_TOKEN_{clean_side}_{uuid.uuid4().hex[:6]}",
        )

        result = OrderResult(
            order_id=f"SIM_{uuid.uuid4().hex[:8].upper()}",
            client_order_id=client_order_id,
            market_id=market_id,
            symbol=symbol,
            side=clean_side,
            contracts=contracts,
            price=executed_price,
            status="SIMULATED",
            latency_ms=round(elapsed_ms, 2),
            timeframe=tf,
            price_to_beat=beat_price,
            martingale_step=martingale_step,
            stage=stage,
        )

        self._total_orders_dispatched += 1
        self._total_fills += 1
        self._order_history.append(result)

        logger.info(
            f"[PAPER] Order Filled: {clean_side} {contracts} contracts on {market_id} ({tf} | {stage}) "
            f"@ {executed_price:.3f} | Latency: {elapsed_ms:.1f}ms | Rem. Balance: ${self._paper_balance_usdt:.2f}"
        )
        return result

    async def settle_expired_positions(
        self,
        active_market_ids: Set[str],
        current_prices: Dict[str, float]
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Settle prediction contracts whose round has ended.
        In LIVE mode: Syncs directly with Binance official ended positions (tab=ENDED)
        to guarantee 100% authoritative WIN/LOSS outcome and PnL matching Binance.
        In PAPER mode: Evaluates deterministic binary settlement against strike price.
        """
        now = time.time()
        tf_durations = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
        total_net_pnl = 0.0
        settled_events: List[Dict[str, Any]] = []

        # ------------------------------------------------------------------
        # 1. LIVE TRADING: Settle using Binance official ended positions
        # ------------------------------------------------------------------
        if not self.paper_trading and (self.api_key and self.api_secret) and self._live_positions:
            ended_list = await self.fetch_ended_prediction_positions(limit=50)
            ended_by_token = {str(p.get("tokenId", "")).strip(): p for p in ended_list if p.get("tokenId")}
            ended_by_market = {str(p.get("marketId", "")).strip(): p for p in ended_list if p.get("marketId")}

            for pid, pos in list(self._live_positions.items()):
                max_duration = tf_durations.get(pos.timeframe.lower(), 900)
                elapsed = now - pos.entry_time
                is_round_expired = (pos.market_id not in active_market_ids) or (elapsed >= (max_duration + 5))

                p_ended = ended_by_token.get(pos.token_id) if pos.token_id else None
                if not p_ended and pos.market_id:
                    for mid_key, p_item in ended_by_market.items():
                        if mid_key and (mid_key in pos.market_id or str(pos.market_id).endswith(mid_key)):
                            p_ended = p_item
                            break

                if not p_ended and is_round_expired:
                    # Match by symbol, outcome, and timestamp proximity within 180s
                    pos_clean_sym = pos.symbol.upper().replace("USDT", "")
                    pos_clean_side = "UP" if pos.side in ("UP", "BUY_YES") else "DOWN"
                    for p_item in ended_list:
                        item_title = str(p_item.get("marketTopicTitle") or p_item.get("marketTitle") or "").upper()
                        item_side = str(p_item.get("outcomeName", "")).upper()
                        item_created = float(p_item.get("createdTime", 0)) / 1000.0 if p_item.get("createdTime") else 0
                        if pos_clean_sym in item_title and item_side == pos_clean_side:
                            if item_created > 0 and abs(item_created - pos.entry_time) < 180.0:
                                p_ended = p_item
                                break

                if p_ended:
                    # Binance has officially settled this contract!
                    self._live_positions.pop(pid, None)
                    won = (p_ended.get("isWinner") is True)
                    outcome_label = "WIN" if won else "LOSS"
                    raw_pnl = float(p_ended.get("unrealizedPnl") or p_ended.get("realizedPnl") or 0.0)
                    cost = float(p_ended.get("totalCost", 0.0) or (pos.contracts * pos.entry_price))
                    shares = float(p_ended.get("shares", 0.0) or pos.contracts)
                    realized_pnl = raw_pnl if raw_pnl != 0.0 else ((shares * 1.00 - cost) if won else -cost)
                    is_claimed = bool(won and p_ended.get("positionStatus") == "CLAIMED")
                    settled_at = float(p_ended.get("updatedTime", 0)) / 1000.0 if p_ended.get("updatedTime") else now
                    spot = current_prices.get(pos.symbol, pos.current_price if pos.current_price > 0 else pos.entry_price)

                    total_net_pnl += realized_pnl

                    # Populate official strike/settlement prices from cache if available
                    m_id_num = None
                    try:
                        m_id_num = int(p_ended.get("marketId", 0) or 0)
                    except (ValueError, TypeError):
                        pass
                    cached_strike, cached_spot = self._market_oracle_cache.get(m_id_num, (0.0, 0.0)) if m_id_num else (0.0, 0.0)
                    final_target_price = cached_strike if cached_strike > 0 else (pos.target_price if pos.target_price > 0 else 0.0)
                    final_settle_price = cached_spot if cached_spot > 0 else spot

                    closed_item = ClosedPositionInfo(
                        position_id=pos.position_id,
                        market_id=pos.market_id,
                        symbol=pos.symbol,
                        side=pos.side,
                        contracts=int(shares) if shares >= 1 else pos.contracts,
                        entry_price=float(p_ended.get("avgPrice", pos.entry_price)),
                        target_price=final_target_price,
                        settlement_price=final_settle_price,
                        timeframe=pos.timeframe,
                        result=outcome_label,
                        realized_pnl=round(realized_pnl, 2),
                        martingale_step=pos.martingale_step,
                        stage=pos.stage,
                        token_id=pos.token_id,
                        is_claimed=is_claimed,
                        entry_time=pos.entry_time,
                        settled_at=settled_at,
                    )
                    self._closed_positions.append(closed_item)
                    if len(self._closed_positions) > 100:
                        self._closed_positions.pop(0)

                    pnl_val = round(realized_pnl, 4) if abs(realized_pnl) < 0.05 else round(realized_pnl, 2)
                    settled_events.append({
                        "position_id": pos.position_id,
                        "symbol": pos.symbol,
                        "market_id": pos.market_id,
                        "side": pos.side,
                        "won": won,
                        "pnl": pnl_val,
                        "martingale_step": pos.martingale_step,
                        "stage": pos.stage,
                        "mode": "LIVE",
                        "token_id": pos.token_id,
                    })

                    self._spawn_task(self.fetch_live_balance())

                    logger.info(
                        f"[BINANCE LIVE SETTLEMENT] {pos.symbol} {pos.side} ({pos.market_id} - {pos.timeframe} | {pos.stage}) "
                        f"OFFICIALLY SETTLED BY BINANCE! Outcome: {outcome_label} (PnL: {realized_pnl:+.4f} USDT) | "
                        f"Claimed: {is_claimed}"
                    )
                elif is_round_expired:
                    # Round time has elapsed, but Binance backend is finalizing the Chainlink oracle settlement
                    if elapsed < (max_duration + 300):
                        logger.info(
                            f"[SETTLEMENT PENDING] Live contract {pos.symbol} {pos.side} on {pos.market_id} "
                            f"expired ({elapsed:.0f}s elapsed). Waiting for official Binance backend settlement publication..."
                        )
                    else:
                        # Clean up if not found after 5+ mins past expiry without guessing
                        # In live trading, NEVER fabricate WIN/LOSS or fake PnL! Discard cleanly.
                        logger.warning(
                            f"[SETTLEMENT TIMEOUT] Live contract {pos.symbol} on {pos.market_id} has no Binance settlement "
                            f"after {elapsed:.0f}s. Discarding unconfirmed/voided position without fabricating PnL."
                        )
                        self._live_positions.pop(pid, None)

        # ------------------------------------------------------------------
        # 2. PAPER TRADING: Simulated deterministic settlement
        # ------------------------------------------------------------------
        expired_paper_ids = []
        for pid, pos in list(self._paper_positions.items()):
            max_duration = tf_durations.get(pos.timeframe.lower(), 900)
            is_time_expired = (now - pos.entry_time) >= (max_duration + 5)
            if pos.market_id not in active_market_ids or is_time_expired:
                expired_paper_ids.append(pid)

        for pid in expired_paper_ids:
            pos = self._paper_positions.pop(pid)
            spot = current_prices.get(pos.symbol, pos.current_price if pos.current_price > 0 else pos.entry_price)
            cost = pos.contracts * pos.entry_price

            is_tie = abs(spot - pos.target_price) < 1e-4
            if is_tie:
                payout = pos.contracts * 0.50
                realized_pnl = payout - cost
                self._paper_balance_usdt += payout
                won = (realized_pnl >= 0)
                outcome_label = "TIE (50-50)"
            elif pos.side in ("UP", "BUY_YES"):
                won = spot > pos.target_price
                payout = pos.contracts * 1.00 if won else 0.0
                realized_pnl = payout - cost
                if won:
                    self._paper_balance_usdt += payout
                outcome_label = "WIN" if won else "LOSS"
            else:  # DOWN
                won = spot < pos.target_price
                payout = pos.contracts * 1.00 if won else 0.0
                realized_pnl = payout - cost
                if won:
                    self._paper_balance_usdt += payout
                outcome_label = "WIN" if won else "LOSS"

            total_net_pnl += realized_pnl

            closed_item = ClosedPositionInfo(
                position_id=pos.position_id,
                market_id=pos.market_id,
                symbol=pos.symbol,
                side=pos.side,
                contracts=pos.contracts,
                entry_price=pos.entry_price,
                target_price=pos.target_price,
                settlement_price=spot,
                timeframe=pos.timeframe,
                result=outcome_label,
                realized_pnl=round(realized_pnl, 2),
                martingale_step=pos.martingale_step,
                stage=pos.stage,
                token_id=pos.token_id,
                is_claimed=False,
                entry_time=pos.entry_time,
                settled_at=now,
            )
            self._closed_positions.append(closed_item)
            if len(self._closed_positions) > 100:
                self._closed_positions.pop(0)

            settled_events.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "market_id": pos.market_id,
                "side": pos.side,
                "won": won,
                "pnl": round(realized_pnl, 2),
                "martingale_step": pos.martingale_step,
                "stage": pos.stage,
                "mode": "PAPER",
                "token_id": pos.token_id,
            })

            logger.info(
                f"[PAPER SETTLEMENT] {pos.symbol} {pos.side} ({pos.market_id} - {pos.timeframe} | {pos.stage}) SETTLED! "
                f"Result: {'WIN (+$' + f'{realized_pnl:.2f})' if won else 'LOSS (-$' + f'{abs(realized_pnl):.2f})'} "
                f"(Current Spot: ${spot:.2f}, Price to Beat: ${pos.target_price:.2f})"
            )

        return round(total_net_pnl, 2), settled_events

    def update_positions_market_data(self, current_prices: Dict[str, float]) -> None:
        """Update live mark price and calculate real-time unrealized PnL for open positions."""
        active_list = list(self._live_positions.values()) if not self.paper_trading else list(self._paper_positions.values())
        for pos in active_list:
            if pos.symbol in current_prices:
                spot = current_prices[pos.symbol]
                pos.current_price = spot
                # Check binary ITM (In-The-Money) status
                if pos.side in ("UP", "BUY_YES"):
                    is_itm = spot >= pos.target_price
                else:
                    is_itm = spot < pos.target_price
                # Binary contract valuation: winning side approaches $0.98, losing side $0.02
                live_contract_val = 0.95 if is_itm else 0.05
                pos.unrealized_pnl = round((pos.contracts * live_contract_val) - (pos.contracts * pos.entry_price), 2)

    async def check_and_execute_early_take_profits(
        self,
        markets: List[Any],
    ) -> List[Dict[str, Any]]:
        """
        Check open positions and close them early if profit target is reached (e.g. odds >= take_profit_odds, default 0.82).
        Locks in gains (+50% to +80%) before expiration, eliminating the risk of late-round reversals.
        """
        if not self.enable_early_take_profit:
            return []

        market_map: Dict[str, Any] = {}
        for m in markets:
            m_id = m.market_id if hasattr(m, "market_id") else (m.get("market_id", "") if isinstance(m, dict) else "")
            if m_id:
                market_map[m_id] = m

        take_profit_events: List[Dict[str, Any]] = []
        target_positions = self._live_positions if not self.paper_trading else self._paper_positions

        for pid, pos in list(target_positions.items()):
            m = market_map.get(pos.market_id)
            if not m:
                continue

            odds_up = getattr(m, "odds_yes", 0.50) if hasattr(m, "odds_yes") else (m.get("odds_yes", 0.50) if isinstance(m, dict) else 0.50)
            odds_down = getattr(m, "odds_no", 0.50) if hasattr(m, "odds_no") else (m.get("odds_no", 0.50) if isinstance(m, dict) else 0.50)

            current_odds = odds_up if pos.side in ("UP", "BUY_YES") else odds_down

            if current_odds >= self.take_profit_odds:
                logger.info(
                    f"[EARLY TAKE-PROFIT TRIGGER] {pos.symbol} {pos.side} ({pos.market_id}) | "
                    f"Entry: ${pos.entry_price:.3f} -> Current Odds: ${current_odds:.3f} >= ${self.take_profit_odds:.2f} | "
                    f"Locking in gains ahead of expiration!"
                )

                if self.paper_trading:
                    cost = pos.contracts * pos.entry_price
                    payout = pos.contracts * current_odds
                    realized_pnl = round(payout - cost, 2)
                    self._paper_balance_usdt += payout

                    closed_item = ClosedPositionInfo(
                        position_id=pos.position_id,
                        market_id=pos.market_id,
                        symbol=pos.symbol,
                        side=pos.side,
                        contracts=pos.contracts,
                        entry_price=pos.entry_price,
                        target_price=pos.target_price,
                        settlement_price=pos.current_price,
                        timeframe=pos.timeframe,
                        result="TAKE_PROFIT",
                        realized_pnl=realized_pnl,
                        martingale_step=pos.martingale_step,
                        stage=pos.stage,
                        token_id=pos.token_id,
                        is_claimed=True,
                        entry_time=pos.entry_time,
                        settled_at=time.time(),
                    )
                    self._closed_positions.append(closed_item)
                    target_positions.pop(pid, None)

                    take_profit_events.append({
                        "position_id": pos.position_id,
                        "symbol": pos.symbol,
                        "market_id": pos.market_id,
                        "side": pos.side,
                        "won": True,
                        "pnl": realized_pnl,
                        "martingale_step": pos.martingale_step,
                        "stage": pos.stage,
                        "mode": "PAPER",
                        "token_id": pos.token_id,
                        "take_profit": True,
                    })
                else:
                    token_id = getattr(pos, "token_id", None)
                    if token_id:
                        amount_wei = str(int(pos.contracts * 10**18))
                        quote_data = await self.get_prediction_quote(
                            token_id=token_id,
                            amount_wei=amount_wei,
                            side="SELL",
                            order_type="MARKET",
                            slippage_bps=self.slippage_bps,
                        )
                        quote_id = quote_data.get("quoteId") if quote_data else None
                        if not quote_id and quote_data and quote_data.get("code") == -9000 and "exceeded your available shares" in str(quote_data.get("msg", "")).lower():
                            logger.warning(
                                f"[GHOST POSITION PURGED] 0 shares available on Binance for {pos.symbol} {pos.market_id}. "
                                f"Purging phantom position from active desk."
                            )
                            target_positions.pop(pid, None)
                            continue

                        if quote_id:
                            query_params = {
                                "accountType": "SPOT",
                                "fundingSource": self.funding_source or "MPC",
                                "orderType": "MARKET",
                                "quoteId": quote_id,
                                "slippageBps": self.slippage_bps,
                                "timeInForce": "FOK",
                                "recvWindow": self.recv_window,
                                "timestamp": self._get_timestamp(),
                            }
                            if self._wallet_address:
                                query_params["walletAddress"] = self._wallet_address
                            if self._wallet_id:
                                query_params["walletId"] = self._wallet_id
                            signed_query = self._sign_payload(query_params)
                            sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
                            endpoint_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle?{signed_query}"

                            try:
                                assert self._session is not None
                                async with self._session.post(endpoint_url, json={}) as resp:
                                    res_data = await resp.json()
                                    is_success = resp.status in (200, 201) and ("orderId" in res_data or res_data.get("code") in ("000000", 0))
                                    already_sold = res_data.get("code") == -9000 and "exceeded your available shares" in str(res_data.get("msg", "")).lower()

                                    if is_success or already_sold:
                                        cost = pos.contracts * pos.entry_price
                                        payout = pos.contracts * current_odds
                                        realized_pnl = round(payout - cost, 2)
                                        closed_item = ClosedPositionInfo(
                                            position_id=pos.position_id,
                                            market_id=pos.market_id,
                                            symbol=pos.symbol,
                                            side=pos.side,
                                            contracts=pos.contracts,
                                            entry_price=pos.entry_price,
                                            target_price=pos.target_price,
                                            settlement_price=pos.current_price,
                                            timeframe=pos.timeframe,
                                            result="TAKE_PROFIT",
                                            realized_pnl=realized_pnl,
                                            martingale_step=pos.martingale_step,
                                            stage=pos.stage,
                                            token_id=pos.token_id,
                                            is_claimed=True,
                                            entry_time=pos.entry_time,
                                            settled_at=time.time(),
                                        )
                                        self._closed_positions.append(closed_item)
                                        target_positions.pop(pid, None)

                                        take_profit_events.append({
                                            "position_id": pos.position_id,
                                            "symbol": pos.symbol,
                                            "market_id": pos.market_id,
                                            "side": pos.side,
                                            "won": True,
                                            "pnl": realized_pnl,
                                            "martingale_step": pos.martingale_step,
                                            "stage": pos.stage,
                                            "mode": "LIVE",
                                            "token_id": pos.token_id,
                                            "take_profit": True,
                                        })
                                        logger.info(f"[LIVE TAKE-PROFIT SUCCESS] Closed position {pid} at profit +${realized_pnl:.2f}!")
                                    else:
                                        logger.warning(f"[LIVE TAKE-PROFIT REJECTED] Failed sell bundle: {res_data}")
                            except Exception as ex:
                                logger.error(f"[LIVE TAKE-PROFIT ERROR] Exception executing early sell: {ex}")

        return take_profit_events

    def clear_paper_positions(self) -> int:
        """Clear lingering simulated paper positions from the active desk."""
        cleared_count = len(self._paper_positions)
        self._paper_positions.clear()
        logger.info(f"Cleared {cleared_count} simulated paper positions from active desk.")
        return cleared_count

    def get_order_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent order execution log."""
        if limit <= 0:
            return []
        recent = list(self._order_history)[-min(limit, self._order_history.maxlen or limit):]
        return [o.model_dump() for o in reversed(recent)]

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return currently held active open positions based on current trading mode."""
        now = time.time()
        tf_durations = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
        positions = self._live_positions.values() if not self.paper_trading else self._paper_positions.values()
        res = []
        for p in positions:
            p_dict = p.model_dump()
            max_duration = tf_durations.get(str(p.timeframe).lower(), 300)
            p_dict["is_settling"] = (now - p.entry_time) >= max_duration
            res.append(p_dict)
        return res

    def get_closed_positions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return historical settled/closed positions."""
        if limit <= 0:
            return []
        return [p.model_dump() for p in reversed(self._closed_positions[-limit:])]

    def get_closed_positions_page(self, offset: int = 0, limit: int = 50) -> Dict[str, Any]:
        """Serialize only the requested newest-first page."""
        total = len(self._closed_positions)
        end = max(0, total - offset)
        start = max(0, end - limit)
        return {
            "closed_positions": [p.model_dump() for p in reversed(self._closed_positions[start:end])],
            "closed_positions_total": total,
            "offset": offset,
            "limit": limit,
        }

    def get_recent_performance(self, symbol: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
        """
        Analyze recent closed positions for feedback-driven AI inference and risk scaling.
        Returns recent outcomes, win rate, consecutive losses, and streak summary.
        """
        relevant = self._closed_positions
        if symbol:
            clean_sym = symbol.upper()
            relevant = [p for p in relevant if p.symbol.upper() == clean_sym]

        recent = relevant[-limit:]
        if not recent:
            return {
                "total_rounds": 0,
                "recent_results": [],
                "win_count": 0,
                "loss_count": 0,
                "win_rate_pct": 0.0,
                "consecutive_losses": 0,
                "consecutive_wins": 0,
                "summary": "No historical rounds yet on this asset.",
            }

        results = [p.result for p in recent]
        win_count = sum(1 for r in results if r in ("WIN", "TAKE_PROFIT"))
        loss_count = sum(1 for r in results if r == "LOSS")
        win_rate = (win_count / len(results)) * 100.0 if results else 0.0

        consecutive_losses = 0
        consecutive_wins = 0
        for r in reversed(results):
            if r == "LOSS":
                if consecutive_wins > 0:
                    break
                consecutive_losses += 1
            elif r in ("WIN", "TAKE_PROFIT"):
                if consecutive_losses > 0:
                    break
                consecutive_wins += 1

        sym_label = symbol or "All Assets"
        summary = f"{sym_label} Last {len(results)} rounds: {results} | Win Rate: {win_rate:.0f}%"
        if consecutive_losses >= 2:
            summary += f" | [DRAWDOWN WARNING: {consecutive_losses} consecutive losses]"
        elif consecutive_wins >= 2:
            summary += f" | [MOMENTUM STREAK: {consecutive_wins} consecutive wins]"

        return {
            "total_rounds": len(results),
            "recent_results": results,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate_pct": round(win_rate, 1),
            "consecutive_losses": consecutive_losses,
            "consecutive_wins": consecutive_wins,
            "summary": summary,
        }

    async def fetch_live_balance(self) -> Optional[Dict[str, float]]:
        """Fetch real-time USDT wallet balance for Prediction trading."""
        if self.paper_trading:
            return None
        if self._session is None or self._session.closed:
            await self.start()
        try:
            params = {
                "recvWindow": self.recv_window,
                "timestamp": self._get_timestamp(),
            }
            signed_query = self._sign_payload(params)
            sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
            assert self._session is not None

            # 1. Check Binance Prediction Markets payment option balances (CeDeFi, SPOT, FUNDING)
            prediction_balance_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/balance/payment-options?{signed_query}"
            try:
                async with self._session.get(prediction_balance_url) as presp:
                    if presp.status == 200:
                        pdata = await presp.json()
                        items = pdata.get("items", [])
                        total_pred_bal = 0.0
                        cedefi_bal = 0.0
                        for item in items:
                            if item.get("enabled", True):
                                try:
                                    val = float(item.get("availableBalanceDisplay", 0.0))
                                    total_pred_bal += val
                                    if item.get("accountType") == "CeDeFi":
                                        cedefi_bal += val
                                except (ValueError, TypeError):
                                    pass
                        if total_pred_bal > 0:
                            if cedefi_bal > 0:
                                self.funding_source = "MPC"
                                self._active_account_type = "CeDeFi"
                            else:
                                self._active_account_type = "SPOT"
                            self._live_balance_usdt = round(total_pred_bal, 2)
                            self._live_available_usdt = round(total_pred_bal, 2)
                            self._live_unrealized_usdt = 0.0
                            if self._live_initial_capital <= 0.0:
                                self._live_initial_capital = self._live_balance_usdt
                            return {
                                "balance": self._live_balance_usdt,
                                "available": self._live_available_usdt,
                                "unrealized": 0.0,
                            }
            except Exception as pe:
                logger.debug(f"Prediction balance query error: {pe}")

            # 2. Fallback to Spot Account API
            spot_url = f"{sapi_host}/api/v3/account?{signed_query}"
            async with self._session.get(spot_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    balances = data.get("balances", [])
                    for item in balances:
                        if item.get("asset") == "USDT":
                            free = float(item.get("free", 0.0))
                            locked = float(item.get("locked", 0.0))
                            total = free + locked
                            self._live_balance_usdt = round(total, 2)
                            self._live_available_usdt = round(free, 2)
                            self._live_unrealized_usdt = 0.0
                            if self._live_initial_capital <= 0.0 and total > 0:
                                self._live_initial_capital = self._live_balance_usdt
                            return {
                                "balance": self._live_balance_usdt,
                                "available": self._live_available_usdt,
                                "unrealized": 0.0,
                            }
                elif resp.status == 404:
                    # Fallback to Futures FAPI balance
                    fapi_url = f"{self.base_url}/fapi/v2/balance?{signed_query}"
                    async with self._session.get(fapi_url) as fresp:
                        if fresp.status == 200:
                            fdata = await fresp.json()
                            if isinstance(fdata, list):
                                for item in fdata:
                                    if item.get("asset") == "USDT":
                                        balance = float(item.get("balance", 0.0))
                                        available = float(item.get("availableBalance", 0.0))
                                        unrealized = float(item.get("crossUnPnl", 0.0))
                                        self._live_balance_usdt = round(balance, 2)
                                        self._live_available_usdt = round(available, 2)
                                        self._live_unrealized_usdt = round(unrealized, 2)
                                        if self._live_initial_capital <= 0.0 and balance > 0:
                                            self._live_initial_capital = self._live_balance_usdt
                                        return {
                                            "balance": self._live_balance_usdt,
                                            "available": self._live_available_usdt,
                                            "unrealized": self._live_unrealized_usdt,
                                        }
        except Exception as e:
            logger.warning(f"Could not fetch Binance live balance: {e}")
        return None

    def get_account_summary(self) -> Dict[str, Any]:
        """Return comprehensive wallet balances, total equity, and execution statistics."""
        if not self.paper_trading:
            total_equity = round(self._live_balance_usdt + self._live_unrealized_usdt, 2)
            available_balance = round(self._live_available_usdt, 2)
            unrealized_pnl = round(self._live_unrealized_usdt, 2)
            committed_margin = round(max(0.0, total_equity - available_balance), 2)
            realized_pnl = round(sum(p.realized_pnl for p in self._closed_positions), 2)
            if self._live_initial_capital > 0:
                total_profit = round(total_equity - self._live_initial_capital, 2)
                total_profit_pct = round((total_profit / self._live_initial_capital) * 100.0, 2)
            else:
                total_profit = 0.0
                total_profit_pct = 0.0
            open_count = len(self._live_positions)
        else:
            committed_margin = round(sum(p.contracts * p.entry_price for p in self._paper_positions.values()), 2)
            unrealized_pnl = round(sum(p.unrealized_pnl for p in self._paper_positions.values()), 2)
            available_balance = round(self._paper_balance_usdt, 2)
            total_equity = round(available_balance + committed_margin + unrealized_pnl, 2)
            realized_pnl = round(sum(p.realized_pnl for p in self._closed_positions), 2)
            initial_capital = 1000.0
            total_profit = round(total_equity - initial_capital, 2)
            total_profit_pct = round((total_profit / initial_capital) * 100.0, 2)
            open_count = len(self._paper_positions)

        return {
            "mode": (
                "PAPER_TRADING" if self.paper_trading
                else "LIVE_TRADING" if self.api_key.strip() and self.api_secret.strip()
                else "CONFIGURATION_ERROR"
            ),
            "balance_usdt": available_balance,
            "available_balance": available_balance,
            "total_equity": total_equity,
            "committed_margin": committed_margin,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_profit": total_profit,
            "total_profit_pct": total_profit_pct,
            "open_positions_count": open_count,
            "total_orders": self._total_orders_dispatched,
            "total_fills": self._total_fills,
            "time_offset_ms": self._time_offset_ms,
        }

    async def fetch_claimable_positions(self) -> List[str]:
        """Fetch pending claim outcome token IDs from Binance Prediction API."""
        if self.paper_trading:
            return []
        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "walletAddress": self._wallet_address,
            "walletId": self._wallet_id,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        signed_query = self._sign_payload(query_params)
        url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/position/list?{signed_query}"

        token_ids: List[str] = []
        try:
            assert self._session is not None
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    positions = data.get("positions", [])
                    for pos in positions:
                        token_id = str(pos.get("tokenId", "")).strip()
                        can_claim = pos.get("canClaim") is True or str(pos.get("canClaim", "")).lower() == "true"
                        try:
                            claimable_amt = float(pos.get("claimableAmount", 0.0) or 0.0)
                        except (ValueError, TypeError):
                            claimable_amt = 0.0
                        if token_id and (can_claim or claimable_amt > 0):
                            token_ids.append(token_id)
        except Exception as e:
            logger.warning(f"Could not fetch claimable positions from Binance API: {e}")
        return token_ids

    async def fetch_ongoing_prediction_positions(
        self, limit: int = 20
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        """
        Fetch official list of active/ongoing prediction positions from Binance.
        Endpoint: GET /sapi/v1/w3w/wallet/prediction/position/list?tab=ONGOING
        Returns (positions, summary, counts).
        """
        if self.paper_trading or not (self.api_key and self.api_secret):
            return [], {}, {}
        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "tab": "ONGOING",
            "limit": limit,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        if self._wallet_address:
            query_params["walletAddress"] = self._wallet_address
        if self._wallet_id:
            query_params["walletId"] = self._wallet_id

        signed_query = self._sign_payload(query_params)
        url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/position/list?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=6.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    summary = data.get("summary", {})
                    counts = data.get("counts", {})
                    if summary and summary.get("walletBalance"):
                        try:
                            wb = float(summary.get("walletBalance", 0.0))
                            if wb > 0:
                                self._live_balance_usdt = round(wb, 2)
                                self._live_available_usdt = round(wb, 2)
                        except (ValueError, TypeError):
                            pass
                    return data.get("positions", []), summary, counts
                else:
                    data = {}
                    try:
                        data = await resp.json()
                    except Exception:
                        pass
                    if data.get("code") == -1021 or "ahead of the server's time" in str(data.get("msg", "")).lower():
                        logger.warning("Detected time drift (-1021) in ongoing positions. Re-syncing time...")
                        await self.sync_server_time()
                        query_params["timestamp"] = self._get_timestamp()
                        signed_query = self._sign_payload(query_params)
                        retry_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/position/list?{signed_query}"
                        async with self._session.get(retry_url, timeout=aiohttp.ClientTimeout(total=6.0)) as r_resp:
                            if r_resp.status == 200:
                                r_data = await r_resp.json()
                                summary = r_data.get("summary", {})
                                counts = r_data.get("counts", {})
                                if summary and summary.get("walletBalance"):
                                    try:
                                        wb = float(summary.get("walletBalance", 0.0))
                                        if wb > 0:
                                            self._live_balance_usdt = round(wb, 2)
                                            self._live_available_usdt = round(wb, 2)
                                    except (ValueError, TypeError):
                                        pass
                                return r_data.get("positions", []), summary, counts
                    logger.debug(f"Failed to fetch ongoing positions: HTTP {resp.status} - {data}")
        except Exception as e:
            logger.warning(f"Exception fetching ongoing positions from Binance: {e}")
        return [], {}, {}

    async def verify_pre_flight_readiness(
        self,
        symbol: str,
        market_id: str,
        side: str,  # "UP" or "DOWN" / "BUY_YES" or "BUY_NO"
        timeframe: str = "5m",
        estimated_cost_usdt: float = 1.5,
        strike_price: float = 0.0,
        spot_price: float = 0.0,
        max_concurrent_positions: int = 5,
    ) -> PreFlightVerificationResult:
        """
        MANDATORY PRE-FLIGHT VERIFICATION PROTOCOL:
        Authoritatively confirms all required data directly from Binance before opening an order:
        1. Confirms live balance directly from Binance SAPI.
        2. Confirms active ongoing positions on Binance to prevent duplicate bets and ensure capacity.
        3. Confirms authoritative historical settlements to ensure Martingale step & PnL are up-to-date.
        4. Confirms round trading status (OPEN) and official Chainlink strike price (startPrice).
        5. Inquires live quote directly from Binance to obtain actual execution price & quoteId.
        """
        start_t = time.perf_counter()
        clean_side = "UP" if ("UP" in side.upper() or "YES" in side.upper()) else "DOWN"
        clean_sym = symbol.upper().strip()

        # Paper Trading Mode
        if self.paper_trading:
            has_dup = any(p.market_id == market_id for p in self._paper_positions.values())
            if has_dup:
                return PreFlightVerificationResult(
                    verified=False,
                    reason=f"[PAPER] Position already open on {market_id}",
                    has_duplicate_position=True,
                    live_balance_usdt=self._paper_balance_usdt,
                    active_ongoing_count=len(self._paper_positions),
                    latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
                )
            if self._paper_balance_usdt < estimated_cost_usdt:
                return PreFlightVerificationResult(
                    verified=False,
                    reason=f"[PAPER] Insufficient balance (${self._paper_balance_usdt:.2f} < ${estimated_cost_usdt:.2f})",
                    live_balance_usdt=self._paper_balance_usdt,
                    active_ongoing_count=len(self._paper_positions),
                    latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
                )
            return PreFlightVerificationResult(
                verified=True,
                reason="Paper trading pre-flight verified",
                live_balance_usdt=self._paper_balance_usdt,
                active_ongoing_count=len(self._paper_positions),
                official_strike_price=strike_price or spot_price,
                token_id="PAPER_TOKEN",
                quote_id=f"PAPER_QUOTE_{int(time.time()*1000)}",
                quoted_price=0.50,
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        if not (self.api_key.strip() and self.api_secret.strip()):
            return PreFlightVerificationResult(
                verified=False,
                reason="Live trading requires both Binance API credentials",
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        if self._session is None or self._session.closed:
            await self.start()

        # Step 1: Query Binance ongoing positions & live balance
        try:
            ongoing_positions, summary, counts = await self.fetch_ongoing_prediction_positions(limit=20)
        except Exception as ex:
            return PreFlightVerificationResult(
                verified=False,
                reason=f"Failed to query Binance ongoing positions: {ex}",
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        wallet_bal = 0.0
        if summary and summary.get("walletBalance"):
            try:
                wallet_bal = float(summary.get("walletBalance", 0.0))
            except (ValueError, TypeError):
                pass
        if wallet_bal <= 0.0:
            wallet_bal = self._live_balance_usdt

        ongoing_count = int(counts.get("ongoingCount", len(ongoing_positions)))
        local_active = len(self._live_positions)
        in_flight = len(self._in_flight_orders)
        effective_ongoing = max(ongoing_count, local_active) + in_flight

        # 1.1 Duplicate Position Check on Binance
        for p in ongoing_positions:
            p_mid = str(p.get("marketId", "")).strip()
            if p_mid and (p_mid == str(market_id) or p_mid in str(market_id)):
                return PreFlightVerificationResult(
                    verified=False,
                    reason=f"Active position already open on Binance for market {market_id}",
                    has_duplicate_position=True,
                    live_balance_usdt=wallet_bal,
                    active_ongoing_count=effective_ongoing,
                    latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
                )

        # 1.2 Max Concurrent Positions Check on Binance
        if effective_ongoing >= max_concurrent_positions:
            return PreFlightVerificationResult(
                verified=False,
                reason=f"Max concurrent positions limit reached on Binance ({effective_ongoing} >= {max_concurrent_positions})",
                live_balance_usdt=wallet_bal,
                active_ongoing_count=effective_ongoing,
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        # 1.3 Minimum Balance Check on Binance
        min_required = max(1.5, estimated_cost_usdt)
        if wallet_bal < min_required:
            return PreFlightVerificationResult(
                verified=False,
                reason=f"Insufficient Binance live balance (${wallet_bal:.2f} < ${min_required:.2f})",
                live_balance_usdt=wallet_bal,
                active_ongoing_count=ongoing_count,
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        # Step 2: Sync latest historical settlements to guarantee Martingale accuracy
        try:
            await self.sync_historical_closed_positions(limit=30)
        except Exception as sync_ex:
            logger.debug(f"Pre-flight history sync note: {sync_ex}")

        # Step 3: Binance Market Topic & Official Strike Price Check
        topics = await self.fetch_prediction_market_topics()
        clean_tf = str(timeframe).lower().strip()
        now_ms = time.time() * 1000.0

        matched_market = None
        matched_token_id = None
        official_strike = strike_price

        for t in topics:
            if not _is_crypto_up_down_topic(t):
                continue
            # Exclude expired or future rounds
            end_date = t.get("endDate")
            if end_date and now_ms >= end_date:
                continue
            start_date = t.get("startDate")
            if start_date and now_ms < start_date:
                continue

            t_title = str(t.get("title", ""))
            if _prediction_topic_symbol(t) != clean_sym:
                continue
            tf_match = bool(re.search(rf"\b{clean_tf}\b", t_title, re.IGNORECASE))
            if not tf_match:
                continue

            variant = t.get("variantData") or {}
            if variant.get("startPrice"):
                try:
                    official_strike = float(variant["startPrice"])
                except (ValueError, TypeError):
                    pass

            for m in t.get("markets", []):
                for out in m.get("outcomes", []):
                    out_name = str(out.get("name", "")).strip().lower()
                    is_side = (
                        (clean_side == "UP" and out_name in ("up", "yes")) or
                        (clean_side == "DOWN" and out_name in ("down", "no"))
                    )
                    tid = str(out.get("tokenId", "")).strip()
                    if is_side and tid:
                        matched_market = m
                        matched_token_id = tid
                        break
                if matched_token_id:
                    break
            if matched_token_id:
                break

        if not matched_token_id:
            return PreFlightVerificationResult(
                verified=False,
                reason=f"Binance prediction outcome token not found for {clean_sym} {clean_side} ({timeframe})",
                live_balance_usdt=wallet_bal,
                active_ongoing_count=ongoing_count,
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        if matched_market:
            m_trading_status = str(matched_market.get("tradingStatus", "OPEN")).upper()
            if m_trading_status not in ("OPEN", "REGISTERED"):
                return PreFlightVerificationResult(
                    verified=False,
                    reason=f"Binance market round is not OPEN (tradingStatus: {m_trading_status})",
                    live_balance_usdt=wallet_bal,
                    active_ongoing_count=ongoing_count,
                    official_strike_price=official_strike,
                    token_id=matched_token_id,
                    latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
                )

        if official_strike <= 0:
            return PreFlightVerificationResult(
                verified=False,
                reason="Binance Price to Beat (startPrice) not yet confirmed for active round",
                live_balance_usdt=wallet_bal,
                active_ongoing_count=ongoing_count,
                official_strike_price=0.0,
                token_id=matched_token_id,
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        # Step 4: Binance Live Quote Confirmation
        quote_amt = max(1.5, estimated_cost_usdt)
        amount_wei = str(int(quote_amt * 10**18))
        quote_data = await self.get_prediction_quote(
            token_id=matched_token_id,
            amount_wei=amount_wei,
            side="BUY",
            order_type="MARKET",
            slippage_bps=self.slippage_bps,
        )

        quote_id = quote_data.get("quoteId") if quote_data else None
        if not quote_id:
            err_msg = quote_data.get("msg") if quote_data else "Failed to obtain quote from Binance"
            return PreFlightVerificationResult(
                verified=False,
                reason=f"Binance quote inquiry rejected: {err_msg}",
                live_balance_usdt=wallet_bal,
                active_ongoing_count=ongoing_count,
                official_strike_price=official_strike,
                token_id=matched_token_id,
                latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
            )

        quoted_price: Optional[float] = None
        if "price" in quote_data:
            try:
                quoted_price = float(quote_data["price"])
            except (ValueError, TypeError):
                pass
        elif "amountOut" in quote_data and "amountIn" in quote_data:
            try:
                amt_in = float(quote_data["amountIn"]) / 10**18
                amt_out = float(quote_data["amountOut"]) / 10**18
                if amt_out > 0:
                    quoted_price = amt_in / amt_out
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        if quoted_price is not None:
            if quoted_price > self.max_odds_cap:
                return PreFlightVerificationResult(
                    verified=False,
                    reason=f"Binance quoted price ${quoted_price:.3f} exceeds max odds cap ${self.max_odds_cap:.2f}",
                    live_balance_usdt=wallet_bal,
                    active_ongoing_count=ongoing_count,
                    official_strike_price=official_strike,
                    token_id=matched_token_id,
                    quote_id=quote_id,
                    quoted_price=quoted_price,
                    latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
                )
            if quoted_price < self.min_odds_floor:
                return PreFlightVerificationResult(
                    verified=False,
                    reason=f"Binance quoted price ${quoted_price:.3f} below minimum odds floor ${self.min_odds_floor:.2f}",
                    live_balance_usdt=wallet_bal,
                    active_ongoing_count=ongoing_count,
                    official_strike_price=official_strike,
                    token_id=matched_token_id,
                    quote_id=quote_id,
                    quoted_price=quoted_price,
                    latency_ms=round((time.perf_counter() - start_t) * 1000.0, 2),
                )

        elapsed = round((time.perf_counter() - start_t) * 1000.0, 2)
        logger.info(
            f"[PRE-FLIGHT VERIFIED] Binance confirmed: Balance: ${wallet_bal:.2f} | Ongoing: {ongoing_count} | "
            f"Strike: ${official_strike:,.2f} | Quote: ${quoted_price:.3f} (ID: {quote_id[:10]}...) | Latency: {elapsed}ms"
        )
        return PreFlightVerificationResult(
            verified=True,
            reason="All Binance pre-flight verifications successfully passed",
            live_balance_usdt=wallet_bal,
            active_ongoing_count=ongoing_count,
            has_duplicate_position=False,
            official_strike_price=official_strike,
            token_id=matched_token_id,
            quote_id=quote_id,
            quoted_price=quoted_price,
            latency_ms=elapsed,
        )

    async def fetch_ended_prediction_positions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch official list of ended/settled prediction positions from Binance.
        Endpoint: GET /sapi/v1/w3w/wallet/prediction/position/list?tab=ENDED
        Provides authoritative isWinner boolean, finalOutcome, and realized PnL.
        """
        if self.paper_trading or not (self.api_key and self.api_secret):
            return []
        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "tab": "ENDED",
            "limit": limit,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        if self._wallet_address:
            query_params["walletAddress"] = self._wallet_address
        if self._wallet_id:
            query_params["walletId"] = self._wallet_id

        signed_query = self._sign_payload(query_params)
        url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/position/list?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=6.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    summary = data.get("summary", {})
                    if summary and summary.get("walletBalance"):
                        try:
                            wb = float(summary.get("walletBalance", 0.0))
                            if wb > 0:
                                self._live_balance_usdt = round(wb, 2)
                                self._live_available_usdt = round(wb, 2)
                        except (ValueError, TypeError):
                            pass
                    return data.get("positions", [])
                else:
                    data = {}
                    try:
                        data = await resp.json()
                    except Exception:
                        pass
                    if data.get("code") == -1021 or "ahead of the server's time" in str(data.get("msg", "")).lower():
                        logger.warning("Detected time drift (-1021) in ended positions. Re-syncing time...")
                        await self.sync_server_time()
                        query_params["timestamp"] = self._get_timestamp()
                        signed_query = self._sign_payload(query_params)
                        retry_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/position/list?{signed_query}"
                        async with self._session.get(retry_url, timeout=aiohttp.ClientTimeout(total=6.0)) as r_resp:
                            if r_resp.status == 200:
                                r_data = await r_resp.json()
                                summary = r_data.get("summary", {})
                                if summary and summary.get("walletBalance"):
                                    try:
                                        wb = float(summary.get("walletBalance", 0.0))
                                        if wb > 0:
                                            self._live_balance_usdt = round(wb, 2)
                                            self._live_available_usdt = round(wb, 2)
                                    except (ValueError, TypeError):
                                        pass
                                return r_data.get("positions", [])
                    logger.debug(f"Failed to fetch ended positions: HTTP {resp.status} - {data}")
        except Exception as e:
            logger.warning(f"Exception fetching ended positions from Binance: {e}")
        return []

    async def fetch_market_oracle_prices(self, market_topic_id: int, market_id: int) -> Tuple[float, float]:
        """
        Fetch official Chainlink Oracle strike (startPrice) and settlement (endPrice)
        for a prediction market from Binance SAPI. Caches resolved prices in-memory (0ms overhead).
        Endpoint: GET /sapi/v1/w3w/wallet/prediction/market/detail?marketTopicId={topic_id}&marketId={market_id}
        """
        if market_id in self._market_oracle_cache:
            return self._market_oracle_cache[market_id]

        if not market_topic_id or not market_id or self.paper_trading or not (self.api_key and self.api_secret):
            return 0.0, 0.0

        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "marketTopicId": market_topic_id,
            "marketId": market_id,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        signed_query = self._sign_payload(query_params)
        url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/market/detail?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.get(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vd = data.get("variantData", {})
                    if vd:
                        sp = float(vd.get("startPrice", 0.0) or 0.0)
                        ep = float(vd.get("endPrice", 0.0) or 0.0)
                        if sp > 0 or ep > 0:
                            self._market_oracle_cache[market_id] = (sp, ep)
                            return sp, ep
                elif resp.status != 404:
                    logger.debug(f"[MARKET DETAIL ORACLE] HTTP {resp.status} for market {market_id} topic {market_topic_id}")
        except Exception as e:
            logger.debug(f"[MARKET DETAIL ORACLE] Error fetching oracle prices for market {market_id}: {e}")

        return 0.0, 0.0

    async def fetch_prediction_order_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch authoritative prediction order history directly from Binance SAPI.
        Used to accurately reconstruct filled contracts and detect early take-profit sales.
        Endpoint: GET /sapi/v1/w3w/wallet/prediction/order/history
        """
        if self.paper_trading or not (self.api_key and self.api_secret):
            return []

        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
            "limit": limit,
        }
        if self._wallet_address:
            query_params["walletAddress"] = self._wallet_address
        if self._wallet_id:
            query_params["walletId"] = self._wallet_id

        signed_query = self._sign_payload(query_params)
        url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/order/history?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("orders", [])
                else:
                    data = {}
                    try:
                        data = await resp.json()
                    except Exception:
                        pass
                    if data.get("code") == -1021 or "ahead of the server's time" in str(data.get("msg", "")).lower():
                        logger.warning("Detected time drift (-1021) in order history. Re-syncing time...")
                        await self.sync_server_time()
                        query_params["timestamp"] = self._get_timestamp()
                        signed_query = self._sign_payload(query_params)
                        retry_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/order/history?{signed_query}"
                        async with self._session.get(retry_url, timeout=aiohttp.ClientTimeout(total=5.0)) as r_resp:
                            if r_resp.status == 200:
                                r_data = await r_resp.json()
                                return r_data.get("orders", [])
                    logger.debug(f"Failed to fetch prediction order history: HTTP {resp.status} - {data}")
        except Exception as e:
            logger.debug(f"Exception fetching prediction order history: {e}")
        return []

    async def sync_historical_closed_positions(self, limit: int = 50) -> int:
        """
        Reconcile and synchronize historical settled positions directly from Binance SAPI.
        Strictly reconstructs closed positions from Binance official ended positions
        to purge any ghost positions and guarantee 100% data parity.
        """
        if self.paper_trading or not (self.api_key and self.api_secret):
            return 0

        # Concurrently fetch ended positions and order history for full fidelity
        ended_res, orders_res = await asyncio.gather(
            self.fetch_ended_prediction_positions(limit=limit),
            self.fetch_prediction_order_history(limit=limit),
            return_exceptions=True,
        )
        ended_list = ended_res if isinstance(ended_res, list) else []
        raw_orders = orders_res if isinstance(orders_res, list) else []

        # Index filled orders by marketId to reconstruct exact bought shares & early take-profit sales
        orders_by_market: Dict[int, List[Dict[str, Any]]] = {}
        for o in raw_orders:
            mid = o.get("marketId")
            if mid:
                try:
                    orders_by_market.setdefault(int(mid), []).append(o)
                except (ValueError, TypeError):
                    pass

        # Concurrently resolve official Oracle strike and settlement prices for unique markets not yet in cache
        missing_markets = {}
        for item in (ended_list or []):
            mid = item.get("marketId")
            tid = item.get("marketTopicId")
            if mid and tid:
                try:
                    mid_int = int(mid)
                    tid_int = int(tid)
                    if mid_int not in self._market_oracle_cache:
                        missing_markets[mid_int] = tid_int
                except (ValueError, TypeError):
                    pass

        for o in (raw_orders or []):
            mid = o.get("marketId")
            tid = o.get("marketTopicId")
            if mid and tid:
                try:
                    mid_int = int(mid)
                    tid_int = int(tid)
                    if mid_int not in self._market_oracle_cache:
                        missing_markets[mid_int] = tid_int
                except (ValueError, TypeError):
                    pass

        if missing_markets:
            sem = asyncio.Semaphore(5)
            async def _fetch_with_sem(t_id: int, m_id: int):
                async with sem:
                    await self.fetch_market_oracle_prices(t_id, m_id)

            await asyncio.gather(*[_fetch_with_sem(tid, mid) for mid, tid in missing_markets.items()], return_exceptions=True)

        existing_by_token: Dict[str, ClosedPositionInfo] = {}
        for p in self._closed_positions:
            if p.token_id:
                existing_by_token[p.token_id] = p

        synced_closed: List[ClosedPositionInfo] = []
        updated_count = 0
        now = time.time()

        for item in ended_list:
            token_id = str(item.get("tokenId", "")).strip()
            if not token_id:
                continue

            mid_int = int(item.get("marketId", 0) or 0)
            strike_price, settlement_price = self._market_oracle_cache.get(mid_int, (0.0, 0.0))

            # Correlate filled buy & sell orders from order history
            m_orders = orders_by_market.get(mid_int, [])
            buys = [o for o in m_orders if o.get("side") == "BUY" and o.get("status") == "FILLED"]
            sells = [o for o in m_orders if o.get("side") == "SELL" and o.get("status") == "FILLED"]

            bought_shares = sum(float(o.get("filledShareQty", 0.0) or 0.0) for o in buys)
            sold_shares = sum(float(o.get("filledShareQty", 0.0) or 0.0) for o in sells)

            won = (item.get("isWinner") is True)
            realized_raw = float(item.get("realizedPnl") or 0.0)
            unrealized_raw = float(item.get("unrealizedPnl") or 0.0)
            cost = float(item.get("totalCost", 0.0) or 0.0)
            shares = float(item.get("shares", 0.0) or 1.0)

            # Binance's official realizedPnl is the round's net result. It can be
            # positive even when the final outcome lost, if an early partial sale
            # covered the cost of the position. Never discard it based on isWinner.
            if abs(realized_raw) > 1e-9:
                pnl = realized_raw
            else:
                buy_cash = [o.get("filledUsdtAmount") for o in buys]
                sell_cash = [o.get("filledUsdtAmount") for o in sells]
                has_fill_cash = bool(buys) and all(
                    value not in (None, "") for value in buy_cash + sell_cash
                )
                if has_fill_cash:
                    remaining_shares = max(0.0, bought_shares - sold_shares)
                    settlement_payout = remaining_shares if won else 0.0
                    pnl = (
                        sum(float(value or 0.0) for value in sell_cash)
                        + settlement_payout
                        - sum(float(value or 0.0) for value in buy_cash)
                    )
                elif won:
                    if unrealized_raw > 0:
                        pnl = unrealized_raw
                    else:
                        pnl = (shares * 1.00 - cost) if cost > 0 else 0.0
                elif unrealized_raw < 0:
                    pnl = unrealized_raw
                elif cost > 0:
                    pnl = -cost
                elif realized_raw < 0:
                    pnl = realized_raw
                else:
                    pnl = 0.0

            # High precision PnL for sub-cent values (e.g. 0.0048) to avoid $0.00 truncation
            pnl_final = round(pnl, 4) if abs(pnl) < 0.05 else round(pnl, 2)
            is_claimed = bool(won and item.get("positionStatus") == "CLAIMED")
            settled_at = float(item.get("updatedTime", 0)) / 1000.0 if item.get("updatedTime") else now
            title = str(item.get("marketTopicTitle") or item.get("marketTitle") or "")
            side = str(item.get("outcomeName", "UP")).upper()
            avg_price = float(item.get("avgPrice", 0.50) or 0.50)

            # Derive symbol and timeframe
            sym = _prediction_position_symbol(item)
            if not sym:
                logger.warning("Skipping Binance ended position with unrecognized prediction pair: %s", title)
                continue
            tf = _prediction_timeframe(item)

            # Reconstruct true position size (contracts) from bought shares
            if bought_shares > 0:
                final_contracts = int(round(bought_shares))
            else:
                final_contracts = int(round(shares)) if shares >= 1 else 1

            # Tag outcome as TAKE_PROFIT if sold before expiration
            outcome_label = "TAKE_PROFIT" if (won and sold_shares > 0) else ("WIN" if won else "LOSS")

            orig = existing_by_token.get(token_id)
            if orig is not None:
                orig.market_id = f"{sym}-{tf.upper()}-B{mid_int}"
                orig.symbol = sym
                orig.side = side
                orig.timeframe = tf
                orig.entry_price = avg_price
                orig.result = outcome_label
                orig.realized_pnl = pnl_final
                orig.contracts = final_contracts
                orig.is_claimed = is_claimed
                orig.settled_at = settled_at
                if strike_price > 0:
                    orig.target_price = strike_price
                if settlement_price > 0:
                    orig.settlement_price = settlement_price
                synced_closed.append(orig)
            else:
                pos_id = f"POS_BINANCE_{item.get('positionId', uuid.uuid4().hex[:6])}"
                m_id = f"{sym}-{tf.upper()}-B{item.get('marketId', '')}"
                closed_item = ClosedPositionInfo(
                    position_id=pos_id,
                    market_id=m_id,
                    symbol=sym,
                    side=side,
                    contracts=final_contracts,
                    entry_price=avg_price,
                    target_price=strike_price,
                    settlement_price=settlement_price,
                    timeframe=tf,
                    result=outcome_label,
                    realized_pnl=pnl_final,
                    martingale_step=0,
                    stage="Binance Official",
                    token_id=token_id,
                    is_claimed=is_claimed,
                    entry_time=settled_at - 300,
                    settled_at=settled_at,
                )
                synced_closed.append(closed_item)
            updated_count += 1

        ended_market_ids: Set[int] = {
            int(item.get("marketId", 0) or 0) for item in ended_list if item.get("marketId")
        }

        # Also reconstruct any early closed / take-profit positions from order history that Binance omitted from tab=ENDED
        for mid_int, m_orders in orders_by_market.items():
            if mid_int in ended_market_ids:
                continue

            # Skip if this market is currently an active open position in bot memory
            if any(str(mid_int) in pos.market_id for pos in self._live_positions.values()):
                continue

            buys = [o for o in m_orders if o.get("side") == "BUY" and o.get("status") == "FILLED"]
            sells = [o for o in m_orders if o.get("side") == "SELL" and o.get("status") == "FILLED"]

            bought_shares = sum(float(o.get("filledShareQty", 0.0) or 0.0) for o in buys)
            sold_shares = sum(float(o.get("filledShareQty", 0.0) or 0.0) for o in sells)

            # Check if this position was closed early via sell orders (Take-Profit or Early Exit)
            if bought_shares > 0 and sold_shares > 0 and (sold_shares >= bought_shares * 0.99):
                realized_sum = sum(float(o.get("realizedPnl", 0.0) or 0.0) for o in sells)
                if abs(realized_sum) < 0.0001:
                    total_usdt_sold = sum(float(o.get("filledUsdtAmount", 0.0) or 0.0) for o in sells)
                    total_usdt_bought = sum(float(o.get("filledUsdtAmount", 0.0) or 0.0) for o in buys)
                    pnl = total_usdt_sold - total_usdt_bought
                else:
                    pnl = realized_sum

                pnl_final = round(pnl, 4) if abs(pnl) < 0.05 else round(pnl, 2)
                won = (pnl_final > 0)
                outcome_label = "TAKE_PROFIT" if won else "LOSS"

                sample_order = buys[0] if buys else sells[0]
                title = str(sample_order.get("marketTopicTitle") or sample_order.get("marketTitle") or "")
                side = str(sample_order.get("outcome", "UP")).upper()
                sym = _prediction_position_symbol(sample_order)
                if not sym:
                    logger.warning("Skipping Binance closed order with unrecognized prediction pair: %s", title)
                    continue
                tf = _prediction_timeframe(sample_order)
                avg_price = float(buys[0].get("price", 0.50) or 0.50) if buys else 0.50
                final_contracts = int(round(bought_shares))

                latest_sell_ms = max(
                    float(o.get("terminalTime") or o.get("modifyTime") or o.get("createTime") or 0)
                    for o in sells
                )
                settled_at = (latest_sell_ms / 1000.0) if latest_sell_ms > 0 else now
                strike_price, settlement_price = self._market_oracle_cache.get(mid_int, (0.0, 0.0))

                token_id = str(sample_order.get("tokenId", "")).strip() or f"TAKE_PROFIT_{mid_int}"

                orig = existing_by_token.get(token_id)
                if orig is not None:
                    orig.result = outcome_label
                    orig.realized_pnl = pnl_final
                    orig.contracts = final_contracts
                    orig.is_claimed = True
                    orig.settled_at = settled_at
                    if strike_price > 0:
                        orig.target_price = strike_price
                    if settlement_price > 0:
                        orig.settlement_price = settlement_price
                    synced_closed.append(orig)
                else:
                    pos_id = f"POS_BINANCE_{sample_order.get('orderId', uuid.uuid4().hex[:6])}"
                    m_id = f"{sym}-{tf.upper()}-B{mid_int}"
                    closed_item = ClosedPositionInfo(
                        position_id=pos_id,
                        market_id=m_id,
                        symbol=sym,
                        side=side,
                        contracts=final_contracts,
                        entry_price=avg_price,
                        target_price=strike_price,
                        settlement_price=settlement_price,
                        timeframe=tf,
                        result=outcome_label,
                        realized_pnl=pnl_final,
                        martingale_step=0,
                        stage="Binance Official",
                        token_id=token_id,
                        is_claimed=True,
                        entry_time=settled_at - 300,
                        settled_at=settled_at,
                    )
                    synced_closed.append(closed_item)
                updated_count += 1

        # Replace _closed_positions with the official synced list (sorted oldest to newest)
        synced_closed.sort(key=lambda x: getattr(x, "settled_at", 0))
        self._closed_positions = synced_closed[-100:]

        # CRITICAL FIX: Immediately purge ended/settled tokens from live active positions
        ended_tokens = {str(item.get("tokenId", "")).strip() for item in ended_list if item.get("tokenId")}
        ended_mids = {str(item.get("marketId", "")).strip() for item in ended_list if item.get("marketId")}
        for c_item in synced_closed:
            if c_item.token_id:
                ended_tokens.add(str(c_item.token_id).strip())
            if "-B" in c_item.market_id:
                ended_mids.add(c_item.market_id.split("-B")[-1].strip())
        for pid, pos in list(self._live_positions.items()):
            if pos.token_id and pos.token_id in ended_tokens:
                logger.info(f"[BINANCE HISTORY SYNC] Purging settled position {pid} ({pos.symbol} {pos.market_id}) from active positions (matched tokenId {pos.token_id}).")
                self._live_positions.pop(pid, None)
            elif any(em and em in pos.market_id for em in ended_mids if em):
                logger.info(f"[BINANCE HISTORY SYNC] Purging settled position {pid} ({pos.symbol} {pos.market_id}) from active positions (matched marketId in ended_list).")
                self._live_positions.pop(pid, None)

        # Cross-reconcile live active positions against Binance ONGOING list
        try:
            ongoing_positions, _, _ = await self.fetch_ongoing_prediction_positions(limit=20)
            ongoing_tokens = {str(item.get("tokenId", "")).strip() for item in ongoing_positions if item.get("tokenId")}
            ongoing_mids = {str(item.get("marketId", "")).strip() for item in ongoing_positions if item.get("marketId")}
            now_ts = time.time()

            # Hydrate missing live active positions from Binance ONGOING list (e.g. after bot restart)
            for op in ongoing_positions:
                op_token = str(op.get("tokenId", "")).strip()
                op_mid = str(op.get("marketId", "")).strip()
                if not op_token and not op_mid:
                    continue
                already_present = any(
                    (pos.token_id and pos.token_id == op_token) or (op_mid and op_mid in pos.market_id)
                    for pos in self._live_positions.values()
                )
                if not already_present:
                    title = str(op.get("marketTopicTitle") or op.get("marketTitle") or "")
                    sym = _prediction_position_symbol(op)
                    if not sym:
                        logger.warning("Skipping Binance ongoing position with unrecognized prediction pair: %s", title)
                        continue
                    tf = _prediction_timeframe(op, default="1h")
                    op_side = str(op.get("outcomeName", "UP")).upper()
                    shares = float(op.get("shares", 0.0) or 1.0)
                    avg_p = float(op.get("avgPrice", 0.50) or 0.50)
                    mid_int = int(op.get("marketId", 0) or 0)
                    strike_price, _ = self._market_oracle_cache.get(mid_int, (0.0, 0.0))
                    created_ms = float(op.get("createdTime") or op.get("updatedTime") or (now_ts * 1000))
                    
                    hydrated_pos = PositionInfo(
                        position_id=f"POS_BINANCE_{op.get('positionId', op_mid)}",
                        market_id=f"{sym}-{tf.upper()}-B{op_mid}",
                        symbol=sym,
                        side=op_side,
                        contracts=int(round(shares)),
                        entry_price=avg_p,
                        current_price=strike_price if strike_price > 0 else avg_p,
                        target_price=strike_price,
                        timeframe=tf,
                        unrealized_pnl=0.0,
                        martingale_step=0,
                        stage="Binance Official",
                        token_id=op_token,
                        is_settling=False,
                        entry_time=created_ms / 1000.0,
                    )
                    self._live_positions[hydrated_pos.position_id] = hydrated_pos
                    logger.info(
                        f"[BINANCE ONGOING RECOVERY] Hydrated live active position from Binance: "
                        f"{hydrated_pos.position_id} ({sym} {op_side} {hydrated_pos.contracts}x @ {avg_p:.3f})"
                    )

            for pid, pos in list(self._live_positions.items()):
                # Give position 25 seconds grace period after entry before declaring ghost
                if (now_ts - getattr(pos, "entry_time", 0)) > 25.0:
                    token_matched = bool(pos.token_id and pos.token_id in ongoing_tokens)
                    mid_matched = any(om and om in pos.market_id for om in ongoing_mids if om)
                    ended_token_matched = bool(pos.token_id and pos.token_id in ended_tokens)
                    ended_mid_matched = any(em and em in pos.market_id for em in ended_mids if em)

                    if not (token_matched or mid_matched or ended_token_matched or ended_mid_matched):
                        logger.warning(
                            f"[GHOST POSITION PURGED] Position {pid} ({pos.symbol} {pos.market_id}) "
                            f"not found in Binance ongoing or ended positions after 25s. Purging phantom position."
                        )
                        self._live_positions.pop(pid, None)
        except Exception as e:
            logger.debug(f"Exception cross-reconciling ongoing positions: {e}")

        if updated_count > 0:
            logger.info(f"[BINANCE HISTORY SYNC] Successfully reconciled {len(self._closed_positions)} closed position(s) from Binance API.")
        return updated_count

    async def redeem_prediction_tokens(self, token_ids: List[str]) -> Dict[str, Any]:
        """
        Execute official Binance Prediction batch-redeem API to claim winnings into wallet balance.
        Endpoint: POST /sapi/v1/w3w/wallet/prediction/batch-redeem
        """
        if not token_ids:
            return {"success": True, "message": "No tokens to redeem", "redeemed_count": 0}

        # Filter out empty or duplicate strings
        clean_ids = list(dict.fromkeys([str(t).strip() for t in token_ids if str(t).strip()]))
        if not clean_ids:
            return {"success": True, "message": "No valid token IDs", "redeemed_count": 0}

        if self.paper_trading or not (self.api_key and self.api_secret):
            logger.info(f"[PAPER AUTO-CLAIM] Simulated claim for {len(clean_ids)} token(s): {clean_ids}")
            for pos in self._closed_positions:
                if pos.token_id in clean_ids:
                    pos.is_claimed = True
            return {"success": True, "message": "Paper claim simulated", "token_ids": clean_ids}

        if self._session is None or self._session.closed:
            await self.start()

        sapi_host = "https://api.binance.com" if "fapi.binance.com" in self.base_url else self.base_url
        query_params = {
            "tokenIds": ",".join(clean_ids),
            "walletAddress": self._wallet_address,
            "walletId": self._wallet_id,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }
        signed_query = self._sign_payload(query_params)
        endpoint_url = f"{sapi_host}/sapi/v1/w3w/wallet/prediction/batch-redeem?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.post(endpoint_url, json={}, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                status = resp.status
                try:
                    data = await resp.json()
                except Exception:
                    raw_text = await resp.text()
                    data = {"raw": raw_text[:200]}

                if status in (200, 201):
                    batch_id = data.get("batchId", "")
                    results = data.get("results", [])
                    success_count = 0
                    for r in results:
                        res_status = r.get("status", "").upper()
                        err = r.get("error", "")
                        # If SUCCESS or already claimed by Binance UI Auto-Claim, it's claimed!
                        if res_status == "SUCCESS" or "already claimed" in err.lower():
                            success_count += 1

                    # Mark matched closed positions as claimed
                    for pos in self._closed_positions:
                        if pos.token_id in clean_ids:
                            pos.is_claimed = True

                    logger.info(
                        f"[BINANCE AUTO-CLAIM SUCCESS] Redeemed {len(clean_ids)} token(s) (Batch: {batch_id}) | "
                        f"Claimed: {success_count}/{len(clean_ids)}"
                    )
                    # Trigger balance refresh so the UI updates immediately
                    self._spawn_task(self.fetch_live_balance())
                    return {
                        "success": True,
                        "batch_id": batch_id,
                        "token_ids": clean_ids,
                        "results": results,
                        "claimed_count": success_count,
                    }
                else:
                    err_msg = data.get("msg", data.get("message", f"HTTP {status}"))
                    logger.warning(f"[BINANCE AUTO-CLAIM ERROR] Batch redeem failed: {err_msg}")
                    # Only mark as claimed if Binance explicitly confirms the token was already claimed/redeemed or invalid.
                    # Never mark as claimed on transient errors (HTTP 429, 502/503, timestamp drift -1021, etc.)
                    # or pending settlement status (e.g. "not claimable yet").
                    is_terminal_claim_error = any(kw in str(err_msg).lower() for kw in [
                        "already claimed", "already redeemed", "invalid token", "not found", "does not exist"
                    ])
                    if is_terminal_claim_error:
                        for pos in self._closed_positions:
                            if pos.token_id in clean_ids:
                                pos.is_claimed = True
                    return {"success": False, "error": err_msg, "token_ids": clean_ids}
        except Exception as e:
            logger.error(f"[BINANCE AUTO-CLAIM EXCEPTION] Error calling batch-redeem: {e}")
            return {"success": False, "error": str(e), "token_ids": clean_ids}

    async def claim_all_won_positions(self) -> Dict[str, Any]:
        """
        Reconcile and sync won prediction positions.
        Automatic background batch-redeem has been disabled because Binance handles
        claiming natively or via user action on Binance UI. In paper trading mode,
        simulates marking winning positions as claimed.
        """
        if not self.paper_trading:
            logger.info("[CLAIM INFO] Autoclaim is disabled; claiming is handled natively by Binance. Reconciling closed positions from Binance...")
            await self.sync_historical_closed_positions(limit=30)
            return {"success": True, "message": "Positions synced from Binance. Settlement/redemption managed natively by Binance.", "claimed_count": 0}
        else:
            # Paper trading simulation
            claimed_count = 0
            for pos in self._closed_positions:
                if pos.result in ("WIN", "TAKE_PROFIT", "TIE (50-50)") and not getattr(pos, "is_claimed", False):
                    pos.is_claimed = True
                    claimed_count += 1
            return {"success": True, "message": f"Paper positions marked as claimed ({claimed_count})", "claimed_count": claimed_count}
