from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.analysis.levels import classic_pivots, fibonacci, previous_periods, reference_levels
from app.analysis.support_resistance import SRConfig, cluster_swings, count_visits, detect_levels
from app.analysis.swings import Swing, find_swings
from app.analysis.trendlines import find_trendlines
from app.indicators.volatility import atr
from app.models.market import AssetType
from tests.helpers import make_frame, oscillating


def _atr(frame: pd.DataFrame) -> float:
    return float(atr(frame, 14).iloc[-1])


def test_swings_are_confirmed_only_with_enough_bars():
    closes = [1, 2, 3, 10, 3, 2, 1, 2, 3, 4, 5]
    frame = make_frame(closes, spread=0.0)
    swings = find_swings(frame, 2)
    highs = [s for s in swings if s.kind == "high"]
    assert [s.index for s in highs] == [3]
    # The last 2 bars can never be swing points.
    assert all(s.index < len(closes) - 2 for s in swings)


def test_cluster_merges_nearby_prices():
    t = pd.Timestamp("2024-01-01", tz="UTC")
    swings = [Swing(i, t, p, "low", None) for i, p in enumerate([100.0, 100.2, 100.4, 105.0])]
    clusters = cluster_swings(swings, tol=0.5)
    assert [len(cl) for cl in clusters] == [3, 1]


def test_count_visits_groups_consecutive_bars():
    highs = np.array([1, 5, 5, 1, 5, 1.0])
    lows = np.array([0, 4, 4, 0, 4, 0.0])
    visits, last = count_visits(highs, lows, 4.5, 5.5)
    assert visits == 2 and last == 4


def test_range_market_produces_support_and_resistance():
    frame = make_frame(oscillating(240, 100, 110, 24), spread=0.3)
    frame = pd.concat([frame, make_frame([105.0] * 3, start="2024-08-28", spread=0.3)])
    result = detect_levels(frame, _atr(frame), SRConfig(lookback=240, swing_window=5))
    price = float(frame["close"].iloc[-1])
    sup, res = result.nearest_support, result.nearest_resistance
    assert sup is not None and res is not None
    assert sup.zone_high < price < res.zone_low
    assert res.price == pytest.approx(110.3, abs=0.6)
    assert sup.price == pytest.approx(99.7, abs=0.6)
    assert res.reactions >= 8 and res.tier == "major"
    assert res.distance == pytest.approx(res.zone_low - price)
    assert res.distance_pct == pytest.approx(res.distance / price * 100, rel=1e-3)
    assert 0 <= res.strength <= 100
    assert set(res.score_breakdown) == {"reactions", "touches", "recency", "volume", "confluence"}


def test_breakout_detected_after_close_above_resistance():
    closes = [*list(oscillating(200, 100, 110, 20)), 111.5, 113.0, 114.0]
    frame = make_frame(closes, spread=0.3)
    result = detect_levels(frame, _atr(frame), SRConfig(lookback=203, swing_window=4))
    broken = [lv for lv in result.levels if lv.status == "breakout"]
    assert broken, [lv.status for lv in result.levels]
    assert broken[0].kind == "support"  # former resistance now below price
    assert broken[0].historical_role == "resistance"
    assert broken[0].status_changed_at is not None


def test_breakdown_and_reclaim():
    base = list(oscillating(200, 100, 110, 20))
    down = make_frame([*base, 98.0, 96.5, 95.0], spread=0.3)
    res_down = detect_levels(down, _atr(down), SRConfig(lookback=203, swing_window=4))
    assert any(lv.status == "breakdown" and lv.kind == "resistance" for lv in res_down.levels)

    reclaim = make_frame([*base, 98.0, 96.5, 95.0, 97.0, 101.5, 102.0], spread=0.3)
    res_reclaim = detect_levels(reclaim, _atr(reclaim), SRConfig(lookback=206, swing_window=4))
    assert any(lv.status == "reclaimed" for lv in res_reclaim.levels)


