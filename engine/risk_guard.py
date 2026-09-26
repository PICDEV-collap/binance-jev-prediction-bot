"""
Risk & Execution Guard.
Multi-tier risk validation firewall:
1. AI Confidence Threshold filtering (e.g. >= 0.80)
2. Cooldown throttle per market round to prevent rapid double-entries
3. Position sizing calculation capped at max USDT exposure
4. Daily drawdown circuit breaker
5. Concurrent position limits and pricing sanity checks
"""

from __future__ import annotations
import logging
import time
from typing import Dict, Optional, Tuple, Any, Set
from pydantic import BaseModel, Field

from .jev_client import JevEvaluationResult, MarketContext
from engine.indicators import check_unrealistic_velocity

logger = logging.getLogger("risk_guard")


class RiskEvaluationResult(BaseModel):
    """Output of pre-trade risk analysis."""
    approved: bool
    reason: str
    adjusted_contracts: int = 0
    confidence: float
    market_id: str
    action: str
    target_price: float = 0.0
    martingale_step: int = 0
    stage_label: str = "ไม้ 1 (Base)"
    multiplier: float = 1.0
    effective_threshold: float = 0.80
    timestamp: float = Field(default_factory=time.time)


class RiskGuard:
    """
    Quantitative pre-trade risk engine ensuring no orders breach risk tolerances.
    Features smart Martingale recovery sizing and dynamic escalating AI conviction hurdles.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.80,
        max_position_size_usdt: float = 50.0,
        default_order_contracts: int = 10,
        cooldown_seconds: int = 45,
        max_daily_loss_usdt: float = 200.0,
        max_concurrent_positions: int = 5,
        max_odds_cap: float = 0.60,
        min_odds_floor: float = 0.20,
        min_ev_edge: float = 0.05,
        min_time_left_seconds: int = 120,
        max_time_left_seconds: int = 850,
        martingale_enabled: bool = True,
        martingale_multiplier: float = 2.0,
        martingale_max_steps: int = 4,
        martingale_confidence_step: float = 0.04,
        martingale_max_confidence: float = 0.95,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.max_position_size_usdt = max_position_size_usdt
        self.default_order_contracts = default_order_contracts
        self.cooldown_seconds = cooldown_seconds
        self.max_daily_loss_usdt = max_daily_loss_usdt
        self.max_concurrent_positions = max_concurrent_positions
        self.max_odds_cap = max_odds_cap
        self.min_odds_floor = min_odds_floor
        self.min_ev_edge = min_ev_edge
        self.min_time_left_seconds = min_time_left_seconds
        self.max_time_left_seconds = max_time_left_seconds

        # Martingale Recovery State
        self.martingale_enabled: bool = martingale_enabled
        self.martingale_multiplier: float = martingale_multiplier
        self.martingale_max_steps: int = martingale_max_steps
        self.martingale_confidence_step: float = martingale_confidence_step
        self.martingale_max_confidence: float = martingale_max_confidence

        self.current_martingale_step: int = 0  # Global active recovery step
        self.consecutive_losses: int = 0
        self.consecutive_wins: int = 0
        self.last_settled_result: str = "NONE"
        self.recovery_cycles_completed: int = 0

        # Per-Symbol Martingale State (Asset-Specific Isolation)
        self._symbol_martingale_step: Dict[str, int] = {}
        self._symbol_consecutive_losses: Dict[str, int] = {}
        self._symbol_consecutive_wins: Dict[str, int] = {}
        self._symbol_last_result: Dict[str, str] = {}
        self._symbol_recovery_cycles: Dict[str, int] = {}

        # State tracking
        self._market_last_traded: Dict[str, float] = {}
        self._daily_realized_loss: float = 0.0
        self._daily_net_pnl: float = 0.0
        self._daily_start_time: float = time.time()
        self._circuit_breaker_active: bool = False
        self._total_evaluated: int = 0
        self._total_approved: int = 0
        self._total_rejected: int = 0
        self._rejection_counts: Dict[str, int] = {
            "PASS_SIGNAL": 0,
            "LOW_CONFIDENCE": 0,
            "COOLDOWN_ACTIVE": 0,
            "CIRCUIT_BREAKER": 0,
            "MAX_CONCURRENT_POSITIONS": 0,
            "EXTREME_ODDS_RISK": 0,
            "WIDE_SPREAD": 0,
            "INVALID_PRICING": 0,
            "OUTSIDE_TIME_WINDOW": 0,
            "EXPIRY_DANGER": 0,
            "CONTRADICTION_RISK": 0,
            "NETWORK_OFFLINE": 0,
            "NEGATIVE_EV_RISK": 0,
            "UNREALISTIC_VELOCITY_RISK": 0,
            "UNSUPPORTED_PREDICTION_ASSET": 0,
            "POSITION_SIZE_EXCEEDED": 0,
        }
        self.supported_prediction_symbols: Set[str] = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}

    def record_market_traded(self, market_id: str) -> None:
        """Mark market round as traded to enforce cooldown period."""
        self._market_last_traded[market_id] = time.time()

    def rollback_market_traded(self, market_id: str) -> None:
        """Rollback market cooldown if order execution failed or was rejected by exchange."""
        self._market_last_traded.pop(market_id, None)

    def set_supported_symbols(self, symbols: Set[str]) -> None:
        """Update the set of valid binary prediction market symbols."""
        if symbols:
            self.supported_prediction_symbols = {s.upper() for s in symbols}

    def get_symbol_martingale_step(self, symbol: Optional[str] = None) -> int:
        """Get Martingale recovery step for a specific symbol or global fallback."""
        if not symbol:
            return self.current_martingale_step
        return self._symbol_martingale_step.get(symbol.upper(), 0)

    def get_effective_confidence_threshold(self, symbol: Optional[str] = None) -> float:
        """
        Calculate required AI conviction hurdle.
        Escalates with each Martingale recovery step to protect capital.
        Base: 80% -> Step 1: 84% -> Step 2: 88% -> Step 3: 92% -> Step 4: 95%
        """
        step = self.get_symbol_martingale_step(symbol)
        if not self.martingale_enabled or step == 0:
            return self.confidence_threshold
        step_boost = step * self.martingale_confidence_step
        return min(self.martingale_max_confidence, self.confidence_threshold + step_boost)

    def get_stage_label(self, symbol: Optional[str] = None) -> str:
        """Human-readable Martingale stage label."""
        step = self.get_symbol_martingale_step(symbol)
        if not self.martingale_enabled or step == 0:
            return "ไม้ 1 (Base)"
        mult = self.martingale_multiplier ** step
        return f"ไม้แก้ {step} ({mult:.0f}x)"

    def record_settlement_result(self, won: bool, pnl: float, symbol: str = "") -> None:
        """
        Feedback from settled round.
        Tracks Martingale recovery step independently per symbol and updates global counters.
        When LOSS: Advance Martingale recovery step, increase multiplier & raise AI conviction hurdle.
        When WIN: Reset Martingale step to 0 (Base Round) and restore base AI hurdle!
        """
        clean_sym = symbol.upper() if symbol else ""
        self.record_pnl(pnl)

        if won:
            # Per-symbol state update
            if clean_sym:
                old_sym_step = self._symbol_martingale_step.get(clean_sym, 0)
                if old_sym_step > 0:
                    self._symbol_recovery_cycles[clean_sym] = self._symbol_recovery_cycles.get(clean_sym, 0) + 1
                    logger.info(
                        f"🎉 [{clean_sym} MARTINGALE RECOVERY SUCCESS!] Won at Step {old_sym_step} "
                        f"(PnL: +${pnl:.2f}). Total recoveries completed for {clean_sym}: "
                        f"{self._symbol_recovery_cycles[clean_sym]}. RESETTING TO BASE ROUND (ไม้ 1)!"
                    )
                else:
                    logger.info(f"✅ [{clean_sym} WIN] Base round won (+${pnl:.2f}). Starting fresh base round.")
                self._symbol_martingale_step[clean_sym] = 0
                self._symbol_consecutive_wins[clean_sym] = self._symbol_consecutive_wins.get(clean_sym, 0) + 1
                self._symbol_consecutive_losses[clean_sym] = 0
                self._symbol_last_result[clean_sym] = "WIN"

            was_recovery = self.current_martingale_step > 0
            if was_recovery:
                self.recovery_cycles_completed += 1

            self.current_martingale_step = max(self._symbol_martingale_step.values(), default=0)
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.last_settled_result = "WIN"
        else:
            # Per-symbol state update
            if clean_sym:
                old_sym_step = self._symbol_martingale_step.get(clean_sym, 0)
                if self.martingale_enabled:
                    new_sym_step = min(self.martingale_max_steps, old_sym_step + 1)
                    self._symbol_martingale_step[clean_sym] = new_sym_step
                    new_sym_hurdle = self.get_effective_confidence_threshold(clean_sym)
                    mult = self.martingale_multiplier ** new_sym_step
                    logger.warning(
                        f"⚠️ [{clean_sym} MARTINGALE LOSS ESCALATION] Round lost (-${abs(pnl):.2f}). "
                        f"Advancing from Step {old_sym_step} -> Step {new_sym_step} (ไม้แก้ {new_sym_step}). "
                        f"Next trade size for {clean_sym}: {mult:.0f}x | Next AI Hurdle: {new_sym_hurdle*100:.0f}%"
                    )
                self._symbol_consecutive_losses[clean_sym] = self._symbol_consecutive_losses.get(clean_sym, 0) + 1
                self._symbol_consecutive_wins[clean_sym] = 0
                self._symbol_last_result[clean_sym] = "LOSS"

            self.current_martingale_step = max(self._symbol_martingale_step.values(), default=0)
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.last_settled_result = "LOSS"

    def validate_and_size_order(
        self,
        decision: JevEvaluationResult,
        market: MarketContext,
        current_open_positions_count: int = 0,
        recent_performance: Optional[Dict[str, Any]] = None,
    ) -> RiskEvaluationResult:
        """
        Evaluate proposed trade against all risk controls and determine safe sizing.
        Includes Martingale recovery sizing and dynamic escalating AI conviction hurdle.
        """
        self._check_and_reset_daily_window()
        self._total_evaluated += 1
        now = time.time()
        sym = market.symbol.upper() if market.symbol else ""
        step = self.get_symbol_martingale_step(sym)
        effective_threshold = self.get_effective_confidence_threshold(sym)
        stage_label = self.get_stage_label(sym)
        multiplier = self.martingale_multiplier ** step if (self.martingale_enabled and step > 0) else 1.0

        # Gate 0A: Network Liveness & Data Freshness (Network Disconnection Protection)
        if getattr(market, "is_stale", False) or getattr(market, "network_offline", False):
            self._record_rejection("NETWORK_OFFLINE")
            logger.warning(
                f"[RISK REJECT] Network Disconnection / Stale Data Protection active for {market.symbol}! "
                f"Market tick is stale or network connection degraded. Capital preserved."
            )
            return RiskEvaluationResult(
                approved=False,
                reason="NETWORK_OFFLINE: Market data is stale or network disconnected. Freezing trading to protect capital.",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 0: Signal is PASS
        if decision.action == "PASS":
            self._record_rejection("PASS_SIGNAL")
            return RiskEvaluationResult(
                approved=False,
                reason="Jev AI signal is PASS (Neutral/No edge)",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action="PASS",
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 0.1: Supported Binary Prediction Asset Check
        clean_sym = market.symbol.upper()
        if clean_sym not in self.supported_prediction_symbols:
            self._record_rejection("UNSUPPORTED_PREDICTION_ASSET")
            valid_list = ", ".join(sorted(self.supported_prediction_symbols))
            reason = f"Asset {clean_sym} has no binary prediction contracts on Binance. Supported: {valid_list}."
            logger.warning(f"[RISK FILTER] {reason} Order not dispatched. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 1: Circuit breaker check (Daily Loss Limit)
        if self._circuit_breaker_active or self._daily_realized_loss >= self.max_daily_loss_usdt:
            self._circuit_breaker_active = True
            self._record_rejection("CIRCUIT_BREAKER")
            logger.warning(
                f"[RISK REJECT] Circuit breaker active! Daily loss ${self._daily_realized_loss:.2f} "
                f"reached threshold ${self.max_daily_loss_usdt:.2f}"
            )
            return RiskEvaluationResult(
                approved=False,
                reason=f"Circuit Breaker: Daily loss reached ${self.max_daily_loss_usdt:.2f}",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 2: Escalating Martingale AI Conviction Hurdle
        if decision.confidence < effective_threshold:
            self._record_rejection("LOW_CONFIDENCE")
            filter_reason = (
                f"AI Signal: {decision.action} ({decision.confidence*100:.1f}%) "
                f"— Filtered: Below {effective_threshold*100:.0f}% conviction gate"
            )
            if step > 0:
                filter_reason += f" [{stage_label} Recovery Hurdle Active: Conviction must be >={effective_threshold*100:.0f}%]"
            logger.info(f"[RISK FILTER] {filter_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=filter_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 2.5: Expiry Danger Zone (Near-Strike Gamma Risk)
        if getattr(market, "expiry_danger_flag", False):
            self._record_rejection("EXPIRY_DANGER")
            danger_reason = (
                f"Expiry Danger Zone Filter: {market.time_left_seconds}s remaining with spot (${market.underlying_price:,.2f}) "
                f"dangerously close to strike (${market.target_price:,.2f}). Volatility noise buffer active."
            )
            logger.info(f"[RISK FILTER] {danger_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=danger_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 2.6: Microstructure / Trend Contradiction Filter
        is_up = decision.action in ("UP", "BUY_YES")
        is_down = decision.action in ("DOWN", "BUY_NO")
        ema_trend = getattr(market, "ema_trend", "NEUTRAL_CHOP")
        obi = getattr(market, "order_book_imbalance", 0.0)

        severe_contradiction = (
            (is_up and ema_trend == "STRONG_DOWNTREND" and obi < -0.35) or
            (is_down and ema_trend == "STRONG_UPTREND" and obi > 0.35)
        )
        if severe_contradiction:
            self._record_rejection("CONTRADICTION_RISK")
            contra_reason = (
                f"Microstructure Contradiction Filter: {decision.action} signal directly opposes {ema_trend} "
                f"with heavy Order Book Imbalance (OBI: {obi:+.2f})."
            )
            logger.info(f"[RISK FILTER] {contra_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=contra_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 2.7: Time-Decay & Required Price Velocity Gate
        # Prevent hopeless underdog bets where price needs impossible speed to cross strike
        atr_val = getattr(market, "atr_1m", 1.0)
        is_unrealistic, req_speed, max_allowed = check_unrealistic_velocity(
            action=decision.action,
            spot_price=market.underlying_price,
            strike_price=market.target_price,
            time_left_seconds=market.time_left_seconds,
            atr_1m=atr_val,
            max_velocity_multiplier=1.8,
        )
        if is_unrealistic:
            self._record_rejection("UNREALISTIC_VELOCITY_RISK")
            velocity_reason = (
                f"Unrealistic Velocity Risk: Trade requires price to move at ${req_speed:.1f}/min "
                f"(max plausible: ${max_allowed:.1f}/min from ATR) with only {market.time_left_seconds}s remaining."
            )
            logger.info(f"[RISK FILTER] {velocity_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=velocity_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 2.8: Round Time-Window Filter (Timing & Asymmetric Payoff Risk)
        # Prevents late-round entries where odds polarize (e.g. 0.85 - 0.99) leaving minimal profit potential
        # with full -100% loss risk, or hopeless 0.01 bets.
        time_left = getattr(market, "time_left_seconds", 300)
        is_15m_or_higher = getattr(market, "timeframe", "15m").lower() in ("15m", "1h", "1d")
        effective_min_time = max(self.min_time_left_seconds, 120) if is_15m_or_higher else self.min_time_left_seconds
        if time_left < effective_min_time:
            self._record_rejection("OUTSIDE_TIME_WINDOW")
            time_reason = (
                f"Time-Window Filter: Only {time_left}s remaining in round (< {effective_min_time}s threshold). "
                f"Late-round entry rejected to prevent low-payout/high-loss asymmetric payoff."
            )
            logger.info(f"[RISK FILTER] {time_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=time_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold
            )

        # Gate 3: Cooldown Throttle per Market
        last_trade_time = self._market_last_traded.get(market.market_id, 0.0)
        elapsed_since_last_trade = now - last_trade_time
        if elapsed_since_last_trade < self.cooldown_seconds:
            remaining_cooldown = int(self.cooldown_seconds - elapsed_since_last_trade)
            self._record_rejection("COOLDOWN_ACTIVE")
            logger.info(
                f"[RISK FILTER] Cooldown active for {market.market_id}: {remaining_cooldown}s remaining."
            )
            return RiskEvaluationResult(
                approved=False,
                reason=f"Cooldown throttle active ({remaining_cooldown}s remaining for market)",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        # Gate 4: Maximum Concurrent Positions Limit
        if current_open_positions_count >= self.max_concurrent_positions:
            self._record_rejection("MAX_CONCURRENT_POSITIONS")
            return RiskEvaluationResult(
                approved=False,
                reason=f"Max concurrent positions limit ({self.max_concurrent_positions}) reached",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        # Gate 5: Expected Value (EV) Gate
        # In Binary Prediction Markets, winning payout is $1.00 per contract.
        # Cost = target_price. EV = (Confidence * $1.00) - target_price.
        # Must have a positive edge (EV >= min_ev_edge, e.g. at least +5% edge over market odds).
        target_price = market.odds_yes if decision.action in ("BUY_YES", "UP") else market.odds_no
        expected_value = (decision.confidence * 1.00) - target_price
        min_ev_edge = self.min_ev_edge
        if expected_value < min_ev_edge:
            self._record_rejection("NEGATIVE_EV_RISK")
            ev_reason = (
                f"Negative/Low Expected Value Filter: EV is {expected_value:+.3f} (Edge: {expected_value*100:+.1f}% < {min_ev_edge*100:.1f}%). "
                f"Market price {target_price:.3f} is too expensive for conviction {decision.confidence*100:.0f}%."
            )
            logger.info(f"[RISK FILTER] {ev_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=ev_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                target_price=target_price,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        # Gate 5.5: Odds Pricing and Skew Sanity Check (Strict Bounds to guarantee favorable Risk/Reward)
        if target_price < self.min_odds_floor:
            self._record_rejection("EXTREME_ODDS_RISK")
            reason_str = (
                f"Entry price {target_price:.3f} below minimum odds floor {self.min_odds_floor:.2f}. "
                f"Extreme underdog bet has negligible statistical probability."
            )
            logger.info(f"[RISK FILTER] {reason_str} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=reason_str,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                target_price=target_price,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        if target_price > self.max_odds_cap:
            self._record_rejection("EXTREME_ODDS_RISK")
            payout_pct = ((1.0 - target_price) / max(0.001, target_price)) * 100.0
            reason_str = (
                f"Entry price {target_price:.3f} exceeds maximum odds cap {self.max_odds_cap:.2f} "
                f"(Win payout: +{payout_pct:.1f}% vs Loss: -100%). Unfavorable Risk/Reward."
            )
            logger.info(f"[RISK FILTER] {reason_str} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=reason_str,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                target_price=target_price,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        # Gate 6: Spread Sanity Check
        if market.spread > 0.06:
            self._record_rejection("WIDE_SPREAD")
            return RiskEvaluationResult(
                approved=False,
                reason=f"Market spread {market.spread:.4f} exceeds max 0.0600 tolerance",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                target_price=target_price,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        # --- Position Sizing Calculation ---
        if target_price <= 0:
            self._record_rejection("INVALID_PRICING")
            return RiskEvaluationResult(
                approved=False,
                reason="Invalid pricing: target_price must be > 0",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        # Formula: Cap position exposure strictly by max_position_size_usdt
        max_possible_contracts = int(self.max_position_size_usdt / target_price)
        if max_possible_contracts < 1:
            self._record_rejection("POSITION_SIZE_EXCEEDED")
            reject_reason = (
                f"Position size budget (${self.max_position_size_usdt:.2f}) is smaller than "
                f"single contract price (${target_price:.3f}). Order rejected to preserve capital."
            )
            logger.info(f"[RISK FILTER] {reject_reason} for {market.market_id}")
            return RiskEvaluationResult(
                approved=False,
                reason=reject_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                target_price=target_price,
                martingale_step=step,
                stage_label=stage_label,
                multiplier=multiplier,
                effective_threshold=effective_threshold,
            )

        if self.martingale_enabled and step > 0:
            raw_contracts = int(self.default_order_contracts * multiplier)
            contracts = max(1, min(raw_contracts, max_possible_contracts))
            approval_reason = f"Martingale Recovery Order ({stage_label}) approved for {sym}"
            logger.info(
                f"[MARTINGALE ORDER SIZING] {sym} {stage_label}: {contracts} contracts "
                f"({multiplier:.0f}x base {self.default_order_contracts}) | Exposure: ${contracts * target_price:.2f}"
            )
        else:
            # Base sizing: Scaled dynamically with confidence excess over threshold
            confidence_scaler = 1.0 + max(0.0, (decision.confidence - self.confidence_threshold) * 2.0)
            budget = min(self.max_position_size_usdt, self.max_position_size_usdt * confidence_scaler)
            base_limit = int(budget / target_price)
            contracts = max(1, min(self.default_order_contracts, base_limit, max_possible_contracts))
            approval_reason = "Base round order approved"
            if recent_performance and recent_performance.get("consecutive_wins", 0) >= 3:
                contracts = min(max_possible_contracts, max(1, int(contracts * 1.2)))
                approval_reason += f" (Win streak boost: {recent_performance.get('consecutive_wins')} wins)"

        # Successfully Approved!
        self._total_approved += 1
        self._market_last_traded[market.market_id] = now

        logger.info(
            f"[RISK APPROVED] {decision.action} on {market.market_id} | {stage_label} | "
            f"Size: {contracts} contracts @ {target_price:.3f} | Conf: {decision.confidence:.2f} (Required: {effective_threshold:.2f})"
        )

        return RiskEvaluationResult(
            approved=True,
            reason=approval_reason,
            adjusted_contracts=contracts,
            confidence=decision.confidence,
            market_id=market.market_id,
            action=decision.action,
            target_price=target_price,
            martingale_step=step,
            stage_label=stage_label,
            multiplier=multiplier,
            effective_threshold=effective_threshold,
        )

    def _check_and_reset_daily_window(self) -> None:
        """Auto-reset daily risk budget every 24 hours."""
        now = time.time()
        if now - self._daily_start_time >= 86400:
            logger.info(
                f"[DAILY RISK RESET] 24-hour cycle elapsed. Resetting daily net PnL "
                f"(${self._daily_net_pnl:+.2f}) and daily loss (${self._daily_realized_loss:.2f})."
            )
            self._daily_net_pnl = 0.0
            self._daily_realized_loss = 0.0
            self._daily_start_time = now
            self._circuit_breaker_active = False

    def record_pnl(self, realized_pnl: float) -> None:
        """
        Update realized daily Net PnL and check circuit breaker.
        Net PnL offsets winning trades against losing trades.
        Daily loss is only accumulated if the daily Net PnL is in the red.
        """
        self._check_and_reset_daily_window()
        self._daily_net_pnl += realized_pnl

        if self._daily_net_pnl < 0:
            self._daily_realized_loss = round(abs(self._daily_net_pnl), 2)
            if self._daily_realized_loss >= self.max_daily_loss_usdt:
                self._circuit_breaker_active = True
                logger.error(
                    f"EMERGENCY: Daily drawdown limit reached! "
                    f"Net daily loss: -${self._daily_realized_loss:.2f} >= Limit ${self.max_daily_loss_usdt:.2f}"
                )
        else:
            # Account is net profitable for the day!
            self._daily_realized_loss = 0.0

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker and daily risk window."""
        self._circuit_breaker_active = False
        self._daily_net_pnl = 0.0
        self._daily_realized_loss = 0.0
        self._daily_start_time = time.time()
        logger.info("Circuit breaker has been manually reset.")

    def update_thresholds(
        self,
        confidence_threshold: Optional[float] = None,
        max_position_size_usdt: Optional[float] = None,
        default_order_contracts: Optional[int] = None,
        cooldown_seconds: Optional[int] = None,
        max_daily_loss_usdt: Optional[float] = None,
        max_concurrent_positions: Optional[int] = None,
        martingale_enabled: Optional[bool] = None,
        martingale_multiplier: Optional[float] = None,
        martingale_max_steps: Optional[int] = None,
        martingale_confidence_step: Optional[float] = None,
        martingale_max_confidence: Optional[float] = None,
        max_odds_cap: Optional[float] = None,
        min_odds_floor: Optional[float] = None,
        min_ev_edge: Optional[float] = None,
        min_time_left_seconds: Optional[int] = None,
        max_time_left_seconds: Optional[int] = None,
    ) -> None:
        """Dynamically update risk parameters and Martingale settings at runtime from web dashboard."""
        if confidence_threshold is not None:
            self.confidence_threshold = max(0.5, min(0.99, confidence_threshold))
        if max_position_size_usdt is not None:
            self.max_position_size_usdt = max(5.0, max_position_size_usdt)
        if default_order_contracts is not None:
            self.default_order_contracts = max(1, min(500, int(default_order_contracts)))
        if cooldown_seconds is not None:
            self.cooldown_seconds = max(5, cooldown_seconds)
        if max_daily_loss_usdt is not None:
            self.max_daily_loss_usdt = max(10.0, float(max_daily_loss_usdt))
        if max_concurrent_positions is not None:
            self.max_concurrent_positions = max(1, min(24, int(max_concurrent_positions)))
        if max_odds_cap is not None:
            self.max_odds_cap = max(0.30, min(0.90, max_odds_cap))
        if min_odds_floor is not None:
            self.min_odds_floor = max(0.01, min(0.50, min_odds_floor))
        if min_ev_edge is not None:
            self.min_ev_edge = max(0.01, min(0.25, min_ev_edge))
        if min_time_left_seconds is not None:
            self.min_time_left_seconds = max(30, min_time_left_seconds)
        if max_time_left_seconds is not None:
            self.max_time_left_seconds = max(60, max_time_left_seconds)
        if martingale_enabled is not None:
            self.martingale_enabled = bool(martingale_enabled)
        if martingale_multiplier is not None:
            self.martingale_multiplier = max(1.1, min(5.0, martingale_multiplier))
        if martingale_max_steps is not None:
            self.martingale_max_steps = max(1, min(10, martingale_max_steps))
        if martingale_confidence_step is not None:
            self.martingale_confidence_step = max(0.01, min(0.10, martingale_confidence_step))
        if martingale_max_confidence is not None:
            self.martingale_max_confidence = max(0.80, min(0.99, martingale_max_confidence))

        logger.info(
            f"Risk parameters updated: Conf={self.confidence_threshold:.2f}, "
            f"MaxOdds={self.max_odds_cap:.2f}, MinOdds={self.min_odds_floor:.2f}, MinEV={self.min_ev_edge:.2f}, "
            f"MinTimeLeft={self.min_time_left_seconds}s, MaxTimeLeft={self.max_time_left_seconds}s, "
            f"BaseContracts={self.default_order_contracts}, Size=${self.max_position_size_usdt:.1f}, "
            f"DailyLossLimit=${self.max_daily_loss_usdt:.1f}, MaxPos={self.max_concurrent_positions}, "
            f"Cooldown={self.cooldown_seconds}s, Martingale={self.martingale_enabled} "
            f"(Mult={self.martingale_multiplier}x, MaxSteps={self.martingale_max_steps})"
        )

    def _record_rejection(self, reason_code: str) -> None:
        self._total_rejected += 1
        if reason_code in self._rejection_counts:
            self._rejection_counts[reason_code] += 1

    @property
    def metrics(self) -> Dict[str, Any]:
        """Telemetry metrics for web dashboard including full Martingale recovery state."""
        approval_rate = (
            (self._total_approved / self._total_evaluated * 100.0)
            if self._total_evaluated > 0 else 0.0
        )
        current_mult = (
            self.martingale_multiplier ** self.current_martingale_step
            if (self.martingale_enabled and self.current_martingale_step > 0)
            else 1.0
        )
        return {
            "confidence_threshold": self.confidence_threshold,
            "max_position_size_usdt": self.max_position_size_usdt,
            "default_order_contracts": self.default_order_contracts,
            "max_concurrent_positions": self.max_concurrent_positions,
            "cooldown_seconds": self.cooldown_seconds,
            "max_daily_loss_usdt": self.max_daily_loss_usdt,
            "max_odds_cap": self.max_odds_cap,
            "min_odds_floor": self.min_odds_floor,
            "min_ev_edge": self.min_ev_edge,
            "min_time_left_seconds": self.min_time_left_seconds,
            "max_time_left_seconds": self.max_time_left_seconds,
            "daily_net_pnl": round(self._daily_net_pnl, 2),
            "daily_realized_loss": round(self._daily_realized_loss, 2),
            "circuit_breaker_active": self._circuit_breaker_active,
            "total_evaluated": self._total_evaluated,
            "total_approved": self._total_approved,
            "total_rejected": self._total_rejected,
            "approval_rate_pct": round(approval_rate, 1),
            "rejections_breakdown": self._rejection_counts,
            "martingale": {
                "enabled": self.martingale_enabled,
                "current_step": self.current_martingale_step,
                "max_steps": self.martingale_max_steps,
                "multiplier": self.martingale_multiplier,
                "current_multiplier": current_mult,
                "confidence_step": self.martingale_confidence_step,
                "max_confidence": self.martingale_max_confidence,
                "effective_threshold": round(self.get_effective_confidence_threshold(), 3),
                "stage_label": self.get_stage_label(),
                "consecutive_losses": self.consecutive_losses,
                "consecutive_wins": self.consecutive_wins,
                "last_settled_result": self.last_settled_result,
                "recovery_cycles_completed": self.recovery_cycles_completed,
                "symbol_steps": dict(self._symbol_martingale_step),
            }
        }
