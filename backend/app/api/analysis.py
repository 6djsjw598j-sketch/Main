"""Technical-analysis endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import Analysis, AssetTypeQ, LimitQ, Symbol, TimeframeQ, Weights
from app.errors import InvalidParameterError
from app.indicators.params import IndicatorParams
from app.models.analysis import (
    BacktestResponse,
    IndicatorsResponse,
    SetupsResponse,
    SummaryResponse,
    SupportResistanceResponse,
)
from app.models.market import AssetType, Timeframe

router = APIRouter(prefix="/analysis", tags=["analysis"])

Params = Annotated[IndicatorParams, Query()]
BullishQ = Annotated[float, Query(ge=0, le=100, description="Score at or above which the bias is bullish")]
BearishQ = Annotated[float, Query(ge=-100, le=0, description="Score at or below which the bias is bearish")]


@router.get("/{symbol}/indicators", response_model=IndicatorsResponse, summary="Indicator series and latest readings")
async def indicators(
    symbol: Symbol,
    analysis: Analysis,
    params: Params,
    timeframe: TimeframeQ = Timeframe.D1,
    limit: LimitQ = 500,
    asset_type: AssetTypeQ = AssetType.AUTO,
) -> IndicatorsResponse:
    return await analysis.indicators(symbol, timeframe, limit, asset_type, params)


@router.get(
    "/{symbol}/support-resistance",
    response_model=SupportResistanceResponse,
    summary="Support/resistance zones, reference levels, pivots, Fibonacci and trendlines",
)
async def support_resistance(
    symbol: Symbol,
    analysis: Analysis,
    timeframe: TimeframeQ = Timeframe.D1,
    limit: LimitQ = 500,
    asset_type: AssetTypeQ = AssetType.AUTO,
    lookback: Annotated[int, Query(ge=50, le=2000, description="Bars used for level detection")] = 300,
    swing_window: Annotated[int | None, Query(ge=2, le=20, description="Bars on each side of a swing; default depends on timeframe")] = None,
    major_threshold: Annotated[float, Query(ge=0, le=100)] = 55.0,
    max_levels: Annotated[int, Query(ge=2, le=30)] = 12,
) -> SupportResistanceResponse:
    bundle = await analysis.support_resistance_bundle(
        symbol,
        timeframe,
        limit,
        asset_type,
        lookback=min(lookback, limit),
        swing_window=swing_window,
        major_threshold=major_threshold,
        max_levels=max_levels,
    )
    return bundle.response


@router.get("/{symbol}/summary", response_model=SummaryResponse, summary="Explainable trend/momentum/volatility summary")
async def summary(
    symbol: Symbol,
    analysis: Analysis,
    params: Params,
    weights: Weights,
    timeframe: TimeframeQ = Timeframe.D1,
    limit: LimitQ = 500,
    asset_type: AssetTypeQ = AssetType.AUTO,
    bullish_threshold: BullishQ = 25.0,
    bearish_threshold: BearishQ = -25.0,
) -> SummaryResponse:
    return await analysis.summary(symbol, timeframe, limit, asset_type, params, weights, bullish_threshold, bearish_threshold)


@router.get("/{symbol}/setups", response_model=SetupsResponse, summary="Educational bullish/bearish technical scenarios")
async def setups(
    symbol: Symbol,
    analysis: Analysis,
    params: Params,
    weights: Weights,
    timeframe: TimeframeQ = Timeframe.D1,
    limit: LimitQ = 500,
    asset_type: AssetTypeQ = AssetType.AUTO,
    bullish_threshold: BullishQ = 25.0,
    bearish_threshold: BearishQ = -25.0,
) -> SetupsResponse:
    return await analysis.setups(symbol, timeframe, limit, asset_type, params, weights, bullish_threshold, bearish_threshold)


@router.get("/{symbol}/backtest", response_model=BacktestResponse, summary="Historical performance of the factor-score signal")
async def backtest(
    symbol: Symbol,
    analysis: Analysis,
    params: Params,
    weights: Weights,
    timeframe: TimeframeQ = Timeframe.D1,
    limit: Annotated[int, Query(ge=300, le=5000)] = 1000,
    asset_type: AssetTypeQ = AssetType.AUTO,
    entry_threshold: Annotated[float, Query(ge=5, le=100)] = 25.0,
    exit_threshold: Annotated[float, Query(ge=-100, le=100)] = 0.0,
    commission_bps: Annotated[float, Query(ge=0, le=200, description="Per side, basis points")] = 10.0,
    slippage_bps: Annotated[float, Query(ge=0, le=200, description="Per side, basis points")] = 5.0,
    direction: Literal["long_only", "long_short"] = "long_only",
) -> BacktestResponse:
    if exit_threshold >= entry_threshold:
        raise InvalidParameterError("exit_threshold must be below entry_threshold.")
    return await analysis.backtest(
        symbol,
        timeframe,
        limit,
        asset_type,
        params,
        weights,
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        direction=direction,
    )
