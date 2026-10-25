"""Trendlines through confirmed swing points.

For support (swing lows) and resistance (swing highs) separately, the most recent pair
of swings at least ``min_separation`` bars apart is chosen such that no close between
the two anchors violates the line by more than the zone tolerance. The line is then
projected to the last bar. A line is reported as broken when the last close is beyond
it by more than the tolerance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.swings import Swing
from app.indicators.engine import fmt_price
from app.models.analysis import Trendline, TrendlinePoint


def find_trendlines(
    window: pd.DataFrame,
    swings: list[Swing],
    tol: float,
    max_candidates: int = 6,
    min_separation: int = 5,
) -> list[Trendline]:
    n = len(window)
    if n < 10:
        return []
    closes = window["close"].to_numpy(dtype=float)
    times = [int(ts.timestamp()) for ts in window.index]
    out: list[Trendline] = []
    for kind, swing_kind in (("support", "low"), ("resistance", "high")):
        pts = [s for s in swings if s.kind == swing_kind][-max_candidates:]
        found: Trendline | None = None
        for j in range(len(pts) - 1, 0, -1):
            for i in range(j - 1, -1, -1):
                a, b = pts[i], pts[j]
                if b.index - a.index < min_separation:
                    continue
                slope = (b.price - a.price) / (b.index - a.index)
                xs = np.arange(a.index, n)
                line = a.price + slope * (xs - a.index)
                seg = closes[a.index : b.index + 1]
                seg_line = line[: b.index - a.index + 1]
                if kind == "support" and np.any(seg < seg_line - tol):
                    continue
                if kind == "resistance" and np.any(seg > seg_line + tol):
                    continue
                current = float(line[-1])
                if current <= 0:
                    continue
                touches = sum(
                    1 for s in swings if s.kind == swing_kind and s.index >= a.index and abs(s.price - (a.price + slope * (s.index - a.index))) <= tol
                )
                last_close = closes[-1]
                broken = bool(last_close < current - tol) if kind == "support" else bool(last_close > current + tol)
                direction = "rising" if slope > 0 else "falling" if slope < 0 else "flat"
                found = Trendline(
                    kind=kind,  # type: ignore[arg-type]
                    points=[TrendlinePoint(time=times[a.index], price=a.price), TrendlinePoint(time=times[-1], price=round(current, 10))],
                    anchors=[TrendlinePoint(time=times[a.index], price=a.price), TrendlinePoint(time=times[b.index], price=b.price)],
                    slope_per_bar=round(slope, 10),
                    current_value=round(current, 10),
                    touches=touches,
                    broken=broken,
                    description=(
                        f"{direction.capitalize()} {kind} line through swing {swing_kind}s at {fmt_price(a.price)} and "
                        f"{fmt_price(b.price)}; projected value now {fmt_price(current)}"
                        + (" — the last close is beyond it (line broken)." if broken else ".")
                    ),
                )
                break
            if found:
                break
        if found:
            out.append(found)
    return out
