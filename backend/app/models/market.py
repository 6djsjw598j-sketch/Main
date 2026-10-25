"""Normalized market-data models shared by every provider."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AssetType(StrEnum):
    AUTO = "auto"
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    CRYPTO = "crypto"
    FOREX = "forex"
    FUND = "fund"
    OTHER = "other"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1M"

    @property
    def seconds(self) -> int | None:
        """Nominal bar length. Monthly bars have no fixed length."""
        return _TF_SECONDS[self]

    @property
    def is_intraday(self) -> bool:
        return self in (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4)


_TF_SECONDS: dict[Timeframe, int | None] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
    Timeframe.MN1: None,
}


class Candle(BaseModel):
    time: int = Field(description="Bar open time, UNIX seconds (UTC). Daily+ bars use 00:00 UTC of the session date.")
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


class AssetInfo(BaseModel):
    symbol: str = Field(description="Canonical symbol used in API paths, e.g. AAPL or BTC-USD")
    display_symbol: str
    name: str | None = None
    asset_type: AssetType
    exchange: str | None = None
    mic_code: str | None = None
    country: str | None = None
    currency: str | None = None
    timezone: str = "UTC"
    provider: str


class SearchResponse(BaseModel):
    query: str
    results: list[AssetInfo]
    notes: list[str] = []


FreshnessStatus = Literal["real_time", "delayed", "end_of_day", "stale"]


class Freshness(BaseModel):
    as_of: datetime = Field(description="Timestamp of the most recent data point (UTC)")
    age_seconds: float
    status: FreshnessStatus
    label: str
    is_stale: bool
    detail: str


SessionStatus = Literal["open", "closed", "pre_market", "after_hours", "always_open", "unknown"]


class MarketSession(BaseModel):
    status: SessionStatus
    label: str
    timezone: str
    is_open: bool
    note: str | None = None


class CacheInfo(BaseModel):
    hit: bool
    stale: bool = False
    age_seconds: float = 0.0
    warning: str | None = None


class Quote(BaseModel):
    symbol: str
    display_symbol: str
    name: str | None = None
    asset_type: AssetType
    exchange: str | None = None
    currency: str | None = None
    price: float
    change: float | None = None
    change_percent: float | None = None
    change_basis: Literal["previous_close", "rolling_24h"] = Field(
        description="previous_close for exchange-traded assets, rolling_24h for 24/7 crypto"
    )
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = Field(default=None, description="Previous session close (equities) or the price 24h ago (crypto)")
    volume: float | None = None
    volume_label: str | None = None
    bid: float | None = None
    ask: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    as_of: datetime
    provider: str
    timezone: str = "UTC"
    market_open_flag: bool | None = Field(default=None, description="Provider-reported market-open flag")


class QuoteResponse(BaseModel):
    quote: Quote
    session: MarketSession
    freshness: Freshness
    cache: CacheInfo
    refresh_seconds: int
    source_note: str


class CandlesResponse(BaseModel):
    symbol: str
    display_symbol: str
    asset_type: AssetType
    provider: str
    timeframe: Timeframe
    timezone: str
    candles: list[Candle]
    count: int
    aggregated_from: str | None = None
    last_bar_complete: bool
    freshness: Freshness
    cache: CacheInfo
    refresh_seconds: int
    limitations: list[str] = []


class OverviewItem(BaseModel):
    symbol: str
    quote: Quote | None = None
    freshness: Freshness | None = None
    error: dict | None = None


class OverviewResponse(BaseModel):
    items: list[OverviewItem]
