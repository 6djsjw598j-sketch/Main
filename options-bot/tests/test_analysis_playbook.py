from datetime import date

import numpy as np
import pytest

from optionsbot.analysis import (
    Bias,
    MarketRegime,
    Regime,
    UnderlyingView,
    analyze_underlying,
    attach_iv,
    bar_stats,
    iv_rank_from_history,
    market_regime,
)
from optionsbot.config import StrategyConfig
from optionsbot.indicators import historical_volatility, rsi
from optionsbot.models import StrategyKind
from optionsbot.strategies import choose_playbook
from tests.helpers import synthetic_bars, trending_bars


def test_rsi_bounds_and_hv_estimate():
    bars = synthetic_bars(100, 800, 0.0, 0.25, seed=3)
    r = rsi(bars["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    hv = historical_volatility(bars["close"], 252).dropna()
    assert hv.iloc[-1] == pytest.approx(0.25, abs=0.04)


def test_uptrend_is_bullish_and_downtrend_bearish():
    up = trending_bars(320, daily=0.002)
    today = up.index[-1]
    view = analyze_underlying("UP", bar_stats(up, today), float(up["close"].iloc[-1]) * 1.001, today)
    assert view.bias in (Bias.BULLISH, Bias.NEUTRAL)  # neutral only if RSI says overbought
    assert view.trend_score >= 3
    down = trending_bars(320, daily=-0.002)
    today = down.index[-1]
    view = analyze_underlying("DN", bar_stats(down, today), float(down["close"].iloc[-1]), today)
    assert view.trend_score <= -3


def test_todays_bar_is_ignored():
    bars = synthetic_bars(100, 300, seed=4)
    today = bars.index[-1]
    with_today = bar_stats(bars, today)
    tampered = bars.copy()
    tampered.loc[today, "close"] = 1_000_000.0
    assert bar_stats(tampered, today) == with_today


def test_iv_rank_history_and_proxy():
    rank, pct = iv_rank_from_history(0.30, [0.10, 0.20, 0.40])
    assert rank == pytest.approx(66.666, abs=0.01) and pct == pytest.approx(66.666, abs=0.01)
    bars = synthetic_bars(100, 400, seed=9)
    today = bars.index[-1]
    stats = bar_stats(bars, today)
    view = analyze_underlying("X", stats, 100.0, today)
    attach_iv(view, 0.5, [], 60, stats)
    assert view.iv_rank_source == "hv_proxy" and view.iv_rank == 100.0
    attach_iv(view, 0.2, list(np.linspace(0.1, 0.3, 80)), 60, stats)
    assert view.iv_rank_source == "iv_history" and view.iv_rank == pytest.approx(50.0)


def make_view(bias: Bias, ivr: float, strong: bool = False, adx: float = 15.0, score: int = 0) -> UnderlyingView:
    return UnderlyingView(
        symbol="SPY", as_of=date(2026, 1, 5), price=500.0, bars=300, prev_close=499.0, sma20=None, sma50=None, sma200=490.0,
        rsi14=50.0, adx14=adx, atr14=5.0, hv20=0.15, hv60=0.15, hv252=0.15, trend_score=score, bias=bias, strong_trend=strong,
        vol_forecast=0.15, atm_iv=0.18, iv_rank=ivr, iv_rank_source="iv_history",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("bias", "ivr", "strong", "adx", "expected"),
    [
        (Bias.BULLISH, 60, False, 20, [StrategyKind.BULL_PUT_CREDIT]),
        (Bias.BEARISH, 45, False, 20, [StrategyKind.BEAR_CALL_CREDIT]),
        (Bias.NEUTRAL, 70, False, 18, [StrategyKind.IRON_CONDOR]),
        (Bias.NEUTRAL, 70, False, 32, []),  # trending too hard for a condor
        (Bias.NEUTRAL, 40, False, 18, []),  # not rich enough for a condor
        (Bias.BULLISH, 10, True, 30, [StrategyKind.BULL_CALL_DEBIT]),
        (Bias.BEARISH, 10, True, 30, [StrategyKind.BEAR_PUT_DEBIT]),
        (Bias.BULLISH, 10, False, 20, []),  # cheap premium, weak trend: stand aside
    ],
)
def test_playbook_matrix(bias, ivr, strong, adx, expected):
    regime = MarketRegime(Regime.NEUTRAL, 1.0, [])
    book = choose_playbook(make_view(bias, ivr, strong, adx), regime, StrategyConfig())
    assert book.strategies == expected
    assert book.reasons


def test_risk_off_and_crisis_and_disabled_strategies():
    view = make_view(Bias.BULLISH, 60)
    assert choose_playbook(view, MarketRegime(Regime.RISK_OFF, 0.5, ["x"]), StrategyConfig()).strategies == []
    assert choose_playbook(view, MarketRegime(Regime.CRISIS, 0.0, ["x"]), StrategyConfig()).strategies == []
    cfg = StrategyConfig(enabled=[StrategyKind.IRON_CONDOR])
    assert choose_playbook(view, MarketRegime(Regime.NEUTRAL, 1.0, []), cfg).strategies == []


def test_market_regime_rules():
    calm = make_view(Bias.BULLISH, 30, score=3)
    assert market_regime(calm, 0.4, 0.04, 0.5).regime is Regime.RISK_ON
    panicky = make_view(Bias.BEARISH, 90, score=-3)
    panicky.atm_iv = 0.45
    assert market_regime(panicky, 0.4, 0.04, 0.5).regime is Regime.CRISIS
    gap = make_view(Bias.NEUTRAL, 50)
    gap.price, gap.prev_close = 470.0, 500.0
    assert market_regime(gap, 0.4, 0.04, 0.5).regime is Regime.CRISIS
    weak = make_view(Bias.BEARISH, 50, score=-3)
    weak.price = 480.0
    r = market_regime(weak, 0.4, 0.04, 0.5)
    assert r.regime is Regime.RISK_OFF and r.size_multiplier == 0.5
    assert market_regime(None, 0.4, 0.04, 0.5).size_multiplier == 0.5


def test_strategy_config_validation():
    with pytest.raises(ValueError):
        StrategyConfig(min_dte=50, target_dte=45, max_dte=60)
