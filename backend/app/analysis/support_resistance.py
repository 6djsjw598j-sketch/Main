"""Support/resistance zone detection.

Method (all steps are deterministic and documented in docs/METHODOLOGY.md):

1. Find swing highs/lows in the lookback window (``swings.find_swings``).
2. Zone tolerance ``tol = max(zone_atr_mult x ATR, min_zone_pct % of price)``.
3. Cluster swing prices: sorted prices join the current cluster while the gap to the
   previous price is <= tol and the cluster stays narrower than 2.5 x tol.
4. Each cluster becomes a zone [min - tol/4, max + tol/4].
5. Score 0-100 = reactions (35) + touches (20) + recency (20) + reaction volume (15)
   + confluence with an observed reference level or Fibonacci level (10).
   Without volume data the remaining 85 points are rescaled to 100.
6. Zones below the last price are support, above are resistance; a zone containing the
   price is being "tested". Breakouts/breakdowns/reclaims/losses are detected from the
   most recent close-through of the zone within ``recent_bars``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.swings import Swing, find_swings
from app.models.analysis import Level

W_REACTIONS, W_TOUCHES, W_RECENCY, W_VOLUME, W_CONFLUENCE = 35.0, 20.0, 20.0, 15.0, 10.0


@dataclass
class SRConfig:
    lookback: int = 300
    swing_window: int = 5
    zone_atr_mult: float = 0.35
    min_zone_pct: float = 0.15
    major_threshold: float = 55.0
    max_levels: int = 12
    recent_bars: int = 10


@dataclass
class SRResult:
    levels: list[Level]
    nearest_support: Level | None
    nearest_resistance: Level | None
    tolerance: float
    swings: list[Swing]
    window: pd.DataFrame


def zone_tolerance(price: float, atr_value: float | None, cfg: SRConfig) -> float:
    atr_part = (atr_value or 0.0) * cfg.zone_atr_mult
    return max(atr_part, price * cfg.min_zone_pct / 100.0)


def cluster_swings(swings: list[Swing], tol: float) -> list[list[Swing]]:
    if not swings:
        return []
    ordered = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = [[ordered[0]]]
    for swing in ordered[1:]:
        current = clusters[-1]
        if swing.price - current[-1].price <= tol and swing.price - current[0].price <= 2.5 * tol:
            current.append(swing)
        else:
            clusters.append([swing])
    return clusters


def count_visits(highs: np.ndarray, lows: np.ndarray, zone_low: float, zone_high: float) -> tuple[int, int]:
    """Number of distinct visits into the zone (consecutive touching bars count once) and last touch index."""
    touching = (lows <= zone_high) & (highs >= zone_low)
    visits, last = 0, -1
    prev = False
    for i, t in enumerate(touching):
        if t and not prev:
            visits += 1
        if t:
            last = i
        prev = bool(t)
    return visits, last


def _status(closes: np.ndarray, zone_low: float, zone_high: float, role: str, recent_bars: int) -> tuple[str, str, int]:
    """Returns (status, explanation, index of the close-through bar or -1)."""
    side = np.where(closes > zone_high, 1, np.where(closes < zone_low, -1, 0))
    last_side, transition_idx, transition_dir = 0, -1, 0
    for i, s in enumerate(side):
        if s == 0:
            continue
        if last_side != 0 and s != last_side:
            transition_idx, transition_dir = i, int(s)
        last_side = int(s)
    n = len(closes)
    if side[-1] == 0:
        return "testing", "The last close is inside the zone; price is testing it.", -1
    if transition_idx >= 0 and n - 1 - transition_idx < recent_bars:
        bars_ago = n - 1 - transition_idx
        when = "on the latest bar" if bars_ago == 0 else f"{bars_ago} bar{'s' if bars_ago != 1 else ''} ago"
        if transition_dir > 0:
            if role == "support":
                return "reclaimed", f"Price closed back above this former support {when} after losing it.", transition_idx
            return "breakout", f"Price closed above this former resistance {when}; it may now act as support.", transition_idx
        if role == "resistance":
            return "lost", f"Price closed back below this level {when} after having been above it.", transition_idx
        return "breakdown", f"Price closed below this former support {when}; it may now act as resistance.", transition_idx
    return "intact", "No close through this zone in the recent bars.", -1


def detect_levels(
    frame: pd.DataFrame,
    atr_value: float | None,
    cfg: SRConfig,
    confluence_prices: list[float] | None = None,
) -> SRResult:
    window = frame.iloc[-cfg.lookback :]
    n = len(window)
    price = float(window["close"].iloc[-1])
    tol = zone_tolerance(price, atr_value, cfg)
    swings = find_swings(window, cfg.swing_window)
    if not swings:
        return SRResult([], None, None, tol, swings, window)

    highs = window["high"].to_numpy(dtype=float)
    lows = window["low"].to_numpy(dtype=float)
    closes = window["close"].to_numpy(dtype=float)
    volume_available = any(s.rel_volume is not None for s in swings)
    pad = tol * 0.25
    candidates: list[Level] = []
    for idx, cluster in enumerate(cluster_swings(swings, tol)):
        prices = [s.price for s in cluster]
        zone_low, zone_high = min(prices) - pad, max(prices) + pad
        center = float(np.mean(prices))
        reactions = len(cluster)
        n_highs = sum(1 for s in cluster if s.kind == "high")
        n_lows = reactions - n_highs
        historical_role = "resistance" if n_highs > n_lows else "support" if n_lows > n_highs else "mixed"
        touches, _ = count_visits(highs, lows, zone_low, zone_high)
        last_reaction = max(cluster, key=lambda s: s.index)
        first = min(cluster, key=lambda s: s.index)
        recency = math.exp(-(n - 1 - last_reaction.index) / max(1.0, n / 3.0))
        rel_vols = [s.rel_volume for s in cluster if s.rel_volume is not None]
        breakdown = {
            "reactions": round(W_REACTIONS * min(reactions, 5) / 5.0, 1),
            "touches": round(W_TOUCHES * min(touches, 8) / 8.0, 1),
            "recency": round(W_RECENCY * recency, 1),
        }
        if volume_available:
            avg_rel = float(np.mean(rel_vols)) if rel_vols else 0.0
            breakdown["volume"] = round(W_VOLUME * min(1.0, max(0.0, (avg_rel - 0.75) / 1.25)), 1)
        confluent = any(zone_low - pad <= p <= zone_high + pad for p in (confluence_prices or []))
        breakdown["confluence"] = W_CONFLUENCE if confluent else 0.0
        total = sum(breakdown.values())
        if not volume_available:
            total *= 100.0 / (100.0 - W_VOLUME)
        strength = round(min(100.0, total), 1)

        if zone_high < price:
            kind = "support"
        elif zone_low > price:
            kind = "resistance"
        else:
            kind = "support" if price >= center else "resistance"
        distance = max(0.0, price - zone_high) if kind == "support" else max(0.0, zone_low - price)
        status, detail, changed_idx = _status(closes, zone_low, zone_high, historical_role, cfg.recent_bars)
        candidates.append(
            Level(
                id=f"L{idx}",
                kind=kind,
                tier="major" if strength >= cfg.major_threshold else "minor",
                price=round(center, 10),
                zone_low=round(zone_low, 10),
                zone_high=round(zone_high, 10),
                touches=touches,
                reactions=reactions,
                strength=strength,
                score_breakdown=breakdown,
                first_seen=first.time.to_pydatetime(),
                last_reaction=last_reaction.time.to_pydatetime(),
                distance=round(distance, 10),
                distance_pct=round(distance / price * 100.0, 4) if price else 0.0,
                status=status,  # type: ignore[arg-type]
                status_detail=detail,
                status_changed_at=window.index[changed_idx].to_pydatetime() if changed_idx >= 0 else None,
                historical_role=historical_role,  # type: ignore[arg-type]
            )
        )

    supports = [lv for lv in candidates if lv.kind == "support"]
    resistances = [lv for lv in candidates if lv.kind == "resistance"]
    nearest_support = max(supports, key=lambda lv: lv.zone_high, default=None)
    nearest_resistance = min(resistances, key=lambda lv: lv.zone_low, default=None)

    chosen = sorted(candidates, key=lambda lv: lv.strength, reverse=True)[: cfg.max_levels]
    for must in (nearest_support, nearest_resistance):
        if must is not None and must not in chosen:
            chosen.append(must)
    chosen.sort(key=lambda lv: lv.price, reverse=True)
    for i, lv in enumerate(chosen):
        lv.id = f"{lv.kind[0].upper()}{i + 1}"
    return SRResult(chosen, nearest_support, nearest_resistance, tol, swings, window)
