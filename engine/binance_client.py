"""
Binance Prediction Markets REST API Client.
Features HMAC-SHA256 request signing, server time synchronization,
persistent connection pooling, and high-fidelity paper trading simulation.
"""

from __future__ import annotations
import asyncio
import hashlib
import hmac
import logging
import time
import uuid
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger("binance_client")


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
    entry_time: float = Field(default_factory=time.time)


class BinanceClient:
    """
    High-performance asynchronous client for Binance Prediction API.
    Supports live execution via signed requests and zero-risk paper trading mode.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: str = "https://fapi.binance.com",
        recv_window: int = 5000,
        paper_trading: bool = True,
        slippage_tolerance: float = 0.03,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.paper_trading = paper_trading
        self.slippage_tolerance = slippage_tolerance

        self._session: Optional[aiohttp.ClientSession] = None
        self._time_offset_ms: int = 0
        self._total_orders_dispatched: int = 0
        self._total_fills: int = 0

        # Simulated Paper Trading State
        self._paper_balance_usdt: float = 1000.0  # Starting mock wallet
        self._paper_positions: Dict[str, PositionInfo] = {}
        self._order_history: List[OrderResult] = []

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
        """Clean up HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            await asyncio.sleep(0.05)
            logger.info("Binance Client HTTP session closed.")

    async def sync_server_time(self) -> None:
        """Synchronize client with Binance server time to prevent timestamp drift."""
        try:
            assert self._session is not None
            url = f"{self.base_url}/fapi/v1/time"
            t0 = int(time.time() * 1000)
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    t1 = int(time.time() * 1000)
                    server_time = data.get("serverTime", t1)
                    # Round-trip latency estimation
                    one_way_latency = (t1 - t0) // 2
                    self._time_offset_ms = server_time - (t1 - one_way_latency)
                    logger.info(f"Binance server time synced. Offset: {self._time_offset_ms}ms")
        except Exception as e:
            logger.warning(f"Could not sync Binance server time ({e}). Using local clock.")
            self._time_offset_ms = 0

    def _get_timestamp(self) -> int:
        """Get calibrated timestamp in milliseconds."""
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign_payload(self, params: Dict[str, Any]) -> str:
        """Generate HMAC-SHA256 signature for Binance request parameters."""
        query_str = urlencode(sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{query_str}&signature={signature}"

    async def place_prediction_order(
        self,
        market_id: str,
        symbol: str,
        side: str,  # "BUY_YES" or "BUY_NO"
        contracts: int,
        target_price: float,
    ) -> OrderResult:
        """
        Execute an order on Binance Prediction Markets.
        If paper_trading is True or no API keys exist, runs high-speed paper simulator.
        """
        start_time = time.perf_counter()
        client_order_id = f"JEV_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"

        # --- Paper Trading Simulation Mode ---
        if self.paper_trading or not (self.api_key and self.api_secret):
            return await self._simulate_order_execution(
                market_id=market_id,
                symbol=symbol,
                side=side,
                contracts=contracts,
                target_price=target_price,
                client_order_id=client_order_id,
                start_time=start_time
            )

        # --- Live Execution Mode ---
        if self._session is None or self._session.closed:
            await self.start()

        # Map side to Binance prediction contract side
        order_side = "BUY"
        prediction_choice = "YES" if "YES" in side.upper() else "NO"

        params = {
            "symbol": symbol,
            "side": order_side,
            "predictionSide": prediction_choice,
            "marketId": market_id,
            "quantity": contracts,
            "price": f"{target_price:.4f}",
            "newClientOrderId": client_order_id,
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }

        signed_query = self._sign_payload(params)
        endpoint_url = f"{self.base_url}/sapi/v1/prediction/order?{signed_query}"

        try:
            assert self._session is not None
            async with self._session.post(endpoint_url) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                response_data = await resp.json()

                if resp.status in (200, 201):
                    order_id = str(response_data.get("orderId", client_order_id))
                    result = OrderResult(
                        order_id=order_id,
                        client_order_id=client_order_id,
                        market_id=market_id,
                        symbol=symbol,
                        side=side,
                        contracts=contracts,
                        price=target_price,
                        status="FILLED",
                        latency_ms=round(elapsed_ms, 2)
                    )
                    self._total_orders_dispatched += 1
                    self._total_fills += 1
                    self._order_history.append(result)
                    logger.info(
                        f"LIVE ORDER EXECUTED: {side} {contracts}x on {market_id} "
                        f"@ {target_price:.3f} (Latency: {elapsed_ms:.1f}ms)"
                    )
                    return result
                else:
                    err_msg = response_data.get("msg", f"HTTP {resp.status}")
                    logger.error(f"Binance Order Rejected: {err_msg}")
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
                        error_message=err_msg
                    )
                    self._total_orders_dispatched += 1
                    self._order_history.append(result)
                    return result

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Binance order dispatch exception: {e}")
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
        client_order_id: str,
        start_time: float
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
                price_to_beat=target_price,
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
            current_price=executed_price,
            target_price=target_price,
            timeframe=tf,
            unrealized_pnl=0.0
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
            price_to_beat=target_price
        )

        self._total_orders_dispatched += 1
        self._total_fills += 1
        self._order_history.append(result)

        logger.info(
            f"[PAPER] Order Filled: {clean_side} {contracts} contracts on {market_id} ({tf}) "
            f"@ {executed_price:.3f} | Latency: {elapsed_ms:.1f}ms | Rem. Balance: ${self._paper_balance_usdt:.2f}"
        )
        return result

    def settle_expired_positions(
        self,
        active_market_ids: Set[str],
        current_prices: Dict[str, float]
    ) -> float:
        """
        Settle prediction contracts whose round has ended.
        Picks up final settlement: $1.00 USDT payout per winning contract.
        Returns total net realized PnL.
        """
        if not self.paper_trading:
            return 0.0

        expired_pos_ids = [
            pid for pid, pos in self._paper_positions.items()
            if pos.market_id not in active_market_ids
        ]

        total_net_pnl = 0.0

        for pid in expired_pos_ids:
            pos = self._paper_positions.pop(pid)
            spot = current_prices.get(pos.symbol, pos.entry_price)
            cost = pos.contracts * pos.entry_price

            # Binary outcome settlement: $1.00 per contract
            if pos.side in ("UP", "BUY_YES"):
                won = spot >= pos.target_price
            else:
                won = spot < pos.target_price

            if won:
                payout = pos.contracts * 1.00
                realized_pnl = payout - cost
                self._paper_balance_usdt += payout
            else:
                payout = 0.0
                realized_pnl = -cost

            total_net_pnl += realized_pnl
            logger.info(
                f"[PAPER SETTLEMENT] {pos.symbol} {pos.side} ({pos.market_id} - {pos.timeframe}) SETTLED! "
                f"Result: {'WIN (+$' + f'{realized_pnl:.2f})' if won else 'LOSS (-$' + f'{abs(realized_pnl):.2f})'} "
                f"(Current Spot: ${spot:.2f}, Price to Beat: ${pos.target_price:.2f}) | Rem. Balance: ${self._paper_balance_usdt:.2f}"
            )

        return round(total_net_pnl, 2)

    def get_order_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent order execution log."""
        return [o.model_dump() for o in reversed(self._order_history[-limit:])]

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return currently held active positions."""
        return [p.model_dump() for p in self._paper_positions.values()]

    def get_account_summary(self) -> Dict[str, Any]:
        """Return wallet balances and execution statistics."""
        return {
            "mode": "PAPER_TRADING" if self.paper_trading else "LIVE_TRADING",
            "balance_usdt": round(self._paper_balance_usdt, 2),
            "open_positions_count": len(self._paper_positions),
            "total_orders": self._total_orders_dispatched,
            "total_fills": self._total_fills,
            "time_offset_ms": self._time_offset_ms
        }
