"""
Jev AI Decision Engine Client.
Connects to Jev AI API (https://api.typesafe.ai/v1/evaluate) using structured outputs
to generate high-conviction directional decisions (BUY_YES, BUY_NO, PASS) and confidence scores.
"""

from __future__ import annotations
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, asdict
from typing import Literal, Optional, Dict, Any

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger("jev_ai_client")


class MarketContext(BaseModel):
    """Normalized snapshot of a prediction market round."""
    market_id: str
    symbol: str
    question: str
    timeframe: str = "15m"  # "5m", "15m", "1h", "1d"
    odds_yes: float = Field(..., ge=0.0, le=1.0)  # UP Odds
    odds_no: float = Field(..., ge=0.0, le=1.0)   # DOWN Odds
    spread: float = 0.01
    volume_24h: float = 0.0
    time_left_seconds: int = 300
    underlying_price: float = 0.0  # Current Price
    target_price: float = 0.0      # Price to Beat
    price_diff: float = 0.0        # Current Price - Price to Beat
    momentum_pct: float = 0.0
    bid: float = 0.49
    ask: float = 0.51
    recent_performance: Optional[Dict[str, Any]] = None
    timestamp: float = Field(default_factory=time.time)

    @property
    def odds_up(self) -> float:
        return self.odds_yes

    @property
    def odds_down(self) -> float:
        return self.odds_no


