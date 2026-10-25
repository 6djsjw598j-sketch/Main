"""Observed reference levels (previous day/week/month high & low), classic pivots and
Fibonacci retracements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from app.models.analysis import FibLevel, FibonacciLevels, PivotLevels, ReferenceLevel
from app.models.market import AssetType, Timeframe

FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


@dataclass
class PeriodHLC:
    key: str
    label: str
    high: float
    low: float
    close: float
    start: date
    end: date


def _today(asset_type: AssetType, timezone: str, now: datetime) -> date:
    if asset_type is AssetType.CRYPTO:
        return now.astimezone(UTC).date()
    try:
        return now.astimezone(ZoneInfo(timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return now.astimezone(UTC).date()


def previous_periods(daily: pd.DataFrame, asset_type: AssetType, timezone: str, now: datetime | None = None) -> list[PeriodHLC]:
    """Completed previous day / week / month from daily bars (index = session date at 00:00 UTC)."""
    if daily.empty:
        return []
    now = now or datetime.now(UTC)
    today = _today(asset_type, timezone, now)
    dates = [ts.date() for ts in daily.index]
    frame = daily.assign(_date=dates)
    out: list[PeriodHLC] = []

    completed = frame[frame["_date"] < today]
    if not completed.empty:
        last = completed.iloc[-1]
        d = last["_date"]
        out.append(PeriodHLC("day", "Previous day", float(last["high"]), float(last["low"]), float(last["close"]), d, d))

    week_start = today - timedelta(days=today.weekday())
    prev_weeks = frame[frame["_date"] < week_start]
    if not prev_weeks.empty:
        last_date = prev_weeks["_date"].iloc[-1]
        wk_start = last_date - timedelta(days=last_date.weekday())
        wk = prev_weeks[prev_weeks["_date"] >= wk_start]
        out.append(
            PeriodHLC(
                "week",
                "Previous week",
                float(wk["high"].max()),
                float(wk["low"].min()),
                float(wk["close"].iloc[-1]),
                wk["_date"].iloc[0],
                wk["_date"].iloc[-1],
            )
        )

    month_start = today.replace(day=1)
    prev_months = frame[frame["_date"] < month_start]
    if not prev_months.empty:
        last_date = prev_months["_date"].iloc[-1]
        mo = prev_months[prev_months["_date"] >= last_date.replace(day=1)]
        # Only report the month if the data covers it from its first trading days.
        if (mo["_date"].iloc[0] - last_date.replace(day=1)).days <= 4:
            out.append(
                PeriodHLC(
                    "month",
                    "Previous month",
                    float(mo["high"].max()),
                    float(mo["low"].min()),
                    float(mo["close"].iloc[-1]),
                    mo["_date"].iloc[0],
                    mo["_date"].iloc[-1],
                )
            )
    return out


def reference_levels(periods: list[PeriodHLC], price: float) -> list[ReferenceLevel]:
    out: list[ReferenceLevel] = []
    for period in periods:
        for side, value in (("high", period.high), ("low", period.low)):
            diff = value - price
            position = "at" if abs(diff) <= price * 0.0005 else "above" if diff > 0 else "below"
            out.append(
                ReferenceLevel(
                    key=f"prev_{period.key}_{side}",
                    name=f"{period.label} {side}",
                    price=value,
                    period_start=datetime(period.start.year, period.start.month, period.start.day, tzinfo=UTC),
                    period_end=datetime(period.end.year, period.end.month, period.end.day, tzinfo=UTC),
                    distance=round(abs(diff), 10),
                    distance_pct=round(abs(diff) / price * 100.0, 4) if price else 0.0,
                    position=position,  # type: ignore[arg-type]
                )
            )
    return out


def pivot_basis(timeframe: Timeframe) -> str:
    if timeframe.is_intraday:
        return "day"
    if timeframe is Timeframe.D1:
        return "week"
    return "month"


def classic_pivots(period: PeriodHLC) -> PivotLevels:
    h, lo, c = period.high, period.low, period.close
    p = (h + lo + c) / 3.0
    levels = {
        "R3": h + 2 * (p - lo),
        "R2": p + (h - lo),
        "R1": 2 * p - lo,
        "P": p,
        "S1": 2 * p - h,
        "S2": p - (h - lo),
        "S3": lo - 2 * (h - p),
    }
    return PivotLevels(
        basis=f"{period.label.lower()} ({period.start.isoformat()} to {period.end.isoformat()})", levels={k: round(v, 10) for k, v in levels.items()}
    )


def fibonacci(window: pd.DataFrame) -> FibonacciLevels | None:
    if len(window) < 20:
        return None
    hi_idx = int(window["high"].to_numpy().argmax())
    lo_idx = int(window["low"].to_numpy().argmin())
    high = float(window["high"].iloc[hi_idx])
    low = float(window["low"].iloc[lo_idx])
    if high <= low or hi_idx == lo_idx:
        return None
    rng = high - low
    if lo_idx < hi_idx:
        direction = "up"
        levels = [FibLevel(ratio=r, price=round(high - r * rng, 10)) for r in FIB_RATIOS]
        note = "Swing low preceded the swing high: retracement levels measured down from the high."
    else:
        direction = "down"
        levels = [FibLevel(ratio=r, price=round(low + r * rng, 10)) for r in FIB_RATIOS]
        note = "Swing high preceded the swing low: retracement levels measured up from the low."
    return FibonacciLevels(
        direction=direction,  # type: ignore[arg-type]
        swing_high=high,
        swing_high_time=window.index[hi_idx].to_pydatetime(),
        swing_low=low,
        swing_low_time=window.index[lo_idx].to_pydatetime(),
        levels=levels,
        note=note + " Based on the highest high and lowest low inside the lookback window.",
    )
