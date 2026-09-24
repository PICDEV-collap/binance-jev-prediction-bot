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
    odds_yes: float = Field(..., ge=0.0, le=1.0)
    odds_no: float = Field(..., ge=0.0, le=1.0)
    spread: float = 0.01
    volume_24h: float = 0.0
    time_left_seconds: int = 300
    underlying_price: float = 0.0
    target_price: float = 0.0
    momentum_pct: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class JevEvaluationResult(BaseModel):
    """Structured decision returned by Jev AI."""
    action: Literal["BUY_YES", "BUY_NO", "PASS"]
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
            total=timeout_seconds,
            connect=1.0,
            sock_read=timeout_seconds
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
                                "enum": ["BUY_YES", "BUY_NO", "PASS"]
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence score from 0.0 to 1.0"
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "Concise quantitative rationale for decision"
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
                        "specialized in prediction markets. Output ONLY a valid JSON object matching "
                        "the schema with action, confidence, and reasoning."
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
                        f"Determine if there is edge to BUY_YES, BUY_NO, or PASS."
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
                    parsed_result = self._parse_api_response(data, elapsed_ms)
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

    def _parse_api_response(self, data: Dict[str, Any], elapsed_ms: float) -> JevEvaluationResult:
        """Parse structured JSON from Jev AI API response."""
        try:
            # Handle typical OpenAI/Typesafe AI response envelope
            content = ""
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"]
            elif "response" in data:
                content = data["response"]
            elif "action" in data and "confidence" in data:
                content = json.dumps(data)

            parsed = json.loads(content)
            action = parsed.get("action", "PASS")
            if action not in ["BUY_YES", "BUY_NO", "PASS"]:
                action = "PASS"

            confidence = float(parsed.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = parsed.get("reasoning", "Evaluated via Jev AI structured engine.")

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
            return JevEvaluationResult(
                action="PASS",
                confidence=0.5,
                reasoning=f"Parsing error: {err}",
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
        Deterministic/Bayesian heuristic fallback decision engine.
        Calculates implied theoretical probability based on price distance,
        momentum, time decay, and market spread.
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
            # Momentum weight decreases as time left increases
            momentum_effect = momentum * 0.08
            # Distance effect
            distance_effect = price_ratio * 15.0
            theoretical_prob = 0.5 + distance_effect + momentum_effect
        else:
            theoretical_prob = context.odds_yes + (momentum * 0.05)

        # Clip theoretical probability between 0.05 and 0.95
        theoretical_prob = max(0.05, min(0.95, theoretical_prob))

        # Edge calculation: discrepancy between theoretical probability and market odds
        edge_yes = theoretical_prob - context.odds_yes
        edge_no = (1.0 - theoretical_prob) - context.odds_no

        action: Literal["BUY_YES", "BUY_NO", "PASS"] = "PASS"
        confidence: float = 0.50
        reasoning: str = ""

        # Threshold to trigger an entry (must have at least +6% edge)
        if edge_yes > 0.06 and context.odds_yes <= 0.88:
            action = "BUY_YES"
            # Confidence scales with edge and momentum convergence
            confidence = min(0.96, 0.75 + (edge_yes * 1.5))
            reasoning = (
                f"Bullish skew: Model prob ({theoretical_prob:.1%}) exceeds market odds ({context.odds_yes:.1%}) "
                f"by +{edge_yes*100:.1f}%. Momentum {momentum:+.2f}% with {time_left}s remaining."
            )
        elif edge_no > 0.06 and context.odds_no <= 0.88:
            action = "BUY_NO"
            confidence = min(0.96, 0.75 + (edge_no * 1.5))
            reasoning = (
                f"Bearish skew: Model No prob ({1.0 - theoretical_prob:.1%}) exceeds market odds ({context.odds_no:.1%}) "
                f"by +{edge_no*100:.1f}%. Momentum {momentum:+.2f}% with {time_left}s remaining."
            )
        else:
            action = "PASS"
            confidence = max(0.40, 0.65 - max(abs(edge_yes), abs(edge_no)))
            reasoning = (
                f"Neutral/Fair valuation: Market odds ({context.odds_yes:.1%}/{context.odds_no:.1%}) "
                f"fairly price theoretical probability ({theoretical_prob:.1%}). Insufficient edge."
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
