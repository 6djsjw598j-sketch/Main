"""Moving-average building blocks used by every indicator.

* ``sma``  - simple moving average; NaN until ``period`` values exist.
* ``ema``  - exponential moving average, alpha = 2 / (period + 1), seeded with the SMA of
  the first ``period`` values (the TA-Lib / TradingView convention).
* ``rma``  - Wilder's smoothing (alpha = 1 / period), seeded the same way. Used by RSI,
  ATR and ADX.

All functions return a Series aligned to the input index and never fill missing history
with invented values: the warm-up period is NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def _seeded_ewm(series: pd.Series, period: int, alpha: float) -> pd.Series:
    values = series.to_numpy(dtype=float)
    n = len(values)
    out = np.full(n, np.nan)
    if n < period:
        return pd.Series(out, index=series.index)
    valid = ~np.isnan(values)
    # first index where the preceding `period` values are all valid
    run = 0
    start = -1
    for i in range(n):
        run = run + 1 if valid[i] else 0
        if run >= period:
            start = i
            break
    if start < 0:
        return pd.Series(out, index=series.index)
    seeded = values.copy()
    seeded[:start] = np.nan
    seeded[start] = values[start - period + 1 : start + 1].mean()
    result = pd.Series(seeded, index=series.index).ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    result.iloc[:start] = np.nan
    return result


def ema(series: pd.Series, period: int) -> pd.Series:
    return _seeded_ewm(series, period, 2.0 / (period + 1))


def rma(series: pd.Series, period: int) -> pd.Series:
    return _seeded_ewm(series, period, 1.0 / period)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1)
    tr = ranges.max(axis=1, skipna=True)
    return tr


def has_volume(df: pd.DataFrame) -> bool:
    vol = df["volume"]
    return bool(vol.notna().sum() >= max(2, int(len(df) * 0.8)) and (vol.fillna(0) > 0).sum() >= max(2, int(len(df) * 0.5)))
