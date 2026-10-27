"""Technical indicators on daily bars (DataFrame with open/high/low/close/volume columns).

Warm-up periods are NaN; nothing is back-filled.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), period)
    loss = wilder(-delta.clip(upper=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1)
    return ranges.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), period)
    plus_di = 100 * wilder(plus_dm, period) / tr
    minus_di = 100 * wilder(minus_dm, period) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, period)


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1))


def historical_volatility(close: pd.Series, period: int = 20) -> pd.Series:
    """Annualised close-to-close volatility."""
    return log_returns(close).rolling(period, min_periods=period).std() * math.sqrt(TRADING_DAYS)


def beta(asset_close: pd.Series, market_close: pd.Series, period: int = TRADING_DAYS) -> float | None:
    joined = pd.concat([log_returns(asset_close), log_returns(market_close)], axis=1, join="inner").dropna().tail(period)
    if len(joined) < 40:
        return None
    a, m = joined.iloc[:, 0], joined.iloc[:, 1]
    var = float(m.var())
    if var <= 0:
        return None
    return float(a.cov(m) / var)


def last(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)
