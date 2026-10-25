"""Coinbase Exchange public market data (no API key required).

Endpoints used (https://docs.cdp.coinbase.com/exchange/):

* ``GET /products``                       - list of tradable pairs (cached 6h)
* ``GET /currencies``                     - currency names (cached 24h)
* ``GET /products/{id}/ticker``           - last trade, bid/ask
* ``GET /products/{id}/stats``            - rolling 24h open/high/low/volume
* ``GET /products/{id}/candles``          - OHLCV, granularity 60/300/900/3600/21600/86400 s,
                                            max 300 candles per request

Public endpoints are limited to 10 requests/second per IP; we stay below that.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.config import Settings
from app.errors import (
    AppError,
    ProviderBadResponseError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from app.market.aggregation import aggregate, last_bar_complete_fixed
from app.models.market import AssetInfo, AssetType, Candle, Quote, Timeframe
from app.providers.base import CandleSeries, MarketDataProvider, ProviderStatus, TimeframeSupport
from app.providers.http import ProviderHTTP
from app.ratelimit import TokenBucket
from app.symbols import ParsedSymbol

logger = logging.getLogger(__name__)

MAX_PER_REQUEST = 300
MAX_SOURCE_CANDLES = MAX_PER_REQUEST * 24

# target timeframe -> (source granularity seconds, source timeframe, aggregation factor)
SOURCE: dict[Timeframe, tuple[int, Timeframe, int]] = {
    Timeframe.M1: (60, Timeframe.M1, 1),
    Timeframe.M5: (300, Timeframe.M5, 1),
    Timeframe.M15: (900, Timeframe.M15, 1),
    Timeframe.M30: (900, Timeframe.M15, 2),
    Timeframe.H1: (3600, Timeframe.H1, 1),
    Timeframe.H4: (3600, Timeframe.H1, 4),
    Timeframe.D1: (86400, Timeframe.D1, 1),
    Timeframe.W1: (86400, Timeframe.D1, 7),
    Timeframe.MN1: (86400, Timeframe.D1, 31),
}


class CoinbaseProvider(MarketDataProvider):
    name = "coinbase"
    display_name = "Coinbase Exchange"
    asset_classes: ClassVar[list[AssetType]] = [AssetType.CRYPTO]

    def __init__(self, settings: Settings, http: ProviderHTTP | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.http = http or ProviderHTTP(self.name, settings.coinbase_base_url, settings.http_timeout_seconds, settings.http_max_retries)
        self.bucket = TokenBucket(settings.coinbase_requests_per_second, settings.coinbase_requests_per_second)
        self._products: dict[str, dict[str, Any]] = {}
        self._products_at = 0.0
        self._currencies: dict[str, str] = {}
        self._currencies_at = 0.0
        self._ref_lock = asyncio.Lock()

    # ------------------------------------------------------------------ transport
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.health.check(self.display_name)
        wait = await self.bucket.acquire(1, max_wait=5.0)
        if wait:
            raise ProviderRateLimitedError("Coinbase request budget exhausted, try again shortly.", provider=self.name, retry_after=wait)
        try:
            response = await self.http.get(path, params=params)
            if response.status_code == 404:
                raise SymbolNotFoundError("Coinbase does not list this product.", details={"provider": self.name})
            if response.status_code == 429:
                raise ProviderRateLimitedError("Coinbase rate limit reached.", provider=self.name, retry_after=2)
            if response.status_code == 400:
                body = self.http.parse_json(response)
                raise ProviderBadResponseError(f"Coinbase rejected the request: {body.get('message', 'bad request')}", provider=self.name)
            if response.status_code >= 400:
                raise ProviderUnavailableError(f"Coinbase returned HTTP {response.status_code}.", provider=self.name, retry_after=10)
            data = self.http.parse_json(response)
        except AppError as exc:
            self.health.record_failure(exc)
            raise
        self.health.record_success()
        return data

    # ------------------------------------------------------------------ reference data
    async def products(self) -> dict[str, dict[str, Any]]:
        if self._products and time.monotonic() - self._products_at < 6 * 3600:
            return self._products
        async with self._ref_lock:
            if self._products and time.monotonic() - self._products_at < 6 * 3600:
                return self._products
            try:
                rows = await self._get("/products")
            except AppError:
                if self._products:  # keep serving the previous list during an outage
                    return self._products
                raise
            if not isinstance(rows, list):
                raise ProviderBadResponseError("Unexpected /products payload.", provider=self.name)
            self._products = {
                str(p["id"]).upper(): p for p in rows if isinstance(p, dict) and p.get("id") and str(p.get("status", "online")).lower() != "delisted"
            }
            self._products_at = time.monotonic()
            return self._products

    async def currency_names(self) -> dict[str, str]:
        if self._currencies and time.monotonic() - self._currencies_at < 24 * 3600:
            return self._currencies
        try:
            rows = await self._get("/currencies")
            if isinstance(rows, list):
                self._currencies = {str(r["id"]).upper(): str(r.get("name") or r["id"]) for r in rows if r.get("id")}
                self._currencies_at = time.monotonic()
        except AppError as exc:
            logger.warning("coinbase currencies unavailable", extra={"error": exc.message})
        return self._currencies

    def _asset(self, product: dict[str, Any], names: dict[str, str]) -> AssetInfo:
        pid = str(product["id"]).upper()
        base = str(product.get("base_currency", pid.split("-")[0])).upper()
        quote = str(product.get("quote_currency", pid.split("-")[-1])).upper()
        base_name = names.get(base, base)
        return AssetInfo(
            symbol=pid,
            display_symbol=pid,
            name=f"{base_name} / {quote}",
            asset_type=AssetType.CRYPTO,
            exchange="Coinbase",
            currency=quote,
            timezone="UTC",
            provider=self.name,
        )

    # ------------------------------------------------------------------ provider API
    async def resolve(self, parsed: ParsedSymbol, asset_type: AssetType) -> AssetInfo | None:
        if asset_type not in (AssetType.AUTO, AssetType.CRYPTO) or parsed.exchange:
            return None
        if parsed.is_pair:
            pid = parsed.ticker
        elif asset_type is AssetType.CRYPTO:
            pid = f"{parsed.ticker}-USD"
        else:
            return None
        products = await self.products()
        product = products.get(pid)
        if product is None:
            return None
        return self._asset(product, await self.currency_names())

    async def get_quote(self, asset: AssetInfo) -> Quote:
        ticker, stats = await asyncio.gather(self._get(f"/products/{asset.symbol}/ticker"), self._get(f"/products/{asset.symbol}/stats"))
        try:
            price = float(ticker["price"])
            as_of = datetime.fromisoformat(str(ticker["time"]).replace("Z", "+00:00"))
            open_24h = _f(stats.get("open"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderBadResponseError("Coinbase ticker response was incomplete.", provider=self.name) from exc
        change = price - open_24h if open_24h else None
        change_pct = (change / open_24h * 100) if change is not None and open_24h else None
        base = asset.symbol.split("-")[0]
        return Quote(
            symbol=asset.symbol,
            display_symbol=asset.display_symbol,
            name=asset.name,
            asset_type=AssetType.CRYPTO,
            exchange="Coinbase",
            currency=asset.currency,
            price=price,
            change=change,
            change_percent=change_pct,
            change_basis="rolling_24h",
            open=open_24h,
            high=_f(stats.get("high")),
            low=_f(stats.get("low")),
            previous_close=open_24h,
            volume=_f(stats.get("volume")),
            volume_label=f"24h volume ({base})",
            bid=_f(ticker.get("bid")),
            ask=_f(ticker.get("ask")),
            as_of=as_of.astimezone(UTC),
            provider=self.name,
            timezone="UTC",
        )

    async def _fetch_window(self, product: str, granularity: int, start: int, end: int) -> list[Candle]:
        rows = await self._get(
            f"/products/{product}/candles",
            params={
                "granularity": granularity,
                "start": datetime.fromtimestamp(start, UTC).isoformat(),
                "end": datetime.fromtimestamp(end, UTC).isoformat(),
            },
        )
        if not isinstance(rows, list):
            raise ProviderBadResponseError("Unexpected candles payload from Coinbase.", provider=self.name)
        out: list[Candle] = []
        for row in rows:
            # [time, low, high, open, close, volume]
            try:
                t, low, high, op, close, vol = row[:6]
                out.append(Candle(time=int(t), open=float(op), high=float(high), low=float(low), close=float(close), volume=float(vol)))
            except (TypeError, ValueError):
                continue
        return [c for c in out if start <= c.time <= end]

    async def get_candles(self, asset: AssetInfo, timeframe: Timeframe, limit: int) -> CandleSeries:
        granularity, source_tf, factor = SOURCE[timeframe]
        needed = min(MAX_SOURCE_CANDLES, limit * factor + factor * 2)
        now = int(time.time())
        end = now - now % granularity + granularity  # include the in-progress bar
        windows: list[tuple[int, int]] = []
        remaining = needed
        while remaining > 0:
            start = end - MAX_PER_REQUEST * granularity
            windows.append((start, end - 1))
            end = start
            remaining -= MAX_PER_REQUEST

        collected: dict[int, Candle] = {}
        for i in range(0, len(windows), 4):  # small concurrent batches, newest first
            pages = await asyncio.gather(*(self._fetch_window(asset.symbol, granularity, s, e) for s, e in windows[i : i + 4]))
            reached_start = False
            for page in pages:
                if not page:
                    reached_start = True
                for candle in page:
                    collected[candle.time] = candle
            if reached_start and collected:
                break  # an empty window means we went past the product's first trade

        candles = [collected[t] for t in sorted(collected)]
        limitations = ["Coinbase publishes no candle for intervals without trades; such gaps are left empty."]
        aggregated_from = None
        if factor > 1:
            candles = aggregate(candles, timeframe)
            aggregated_from = source_tf.value
            alignment = {
                Timeframe.W1: "weeks start Monday 00:00 UTC",
                Timeframe.MN1: "months start on the 1st, 00:00 UTC",
            }.get(timeframe, "buckets aligned to 00:00 UTC")
            limitations.append(f"{timeframe.value} candles are aggregated from Coinbase {source_tf.value} candles ({alignment}).")
        if timeframe is Timeframe.MN1 and len(candles) < limit:
            limitations.append("Monthly history is limited to the product's trading history on Coinbase.")
        candles = candles[-limit:]
        return CandleSeries(
            asset=asset,
            timeframe=timeframe,
            candles=candles,
            timezone="UTC",
            last_bar_complete=last_bar_complete_fixed(candles, timeframe, now),
            aggregated_from=aggregated_from,
            limitations=limitations,
        )

    async def search(self, query: str, asset_type: AssetType) -> list[AssetInfo]:
        if asset_type not in (AssetType.AUTO, AssetType.CRYPTO):
            return []
        q = query.strip().upper().replace("/", "-")
        if not q:
            return []
        products = await self.products()
        names = await self.currency_names()
        scored: list[tuple[float, str]] = []
        for pid, product in products.items():
            base = str(product.get("base_currency", "")).upper()
            quote = str(product.get("quote_currency", "")).upper()
            name = names.get(base, "").upper()
            if pid == q:
                score = 0.0
            elif base == q:
                score = 1.0
            elif pid.startswith(q):
                score = 2.0
            elif name.startswith(q):
                score = 3.0
            elif q in name or q in pid:
                score = 5.0
            else:
                continue
            if quote == "USD":
                score -= 0.5
            elif quote not in ("USDC", "USDT", "EUR", "GBP"):
                score += 1.0
            scored.append((score, pid))
        scored.sort()
        return [self._asset(products[pid], names) for _, pid in scored[:15]]

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            display_name=self.display_name,
            asset_classes=["crypto"],
            configured=self.settings.coinbase_enabled,
            status=self.health.status if self.settings.coinbase_enabled else "not_configured",
            last_success_at=self.health.last_success_at,
            last_error=self.health.last_error,
            last_error_at=self.health.last_error_at,
            data_type="Real-time exchange data (public market data, no key required)",
            limits="10 requests/second per IP (public endpoints); max 300 candles per request",
            timeframes=[
                TimeframeSupport(
                    timeframe=tf,
                    native=factor == 1,
                    source=f"Coinbase {src.value}",
                    note=None if factor == 1 else f"Aggregated from {src.value} candles",
                )
                for tf, (_, src, factor) in SOURCE.items()
            ],
            notes=[
                "Prices are Coinbase Exchange trades only, not a cross-exchange average.",
                "24h change is measured against the price 24 hours ago (rolling window).",
                "Intraday history depth is limited to ~7,200 source candles per request (e.g. ~5 days of 1-minute data).",
            ],
        )

    async def close(self) -> None:
        await self.http.close()


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
