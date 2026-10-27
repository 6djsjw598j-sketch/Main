"""Deterministic synthetic market data for tests (no network)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def business_days(start: date, count: int) -> list[date]:
    days = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def synthetic_bars(
    start_price: float = 100.0,
    days: int = 600,
    drift: float = 0.08,
    vol: float = 0.18,
    seed: int = 7,
    start: date = date(2023, 1, 2),
    regimes: list[tuple[int, float, float]] | None = None,
) -> pd.DataFrame:
    """Geometric Brownian motion daily bars. ``regimes`` = [(from_index, drift, vol), ...]."""
    rng = np.random.default_rng(seed)
    idx = business_days(start, days)
    mu = np.full(days, drift)
    sigma = np.full(days, vol)
    for begin, d, v in regimes or []:
        mu[begin:] = d
        sigma[begin:] = v
    dt = 1 / 252
    shocks = rng.standard_normal(days)
    rets = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * shocks
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]]) * np.exp(rng.normal(0, 0.002, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, days)))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e6}, index=pd.Index(idx, name="date"))


def trending_bars(days: int = 320, start_price: float = 100.0, daily: float = 0.004, start: date = date(2024, 1, 2)) -> pd.DataFrame:
    """A clean, low-noise uptrend (daily > 0) or downtrend (daily < 0)."""
    idx = business_days(start, days)
    wiggle = 0.004 * np.sin(np.arange(days) / 3.0)
    close = start_price * np.exp(np.cumsum(np.full(days, daily) + wiggle - np.roll(wiggle, 1) * (np.arange(days) > 0)))
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * 1.003
    low = np.minimum(open_, close) * 0.997
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e6}, index=pd.Index(idx, name="date"))
