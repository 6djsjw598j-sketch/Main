"""Provider abstraction. Add a new market-data source by subclassing ``MarketDataProvider``."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel

from app.errors import AppError, ProviderUnavailableError
from app.models.market import AssetInfo, AssetType, Candle, Quote, Timeframe
from app.symbols import ParsedSymbol


@dataclass
class CandleSeries:
    asset: AssetInfo
    timeframe: Timeframe
    candles: list[Candle]
    timezone: str
    last_bar_complete: bool
    aggregated_from: str | None = None
    limitations: list[str] = field(default_factory=list)


class TimeframeSupport(BaseModel):
    timeframe: Timeframe
    native: bool
    source: str
    note: str | None = None


class ProviderStatus(BaseModel):
    name: str
    display_name: str
    asset_classes: list[str]
    configured: bool
    status: str  # ok | degraded | down | not_configured | unknown
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    data_type: str
    limits: str
    credits_remaining_today: int | None = None
    timeframes: list[TimeframeSupport] = []
    notes: list[str] = []


class ProviderHealth:
    """Tracks failures and implements a simple circuit breaker."""

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self._open_until = 0.0

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success_at = datetime.now(UTC)
        self._open_until = 0.0

    def record_failure(self, error: AppError) -> None:
        # Bad symbols and plan restrictions are not provider-health problems.
        if not error.retryable:
            return
        self.consecutive_failures += 1
        self.last_error = error.message
        self.last_error_at = datetime.now(UTC)
        if self.consecutive_failures >= self.failure_threshold:
            self._open_until = time.monotonic() + self.cooldown_seconds

    def check(self, provider: str) -> None:
        remaining = self._open_until - time.monotonic()
        if remaining > 0:
            raise ProviderUnavailableError(
                f"{provider} is temporarily unavailable after repeated failures.",
                provider=provider,
                retry_after=remaining,
            )

    @property
    def status(self) -> str:
        if self._open_until > time.monotonic():
            return "down"
        if self.consecutive_failures > 0:
            return "degraded"
        if self.last_success_at is None:
            return "unknown"
        return "ok"


class MarketDataProvider(ABC):
    name: str
    display_name: str
    asset_classes: ClassVar[list[AssetType]]

    def __init__(self) -> None:
        self.health = ProviderHealth()

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    async def resolve(self, parsed: ParsedSymbol, asset_type: AssetType) -> AssetInfo | None:
        """Return asset info if this provider can serve the symbol, else ``None``."""

    @abstractmethod
    async def get_quote(self, asset: AssetInfo) -> Quote: ...

    @abstractmethod
    async def get_candles(self, asset: AssetInfo, timeframe: Timeframe, limit: int) -> CandleSeries: ...

    @abstractmethod
    async def search(self, query: str, asset_type: AssetType) -> list[AssetInfo]: ...

    @abstractmethod
    def status(self) -> ProviderStatus: ...

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None