def test_monotonic_series_has_no_levels_and_does_not_crash():
    frame = make_frame(np.linspace(100, 200, 100), spread=0.0)
    result = detect_levels(frame, _atr(frame), SRConfig(lookback=100, swing_window=5))
    assert result.levels == []
    assert result.nearest_support is None and result.nearest_resistance is None


def test_without_volume_score_is_rescaled():
    frame = make_frame(oscillating(200, 100, 110, 20), spread=0.3, volume=None)
    result = detect_levels(frame, _atr(frame), SRConfig(lookback=200, swing_window=4))
    assert result.levels
    assert all("volume" not in lv.score_breakdown for lv in result.levels)
    assert max(lv.strength for lv in result.levels) <= 100


def test_previous_periods_from_daily_bars():
    idx = pd.date_range("2024-02-20", "2024-03-13", freq="D", tz="UTC")
    n = len(idx)
    daily = pd.DataFrame(
        {"open": np.arange(n) + 1.0, "high": np.arange(n) + 2.0, "low": np.arange(n) + 0.5, "close": np.arange(n) + 1.5, "volume": 1.0}, index=idx
    )
    now = datetime(2024, 3, 13, 15, 0, tzinfo=UTC)  # Wednesday; today's bar still forming
    periods = {p.key: p for p in previous_periods(daily, AssetType.CRYPTO, "UTC", now)}
    i_mar12 = list(idx.date).index(datetime(2024, 3, 12).date())
    assert periods["day"].high == daily["high"].iloc[i_mar12]
    week = daily.loc["2024-03-04":"2024-03-10"]
    assert periods["week"].high == week["high"].max() and periods["week"].low == week["low"].min()
    month = daily.loc["2024-02-20":"2024-02-29"]
    assert "month" not in periods or periods["month"].start.day <= 5  # partial February is not reported
    refs = reference_levels(list(periods.values()), price=10.0)
    assert {r.key for r in refs} >= {"prev_day_high", "prev_day_low", "prev_week_high", "prev_week_low"}
    assert all(r.source == "observed" for r in refs)
    del month


def test_classic_pivots_formula():
    from app.analysis.levels import PeriodHLC

    p = classic_pivots(
        PeriodHLC("day", "Previous day", high=110, low=90, close=105, start=datetime(2024, 1, 1).date(), end=datetime(2024, 1, 1).date())
    )
    pp = (110 + 90 + 105) / 3
    assert p.levels["P"] == pytest.approx(pp)
    assert p.levels["R1"] == pytest.approx(2 * pp - 90)
    assert p.levels["S1"] == pytest.approx(2 * pp - 110)
    assert p.levels["R2"] == pytest.approx(pp + 20)
    assert p.levels["S3"] == pytest.approx(90 - 2 * (110 - pp))


def test_fibonacci_direction_and_levels():
    up = make_frame(list(np.linspace(100, 50, 30)) + list(np.linspace(50, 150, 40)), spread=0.0)
    fib = fibonacci(up)
    assert fib is not None and fib.direction == "up"
    by_ratio = {lv.ratio: lv.price for lv in fib.levels}
    assert by_ratio[0.0] == pytest.approx(150) and by_ratio[1.0] == pytest.approx(50)
    assert by_ratio[0.5] == pytest.approx(100)
    down = make_frame(list(np.linspace(100, 150, 30)) + list(np.linspace(150, 60, 30)), spread=0.0)
    fib_down = fibonacci(down)
    assert fib_down is not None and fib_down.direction == "down"
    assert {lv.ratio: lv.price for lv in fib_down.levels}[0.618] == pytest.approx(60 + 0.618 * 90)


def test_rising_support_trendline():
    closes = []
    for k in range(6):
        base = 100 + 4 * k
        closes += list(np.linspace(base, base + 10, 8)) + list(np.linspace(base + 10, base + 4, 6))
    frame = make_frame(closes, spread=0.2)
    swings = find_swings(frame, 3)
    lines = find_trendlines(frame, swings, tol=0.8)
    support = [ln for ln in lines if ln.kind == "support"]
    assert support and support[0].slope_per_bar > 0
    assert support[0].points[-1].time == int(frame.index[-1].timestamp())
    assert support[0].touches >= 2
