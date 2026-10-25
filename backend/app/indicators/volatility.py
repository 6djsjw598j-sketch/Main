"""Volatility indicators: Bollinger Bands, ATR, Keltner Channels, historical volatility."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.core import ema, rma, sma, true_range


def bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands with the population standard deviation (as defined by J. Bollinger)."""
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = upper - lower
    return pd.DataFrame(
        {
            "middle": mid,
            "upper": upper,
            "lower": lower,
            "percent_b": (close - lower) / width.replace(0.0, np.nan),
            "bandwidth": width / mid.replace(0.0, np.nan),
        },
        index=close.index,
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average True Range. The first bar's true range is its high-low range."""
    return rma(true_range(df), period)


def keltner(df: pd.DataFrame, period: int = 20, atr_period: int = 10, multiplier: float = 2.0) -> pd.DataFrame:
    mid = ema(df["close"], period)
    band = multiplier * atr(df, atr_period)
    return pd.DataFrame({"middle": mid, "upper": mid + band, "lower": mid - band}, index=df.index)


def historical_volatility(close: pd.Series, period: int = 20, periods_per_year: float = 252.0) -> pd.Series:
    """Annualized close-to-close volatility in percent: stdev(ln returns, ddof=1) * sqrt(N) * 100."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(period, min_periods=period).std(ddof=1) * np.sqrt(periods_per_year) * 100.0
