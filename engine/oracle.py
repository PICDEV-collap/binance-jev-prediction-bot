"""Strict parsing of dated Binance prediction market metadata."""
import json
import math


def round_window(topic):
    try:
        start = float(topic.get("startDate", 0))
        end = float(topic.get("endDate", 0))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0 or end <= start:
        return None
    return start, end


def start_price(topic):
    variant = topic.get("variantData") or {}
    if isinstance(variant, str):
        try:
            variant = json.loads(variant)
        except (ValueError, TypeError):
            return None
    if not isinstance(variant, dict):
        return None
    try:
        value = float(variant.get("startPrice", 0))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def topic_id(topic):
    return str(topic.get("marketTopicId") or topic.get("topicId") or "")
