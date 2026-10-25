"""Swing (fractal) high/low detection.

A bar is a swing high when its high is the highest high of the ``window`` bars on each
side, and strictly higher than every bar on its left (so a flat top is counted once).
Swing lows are symmetric. The last ``window`` bars can never be confirmed - doing so
would require future data - so recent pivots appear only once enough bars have closed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.models.market import Timeframe

DEFAULT_SWING_WINDOW = {
    Timeframe.M1: 5,
    Timeframe.M5: 5,
    Timeframe.M15: 5,
    Timeframe.M30: 5,
    Timeframe.H1: 5,
    Timeframe.H4: 4,
    Timeframe.D1: 5,
    Timeframe.W1: 3,
    Timeframe.MN1: 2,
}


@dataclass(frozen=True)
class Swing:
    index: int
    time: pd.Timestamp
    price: float
    kind: str  # "high" | "low"
    rel_volume: float | None


def find_swings(frame: pd.DataFrame, window: int) -> list[Swing]:
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    volume = frame["volume"].to_numpy(dtype=float) if "volume" in frame else np.full(len(frame), np.nan)
    n = len(frame)
    swings: list[Swing] = []
    for i in range(window, n - window):
        left_h, right_h = highs[i - window : i], highs[i + 1 : i + window + 1]
        left_l, right_l = lows[i - window : i], lows[i + 1 : i + window + 1]
        rel_vol = _relative_volume(volume, i)
        if highs[i] > left_h.max() and highs[i] >= right_h.max():
            swings.append(Swing(i, frame.index[i], float(highs[i]), "high", rel_vol))
        if lows[i] < left_l.min() and lows[i] <= right_l.min():
            swings.append(Swing(i, frame.index[i], float(lows[i]), "low", rel_vol))
    return swings


def _relative_volume(volume: np.ndarray, i: int, lookback: int = 20) -> float | None:
    if np.isnan(volume[i]) or i < 5:
        return None
    hist = volume[max(0, i - lookback) : i]
    hist = hist[~np.isnan(hist)]
    if len(hist) < 5 or hist.mean() <= 0:
        return None
    return float(volume[i] / hist.mean())
