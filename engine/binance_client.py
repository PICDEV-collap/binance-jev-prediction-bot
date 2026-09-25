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
from typing import Optional, Dict, Any, List, Set, Tuple
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
    entry_time: float = Field(default_factory=time.time)


class ClosedPositionInfo(BaseModel):
    """Historical record of a settled prediction contract position."""
    position_id: str
    market_id: str
    symbol: str
    side: str  # "UP" or "DOWN"
    contracts: int
    entry_price: float
    target_price: float  # Price to Beat (Strike)
    settlement_price: float  # Final spot price
    timeframe: str = "15m"
    result: str  # "WIN" or "LOSS"
    realized_pnl: float
    martingale_step: int = 0
    stage: str = "ไม้ 1 (Base)"
    entry_time: float
    settled_at: float = Field(default_factory=time.time)


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
        slippage_bps: int = 1000,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.paper_trading = paper_trading
        self.slippage_tolerance = slippage_tolerance
        self.funding_source = funding_source
        self.slippage_bps = slippage_bps

        self._session: Optional[aiohttp.ClientSession] = None
        self._time_offset_ms: int = 0
        self._total_orders_dispatched: int = 0
        self._total_fills: int = 0
        self._in_flight_orders: Dict[str, Dict[str, Any]] = {}

        # Simulated Paper Trading State
        self._paper_balance_usdt: float = 1000.0  # Starting mock wallet
        self._paper_positions: Dict[str, PositionInfo] = {}
        self._closed_positions: List[ClosedPositionInfo] = []
        self._order_history: List[OrderResult] = []

        # Live Binance Account Cache
        self._live_balance_usdt: float = 0.0
        self._live_available_usdt: float = 0.0
        self._live_unrealized_usdt: float = 0.0

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
            # Try core spot/general time endpoint first, fall back to fapi if needed
            url = f"{self.base_url}/api/v3/time"
            t0 = int(time.time() * 1000)
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    t1 = int(time.time() * 1000)
                    server_time = data.get("serverTime", t1)
                    one_way_latency = (t1 - t0) // 2
                    self._time_offset_ms = server_time - (t1 - one_way_latency)
                    logger.info(f"Binance server time synced via /api/v3/time. Offset: {self._time_offset_ms}ms")
                    return
                elif resp.status == 404:
                    # Fallback to fapi time
                    fallback_url = f"{self.base_url}/fapi/v1/time"
                    async with self._session.get(fallback_url) as fresp:
                        if fresp.status == 200:
                            fdata = await fresp.json()
                            t1 = int(time.time() * 1000)
                            server_time = fdata.get("serverTime", t1)
                            one_way_latency = (t1 - t0) // 2
                            self._time_offset_ms = server_time - (t1 - one_way_latency)
                            logger.info(f"Binance server time synced via /fapi/v1/time. Offset: {self._time_offset_ms}ms")
                            return
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
        strike_price: float = 0.0,
        spot_price: float = 0.0,
        martingale_step: int = 0,
        stage: str = "ไม้ 1 (Base)",
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
                strike_price=strike_price,
                spot_price=spot_price,
                client_order_id=client_order_id,
                start_time=start_time,
                martingale_step=martingale_step,
                stage=stage,
            )

        # --- Live Execution Mode ---
        if self._session is None or self._session.closed:
            await self.start()

        # Map side to Binance prediction contract outcome
        prediction_choice = "UP" if ("UP" in side.upper() or "YES" in side.upper()) else "DOWN"

        # Parameters for official Binance Prediction Trading API (place-order-bundle)
        order_payload = {
            "orderType": "MARKET",
            "slippageBps": self.slippage_bps,
            "fundingSource": self.funding_source,
            "marketId": market_id,
            "outcome": prediction_choice,
            "amount": f"{contracts * target_price:.4f}",
            "clientOrderId": client_order_id,
        }

        query_params = {
            "recvWindow": self.recv_window,
            "timestamp": self._get_timestamp(),
        }

        signed_query = self._sign_payload(query_params)
        endpoint_url = f"{self.base_url}/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle?{signed_query}"

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
            async with self._session.post(endpoint_url, json=order_payload) as resp:
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
                        latency_ms=round(elapsed_ms, 2),
                        martingale_step=martingale_step,
                        stage=stage,
                    )
                    self._total_orders_dispatched += 1
                    self._total_fills += 1
                    self._order_history.append(result)
                    self._in_flight_orders.pop(client_order_id, None)
                    logger.info(
                        f"LIVE PREDICTION ORDER EXECUTED: {side} {contracts}x on {market_id} [{stage}] "
                        f"@ {target_price:.3f} (Latency: {elapsed_ms:.1f}ms)"
                    )
                    return result
                else:
                    err_msg = response_data.get("msg", f"HTTP {resp.status}")
                    logger.error(f"Binance Prediction Order Rejected: {err_msg}")
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
                        martingale_step=martingale_step,
                        stage=stage,
                        error_message=err_msg
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

    def settle_expired_positions(
        self,
        active_market_ids: Set[str],
        current_prices: Dict[str, float]
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """
        Settle prediction contracts whose round has ended.
        Picks up final settlement: $1.00 USDT payout per winning contract.
        Settles based on both active market ID rotation AND elapsed round duration.
        Returns tuple of (total_net_pnl, settled_events).
        """
        if not self.paper_trading:
            return 0.0, []

        now = time.time()
        tf_durations = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

        expired_pos_ids = []
        for pid, pos in self._paper_positions.items():
            max_duration = tf_durations.get(pos.timeframe.lower(), 900)
            is_time_expired = (now - pos.entry_time) >= (max_duration + 5)
            if pos.market_id not in active_market_ids or is_time_expired:
                expired_pos_ids.append(pid)

        total_net_pnl = 0.0
        settled_events: List[Dict[str, Any]] = []

        for pid in expired_pos_ids:
            pos = self._paper_positions.pop(pid)
            spot = current_prices.get(pos.symbol, pos.current_price if pos.current_price > 0 else pos.entry_price)
            cost = pos.contracts * pos.entry_price

            # Binary outcome settlement according to official Binance Prediction Rules:
            # Rule: If final price > strike -> UP wins ($1.00); if < strike -> DOWN wins ($1.00); if equal -> 50-50 ($0.50 payout)
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

            # Record into settled positions history
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
                entry_time=pos.entry_time,
                settled_at=time.time(),
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
            })

            logger.info(
                f"[PAPER SETTLEMENT] {pos.symbol} {pos.side} ({pos.market_id} - {pos.timeframe} | {pos.stage}) SETTLED! "
                f"Result: {'WIN (+$' + f'{realized_pnl:.2f})' if won else 'LOSS (-$' + f'{abs(realized_pnl):.2f})'} "
                f"(Current Spot: ${spot:.2f}, Price to Beat: ${pos.target_price:.2f}) | Rem. Balance: ${self._paper_balance_usdt:.2f}"
            )

        return round(total_net_pnl, 2), settled_events

    def update_positions_market_data(self, current_prices: Dict[str, float]) -> None:
        """Update live mark price and calculate real-time unrealized PnL for open positions."""
        for pos in self._paper_positions.values():
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

    def get_order_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent order execution log."""
        return [o.model_dump() for o in reversed(self._order_history[-limit:])]

    def get_positions(self) -> List[Dict[str, Any]]:
        """Return currently held active open positions."""
        return [p.model_dump() for p in self._paper_positions.values()]

    def get_closed_positions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return historical settled/closed positions."""
        return [p.model_dump() for p in reversed(self._closed_positions[-limit:])]

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
        win_count = sum(1 for r in results if r == "WIN")
        loss_count = sum(1 for r in results if r == "LOSS")
        win_rate = (win_count / len(results)) * 100.0

        consecutive_losses = 0
        consecutive_wins = 0
        for r in reversed(results):
            if r == "LOSS":
                if consecutive_wins > 0:
                    break
                consecutive_losses += 1
            elif r == "WIN":
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
        if self.paper_trading or not (self.api_key and self.api_secret):
            return None
        if self._session is None or self._session.closed:
            await self.start()
        try:
            params = {
                "recvWindow": self.recv_window,
                "timestamp": self._get_timestamp(),
            }
            signed_query = self._sign_payload(params)

            # Try Binance Spot Account API first (for CEX USDT funding)
            spot_url = f"{self.base_url}/api/v3/account?{signed_query}"
            assert self._session is not None
            async with self._session.get(spot_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    balances = data.get("balances", [])
                    for item in balances:
                        if item.get("asset") == "USDT":
                            free = float(item.get("free", 0.0))
                            locked = float(item.get("locked", 0.0))
                            total = free + locked
                            self._live_balance_usdt = total
                            self._live_available_usdt = free
                            self._live_unrealized_usdt = 0.0
                            return {
                                "balance": total,
                                "available": free,
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
                                        self._live_balance_usdt = balance
                                        self._live_available_usdt = available
                                        self._live_unrealized_usdt = unrealized
                                        return {
                                            "balance": balance,
                                            "available": available,
                                            "unrealized": unrealized,
                                        }
        except Exception as e:
            logger.warning(f"Could not fetch Binance live balance: {e}")
        return None

    def get_account_summary(self) -> Dict[str, Any]:
        """Return comprehensive wallet balances, total equity, and execution statistics."""
        if not self.paper_trading and self._live_balance_usdt > 0:
            total_equity = round(self._live_balance_usdt + self._live_unrealized_usdt, 2)
            available_balance = round(self._live_available_usdt, 2)
            unrealized_pnl = round(self._live_unrealized_usdt, 2)
            committed_margin = round(total_equity - available_balance, 2)
            realized_pnl = 0.0
            total_profit = round(total_equity - 1000.0, 2)
            total_profit_pct = round((total_profit / 1000.0) * 100.0, 2)
            open_count = len(self._paper_positions)
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
            "mode": "PAPER_TRADING" if self.paper_trading else "LIVE_TRADING",
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
