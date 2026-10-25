"""Volume indicators: OBV, Money Flow Index, Accumulation/Distribution line."""

from __future__ import annotations

import numpy as np
import pandas as pd


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume.fillna(0.0)).cumsum()


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    flow = typical * df["volume"].fillna(0.0)
    change = typical.diff()
    positive = flow.where(change > 0, 0.0)
    negative = flow.where(change < 0, 0.0)
    positive.iloc[0] = np.nan
    negative.iloc[0] = np.nan
    pos_sum = positive.rolling(period, min_periods=period).sum()
    neg_sum = negative.rolling(period, min_periods=period).sum()
    ratio = pos_sum / neg_sum.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + ratio)
    out = out.where(neg_sum != 0, np.where(pos_sum > 0, 100.0, 50.0))
    return out.where(pos_sum.notna())


def accumulation_distribution(df: pd.DataFrame) -> pd.Series:
    span = (df["high"] - df["low"]).replace(0.0, np.nan)
    multiplier = (((df["close"] - df["low"]) - (df["high"] - df["close"])) / span).fillna(0.0)
    return (multiplier * df["volume"].fillna(0.0)).cumsum()
