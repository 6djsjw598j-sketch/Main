"""Orchestrates candles -> indicators -> levels -> summary/setups/backtest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd

from app.analysis import backtest as bt
from app.analysis.events import detect_events
from app.analysis.levels import (
    classic_pivots,
    fibonacci,
    pivot_basis,
    previous_periods,
    reference_levels,
)
from app.analysis.setups import METHODOLOGY as SETUP_METHODOLOGY
from app.analysis.setups import build_scenario
from app.analysis.signals import SignalFrame, compute_signal_frame
from app.analysis.summary import (
    METHODOLOGY as SUMMARY_METHODOLOGY,
)
from app.analysis.summary import (
    conditions_and_levels,
    momentum_summary,
    overall_and_factors,
    trend_horizons,
    volatility_summary,
    volume_summary,
)
from app.analysis.support_resistance import SRConfig, SRResult, detect_levels
from app.analysis.swings import DEFAULT_SWING_WINDOW
from app.analysis.trendlines import find_trendlines
from app.errors import AppError
from app.indicators.engine import build_readings, compute_indicator_set, series_payload
from app.indicators.params import IndicatorParams
from app.indicators.volatility import atr
from app.market.service import CandleData, MarketDataService
from app.models.analysis import (
    AnalysisContext,
    BacktestResponse,
    IndicatorsResponse,
    SetupsResponse,
    SummaryResponse,
    SupportResistanceParams,
    SupportResistanceResponse,
)
from app.models.market import AssetType, Timeframe

SR_METHODOLOGY = [
    "Swing highs/lows: a bar whose high (low) is the extreme of the N bars on each side (N = swing window). The last N bars cannot be confirmed yet.",
    "Zone tolerance = max(0.35 x ATR, 0.15% of price). Swing prices closer than the tolerance are clustered into one zone.",
    "Strength (0-100) = reactions (35: swings in the zone, capped at 5) + touches (20: separate visits, capped at 8) + "
    "recency (20: exponential decay of the last reaction) + volume (15: relative volume at the reactions) + confluence (10: an observed "
    "prior-period high/low or Fibonacci level inside the zone). Without volume data the score is rescaled from 85 to 100 points.",
    "Major zones score at or above the major threshold; others are minor. Zones below the last price are support, above are resistance.",
    "Breakout/breakdown = the latest close-through of a zone within the last 10 bars. Reclaimed = a former support regained; lost = a level "
    "price had been above and closed back below.",
    "Previous day/week/month highs and lows are observed facts from completed periods. Zones, pivots, Fibonacci levels and trendlines are "
    "algorithmic estimates; none is guaranteed to hold.",
]


@dataclass
class SRBundle:
    result: SRResult
    response: SupportResistanceResponse


def _context(data: CandleData) -> AnalysisContext:
    resp = data.response
    if not resp.candles:
        raise AppError("No candle data available.")
    last = resp.candles[-1]
    return AnalysisContext(
        symbol=resp.symbol,
        display_symbol=resp.display_symbol,
        asset_type=resp.asset_type,
        provider=resp.provider,
        timeframe=resp.timeframe,
        bars=resp.count,
        as_of=datetime.fromtimestamp(last.time, UTC),
        last_price=last.close,
        last_bar_complete=resp.last_bar_complete,
    )


class AnalysisService:
    def __init__(self, market: MarketDataService) -> None:
        self.market = market

    async def _candles(self, symbol: str, timeframe: Timeframe, limit: int, asset_type: AssetType, min_bars: int) -> CandleData:
        return await self.market.candles(symbol, timeframe, limit, asset_type, min_bars=min_bars)

    async def indicators(self, symbol: str, timeframe: Timeframe, limit: int, asset_type: AssetType, params: IndicatorParams) -> IndicatorsResponse:
        data = await self._candles(symbol, timeframe, limit, asset_type, min_bars=2)
        ind = compute_indicator_set(data.frame, params, timeframe, data.asset.asset_type, data.response.timezone, data.response.last_bar_complete)
        times, series = series_payload(ind)
        notes = list(ind.notes)
        if not data.response.last_bar_complete:
            notes.append("Latest values include the still-forming bar and will change until it closes.")
        return IndicatorsResponse(
            context=_context(data),
            times=times,
            series=series,
            future_times=ind.future_times,
            future_series=ind.future,
            readings=build_readings(ind),
            notes=notes,
        )

    async def _daily_frame(self, data: CandleData, symbol: str, asset_type: AssetType) -> pd.DataFrame:
        if data.response.timeframe is Timeframe.D1 and len(data.frame) >= 45:
            return data.frame
        try:
            daily = await self.market.candles(symbol, Timeframe.D1, 70, asset_type)
        except AppError:
            return pd.DataFrame()  # reference levels are optional context
        return daily.frame

    async def support_resistance_bundle(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        asset_type: AssetType,
        *,
        lookback: int,
        swing_window: int | None,
        major_threshold: float,
        max_levels: int,
        data: CandleData | None = None,
    ) -> SRBundle:
        data = data or await self._candles(symbol, timeframe, limit, asset_type, min_bars=30)
        frame = data.frame
        price = float(frame["close"].iloc[-1])
        atr_series = atr(frame, 14)
        atr_value = None if pd.isna(atr_series.iloc[-1]) else float(atr_series.iloc[-1])
        window = frame.iloc[-lookback:]

        daily = await self._daily_frame(data, symbol, asset_type)
        periods = previous_periods(daily, data.asset.asset_type, data.response.timezone) if not daily.empty else []
        refs = reference_levels(periods, price)
        basis = pivot_basis(timeframe)
        pivot_period = next((p for p in periods if p.key == basis), None)
        pivots = classic_pivots(pivot_period) if pivot_period else None
        fib = fibonacci(window)
        confluence = [r.price for r in refs] + ([lv.price for lv in fib.levels if 0 < lv.ratio < 1] if fib else [])

        cfg = SRConfig(
            lookback=lookback, swing_window=swing_window or DEFAULT_SWING_WINDOW[timeframe], major_threshold=major_threshold, max_levels=max_levels
        )
        result = detect_levels(frame, atr_value, cfg, confluence)
        trendlines = find_trendlines(result.window, result.swings, result.tolerance)
        response = SupportResistanceResponse(
            context=_context(data),
            params=SupportResistanceParams(
                lookback=len(window),
                swing_window=cfg.swing_window,
                tolerance=round(result.tolerance, 10),
                major_threshold=major_threshold,
                max_levels=max_levels,
            ),
            atr=atr_value,
            levels=result.levels,
            nearest_support=result.nearest_support,
            nearest_resistance=result.nearest_resistance,
            reference_levels=refs,
            pivots=pivots,
            fibonacci=fib,
            trendlines=trendlines,
            methodology=SR_METHODOLOGY,
        )
        return SRBundle(result, response)

    def _signals(self, data: CandleData, params: IndicatorParams, weights: dict[str, float]) -> SignalFrame:
        return compute_signal_frame(data.frame, params, weights)

    async def summary(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        asset_type: AssetType,
        params: IndicatorParams,
        weights: dict[str, float],
        bullish_threshold: float,
        bearish_threshold: float,
    ) -> SummaryResponse:
        data = await self._candles(symbol, timeframe, limit, asset_type, min_bars=30)
        sr = await self.support_resistance_bundle(
            symbol,
            timeframe,
            limit,
            asset_type,
            lookback=min(300, limit),
            swing_window=None,
            major_threshold=55.0,
            max_levels=12,
            data=data,
        )
        sig = self._signals(data, params, weights)
        overall, factors, conflicts = overall_and_factors(sig, bullish_threshold, bearish_threshold)
        horizons = trend_horizons(sig)
        conditions, key_levels = conditions_and_levels(data.frame, sig, sr.result, overall.bias, horizons)
        return SummaryResponse(
            context=_context(data),
            overall=overall,
            factors=factors,
            conflicts=conflicts,
            trend=horizons,
            momentum=momentum_summary(sig),
            volatility=volatility_summary(sig, data.frame),
            volume=volume_summary(sig, data.response.last_bar_complete),
            nearest_support=sr.result.nearest_support,
            nearest_resistance=sr.result.nearest_resistance,
            conditions=conditions,
            key_levels=key_levels,
            events=detect_events(data.frame, sig, sr.result),
            methodology=SUMMARY_METHODOLOGY,
        )

    async def setups(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        asset_type: AssetType,
        params: IndicatorParams,
        weights: dict[str, float],
        bullish_threshold: float,
        bearish_threshold: float,
    ) -> SetupsResponse:
        data = await self._candles(symbol, timeframe, limit, asset_type, min_bars=30)
        sr = await self.support_resistance_bundle(
            symbol,
            timeframe,
            limit,
            asset_type,
            lookback=min(300, limit),
            swing_window=None,
            major_threshold=55.0,
            max_levels=12,
            data=data,
        )
        sig = self._signals(data, params, weights)
        overall, _, _ = overall_and_factors(sig, bullish_threshold, bearish_threshold)
        atr_value = sr.response.atr
        res, sup = sr.result.nearest_resistance, sr.result.nearest_support
        observed = {
            "last_price": float(data.frame["close"].iloc[-1]),
            "atr": atr_value,
            "nearest_support_low": sup.zone_low if sup else None,
            "nearest_support_high": sup.zone_high if sup else None,
            "nearest_resistance_low": res.zone_low if res else None,
            "nearest_resistance_high": res.zone_high if res else None,
            "factor_score": overall.score,
        }
        return SetupsResponse(
            context=_context(data),
            observed=observed,
            bullish=build_scenario(data.frame, sr.result, atr_value, overall.bias, True, sig.volume_available),
            bearish=build_scenario(data.frame, sr.result, atr_value, overall.bias, False, sig.volume_available),
            methodology=SETUP_METHODOLOGY,
        )

    async def backtest(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        asset_type: AssetType,
        params: IndicatorParams,
        weights: dict[str, float],
        *,
        entry_threshold: float,
        exit_threshold: float,
        commission_bps: float,
        slippage_bps: float,
        direction: str,
    ) -> BacktestResponse:
        data = await self._candles(symbol, timeframe, limit, asset_type, min_bars=50)
        sig = self._signals(data, params, weights)
        assumptions, metrics, trades, equity, bench = bt.run_backtest(
            data.frame,
            sig,
            last_bar_complete=data.response.last_bar_complete,
            entry_threshold=entry_threshold,
            exit_threshold=exit_threshold,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
            direction=direction,
        )
        return BacktestResponse(
            context=_context(data),
            assumptions=assumptions,
            metrics=metrics,
            trades=trades[-200:],
            equity_curve=equity,
            benchmark_curve=bench,
            caveats=bt.CAVEATS,
        )
