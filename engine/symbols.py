"""Shared validation for Binance USDT trading pairs."""

from __future__ import annotations

import re
from typing import Iterable, Sequence

DEFAULT_ACTIVE_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "BNBUSDT")
MAX_ACTIVE_SYMBOLS = 24
_USDT_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}USDT$")


def normalize_symbol(value: object) -> str:
    """Normalize a pair such as ``btc/usdt`` and require a USDT quote asset."""
    symbol = str(value or "").strip().upper().replace("/", "").replace(" ", "")
    if not _USDT_SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(f"Invalid trading pair '{value}'. Use a Binance USDT pair such as BTCUSDT.")
    return symbol


def normalize_active_symbols(
    values: str | Iterable[object] | None,
    *,
    allow_empty: bool = False,
    max_symbols: int = MAX_ACTIVE_SYMBOLS,
) -> tuple[str, ...]:
    """Validate and deduplicate a configured active-pair list while preserving order."""
    if values is None:
        raw_values: Sequence[object] = ()
    elif isinstance(values, str):
        raw_values = tuple(part for part in values.split(",") if part.strip())
    else:
        raw_values = tuple(values)

    symbols: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        symbol = normalize_symbol(value)
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)

    if not symbols and not allow_empty:
        raise ValueError("At least one active trading pair is required.")
    if len(symbols) > max_symbols:
        raise ValueError(f"A maximum of {max_symbols} active trading pairs is allowed.")
    return tuple(symbols)
