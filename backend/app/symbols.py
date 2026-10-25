"""Symbol validation and normalization.

Canonical symbols are URL-safe:

* Equities / ETFs / indices: ``AAPL``, ``BRK.B``, ``SPX``; an optional exchange suffix
  ``VOD:LSE`` selects a specific listing.
* Currency or crypto pairs: ``BTC-USD``, ``EUR-USD`` (``/`` is accepted on input and
  converted to ``-`` so the symbol can live in a URL path segment).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.errors import InvalidSymbolError

_ALLOWED = re.compile(r"^[A-Z0-9^][A-Z0-9.\-_^=:]{0,31}$")
PAIR_RE = re.compile(r"^([A-Z0-9]{2,12})-([A-Z]{3,5})$")


@dataclass(frozen=True)
class ParsedSymbol:
    symbol: str  # canonical
    ticker: str  # without exchange suffix
    exchange: str | None
    is_pair: bool
    base: str | None = None
    quote: str | None = None


def normalize_symbol(raw: str) -> ParsedSymbol:
    if raw is None:
        raise InvalidSymbolError("A symbol is required.")
    text = raw.strip().upper().replace("/", "-").replace(" ", "")
    if not text:
        raise InvalidSymbolError("A symbol is required.")
    if not _ALLOWED.match(text):
        raise InvalidSymbolError(
            f"'{raw[:40]}' is not a valid symbol. Use letters, digits and . - _ ^ = : only (max 32 characters).",
            details={"symbol": raw[:40]},
        )
    ticker, exchange = text, None
    if ":" in text:
        ticker, _, exchange = text.partition(":")
        if not ticker or not exchange or ":" in exchange:
            raise InvalidSymbolError(f"'{raw[:40]}' is not a valid TICKER:EXCHANGE symbol.")
    match = PAIR_RE.match(ticker)
    if match and exchange is None:
        return ParsedSymbol(text, ticker, None, True, match.group(1), match.group(2))
    return ParsedSymbol(text, ticker, exchange, False)
