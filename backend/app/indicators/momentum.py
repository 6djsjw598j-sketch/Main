"""Momentum indicators: RSI, MACD, Stochastic, ROC, Williams %R."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.core import ema, rma, sma


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. 100 when there were no losses, 50 when the price did not move."""
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    avg_gain = rma(gains, period)
    avg_loss = rma(losses, period)
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, np.where(avg_gain > 0, 100.0, 50.0))
    out[avg_gain.isna()] = np.nan
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    signal_line = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": signal_line, "histogram": line - signal_line}, index=close.index)


def stochastic(df: pd.DataFrame, k_period: int = 14, smooth_k: int = 3, d_period: int = 3) -> pd.DataFrame:
    """Full stochastic: %K = SMA(raw %K, smooth_k), %D = SMA(%K, d_period)."""
    lowest = df["low"].rolling(k_period, min_periods=k_period).min()
    highest = df["high"].rolling(k_period, min_periods=k_period).max()
    span = highest - lowest
    raw = 100.0 * (df["close"] - lowest) / span.replace(0.0, np.nan)
    raw = raw.where(span != 0, 50.0).where(span.notna())
    k = sma(raw, smooth_k) if smooth_k > 1 else raw
    d = sma(k, d_period)
    return pd.DataFrame({"k": k, "d": d}, index=df.index)


def roc(close: pd.Series, period: int = 12) -> pd.Series:
    prev = close.shift(period)
    return 100.0 * (close - prev) / prev.replace(0.0, np.nan)


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    highest = df["high"].rolling(period, min_periods=period).max()
    lowest = df["low"].rolling(period, min_periods=period).min()
    span = highest - lowest
    out = -100.0 * (highest - df["close"]) / span.replace(0.0, np.nan)
    return out.where(span != 0, -50.0).where(span.notna())
