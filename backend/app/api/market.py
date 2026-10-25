"""Asset search, quotes and candles."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AssetTypeQ, Market, Symbol, TimeframeQ
from app.errors import InvalidParameterError
from app.models.market import AssetType, CandlesResponse, OverviewResponse, QuoteResponse, SearchResponse, Timeframe
from app.symbols import normalize_symbol

router = APIRouter(tags=["market data"])


@router.get("/assets/search", response_model=SearchResponse, summary="Search supported assets")
async def search_assets(
    market: Market,
    q: Annotated[str, Query(min_length=1, max_length=40, description="Ticker or name fragment")],
    asset_type: AssetTypeQ = AssetType.AUTO,
) -> SearchResponse:
    return await market.search(q, asset_type)


@router.get("/market/overview", response_model=OverviewResponse, summary="Compact quotes for several symbols")
async def overview(
    market: Market,
    symbols: Annotated[str, Query(min_length=1, max_length=800, description="Comma-separated, max 20")],
) -> OverviewResponse:
    items = [s for s in (p.strip() for p in symbols.split(",")) if s]
    if not items or len(items) > 20:
        raise InvalidParameterError("Provide between 1 and 20 comma-separated symbols.")
    canonical = list(dict.fromkeys(normalize_symbol(s).symbol for s in items))
    return await market.overview(canonical)


@router.get("/market/{symbol}/quote", response_model=QuoteResponse, summary="Latest quote with session and freshness")
async def get_quote(symbol: Symbol, market: Market, asset_type: AssetTypeQ = AssetType.AUTO) -> QuoteResponse:
    return await market.quote(symbol, asset_type)


@router.get("/market/{symbol}/candles", response_model=CandlesResponse, summary="OHLCV candles")
async def get_candles(
    symbol: Symbol,
    market: Market,
    timeframe: TimeframeQ = Timeframe.D1,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    asset_type: AssetTypeQ = AssetType.AUTO,
) -> CandlesResponse:
    data = await market.candles(symbol, timeframe, limit, asset_type)
    return data.response
