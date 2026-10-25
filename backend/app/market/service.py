"""Market-data service: provider routing + caching + freshness/session labelling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from app.cache import CachedFetcher, CacheResult
from app.config import Settings
from app.errors import AppError, InsufficientDataError
from app.market.aggregation import bucket_end
from app.market.freshness import classify_candle_freshness, classify_freshness
from app.market.sessions import equity_market_likely_closed, market_session
from app.models.market import (
    AssetInfo,
    AssetType,
    CacheInfo,
    Candle,
    CandlesResponse,
    OverviewItem,
    OverviewResponse,
    Quote,
    QuoteResponse,
    SearchResponse,
    Timeframe,
)
from app.providers.base import CandleSeries, MarketDataProvider
from app.providers.registry import ProviderRegistry

FETCH_SIZES = (300, 500, 1000, 2000, 5000)

CRYPTO_CANDLE_TTL = {
    Timeframe.M1: 10,
    Timeframe.M5: 20,
    Timeframe.M15: 30,
    Timeframe.M30: 45,
    Timeframe.H1: 60,
    Timeframe.H4: 120,
    Timeframe.D1: 300,
    Timeframe.W1: 900,
    Timeframe.MN1: 1800,
}
# Equity data is credit-limited on free plans, so cache longer.
EQUITY_CANDLE_TTL = {
    Timeframe.M1: 60,
    Timeframe.M5: 120,
    Timeframe.M15: 180,
    Timeframe.M30: 300,
    Timeframe.H1: 300,
    Timeframe.H4: 600,
    Timeframe.D1: 900,
    Timeframe.W1: 3600,
    Timeframe.MN1: 3600,
}
CLOSED_MARKET_TTL = 3600


@dataclass
class CandleData:
    response: CandlesResponse
    asset: AssetInfo
    frame: pd.DataFrame


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"], dtype=float)
    frame = pd.DataFrame(
        {
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [np.nan if c.volume is None else c.volume for c in candles],
        },
        index=pd.to_datetime([c.time for c in candles], unit="s", utc=True),
        dtype=float,
    )
    frame.index.name = "time"
    return frame


def _series_to_json(series: CandleSeries) -> dict:
    return {
        "asset": series.asset.model_dump(mode="json"),
        "timeframe": series.timeframe.value,
        "candles": [[c.time, c.open, c.high, c.low, c.close, c.volume] for c in series.candles],
        "timezone": series.timezone,
        "last_bar_complete": series.last_bar_complete,
        "aggregated_from": series.aggregated_from,
        "limitations": series.limitations,
    }


def _series_from_json(data: dict) -> CandleSeries:
    return CandleSeries(
        asset=AssetInfo.model_validate(data["asset"]),
        timeframe=Timeframe(data["timeframe"]),
        candles=[Candle(time=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in data["candles"]],
        timezone=data["timezone"],
        last_bar_complete=data["last_bar_complete"],
        aggregated_from=data.get("aggregated_from"),
        limitations=list(data.get("limitations") or []),
    )


def _cache_info(result: CacheResult) -> CacheInfo:
    return CacheInfo(hit=result.hit, stale=result.stale, age_seconds=round(result.age_seconds, 1), warning=result.warning)


class MarketDataService:
    def __init__(self, registry: ProviderRegistry, cache: CachedFetcher, settings: Settings) -> None:
        self.registry = registry
        self.cache = cache
        self.settings = settings

    async def resolve(self, symbol: str, asset_type: AssetType) -> tuple[MarketDataProvider, AssetInfo]:
        return await self.registry.resolve(symbol, asset_type)

    # ------------------------------------------------------------------ quotes
    async def quote(self, symbol: str, asset_type: AssetType = AssetType.AUTO) -> QuoteResponse:
        provider, asset = await self.resolve(symbol, asset_type)
        is_crypto = asset.asset_type is AssetType.CRYPTO
        ttl = self.settings.quote_ttl_crypto if is_crypto else self.settings.quote_ttl_equity
        if not is_crypto and equity_market_likely_closed(asset.timezone):
            ttl = max(ttl, 600)

        async def fetch() -> dict:
            return (await provider.get_quote(asset)).model_dump(mode="json")

        result = await self.cache.get_or_fetch(f"quote:{provider.name}:{asset.symbol}", fetch, ttl=ttl, stale_ttl=self.settings.stale_ttl)
        quote = Quote.model_validate(result.value)
        now = datetime.now(UTC)
        session = market_session(quote.asset_type, quote.timezone, now, quote.market_open_flag)
        freshness = classify_freshness(quote.as_of, is_crypto=is_crypto, session=session, now=now)
        if is_crypto:
            refresh = 5
        else:
            refresh = 60 if session.is_open else 300
        source_note = (
            "Coinbase Exchange public market data; 24h change uses a rolling 24-hour window."
            if provider.name == "coinbase"
            else "Twelve Data; change is measured from the previous regular-session close."
        )
        return QuoteResponse(
            quote=quote,
            session=session,
            freshness=freshness,
            cache=_cache_info(result),
            refresh_seconds=refresh,
            source_note=source_note,
        )

    async def overview(self, symbols: list[str]) -> OverviewResponse:
        async def one(sym: str) -> OverviewItem:
            try:
                resp = await self.quote(sym)
                return OverviewItem(symbol=resp.quote.symbol, quote=resp.quote, freshness=resp.freshness)
            except AppError as exc:
                return OverviewItem(symbol=sym.upper(), error=exc.to_dict()["error"])

        return OverviewResponse(items=list(await asyncio.gather(*(one(s) for s in symbols))))

    # ------------------------------------------------------------------ candles
    async def candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 500,
        asset_type: AssetType = AssetType.AUTO,
        min_bars: int = 1,
    ) -> CandleData:
        provider, asset = await self.resolve(symbol, asset_type)
        fetch_size = next((s for s in FETCH_SIZES if s >= limit), FETCH_SIZES[-1])
        is_crypto = asset.asset_type is AssetType.CRYPTO
        ttl = (CRYPTO_CANDLE_TTL if is_crypto else EQUITY_CANDLE_TTL)[timeframe]
        if not is_crypto and equity_market_likely_closed(asset.timezone):
            ttl = max(ttl, CLOSED_MARKET_TTL)

        async def fetch() -> dict:
            return _series_to_json(await provider.get_candles(asset, timeframe, fetch_size))

        result = await self.cache.get_or_fetch(
            f"candles:{provider.name}:{asset.symbol}:{timeframe.value}:{fetch_size}",
            fetch,
            ttl=ttl,
            stale_ttl=self.settings.stale_ttl,
        )
        series = _series_from_json(result.value)
        candles = series.candles[-limit:]
        if len(candles) < min_bars:
            raise InsufficientDataError(
                f"Only {len(candles)} {timeframe.value} bars are available for {asset.symbol}; at least {min_bars} are needed.",
                details={"available": len(candles), "required": min_bars},
            )
        resolved = series.asset
        now = datetime.now(UTC)
        session = market_session(resolved.asset_type, series.timezone, now)
        if candles:
            last_open = datetime.fromtimestamp(candles[-1].time, UTC)
            bar_end = datetime.fromtimestamp(bucket_end(candles[-1].time, timeframe), UTC)
            freshness = classify_candle_freshness(last_open, bar_end, is_crypto=is_crypto, session=session, timeframe_label=timeframe.value, now=now)
        else:
            freshness = classify_freshness(now, is_crypto=is_crypto, session=session, now=now)
        limitations = list(series.limitations)
        if not series.last_bar_complete:
            limitations.append("The most recent bar is still forming and will change until it closes.")
        response = CandlesResponse(
            symbol=resolved.symbol,
            display_symbol=resolved.display_symbol,
            asset_type=resolved.asset_type,
            provider=provider.name,
            timeframe=timeframe,
            timezone=series.timezone,
            candles=candles,
            count=len(candles),
            aggregated_from=series.aggregated_from,
            last_bar_complete=series.last_bar_complete,
            freshness=freshness,
            cache=_cache_info(result),
            refresh_seconds=max(5, int(ttl)) if (is_crypto or session.is_open) else 300,
            limitations=limitations,
        )
        return CandleData(response=response, asset=resolved, frame=candles_to_frame(candles))

    # ------------------------------------------------------------------ search
    async def search(self, query: str, asset_type: AssetType = AssetType.AUTO) -> SearchResponse:
        q = query.strip()[:40]

        async def fetch() -> dict:
            results, notes = await self.registry.search(q, asset_type)
            return {"results": [r.model_dump(mode="json") for r in results], "notes": notes}

        key = f"search:{asset_type.value}:{q.upper()}:{int(self.registry.twelvedata.configured)}"
        result = await self.cache.get_or_fetch(key, fetch, ttl=self.settings.search_ttl, stale_ttl=self.settings.search_ttl)
        data = result.value
        if data["notes"] and not result.hit:
            # Don't keep partial results (e.g. a rate-limited provider) for hours.
            await self.cache.backend.delete(key)
        return SearchResponse(
            query=q,
            results=[AssetInfo.model_validate(r) for r in data["results"]],
            notes=data["notes"],
        )
