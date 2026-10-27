"""Earnings dates. Experienced premium sellers don't hold short options through an
earnings report they didn't plan for, so the bot needs to *know* the next report date for
every single stock it trades; when it can't find out, it skips the stock.

Sources, tried in order: funds without earnings (config), a local CSV file
(``symbol,date``), and the Finnhub earnings calendar (free API key).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EarningsInfo:
    known: bool
    next_date: date | None = None
    source: str = ""


class EarningsSource(Protocol):
    def lookup(self, symbol: str, start: date, end: date) -> EarningsInfo | None:
        """Return None when this source has no opinion about the symbol."""
        ...


class NoEarningsSymbols:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = {s.upper() for s in symbols}

    def lookup(self, symbol: str, start: date, end: date) -> EarningsInfo | None:
        if symbol.upper() in self.symbols:
            return EarningsInfo(known=True, next_date=None, source="no-earnings list")
        return None


class FileEarningsCalendar:
    """CSV with a header row ``symbol,date``. A symbol listed with no upcoming date is
    treated as known to have none in the window."""

    def __init__(self, path: Path) -> None:
        self.dates: dict[str, list[date]] = {}
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                symbol = (row.get("symbol") or "").strip().upper()
                raw = (row.get("date") or "").strip()
                if not symbol:
                    continue
                self.dates.setdefault(symbol, [])
                if raw:
                    self.dates[symbol].append(date.fromisoformat(raw))

    def lookup(self, symbol: str, start: date, end: date) -> EarningsInfo | None:
        dates = self.dates.get(symbol.upper())
        if dates is None:
            return None
        upcoming = sorted(d for d in dates if start <= d <= end)
        return EarningsInfo(known=True, next_date=upcoming[0] if upcoming else None, source="earnings file")


class FinnhubEarningsCalendar:
    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=10)
        self._cache: dict[tuple[str, date], EarningsInfo] = {}

    def lookup(self, symbol: str, start: date, end: date) -> EarningsInfo | None:
        key = (symbol.upper(), start)
        if key in self._cache:
            return self._cache[key]
        try:
            response = self._client.get(
                "/calendar/earnings",
                params={"from": start.isoformat(), "to": end.isoformat(), "symbol": symbol.upper(), "token": self._key},
            )
            response.raise_for_status()
            rows = (response.json() or {}).get("earningsCalendar") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("finnhub earnings lookup failed for %s: %s", symbol, type(exc).__name__)
            return None
        dates = sorted(date.fromisoformat(r["date"]) for r in rows if r.get("date") and r.get("symbol", "").upper() == symbol.upper())
        info = EarningsInfo(known=True, next_date=dates[0] if dates else None, source="finnhub")
        self._cache[key] = info
        return info


class EarningsCalendar:
    def __init__(self, sources: list[EarningsSource]) -> None:
        self.sources = sources

    def lookup(self, symbol: str, today: date, horizon_days: int = 120) -> EarningsInfo:
        end = today + timedelta(days=horizon_days)
        for source in self.sources:
            info = source.lookup(symbol, today, end)
            if info is not None:
                return info
        return EarningsInfo(known=False)
