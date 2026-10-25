"""Routes a symbol to the provider that can serve it.

Routing order:

1. Crypto pairs listed on Coinbase (``BTC-USD``) or ``asset_type=crypto`` -> Coinbase.
2. Everything else (stocks, ETFs, indices, FX, crypto pairs not on Coinbase) -> Twelve Data,
   if an API key is configured.
3. Otherwise a ``provider_not_configured`` error explains how to enable it.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import Settings
from app.errors import AppError, ProviderNotConfiguredError, SymbolNotFoundError
from app.models.market import AssetInfo, AssetType
from app.providers.base import MarketDataProvider, ProviderStatus
from app.providers.coinbase import CoinbaseProvider
from app.providers.twelvedata import NOT_CONFIGURED_MESSAGE, TwelveDataProvider
from app.symbols import normalize_symbol

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, settings: Settings, coinbase: CoinbaseProvider | None = None, twelvedata: TwelveDataProvider | None = None) -> None:
        self.settings = settings
        self.coinbase = coinbase or CoinbaseProvider(settings)
        self.twelvedata = twelvedata or TwelveDataProvider(settings)

    @property
    def providers(self) -> list[MarketDataProvider]:
        return [self.coinbase, self.twelvedata]

    def get(self, name: str) -> MarketDataProvider:
        for provider in self.providers:
            if provider.name == name:
                return provider
        raise KeyError(name)

    async def resolve(self, raw_symbol: str, asset_type: AssetType = AssetType.AUTO) -> tuple[MarketDataProvider, AssetInfo]:
        parsed = normalize_symbol(raw_symbol)
        if self.settings.coinbase_enabled and asset_type in (AssetType.AUTO, AssetType.CRYPTO):
            info = await self.coinbase.resolve(parsed, asset_type)
            if info is not None:
                return self.coinbase, info
        if asset_type is AssetType.CRYPTO and not parsed.is_pair:
            parsed = normalize_symbol(f"{parsed.ticker}-USD")
        if self.twelvedata.configured:
            info = await self.twelvedata.resolve(parsed, asset_type)
            if info is not None:
                return self.twelvedata, info
        if asset_type is AssetType.CRYPTO:
            raise SymbolNotFoundError(
                f"{parsed.symbol} is not listed on Coinbase. Other crypto pairs need a Twelve Data API key.",
                details={"symbol": parsed.symbol},
            )
        raise ProviderNotConfiguredError(
            NOT_CONFIGURED_MESSAGE, details={"symbol": parsed.symbol, "provider": "twelvedata", "env": "TWELVEDATA_API_KEY"}
        )

    async def search(self, query: str, asset_type: AssetType) -> tuple[list[AssetInfo], list[str]]:
        notes: list[str] = []
        tasks = []
        if self.settings.coinbase_enabled and asset_type in (AssetType.AUTO, AssetType.CRYPTO):
            tasks.append(self.coinbase.search(query, asset_type))
        if asset_type is not AssetType.CRYPTO:
            if self.twelvedata.configured:
                tasks.append(self.twelvedata.search(query, asset_type))
            else:
                notes.append("Stock/ETF/index search is disabled until a Twelve Data API key is configured.")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[AssetInfo] = []
        for result in results:
            if isinstance(result, AppError):
                notes.append(result.message)
                continue
            if isinstance(result, BaseException):
                logger.exception("search failed", exc_info=result)
                notes.append("A search provider failed unexpectedly.")
                continue
            merged.extend(result)
        q = query.strip().upper().replace("/", "-")
        # Exact symbol matches first, then keep provider ordering.
        merged.sort(key=lambda a: a.symbol != q and a.display_symbol != q and not a.symbol.startswith(q + "-"))
        return merged[:25], notes

    def statuses(self) -> list[ProviderStatus]:
        return [p.status() for p in self.providers]

    async def close(self) -> None:
        for provider in self.providers:
            await provider.close()
