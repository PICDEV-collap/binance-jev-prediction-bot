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
from typing import Dict, Optional, Tuple, Any
from pydantic import BaseModel, Field

from .jev_client import JevEvaluationResult, MarketContext

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
    timestamp: float = Field(default_factory=time.time)


class RiskGuard:
    """
    Quantitative pre-trade risk engine ensuring no orders breach risk tolerances.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.80,
        max_position_size_usdt: float = 50.0,
        default_order_contracts: int = 10,
        cooldown_seconds: int = 45,
        max_daily_loss_usdt: float = 200.0,
        max_concurrent_positions: int = 5,
        max_odds_cap: float = 0.90,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.max_position_size_usdt = max_position_size_usdt
        self.default_order_contracts = default_order_contracts
        self.cooldown_seconds = cooldown_seconds
        self.max_daily_loss_usdt = max_daily_loss_usdt
        self.max_concurrent_positions = max_concurrent_positions
        self.max_odds_cap = max_odds_cap

        # State tracking
        self._market_last_traded: Dict[str, float] = {}
        self._daily_realized_loss: float = 0.0
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
        }

    def validate_and_size_order(
        self,
        decision: JevEvaluationResult,
        market: MarketContext,
        current_open_positions_count: int = 0,
        recent_performance: Optional[Dict[str, Any]] = None,
    ) -> RiskEvaluationResult:
        """
        Evaluate proposed trade against all risk controls and determine safe sizing.
        Includes Approach 3 Hybrid feedback: dynamic defensive hurdles and anti-martingale sizing.
        """
        self._total_evaluated += 1
        now = time.time()

        # Gate 0: Signal is PASS
        if decision.action == "PASS":
            self._record_rejection("PASS_SIGNAL")
            return RiskEvaluationResult(
                approved=False,
                reason="Jev AI signal is PASS (Neutral/No edge)",
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action="PASS"
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
                action=decision.action
            )

        # Gate 2: Confidence Threshold Check (Required >= threshold)
        # Dynamic Defensive Hurdle: if on consecutive losses, demand higher conviction
        effective_threshold = self.confidence_threshold
        consecutive_losses = 0
        if recent_performance:
            consecutive_losses = recent_performance.get("consecutive_losses", 0)
            if consecutive_losses >= 2:
                # Dynamic defensive hurdle: raise required threshold by 3% per loss past 1 (capped at 92%)
                effective_threshold = min(0.92, self.confidence_threshold + (0.03 * (consecutive_losses - 1)))

        if decision.confidence < effective_threshold:
            self._record_rejection("LOW_CONFIDENCE")
            filter_reason = (
                f"AI Signal: {decision.action} ({decision.confidence*100:.1f}%) "
                f"— Filtered: Below {effective_threshold*100:.0f}% conviction gate"
            )
            if consecutive_losses >= 2:
                filter_reason += f" (Defensive gate active: {consecutive_losses} consecutive losses on {market.symbol})"
            logger.info(f"[RISK FILTER] {filter_reason} for {market.market_id}. Capital preserved.")
            return RiskEvaluationResult(
                approved=False,
                reason=filter_reason,
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action
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
                action=decision.action
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
                action=decision.action
            )

        # Gate 5: Odds Pricing and Skew Sanity Check
        target_price = market.odds_yes if decision.action in ("BUY_YES", "UP") else market.odds_no
        if target_price <= 0.02 or target_price > self.max_odds_cap:
            self._record_rejection("EXTREME_ODDS_RISK")
            return RiskEvaluationResult(
                approved=False,
                reason=(
                    f"Entry price {target_price:.3f} outside safe odds bounds "
                    f"(0.02 - {self.max_odds_cap:.2f})"
                ),
                adjusted_contracts=0,
                confidence=decision.confidence,
                market_id=market.market_id,
                action=decision.action,
                target_price=target_price
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
                target_price=target_price
            )

        # --- Position Sizing Calculation ---
        # Formula: Cap position exposure by max_position_size_usdt
        # Scaled dynamically with confidence excess over threshold
        confidence_scaler = 1.0 + (decision.confidence - self.confidence_threshold) * 2.0
        budget = min(self.max_position_size_usdt, self.max_position_size_usdt * confidence_scaler)
        max_possible_contracts = int(budget / target_price) if target_price > 0 else 0

        contracts = max(1, min(self.default_order_contracts, max_possible_contracts))

        # Anti-Martingale / Defensive sizing if on losing streak
        if consecutive_losses >= 2:
            contracts = max(1, contracts // 2)
            logger.info(
                f"[RISK DEFENSE] Sizing reduced to {contracts}x on {market.symbol} "
                f"due to {consecutive_losses} consecutive losses."
            )
        elif recent_performance and recent_performance.get("consecutive_wins", 0) >= 3:
            contracts = min(max_possible_contracts, int(contracts * 1.2))

        # Successfully Approved!
        self._total_approved += 1
        self._market_last_traded[market.market_id] = now
        approval_reason = "All risk & execution gates passed successfully"
        if consecutive_losses >= 2:
            approval_reason += f" (Defensive sizing: {consecutive_losses} losses on {market.symbol})"
        elif recent_performance and recent_performance.get("consecutive_wins", 0) >= 2:
            approval_reason += f" (Momentum streak: {recent_performance.get('consecutive_wins')} wins on {market.symbol})"

        logger.info(
            f"[RISK APPROVED] {decision.action} on {market.market_id} | "
            f"Size: {contracts} contracts @ {target_price:.3f} | Conf: {decision.confidence:.2f}"
        )

        return RiskEvaluationResult(
            approved=True,
            reason=approval_reason,
            adjusted_contracts=contracts,
            confidence=decision.confidence,
            market_id=market.market_id,
            action=decision.action,
            target_price=target_price
        )

    def record_pnl(self, realized_pnl: float) -> None:
        """Update realized daily PnL and check circuit breaker."""
        if realized_pnl < 0:
            self._daily_realized_loss += abs(realized_pnl)
            if self._daily_realized_loss >= self.max_daily_loss_usdt:
                self._circuit_breaker_active = True
                logger.error(
                    f"EMERGENCY: Daily drawdown limit reached! "
                    f"Total loss: ${self._daily_realized_loss:.2f}"
                )

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker."""
        self._circuit_breaker_active = False
        self._daily_realized_loss = 0.0
        logger.info("Circuit breaker has been manually reset.")

    def update_thresholds(
        self,
        confidence_threshold: Optional[float] = None,
        max_position_size_usdt: Optional[float] = None,
        cooldown_seconds: Optional[int] = None,
    ) -> None:
        """Dynamically update risk parameters at runtime from web dashboard."""
        if confidence_threshold is not None:
            self.confidence_threshold = max(0.5, min(0.99, confidence_threshold))
        if max_position_size_usdt is not None:
            self.max_position_size_usdt = max(5.0, max_position_size_usdt)
        if cooldown_seconds is not None:
            self.cooldown_seconds = max(5, cooldown_seconds)
        logger.info(
            f"Risk parameters updated: Conf={self.confidence_threshold:.2f}, "
            f"Size=${self.max_position_size_usdt:.1f}, Cooldown={self.cooldown_seconds}s"
        )

    def _record_rejection(self, reason_code: str) -> None:
        self._total_rejected += 1
        if reason_code in self._rejection_counts:
            self._rejection_counts[reason_code] += 1

    @property
    def metrics(self) -> Dict[str, Any]:
        """Telemetry metrics for web dashboard."""
        approval_rate = (
            (self._total_approved / self._total_evaluated * 100.0)
            if self._total_evaluated > 0 else 0.0
        )
        return {
            "confidence_threshold": self.confidence_threshold,
            "max_position_size_usdt": self.max_position_size_usdt,
            "cooldown_seconds": self.cooldown_seconds,
            "max_daily_loss_usdt": self.max_daily_loss_usdt,
            "daily_realized_loss": round(self._daily_realized_loss, 2),
            "circuit_breaker_active": self._circuit_breaker_active,
            "total_evaluated": self._total_evaluated,
            "total_approved": self._total_approved,
            "total_rejected": self._total_rejected,
            "approval_rate_pct": round(approval_rate, 1),
            "rejections_breakdown": self._rejection_counts,
        }