class JevEvaluationResult(BaseModel):
    """Structured decision returned by Jev AI (Strictly binary UP or DOWN)."""
    action: Literal["UP", "DOWN", "BUY_YES", "BUY_NO"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    model: str = "jev-predict-v1"
    latency_ms: float = 0.0
    is_mock: bool = False
    timestamp: float = Field(default_factory=time.time)


class JevClient:
    """
    Asynchronous client for interacting with Jev AI / Typesafe AI Decision API.
    Maintains a persistent connection pool via aiohttp.ClientSession for minimal latency.
    """

    def __init__(
        self,
        api_key: str = "",
        endpoint: str = "https://api.typesafe.ai/v1/evaluate",
        model: str = "jev-predict-v1",
        timeout_seconds: float = 3.5,
    ) -> None:
        self.api_key = api_key.strip()
        self.endpoint = endpoint
        self.model = model
        self.timeout = aiohttp.ClientTimeout(
            total=max(timeout_seconds, 6.0),
            connect=4.0,
            sock_read=max(timeout_seconds, 6.0)
        )
        self._session: Optional[aiohttp.ClientSession] = None
        self._total_evaluations: int = 0
        self._average_latency_ms: float = 0.0

    async def start(self) -> None:
        """Initialize persistent HTTP session."""
        if self._session is None or self._session.closed:
            # Optimal connection pooling for high-frequency evaluation
            connector = aiohttp.TCPConnector(
                limit=50,
                keepalive_timeout=60.0,
                enable_cleanup_closed=True
            )
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Binance-Jev-Bot/1.0",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            self._session = aiohttp.ClientSession(
                connector=connector,
                headers=headers,
                timeout=self.timeout
            )
            logger.info("Jev AI HTTP Client session initialized.")

    async def close(self) -> None:
        """Close persistent HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            # Allow underlying SSL connections to terminate
            await asyncio.sleep(0.05)
            logger.info("Jev AI HTTP Client session closed.")

    async def evaluate_market(self, context: MarketContext) -> JevEvaluationResult:
        """
        Send market telemetry to Jev AI and return a structured directional decision.
        Falls back to local quantitative heuristic if API key is not configured or in offline mode.
        """
        start_time = time.perf_counter()

        # If no API key configured, use local intelligent heuristic model
        if not self.api_key:
            return await self._evaluate_heuristic(context, start_time)

        if self._session is None or self._session.closed:
            await self.start()

        perf_summary = context.recent_performance.get("summary", "") if context.recent_performance else ""
        recent_track_record_line = f"Recent Track Record: {perf_summary}\n" if perf_summary else ""

        if "systemone" in self.endpoint:
            clean_sym = context.symbol.replace("USDT", "")
            diff_str = f"{context.price_diff:+,.2f}" if context.price_diff else f"{context.underlying_price - context.target_price:+,.2f}"
            market_state = (
                f"Binance Up or Down Prediction Market Analysis:\n"
                f"Market: {clean_sym} Up or Down {context.timeframe} (ID: {context.market_id})\n"
                f"Symbol: {context.symbol} | Timeframe: {context.timeframe}\n"
                f"Current Price: ${context.underlying_price:,.2f} | Price to Beat: ${context.target_price:,.2f} (Diff: {diff_str})\n"
                f"Current Market Odds: UP {context.odds_yes:.3f} ({context.odds_yes*100:.1f}%) | "
                f"DOWN {context.odds_no:.3f} ({context.odds_no*100:.1f}%)\n"
                f"Spread: {context.spread:.3f} | 24h Volume: ${context.volume_24h:,.0f}\n"
                f"Momentum: {context.momentum_pct:+.3f}%\n"
                f"{recent_track_record_line}"
                f"Time Remaining to Expiration: {context.time_left_seconds} seconds."
            )
            payload = {
                "model": self.model or "jev-latest",
                "state": market_state,
                "questions": {
                    "action": {
                        "type": "choice",
                        "instructions": f"Predict strictly whether Current Price will settle UP or DOWN at {context.timeframe} expiration (binary prediction)",
                        "criteria": {
                            "UP": "High conviction that Current Price will settle greater than or equal to Price to Beat at round expiration",
                            "DOWN": "High conviction that Current Price will settle strictly below Price to Beat at round expiration"
                        }
                    },
                    "settle_above_price_to_beat": {
                        "type": "noul",
                        "instructions": "Will the underlying market price settle greater than or equal to the Price to Beat at expiration?"
                    }
                }
            }
        else:
            payload = {
                "model": self.model,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "prediction_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["BUY_YES", "BUY_NO", "UP", "DOWN"]
                                },
                                "confidence": {
                                    "type": "number",
                                    "description": "Confidence score from 0.50 to 1.0"
                                },
                                "reasoning": {
                                    "type": "string",
                                    "description": "Concise quantitative rationale for UP or DOWN decision"
                                }
                            },
                            "required": ["action", "confidence", "reasoning"],
                            "additionalProperties": False
                        }
                    }
                },
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are Jev AI, an ultra-low latency quantitative decision engine "
                            "specialized in binary prediction markets. Binary decision mode is strictly active: "
                            "you MUST predict either UP or DOWN. "
                            "FEEDBACK DIRECTIVE: If a recent track record is provided, use it to gauge current market regime consistency. "
                            "If on a losing streak, require higher analytical momentum conviction before assigning high confidence. "
                            "AVOID GAMBLER'S FALLACY: Past outcomes do NOT guarantee an alternation of UP or DOWN. "
                            "Output ONLY a valid JSON object matching the schema with action, confidence, and reasoning."
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Evaluate prediction market opportunity:\n"
                            f"Market: {context.question} (ID: {context.market_id})\n"
                            f"Symbol: {context.symbol}\n"
                            f"Current Odds - Yes: {context.odds_yes:.3f} | No: {context.odds_no:.3f}\n"
                            f"Spread: {context.spread:.4f} | 24h Volume: ${context.volume_24h:,.0f}\n"
                            f"Time Remaining: {context.time_left_seconds}s\n"
                            f"Underlying Spot: ${context.underlying_price:,.2f} | Target: ${context.target_price:,.2f}\n"
                            f"5m Momentum: {context.momentum_pct:+.2f}%\n"
                            f"{recent_track_record_line}"
                            f"Determine whether to predict UP or DOWN."
                        )
                    }
                ]
            }

        try:
            assert self._session is not None
            async with self._session.post(self.endpoint, json=payload) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                if resp.status == 200:
                    data = await resp.json()
                    # Parse Jev AI structured response
                    parsed_result = self._parse_api_response(data, elapsed_ms, context)
                    self._update_stats(elapsed_ms)
                    return parsed_result
                else:
                    error_text = await resp.text()
                    logger.warning(
                        f"Jev AI API error HTTP {resp.status}: {error_text[:200]}. "
                        f"Falling back to local heuristic engine."
                    )
                    return await self._evaluate_heuristic(context, start_time)

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning(f"Jev AI API request timed out ({elapsed_ms:.1f}ms). Falling back to heuristic.")
            return await self._evaluate_heuristic(context, start_time)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Jev AI client exception: {e}. Falling back to heuristic.")
            return await self._evaluate_heuristic(context, start_time)

    def _parse_api_response(
        self,
        data: Dict[str, Any],
        elapsed_ms: float,
        context: Optional[MarketContext] = None
    ) -> JevEvaluationResult:
        """Parse structured JSON from Jev AI API response."""
        try:
            # Handle Typesafe SystemOne format response
            if "answers" in data:
                answers = data.get("answers", {})
                action_ans = answers.get("action", {})
                raw_action = str(action_ans.get("choice", "UP")).upper()
                if raw_action in ["DOWN", "BUY_NO"]:
                    action: Literal["UP", "DOWN", "BUY_YES", "BUY_NO"] = "DOWN"
                else:
                    action = "UP"

                probs = action_ans.get("probabilities", {})
                yes_ans = answers.get("settle_above_price_to_beat", answers.get("settle_above_strike", {}))
                yes_prob = float(yes_ans.get("noul", context.odds_yes if context else 0.50))

                odds_yes = context.odds_yes if context else 0.50
                odds_no = context.odds_no if context else 0.50

                if action in ["UP", "BUY_YES"]:
                    prob_choice = max(yes_prob, probs.get("UP", 0.50))
                    confidence = round(max(0.50, min(1.0, float(prob_choice))), 3)
                    edge = yes_prob - odds_yes
                    reasoning = (
                        f"Binary UP conviction: Model P(UP) {yes_prob:.1%} vs market odds ({odds_yes:.1%}) "
                        f"with {edge*100:+.1f}% edge. Model confidence: {confidence*100:.1f}%."
                    )
                else:
                    prob_down = 1.0 - yes_prob
                    prob_choice = max(prob_down, probs.get("DOWN", 0.50))
                    confidence = round(max(0.50, min(1.0, float(prob_choice))), 3)
                    edge = prob_down - odds_no
                    reasoning = (
                        f"Binary DOWN conviction: Model P(DOWN) {prob_down:.1%} vs market odds ({odds_no:.1%}) "
                        f"with {edge*100:+.1f}% edge. Model confidence: {confidence*100:.1f}%."
                    )

                return JevEvaluationResult(
                    action=action,
                    confidence=confidence,
                    reasoning=reasoning,
                    model=data.get("model", self.model),
                    latency_ms=round(elapsed_ms, 2),
                    is_mock=False
                )

            # Handle typical OpenAI response envelope
            content = ""
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"]
            elif "response" in data:
                content = data["response"]
            elif "action" in data and "confidence" in data:
                content = json.dumps(data)

            parsed = json.loads(content)
            raw_action = str(parsed.get("action", "UP")).upper()
            action = "DOWN" if raw_action in ["DOWN", "BUY_NO"] else "UP"
            confidence = float(parsed.get("confidence", 0.55))
            confidence = max(0.50, min(1.0, confidence))
            reasoning = parsed.get("reasoning", f"Binary {action} conviction evaluated via Jev AI structured engine.")

            return JevEvaluationResult(
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                model=self.model,
                latency_ms=round(elapsed_ms, 2),
                is_mock=False
            )
        except Exception as err:
            logger.error(f"Failed to parse Jev AI structured payload: {err}")
            fallback_action = "UP" if (context and context.odds_yes >= 0.50) else "DOWN"
            return JevEvaluationResult(
                action=fallback_action,
                confidence=0.50,
                reasoning=f"Parsing error ({err}). Defaulting to {fallback_action}.",
                model=self.model,
                latency_ms=round(elapsed_ms, 2),
                is_mock=False
            )

    async def _evaluate_heuristic(
        self,
        context: MarketContext,
        start_time: float
    ) -> JevEvaluationResult:
        """
        Deterministic/Bayesian heuristic decision engine.
        Calculates strictly binary directional conviction (UP or DOWN) based on
        price distance, momentum, time decay, and market spread.
        """
        # Slight simulated computation time (5-15ms)
        await asyncio.sleep(0.01)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        # Calculate delta between spot and target
        spot = context.underlying_price
        target = context.target_price
        momentum = context.momentum_pct
        time_left = max(10, context.time_left_seconds)

        # Baseline theoretical Yes probability calculation
        if target > 0 and spot > 0:
            price_ratio = (spot - target) / target
            momentum_effect = momentum * 0.08
            distance_effect = price_ratio * 15.0
            theoretical_prob = 0.5 + distance_effect + momentum_effect
        else:
            theoretical_prob = context.odds_yes + (momentum * 0.05)

        # Clip theoretical probability between 0.05 and 0.95
        theoretical_prob = max(0.05, min(0.95, theoretical_prob))

        # Edge calculation: discrepancy between theoretical probability and market odds
        edge_yes = theoretical_prob - context.odds_yes
        edge_no = (1.0 - theoretical_prob) - context.odds_no

        # Binary decision: strictly UP or DOWN based on expectancy / probability
        feedback_note = ""
        streak_modifier = 1.0
        if context.recent_performance:
            consecutive_losses = context.recent_performance.get("consecutive_losses", 0)
            consecutive_wins = context.recent_performance.get("consecutive_wins", 0)
            if consecutive_losses >= 2:
                # Regulate confidence down slightly during drawdown to demand higher signal threshold
                streak_modifier = max(0.85, 1.0 - (0.05 * (consecutive_losses - 1)))
                feedback_note = f" [Adaptive Defense: {consecutive_losses} consecutive losses on {context.symbol}]"
            elif consecutive_wins >= 2:
                streak_modifier = min(1.05, 1.0 + (0.02 * consecutive_wins))
                feedback_note = f" [Momentum Feedback: {consecutive_wins} win streak on {context.symbol}]"

        if theoretical_prob >= 0.50 or edge_yes >= edge_no:
            action: Literal["UP", "DOWN", "BUY_YES", "BUY_NO"] = "UP"
            raw_conf = max(0.50, min(0.98, theoretical_prob + (max(0.0, edge_yes) * 0.5)))
            confidence = max(0.50, min(0.98, raw_conf * streak_modifier))
            reasoning = (
                f"Binary UP conviction: Theoretical P(UP) {theoretical_prob:.1%} (Market Odds: {context.odds_yes:.1%}, "
                f"Edge: {edge_yes*100:+.1f}%). Momentum: {momentum:+.2f}% with {time_left}s remaining.{feedback_note}"
            )
        else:
            action = "DOWN"
            prob_down = 1.0 - theoretical_prob
            raw_conf = max(0.50, min(0.98, prob_down + (max(0.0, edge_no) * 0.5)))
            confidence = max(0.50, min(0.98, raw_conf * streak_modifier))
            reasoning = (
                f"Binary DOWN conviction: Theoretical P(DOWN) {prob_down:.1%} (Market Odds: {context.odds_no:.1%}, "
                f"Edge: {edge_no*100:+.1f}%). Momentum: {momentum:+.2f}% with {time_left}s remaining.{feedback_note}"
            )

        self._update_stats(elapsed_ms)
        return JevEvaluationResult(
            action=action,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            model="jev-heuristic-fallback",
            latency_ms=round(elapsed_ms, 2),
            is_mock=True
        )

    def _update_stats(self, elapsed_ms: float) -> None:
        self._total_evaluations += 1
        self._average_latency_ms = (
            (self._average_latency_ms * (self._total_evaluations - 1) + elapsed_ms)
            / self._total_evaluations
        )

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_evaluations": self._total_evaluations,
            "average_latency_ms": round(self._average_latency_ms, 2),
            "endpoint": self.endpoint,
            "model": self.model,
            "has_api_key": bool(self.api_key),
        }
