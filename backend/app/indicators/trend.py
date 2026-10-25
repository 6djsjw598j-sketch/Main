"""Trend indicators: SMA/EMA (see core), VWAP, ADX/DMI and Ichimoku."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.core import rma, true_range


def vwap(df: pd.DataFrame, session_keys: pd.Series) -> pd.Series:
    """Session-anchored VWAP using the typical price (H+L+C)/3.

    ``session_keys`` labels each bar with its session (e.g. the exchange-local date);
    the cumulative sums reset whenever the key changes.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    volume = df["volume"].fillna(0.0)
    pv = (typical * volume).groupby(session_keys.to_numpy()).cumsum()
    cum_vol = volume.groupby(session_keys.to_numpy()).cumsum()
    out = pv / cum_vol.replace(0.0, np.nan)
    out.index = df.index
    return out


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder's Directional Movement System: +DI, -DI and ADX."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = true_range(df)
    # The first bar has no prior bar, so it carries no directional movement.
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr.iloc[0] = np.nan
    atr_ = rma(tr, period)
    plus_di = 100.0 * rma(plus_dm, period) / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * rma(minus_dm, period) / atr_.replace(0.0, np.nan)
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return pd.DataFrame({"plus_di": plus_di, "minus_di": minus_di, "adx": rma(dx, period)}, index=df.index)


def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26) -> pd.DataFrame:
    """Ichimoku Kinko Hyo.

    Spans are plotted ``displacement - 1`` bars ahead (TradingView convention, the current
    bar counts as one). Columns:

    * ``senkou_a`` / ``senkou_b`` - the cloud value *at each bar* (computed from data
      ``displacement - 1`` bars earlier, so it is causal and safe for signals).
    * ``senkou_a_raw`` / ``senkou_b_raw`` - unshifted values, used to project the cloud
      into the future on the chart.
    * ``chikou`` - the close plotted ``displacement - 1`` bars back (display only; using it
      for signals would be look-ahead).
    """
    hi, lo = df["high"], df["low"]
    tenkan_line = (hi.rolling(tenkan, min_periods=tenkan).max() + lo.rolling(tenkan, min_periods=tenkan).min()) / 2
    kijun_line = (hi.rolling(kijun, min_periods=kijun).max() + lo.rolling(kijun, min_periods=kijun).min()) / 2
    span_a_raw = (tenkan_line + kijun_line) / 2
    span_b_raw = (hi.rolling(senkou_b, min_periods=senkou_b).max() + lo.rolling(senkou_b, min_periods=senkou_b).min()) / 2
    shift = displacement - 1
    return pd.DataFrame(
        {
            "tenkan": tenkan_line,
            "kijun": kijun_line,
            "senkou_a": span_a_raw.shift(shift),
            "senkou_b": span_b_raw.shift(shift),
            "senkou_a_raw": span_a_raw,
            "senkou_b_raw": span_b_raw,
            "chikou": df["close"].shift(-shift),
        },
        index=df.index,
    )
