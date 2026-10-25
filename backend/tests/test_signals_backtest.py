"""Factor model, summary, scenarios and backtest behaviour."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis.backtest import run_backtest
from app.analysis.setups import build_scenario
from app.analysis.signals import FACTOR_KEYS, SignalFrame, compute_signal_frame, parse_weights
from app.analysis.summary import overall_and_factors, trend_horizons, volatility_summary
from app.analysis.support_resistance import SRConfig, detect_levels
from app.errors import InsufficientDataError
from app.indicators.params import IndicatorParams
from app.indicators.volatility import atr
from tests.helpers import make_frame, ohlc_random, oscillating, random_walk

PARAMS = IndicatorParams()


def test_parse_weights():
    w = parse_weights("rsi:2, macd:0")
    assert w["rsi"] == 2 and w["macd"] == 0 and w["price_vs_sma200"] == 1.5
    for bad in ("nope:1", "rsi:abc", "rsi:50"):
        with pytest.raises(ValueError):
            parse_weights(bad)
    with pytest.raises(ValueError):
        parse_weights(",".join(f"{k}:0" for k in FACTOR_KEYS))


def test_factors_are_bounded_and_causal():
    frame = ohlc_random(400, seed=21)
    full = compute_signal_frame(frame, PARAMS)
    values = full.factors.to_numpy()
    finite = values[np.isfinite(values)]
    assert finite.min() >= -1 and finite.max() <= 1
    # No look-ahead: computing on a truncated history gives identical values for earlier bars.
    cut = 300
    truncated = compute_signal_frame(frame.iloc[:cut], PARAMS)
    pd.testing.assert_frame_equal(full.factors.iloc[:cut], truncated.factors, check_exact=False, rtol=1e-9)
    s_full, _ = full.score()
    s_cut, _ = truncated.score()
    pd.testing.assert_series_equal(s_full.iloc[:cut], s_cut, check_exact=False, rtol=1e-9)


def test_uptrend_is_bullish_and_downtrend_bearish():
    up = make_frame(np.linspace(100, 220, 300) + np.sin(np.arange(300)) * 0.5)
    sig_up = compute_signal_frame(up, PARAMS)
    overall, factors, _ = overall_and_factors(sig_up, 25, -25)
    assert overall.bias == "bullish" and overall.score > 50
    assert sum(f.contribution or 0 for f in factors if f.available) == pytest.approx(overall.score, abs=0.2)
    assert all(h.direction == "bullish" for h in trend_horizons(sig_up))

    down = make_frame(np.linspace(220, 100, 300) + np.sin(np.arange(300)) * 0.5)
    overall_down, _, _ = overall_and_factors(compute_signal_frame(down, PARAMS), 25, -25)
    assert overall_down.bias == "bearish"


def test_short_history_excludes_factors_instead_of_guessing():
    frame = make_frame(random_walk(80, seed=4))
    overall, factors, _ = overall_and_factors(compute_signal_frame(frame, PARAMS), 25, -25)
    by_key = {f.key: f for f in factors}
    assert by_key["price_vs_sma200"].available is False and by_key["price_vs_sma200"].value is None
    assert "200 bars" in by_key["price_vs_sma200"].detail
    assert overall.factors_used < overall.factors_total
    assert all(h.direction == "insufficient_data" for h in trend_horizons(compute_signal_frame(frame, PARAMS))[2:])


def test_zero_weight_disables_factor():
    frame = make_frame(np.linspace(100, 220, 300))
    weights = parse_weights("rsi:0")
    _, factors, _ = overall_and_factors(compute_signal_frame(frame, PARAMS, weights), 25, -25)
    rsi = next(f for f in factors if f.key == "rsi")
    assert rsi.available is False and "Disabled" in rsi.detail


def test_volatility_regime():
    calm_then_wild = np.concatenate([100 + np.sin(np.arange(250)) * 0.2, 100 + np.cumsum(np.random.default_rng(1).normal(0, 3, 60))])
    frame = make_frame(calm_then_wild, spread=0.1)
    vol = volatility_summary(compute_signal_frame(frame, PARAMS), frame)
    assert vol.state == "high" and vol.percentile is not None and vol.percentile > 67


# ----------------------------------------------------------------------------- backtest
def _controlled_signal(frame: pd.DataFrame, scores: list[float]) -> SignalFrame:
    factors = pd.DataFrame({k: np.nan for k in FACTOR_KEYS}, index=frame.index)
    factors["rsi"] = np.array(scores) / 100.0
    for key in ("price_vs_sma200", "sma50_vs_sma200", "price_vs_sma50", "ema_9_21", "adx_dmi", "ichimoku", "macd", "stochastic", "roc"):
        factors[key] = 0.0
    weights = {k: 0.0 for k in FACTOR_KEYS} | {"rsi": 1.0}
    return SignalFrame(factors=factors, raw=pd.DataFrame(index=frame.index), volume_available=False, params=PARAMS, weights=weights)


def test_backtest_executes_next_bar_open_with_costs():
    n = 150
    frame = make_frame(np.linspace(100, 250, n), spread=0.0)
    scores = [0.0] * n
    scores[10] = 50.0  # entry signal at close of bar 10 -> fill at open of bar 11
    for i in range(11, 40):
        scores[i] = 50.0
    scores[40] = -10.0  # exit signal at close of bar 40 -> fill at open of bar 41
    sig = _controlled_signal(frame, scores)
    _, metrics, trades, equity, bench = run_backtest(
        frame,
        sig,
        last_bar_complete=True,
        entry_threshold=25,
        exit_threshold=0,
        commission_bps=10,
        slippage_bps=5,
    )
    assert len(trades) == 1
    trade = trades[0]
    opens = frame["open"].to_numpy()
    cost = 15 / 10_000
    assert trade.entry_time == int(frame.index[11].timestamp())
    assert trade.entry_price == pytest.approx(opens[11] * (1 + cost))
    assert trade.exit_price == pytest.approx(opens[41] * (1 - cost))
    assert trade.return_pct == pytest.approx((opens[41] * (1 - cost) / (opens[11] * (1 + cost)) - 1) * 100, rel=1e-6)
    assert metrics.trades == 1 and metrics.win_rate == 100.0
    assert metrics.buy_and_hold_return_pct > metrics.total_return_pct  # strategy was in the market only part of the time
    assert equity[0].price == pytest.approx(1.0) and bench[0].price == pytest.approx(1.0)


def test_backtest_drops_forming_bar_and_marks_open_trade():
    n = 150
    frame = make_frame(np.linspace(100, 160, n), spread=0.0)
    scores = [50.0] * n
    sig = _controlled_signal(frame, scores)
    assumptions, _, trades, _, _ = run_backtest(
        frame, sig, last_bar_complete=False, entry_threshold=25, exit_threshold=0, commission_bps=0, slippage_bps=0
    )
    assert assumptions.evaluation_end == int(frame.index[-2].timestamp())
    assert trades[-1].open is True


def test_backtest_short_side():
    n = 150
    frame = make_frame(np.linspace(200, 100, n), spread=0.0)
    sig = _controlled_signal(frame, [-60.0] * n)
    _, metrics, trades, _, _ = run_backtest(
        frame, sig, last_bar_complete=True, entry_threshold=25, exit_threshold=0, commission_bps=0, slippage_bps=0, direction="long_short"
    )
    assert trades and trades[0].direction == "short" and metrics.total_return_pct > 0
    _, long_only, _, _, _ = run_backtest(frame, sig, last_bar_complete=True, entry_threshold=25, exit_threshold=0, commission_bps=0, slippage_bps=0)
    assert long_only.trades == 0


def test_backtest_requires_enough_history():
    frame = make_frame(random_walk(220, seed=9))
    with pytest.raises(InsufficientDataError):
        run_backtest(
            frame,
            compute_signal_frame(frame, PARAMS),
            last_bar_complete=True,
            entry_threshold=25,
            exit_threshold=0,
            commission_bps=10,
            slippage_bps=5,
        )


# ----------------------------------------------------------------------------- scenarios
def _sr(frame):
    return detect_levels(frame, float(atr(frame, 14).iloc[-1]), SRConfig(lookback=len(frame), swing_window=4))


def test_scenarios_use_detected_levels_only():
    frame = make_frame([*list(oscillating(240, 100, 110, 24)), 104.0, 103.0, 102.5], spread=0.3)
    sr = _sr(frame)
    atr_v = float(atr(frame, 14).iloc[-1])
    bull = build_scenario(frame, sr, atr_v, "neutral", True, True)
    assert bull.available and bull.entry_zone_low is not None and bull.invalidation is not None
    assert bull.invalidation < bull.entry_zone_low
    level_edges = {lv.zone_low for lv in sr.levels} | {lv.zone_high for lv in sr.levels}
    assert all(t.price in level_edges for t in bull.targets)
    if bull.targets:
        t = bull.targets[0]
        expected = (t.price - bull.entry_reference) / (bull.entry_reference - bull.invalidation)
        assert t.reward_risk == pytest.approx(round(expected, 2))
    bear = build_scenario(frame, sr, atr_v, "neutral", False, True)
    assert bear.available and bear.invalidation > bear.entry_zone_high


def test_scenario_unavailable_with_insufficient_data():
    frame = make_frame(random_walk(60, seed=2))
    sr = _sr(frame)
    scenario = build_scenario(frame, sr, float(atr(frame, 14).iloc[-1]), "neutral", True, True)
    assert scenario.available is False and "100 bars" in (scenario.unavailable_reason or "")


def test_scenario_without_levels_is_not_invented():
    frame = make_frame(np.linspace(100, 300, 150), spread=0.0)
    sr = _sr(frame)
    scenario = build_scenario(frame, sr, float(atr(frame, 14).iloc[-1]), "bullish", True, True)
    assert scenario.available is False
