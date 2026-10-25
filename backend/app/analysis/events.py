"""Recent technical events (crossovers, threshold crossings, level breaks, volume spikes)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.signals import SignalFrame
from app.analysis.support_resistance import SRResult
from app.indicators.engine import fmt_price
from app.indicators.volatility import bollinger
from app.models.analysis import TechnicalEvent


def _crosses(a: pd.Series, b: pd.Series | float) -> tuple[np.ndarray, np.ndarray]:
    b_series = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    diff = a - b_series
    prev = diff.shift(1)
    up = ((diff > 0) & (prev <= 0)).to_numpy()
    down = ((diff < 0) & (prev >= 0)).to_numpy()
    return up, down


def detect_events(frame: pd.DataFrame, sig: SignalFrame, sr: SRResult | None, lookback: int = 60, limit: int = 30) -> list[TechnicalEvent]:
    n = len(frame)
    start = max(1, n - lookback)
    times = [int(ts.timestamp()) for ts in frame.index]
    close = frame["close"]
    raw = sig.raw
    p = sig.params
    events: list[TechnicalEvent] = []

    def add(i: int, kind: str, direction: str, text: str, price: float | None = None) -> None:
        if i >= start:
            events.append(
                TechnicalEvent(
                    time=times[i],
                    type=kind,
                    direction=direction,
                    description=text,  # type: ignore[arg-type]
                    price=None if price is None else float(price),
                )
            )

    def scan(a: pd.Series, b: pd.Series | float, kind: str, up_text: str, down_text: str, up_dir: str = "bullish", down_dir: str = "bearish") -> None:
        up, down = _crosses(a, b)
        for i in np.flatnonzero(up):
            add(int(i), kind, up_dir, up_text, close.iloc[i])
        for i in np.flatnonzero(down):
            add(int(i), kind, down_dir, down_text, close.iloc[i])

    scan(raw["ema9"], raw["ema21"], "ema_cross", "EMA 9 crossed above EMA 21", "EMA 9 crossed below EMA 21")
    scan(raw["sma50"], raw["sma200"], "golden_death_cross", "Golden cross: SMA 50 crossed above SMA 200", "Death cross: SMA 50 crossed below SMA 200")
    scan(raw["macd"], raw["macd_signal"], "macd_signal_cross", "MACD crossed above its signal line", "MACD crossed below its signal line")
    scan(raw["macd"], 0.0, "macd_zero_cross", "MACD crossed above zero", "MACD crossed below zero")
    scan(
        raw["rsi"],
        p.rsi_overbought,
        "rsi_overbought",
        f"RSI rose above {p.rsi_overbought:g} (overbought territory)",
        f"RSI fell back below {p.rsi_overbought:g}",
        up_dir="neutral",
        down_dir="bearish",
    )
    scan(
        raw["rsi"],
        p.rsi_oversold,
        "rsi_oversold",
        f"RSI rose back above {p.rsi_oversold:g}",
        f"RSI fell below {p.rsi_oversold:g} (oversold territory)",
        up_dir="bullish",
        down_dir="neutral",
    )
    scan(raw["close"], raw["sma200"], "sma200_cross", "Close crossed above the SMA 200", "Close crossed below the SMA 200")

    bb = bollinger(close, p.bb_period, p.bb_std)
    up_out, _ = _crosses(close, bb["upper"])
    _, down_out = _crosses(close, bb["lower"])
    for i in np.flatnonzero(up_out):
        add(int(i), "bollinger_break", "neutral", "Close moved above the upper Bollinger Band", close.iloc[i])
    for i in np.flatnonzero(down_out):
        add(int(i), "bollinger_break", "neutral", "Close moved below the lower Bollinger Band", close.iloc[i])

    if sig.volume_available and "volume_sma" in raw:
        spikes = (raw["volume"] > 2.5 * raw["volume_sma"]).to_numpy()
        for i in np.flatnonzero(spikes):
            direction = "bullish" if close.iloc[i] >= frame["open"].iloc[i] else "bearish"
            ratio = raw["volume"].iloc[i] / raw["volume_sma"].iloc[i]
            add(
                int(i),
                "volume_spike",
                direction,
                f"Volume spike: {ratio:.1f}x the average on a {'rising' if direction == 'bullish' else 'falling'} bar",
                close.iloc[i],
            )

    if n > 51:
        prior_high = frame["high"].shift(1).rolling(50, min_periods=50).max()
        prior_low = frame["low"].shift(1).rolling(50, min_periods=50).min()
        new_high = (close > prior_high) & (close.shift(1) <= prior_high.shift(1))
        new_low = (close < prior_low) & (close.shift(1) >= prior_low.shift(1))
        for i in np.flatnonzero(new_high.to_numpy()):
            add(int(i), "range_breakout", "bullish", "Close above the prior 50-bar high", close.iloc[i])
        for i in np.flatnonzero(new_low.to_numpy()):
            add(int(i), "range_breakdown", "bearish", "Close below the prior 50-bar low", close.iloc[i])

    if sr is not None:
        labels = {
            "breakout": ("bullish", "broke above resistance"),
            "reclaimed": ("bullish", "reclaimed level"),
            "breakdown": ("bearish", "broke below support"),
            "lost": ("bearish", "lost level"),
        }
        for level in sr.levels:
            if level.status in labels and level.status_changed_at is not None:
                direction, verb = labels[level.status]
                ts = int(level.status_changed_at.timestamp())
                if ts in times and times.index(ts) >= start:
                    events.append(
                        TechnicalEvent(
                            time=ts,
                            type=f"level_{level.status}",
                            direction=direction,  # type: ignore[arg-type]
                            description=f"Price {verb} zone {fmt_price(level.zone_low)}–{fmt_price(level.zone_high)}",
                            price=level.price,
                        )
                    )

    events.sort(key=lambda e: e.time, reverse=True)
    return events[:limit]
