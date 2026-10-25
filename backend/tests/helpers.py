"""Deterministic test data. These synthetic series are used only in tests, never by the app."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def make_frame(
    closes: list[float] | np.ndarray,
    start: str = "2024-01-01",
    step_seconds: int = 86400,
    spread: float = 0.5,
    volume: float | list[float] | None = 1000.0,
) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    index = pd.to_datetime(pd.Timestamp(start, tz="UTC").value // 10**9 + np.arange(len(closes)) * step_seconds, unit="s", utc=True)
    if volume is None:
        vol = np.full(len(closes), np.nan)
    elif isinstance(volume, (int, float)):
        vol = np.full(len(closes), float(volume))
    else:
        vol = np.asarray(volume, dtype=float)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vol}, index=index)


def random_walk(n: int, seed: int = 7, start: float = 100.0, drift: float = 0.0, vol: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol, n)
    return np.maximum(1.0, start + np.cumsum(steps))


def ohlc_random(n: int, seed: int = 3, start: float = 100.0) -> pd.DataFrame:
    """Random OHLCV with realistic high/low/volume structure (for library cross-checks)."""
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    open_ = np.concatenate([[start], close[:-1]]) * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    volume = rng.integers(1_000, 50_000, n).astype(float)
    index = pd.date_range("2022-01-03", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index)


def oscillating(n: int, low: float = 100.0, high: float = 110.0, period: int = 20) -> np.ndarray:
    mid, amp = (low + high) / 2, (high - low) / 2
    return np.array([mid + amp * math.sin(2 * math.pi * i / period) for i in range(n)])


def synthetic_price(ts: int) -> float:
    """Smooth deterministic price path used by the mocked upstream APIs."""
    days = ts / 86400.0
    return 100.0 + 8.0 * math.sin(days / 9.0) + 3.0 * math.sin(days * 1.7) + 0.01 * (days % 1000)
