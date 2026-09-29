"""
Jev AI Decision Engine Client.
Connects to Jev AI API (https://api.typesafe.ai/v1/evaluate) using structured outputs
to generate high-conviction directional decisions (BUY_YES, BUY_NO, PASS) and confidence scores.
"""

from __future__ import annotations
import asyncio
import json
import logging
import math
import random
import time
from dataclasses import dataclass, asdict
from typing import Literal, Optional, Dict, Any, List

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
    # ``odds_yes/no`` are model-generated directional estimates, not executable contract prices.
    spread: Optional[float] = None  # Real prediction-contract spread is unavailable from this feed.
    contract_up_ask: Optional[float] = None
    contract_down_ask: Optional[float] = None
    contract_quote_timestamp: Optional[float] = None
    contract_quote_source: str = "unavailable"
    volume_24h: float = 0.0
    time_left_seconds: int = 300
    underlying_price: float = 0.0  # Current Price
    target_price: float = 0.0      # Price to Beat
    price_diff: float = 0.0        # Current Price - Price to Beat
    momentum_pct: float = 0.0
    bid: float = 0.49
    ask: float = 0.51
    recent_performance: Optional[Dict[str, Any]] = None
    martingale_step: int = 0
    martingale_stage: str = "ไม้ 1 (Base)"
    effective_hurdle: float = 0.80
    timestamp: float = Field(default_factory=time.time)

    # --- Enriched Quantitative & Volatility Features ---
    atr_1m: float = 0.0
    dvr_ratio: float = 0.0             # Distance-to-expected-travel ratio, not a statistical sigma
    strike_diff_bps: float = 0.0       # Normalized distance to strike in basis points ((spot - strike) / strike * 10000)
    strike_diff_pct: float = 0.0       # Normalized distance to strike in percentage ((spot - strike) / strike * 100)
    strike_velocity_bps_s: float = 0.0 # Velocity of distance to strike (bps / second)
    strike_velocity_desc: str = "STEADY_STAGNANT" # EXPANDING_BULL_BUFFER, EXPANDING_BEAR_BUFFER, RETREATING_TO_STRIKE, STEADY_STAGNANT
    market_session: str = "ASIAN_HOURS" # ASIAN_HOURS, LONDON_ACTIVE, US_EU_OVERLAP, NEW_YORK_ACTIVE, PACIFIC_TRANSITION
    recent_candles_summary: str = "[]" # Sequence of recent 3 completed 1m candles (e.g. [+0.08% Bull, -0.02% Bear])
    macro_trend_15m: str = "NEUTRAL"   # Macro trend direction on higher timeframe (BULLISH, BEARISH, NEUTRAL)
    rsi_1m: float = 50.0               # 1m Fast RSI
    rsi_5m: float = 50.0               # 5m Trend RSI
    ema_trend: str = "NEUTRAL_CHOP"    # STRONG_UPTREND, STRONG_DOWNTREND, etc.
    order_book_imbalance: float = 0.0  # -1.0 to +1.0 (Bid vs Ask pressure)
    market_regime: str = "RANGING"     # TREND_EXPANSION, RANGING, HIGH_VOLATILITY_CHOP
    expiry_danger_flag: bool = False   # True if < 60s and dangerously close to strike
    btc_correlation_dir: str = "FLAT"  # BULLISH, BEARISH, FLAT
    is_stale: bool = False             # Flagged True if network/data silence exceeds tolerance threshold
    strike_confirmed: bool = False     # Flagged True ONLY when Price to Beat (startPrice) is confirmed from Binance SAPI
    round_start_time_sec: Optional[float] = None
    round_end_time_sec: Optional[float] = None
    binance_topic_id: Optional[str] = None
    binance_market_ids: List[str] = Field(default_factory=list)
    spot_source_timestamp_ms: Optional[int] = None
    spot_data_age_ms: Optional[float] = None
    indicator_data_ready: bool = False
    one_minute_sample_count: int = 0
    five_minute_sample_count: int = 0
    momentum_available: bool = False
    obi_available: bool = False

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
    # Probability that UP settles true. RiskGuard converts this to the selected side's
    # probability; confidence remains a separate conviction/hurdle signal.
    probability_up: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reasoning: str
    model: str = "jev-predict-v1"
    latency_ms: float = 0.0
    is_mock: bool = False
    fallback_reason: Optional[str] = None
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
            return await self._evaluate_heuristic(
                context,
                start_time,
                fallback_reason="missing_api_key",
            )

        if self._session is None or self._session.closed:
            await self.start()

        perf_summary = context.recent_performance.get("summary", "") if context.recent_performance else ""
        recent_track_record_line = f"Recent Track Record: {perf_summary}\n" if perf_summary else ""
        martingale_directive = ""
        if context.martingale_step > 0:
            martingale_directive = (
                f"\n🚨 MARTINGALE RECOVERY ACTIVE: {context.martingale_stage} (Step {context.martingale_step}). "
                f"Elevated conviction hurdle is {context.effective_hurdle*100:.0f}%. "
                f"Only assign confidence >= {context.effective_hurdle*100:.0f}% if trend alignment, odds discrepancy, and momentum offer exceptional edge. "
                f"Do not guess — protect recovery capital."
            )

        tf_directive = ""
        if context.timeframe.lower() == "15m":
            tf_directive = (
                "\n⏱️ 15M TIMEFRAME PERSISTENCE: Macro candle trend structure and order flow carry higher inertia and lower noise. "
                "Prioritize sustained directional persistence over minor 1m counter-ticks."
            )

        quote_age_ms = (
            max(0.0, (time.time() - context.contract_quote_timestamp) * 1000.0)
            if context.contract_quote_timestamp is not None
            else None
        )
        if context.contract_up_ask is not None and context.contract_down_ask is not None:
            quote_age_text = f"{quote_age_ms:.0f}ms" if quote_age_ms is not None else "unknown"
            contract_quote_line = (
                f"Executable Binance buy quotes for this amount: UP ${context.contract_up_ask:.4f} | "
                f"DOWN ${context.contract_down_ask:.4f} | source={context.contract_quote_source} | "
                f"age={quote_age_text}\n"
            )
        else:
            contract_quote_line = "Executable Binance buy quotes: unavailable; do not treat model estimates as market prices.\n"

        data_quality_line = (
            f"Data quality: spot age={context.spot_data_age_ms:.0f}ms | "
            f"1m candles={context.one_minute_sample_count} | 5m candles={context.five_minute_sample_count} | "
            f"indicators_ready={context.indicator_data_ready} | "
            f"spot top-of-book OBI={'available' if context.obi_available else 'unavailable'} | "
            f"momentum={'available' if context.momentum_available else 'unavailable'}\n"
            if context.spot_data_age_ms is not None
            else "Data quality: spot event timestamp unavailable.\n"
        )

        ev_directive = (
            "\nExpected value rule: probability and conviction are separate. A low contract price alone does not imply positive EV. "
            "Estimate P(UP) explicitly; compare the selected side's probability with its executable buy quote, allowing for fees and slippage. "
            "Do not invent quote, spread, volume, or unavailable indicator values. "
            "When trend and spot top-of-book imbalance conflict, lower conviction."
        )

        if "systemone" in self.endpoint:
            clean_sym = context.symbol.replace("USDT", "")
            diff_str = f"{context.price_diff:+,.2f}" if context.price_diff else f"{context.underlying_price - context.target_price:+,.2f}"
            bps_str = f"{context.strike_diff_bps:+.1f} bps ({context.strike_diff_pct:+.2f}%)"
            tf_secs = 300 if "5m" in context.timeframe else (900 if "15m" in context.timeframe else (3600 if "1h" in context.timeframe else 86400))
            elapsed_pct = max(0, min(100, int(((tf_secs - context.time_left_seconds) / tf_secs) * 100)))

            vel_str = f" | Velocity: {context.strike_velocity_bps_s:+.2f} bps/s ({context.strike_velocity_desc})" if (context.strike_velocity_bps_s != 0.0 or context.strike_velocity_desc != "STEADY_STAGNANT") else ""
            dvr_line = f"Volatility & Strike Distance: ATR(1m) = ${context.atr_1m:.2f} | Strike Diff = {bps_str}{vel_str} | DVR = {context.dvr_ratio:+.2f} expected-travel units (ATR scaled by square-root time, not calibrated sigma)\n" if context.atr_1m > 0 else f"Strike Distance: {bps_str}{vel_str}\n"
            pa_line = f"Price Action & Macro Trend: Recent 1m Candles = {context.recent_candles_summary} | Macro Trend (5m/15m) = {context.macro_trend_15m} | Session: {context.market_session}\n"
            micro_line = f"Spot top-of-book imbalance = {context.order_book_imbalance:+.2f} ({'available' if context.obi_available else 'unavailable'}) | BTC short-term direction = {context.btc_correlation_dir}\n"
            tech_line = f"Technicals: Trend = {context.ema_trend} | RSI(1m) = {context.rsi_1m:.1f} | RSI(5m) = {context.rsi_5m:.1f} | Regime = {context.market_regime}\n"
            danger_line = "⚠️ EXPIRY DANGER ZONE ACTIVE: Under 60s remaining and price is inside volatility noise buffer. Exercise extreme caution.\n" if context.expiry_danger_flag else ""
            
            market_state = (
                f"Binance Up or Down Prediction Market Analysis:\n"
                f"Market: {clean_sym} Up or Down {context.timeframe} (ID: {context.market_id})\n"
                f"Symbol: {context.symbol} | Timeframe: {context.timeframe}\n"
                f"Current Price: ${context.underlying_price:,.2f} | Price to Beat: ${context.target_price:,.2f} (Diff: {diff_str} | {bps_str})\n"
                f"Model-derived directional estimate (not a contract price): UP {context.odds_yes:.3f} | DOWN {context.odds_no:.3f}\n"
                f"{contract_quote_line}"
                f"Underlying spot 24h quote volume: ${context.volume_24h:,.0f}\n"
                f"Approx. 5m spot momentum: {context.momentum_pct:+.3f}% ({'available' if context.momentum_available else 'unavailable'})\n"
                f"{data_quality_line}"
                f"{dvr_line}"
                f"{pa_line}"
                f"{micro_line}"
                f"{tech_line}"
                f"{danger_line}"
                f"{recent_track_record_line}"
                f"{martingale_directive}"
                f"{tf_directive}"
                f"{ev_directive}\n"
                f"Round Progress: {elapsed_pct}% elapsed ({context.time_left_seconds} seconds remaining to expiration)."
            )
            payload = {
                "model": self.model or "jev-latest",
                "state": market_state,
                "questions": {
                    "action": {
                        "type": "choice",
                        "instructions": (
                            f"Predict strictly whether Current Price will settle UP or DOWN at {context.timeframe} expiration. "
                            "The action must agree with probability_up: UP requires probability_up >= 0.50; "
                            "DOWN requires probability_up < 0.50. Correct either field before responding if they conflict."
                        ),
                        "criteria": {
                            "UP": "High conviction that Current Price will settle greater than or equal to Price to Beat at round expiration",
                            "DOWN": "High conviction that Current Price will settle strictly below Price to Beat at round expiration"
                        }
                    },
                    "settle_above_price_to_beat": {
                        "type": "noul",
                        "instructions": "Will the underlying market price settle greater than or equal to the Price to Beat at expiration?"
                    },
                    "probability_up": {
                        "type": "noul",
                        "instructions": (
                            "Give your explicit probability from 0 to 1 that the underlying price settles UP. "
                            "This is separate from confidence and must agree with action: UP is >= 0.50; DOWN is < 0.50."
                        )
                    }
                }
            }
        else:
            diff_str = f"{context.price_diff:+,.2f}" if context.price_diff else f"{context.underlying_price - context.target_price:+,.2f}"
            bps_str = f"{context.strike_diff_bps:+.1f} bps ({context.strike_diff_pct:+.2f}%)"
            tf_secs = 300 if "5m" in context.timeframe else (900 if "15m" in context.timeframe else (3600 if "1h" in context.timeframe else 86400))
            elapsed_pct = max(0, min(100, int(((tf_secs - context.time_left_seconds) / tf_secs) * 100)))

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
                                    "description": "Analytical conviction from 0.50 to 1.0; do not use this as the outcome probability"
                                },
                                "probability_up": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0,
                                    "description": "Explicit probability that the underlying price settles UP, distinct from conviction"
                                },
                                "reasoning": {
                                    "type": "string",
                                    "description": "Concise quantitative rationale for UP or DOWN decision"
                                }
                            },
                            "required": ["action", "confidence", "probability_up", "reasoning"],
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
                            "TIMEFRAME PERSISTENCE: For 15m markets, give priority to macro trend structure and order flow over transient noise. "
                            "EXPECTED VALUE: A low quote alone does not imply positive expectancy. Estimate P(UP) separately from conviction and compare it with the executable quote. "
                            "Demand multi-factor confluence (EMA trend, OBI flow, and DVR) before assigning high confidence (>= 0.65). "
                            "When signals conflict (e.g. Trend vs OBI contradiction), penalize confidence towards neutral (0.50). "
                            "CONSISTENCY: action UP requires probability_up >= 0.50; action DOWN requires probability_up < 0.50. "
                            "Before returning JSON, verify these fields agree and correct the response if they do not. "
                            "FEEDBACK DIRECTIVE: If a recent track record is provided, use it to gauge current market regime consistency. "
                            "MARTINGALE RECOVERY RULES: When Martingale Recovery is active, an escalating conviction hurdle is enforced. "
                            "Demand higher analytical momentum and price distance conviction before outputting high confidence. "
                            "AVOID GAMBLER'S FALLACY: Past outcomes do NOT guarantee an alternation of UP or DOWN. "
                            "Output ONLY a valid JSON object matching the schema with action, confidence, probability_up, and reasoning."
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Evaluate prediction market opportunity:\n"
                            f"Market: {context.question} (ID: {context.market_id})\n"
                            f"Symbol: {context.symbol} | Timeframe: {context.timeframe}\n"
                            f"Model-derived directional estimate (not a contract price): UP {context.odds_yes:.3f} | DOWN {context.odds_no:.3f}\n"
                            f"{contract_quote_line}"
                            f"Underlying spot 24h quote volume: ${context.volume_24h:,.0f}\n"
                            f"Round Progress: {elapsed_pct}% elapsed ({context.time_left_seconds}s remaining)\n"
                            f"Underlying Spot: ${context.underlying_price:,.2f} | Target (Strike): ${context.target_price:,.2f} (Diff: {diff_str} | {bps_str})\n"
                            f"Quantitative Signals:\n"
                            f"- Strike Distance: {bps_str}{vel_str} | DVR = {context.dvr_ratio:+.2f} expected-travel units | ATR(1m) = ${context.atr_1m:.2f}\n"
                            f"- Price Action & Macro: Recent 1m Candles = {context.recent_candles_summary} | Macro Trend = {context.macro_trend_15m} | Session = {context.market_session}\n"
                            f"- Microstructure: Spot top-of-book imbalance = {context.order_book_imbalance:+.2f} ({'available' if context.obi_available else 'unavailable'}) | BTC short-term direction = {context.btc_correlation_dir}\n"
                            f"- Technicals: Trend = {context.ema_trend} | RSI(1m) = {context.rsi_1m:.1f} | RSI(5m) = {context.rsi_5m:.1f}\n"
                            f"- Approx. 5m spot momentum: {context.momentum_pct:+.2f}% ({'available' if context.momentum_available else 'unavailable'})\n"
                            f"{data_quality_line}"
                            f"- Danger Warning: {'EXPIRY DANGER ZONE (<60s and within noise)' if context.expiry_danger_flag else 'None'}\n"
                            f"{recent_track_record_line}"
                            f"{martingale_directive}"
                            f"{tf_directive}"
                            f"{ev_directive}\n"
                            f"Determine whether to predict UP or DOWN and return probability_up as an explicit probability, separate from confidence."
                        )
                    }
                ]
            }

        try:
            assert self._session is not None
            request_timeout = aiohttp.ClientTimeout(total=8.0, connect=5.0)
            async with self._session.post(self.endpoint, json=payload, timeout=request_timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                if resp.status == 200:
                    data = await resp.json()
                    # Parse Jev AI structured response
                    parsed_result = self._parse_api_response(data, elapsed_ms, context)
                    self._update_stats(elapsed_ms)
                    return parsed_result
                else:
                    await resp.read()
                    logger.warning(
                        f"Jev AI API returned HTTP {resp.status}. "
                        f"Falling back to local heuristic engine."
                    )
                    return await self._evaluate_heuristic(
                        context,
                        start_time,
                        fallback_reason=f"api_http_{resp.status}",
                    )

        except asyncio.TimeoutError as te:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning(f"Jev AI API request timed out ({elapsed_ms:.1f}ms): {te!r}. Falling back to heuristic.")
            return await self._evaluate_heuristic(
                context,
                start_time,
                fallback_reason="api_timeout",
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Jev AI client exception ({elapsed_ms:.1f}ms): {type(e).__name__} - {e}. Falling back to heuristic.")
            return await self._evaluate_heuristic(
                context,
                start_time,
                fallback_reason="api_client_error",
            )

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
                if raw_action not in {"UP", "DOWN", "BUY_YES", "BUY_NO"}:
                    raise ValueError(f"Unsupported action: {raw_action}")
                action: Literal["UP", "DOWN", "BUY_YES", "BUY_NO"] = (
                    "DOWN" if raw_action in {"DOWN", "BUY_NO"} else "UP"
                )
                probs = action_ans.get("probabilities", {})
                yes_ans = answers.get(
                    "probability_up",
                    answers.get("settle_above_price_to_beat", answers.get("settle_above_strike", {})),
                )
                if isinstance(yes_ans, dict):
                    yes_prob = float(yes_ans["noul"])
                else:
                    yes_prob = float(yes_ans)
                if not math.isfinite(yes_prob) or not 0.0 <= yes_prob <= 1.0:
                    raise ValueError("probability_up must be finite and between 0 and 1")
                if (action == "UP" and yes_prob < 0.5) or (action == "DOWN" and yes_prob >= 0.5):
                    raise ValueError("action conflicts with probability_up")

                raw_confidence = probs.get(raw_action, action_ans.get("confidence", 0.50))
                confidence = float(raw_confidence)
                if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                    raise ValueError("confidence must be finite and between 0 and 1")
                reasoning = (
                    f"Binary {action} forecast: P(UP) {yes_prob:.1%}; "
                    f"analytical conviction {confidence:.1%}."
                )

                return JevEvaluationResult(
                    action=action,
                    confidence=confidence,
                    probability_up=yes_prob,
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

            # Clean and extract JSON safely from markdown code blocks or wrapper text
            cleaned = content.strip()
            if cleaned.startswith("```"):
                first_nl = cleaned.find("\n")
                if first_nl != -1:
                    cleaned = cleaned[first_nl + 1:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3].strip()
            if "{" in cleaned and "}" in cleaned:
                start_idx = cleaned.find("{")
                end_idx = cleaned.rfind("}")
                cleaned = cleaned[start_idx : end_idx + 1]

            parsed = json.loads(cleaned) if cleaned else {}
            if not isinstance(parsed, dict):
                parsed = {}
            raw_action = str(parsed.get("action", "")).upper()
            if raw_action not in {"UP", "DOWN", "BUY_YES", "BUY_NO"}:
                raise ValueError(f"Unsupported or missing action: {raw_action}")
            action = "DOWN" if raw_action in {"DOWN", "BUY_NO"} else "UP"
            probability_up = float(parsed["probability_up"])
            if not math.isfinite(probability_up) or not 0.0 <= probability_up <= 1.0:
                raise ValueError("probability_up must be finite and between 0 and 1")
            if (action == "UP" and probability_up < 0.5) or (action == "DOWN" and probability_up >= 0.5):
                raise ValueError("action conflicts with probability_up")
            confidence = float(parsed["confidence"])
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence must be finite and between 0 and 1")
            reasoning = parsed.get("reasoning", f"Binary {action} conviction evaluated via Jev AI structured engine.")

            return JevEvaluationResult(
                action=action,
                confidence=confidence,
                probability_up=probability_up,
                reasoning=reasoning,
                model=self.model,
                latency_ms=round(elapsed_ms, 2),
                is_mock=False
            )
        except Exception as err:
            fallback_reason = (
                "action_probability_conflict"
                if isinstance(err, ValueError) and str(err) == "action conflicts with probability_up"
                else "invalid_structured_response"
            )
            logger.error(
                "Rejected Jev AI structured response (%s): %s",
                fallback_reason,
                err,
            )
            return JevEvaluationResult(
                action="UP",
                confidence=0.50,
                probability_up=None,
                reasoning=f"Invalid structured response ({type(err).__name__}); no actionable forecast.",
                model="jev-invalid-response",
                latency_ms=round(elapsed_ms, 2),
                is_mock=True,
                fallback_reason=fallback_reason,
            )

    async def _evaluate_heuristic(
        self,
        context: MarketContext,
        start_time: float,
        fallback_reason: Optional[str] = None,
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

        # Multi-Factor Quantitative Probability Modeling:
        # Timeframe weight multiplier (15m candles have more momentum & trend inertia)
        is_15m = context.timeframe.lower() in ("15m", "30m", "1h")
        trend_mult = 1.35 if is_15m else 1.0

        # Factor 1: DVR (Distance-to-Volatility Ratio) via Sigmoid Probability Mapping
        if context.dvr_ratio != 0.0:
            # Sigmoid maps +/- 2.0 sigma to ~ 95% / 5%
            prob_dvr = 1.0 / (1.0 + math.exp(-max(-6.0, min(6.0, 1.45 * context.dvr_ratio))))
        elif target > 0 and spot > 0:
            price_ratio = (spot - target) / target
            prob_dvr = 0.5 + (price_ratio * 15.0)
        else:
            prob_dvr = context.odds_yes

        # Factor 2: Microstructure / Order Book Imbalance (OBI)
        obi_effect = context.order_book_imbalance * 0.05

        # Factor 3: Momentum & Trend Alignment (Scaled by timeframe inertia)
        momentum_effect = momentum * 0.04
        trend_effect = 0.0
        if context.ema_trend == "STRONG_UPTREND":
            trend_effect = +0.035 * trend_mult
        elif context.ema_trend == "UPTREND":
            trend_effect = +0.020 * trend_mult
        elif context.ema_trend == "STRONG_DOWNTREND":
            trend_effect = -0.035 * trend_mult
        elif context.ema_trend == "DOWNTREND":
            trend_effect = -0.020 * trend_mult

        # Factor 4: Technical RSI Overbought / Oversold Mean-Reversion Dampener
        rsi_effect = 0.0
        if context.rsi_5m >= 75:
            rsi_effect = -0.03  # Pullback risk from extreme overbought
        elif context.rsi_5m <= 25:
            rsi_effect = +0.03  # Bounce potential from extreme oversold

        # Factor 5: Multi-Indicator Confluence & Contradiction Dampener
        confluence_effect = 0.0
        confluence_note = ""
        is_bull_trend = "UPTREND" in context.ema_trend
        is_bear_trend = "DOWNTREND" in context.ema_trend
        obi = context.order_book_imbalance

        if is_bull_trend and obi > 0.15 and context.rsi_5m < 70 and momentum >= 0:
            confluence_effect = +0.035  # High-conviction bullish confluence
            confluence_note = " [Bullish Confluence: Trend+OBI+Mom]"
        elif is_bear_trend and obi < -0.15 and context.rsi_5m > 30 and momentum <= 0:
            confluence_effect = -0.035  # High-conviction bearish confluence
            confluence_note = " [Bearish Confluence: Trend+OBI+Mom]"
        elif (is_bull_trend and obi < -0.25) or (is_bear_trend and obi > 0.25):
            confluence_note = " [Contradiction Dampener: Trend vs OBI fight]"

        # Factor 6: BTC Macro Correlation (Crypto-wide directional tailwind)
        btc_effect = 0.0
        if context.btc_correlation_dir == "BULLISH":
            btc_effect = +0.015
        elif context.btc_correlation_dir == "BEARISH":
            btc_effect = -0.015

        # Factor 7: Strike Approach Velocity (Rate of cushion expansion/decay)
        velocity_effect = 0.0
        if context.strike_velocity_desc == "EXPANDING_BULL_BUFFER":
            velocity_effect = +0.015
        elif context.strike_velocity_desc == "EXPANDING_BEAR_BUFFER":
            velocity_effect = -0.015
        elif context.strike_velocity_desc == "RETREATING_TO_STRIKE":
            confluence_note += " [Velocity Alert: Cushion eroding towards strike]"

        # Aggregate theoretical probability
        theoretical_prob = (
            prob_dvr
            + obi_effect
            + momentum_effect
            + trend_effect
            + rsi_effect
            + confluence_effect
            + btc_effect
            + velocity_effect
        )
        if "Contradiction Dampener" in confluence_note:
            theoretical_prob = 0.50 + (theoretical_prob - 0.50) * 0.70
        if "Velocity Alert" in confluence_note:
            theoretical_prob = 0.50 + (theoretical_prob - 0.50) * 0.85

        theoretical_prob = max(0.05, min(0.95, theoretical_prob))

        # Compare the forecast with actual executable quotes when present. The
        # spot-derived odds fields are not contract prices and must not be used as EV.
        quote_up = context.contract_up_ask
        quote_down = context.contract_down_ask
        edge_yes = theoretical_prob - quote_up if quote_up is not None else 0.0
        edge_no = (1.0 - theoretical_prob) - quote_down if quote_down is not None else 0.0

        # Binary decision: strictly UP or DOWN based on expectancy / probability
        feedback_note = confluence_note
        streak_modifier = 1.0
        if context.recent_performance:
            consecutive_losses = context.recent_performance.get("consecutive_losses", 0)
            consecutive_wins = context.recent_performance.get("consecutive_wins", 0)
            if consecutive_losses >= 2:
                # Regulate confidence down slightly during drawdown to demand higher signal threshold
                streak_modifier = max(0.85, 1.0 - (0.05 * (consecutive_losses - 1)))
                feedback_note += f" [Adaptive Defense: {consecutive_losses} consecutive losses on {context.symbol}]"
            elif consecutive_wins >= 2:
                streak_modifier = min(1.05, 1.0 + (0.02 * consecutive_wins))
                feedback_note += f" [Momentum Feedback: {consecutive_wins} win streak on {context.symbol}]"

        # Expiry Danger Filter (Near-strike Gamma Risk)
        danger_modifier = 1.0
        if context.expiry_danger_flag:
            danger_modifier = 0.60
            feedback_note += " [⚠️ GAMMA RISK: Expiry Danger Zone near Strike]"

        if theoretical_prob >= 0.50:
            action: Literal["UP", "DOWN", "BUY_YES", "BUY_NO"] = "UP"
            chosen_quote = quote_up
            chosen_edge = edge_yes
            prob_chosen = theoretical_prob
        else:
            action = "DOWN"
            prob_chosen = 1.0 - theoretical_prob
            chosen_quote = quote_down
            chosen_edge = edge_no

        # Small conviction adjustment is allowed only when an executable quote exists.
        odds_modifier = 1.0
        if chosen_quote is not None and chosen_quote > 0.60:
            odds_modifier = 0.90
            feedback_note += " [Quote Price Penalty: Executable price > 0.60]"
        elif chosen_quote is not None and chosen_quote <= 0.55 and chosen_edge >= 0.05:
            odds_modifier = 1.03

        # Contradiction modifier: dampen confidence when trend opposes order flow
        contradiction_modifier = 0.85 if "Contradiction Dampener" in confluence_note else 1.0

        raw_conf = max(0.50, min(0.98, prob_chosen + (max(0.0, chosen_edge) * 0.5)))
        confidence = max(0.50, min(0.98, raw_conf * streak_modifier * danger_modifier * odds_modifier * contradiction_modifier))

        # Martingale Recovery Conviction Hurdle Alignment
        if context.martingale_step > 0 and not context.expiry_danger_flag:
            momentum_aligned = (momentum > 0.03 and action == "UP") or (momentum < -0.03 and action == "DOWN")
            if chosen_edge > 0.03 and momentum_aligned:
                boost = min(0.12, 0.03 * context.martingale_step + chosen_edge * 0.5)
                confidence = min(0.96, max(context.effective_hurdle + 0.01, confidence + boost))
                feedback_note += f" [🎯 {context.martingale_stage} Recovery High Conviction: Edge {chosen_edge*100:+.1f}%, Hurdle {context.effective_hurdle*100:.0f}% Achieved]"
            else:
                feedback_note += f" [⚠️ {context.martingale_stage} Recovery: Edge {chosen_edge*100:+.1f}% below recovery hurdle]"

        dvr_info = f"DVR: {context.dvr_ratio:+.2f} expected-travel units, spot top-of-book imbalance: {context.order_book_imbalance:+.2f}, Trend: {context.ema_trend}"
        if action == "UP":
            reasoning = (
                f"Binary UP forecast: P(UP) {theoretical_prob:.1%}; executable buy quote "
                f"{f'${quote_up:.3f}' if quote_up is not None else 'unavailable'}; "
                f"estimated quote edge {edge_yes*100:+.1f}%. {dvr_info} with {time_left}s remaining.{feedback_note}"
            )
        else:
            reasoning = (
                f"Binary DOWN forecast: P(DOWN) {prob_chosen:.1%}; executable buy quote "
                f"{f'${quote_down:.3f}' if quote_down is not None else 'unavailable'}; "
                f"estimated quote edge {edge_no*100:+.1f}%. {dvr_info} with {time_left}s remaining.{feedback_note}"
            )

        self._update_stats(elapsed_ms)
        return JevEvaluationResult(
            action=action,
            confidence=round(confidence, 3),
            probability_up=round(theoretical_prob, 5),
            reasoning=reasoning,
            model="jev-heuristic-fallback",
            latency_ms=round(elapsed_ms, 2),
            is_mock=True,
            fallback_reason=fallback_reason,
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
