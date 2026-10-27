from datetime import date, timedelta

import pytest

from optionsbot.analysis import analyze_underlying, atm_implied_vol, attach_iv, bar_stats
from optionsbot.config import BotConfig, LiquidityConfig
from optionsbot.models import OptionQuote, OptionType, Side, StrategyKind
from optionsbot.strategies import BuildContext, build_candidates, pick_expiration
from optionsbot.strategies.builder import is_liquid
from optionsbot.timeutil import dte, is_monthly_expiration


def context(sim, config: BotConfig, budget: float = 2000.0, earnings: date | None = None) -> BuildContext:
    today = sim.today
    spot = sim.get_spot("SPY")
    stats = bar_stats(sim.get_daily_bars("SPY", today - timedelta(days=600), today), today)
    view = analyze_underlying("SPY", stats, spot, today)
    chain = sim.get_option_chain("SPY", today + timedelta(days=20), today + timedelta(days=60), spot)
    attach_iv(view, atm_implied_vol(chain, sim.now, today), [], 60, stats)
    return BuildContext(chain, view, sim.now, today, config, budget, earnings)


def test_expiration_prefers_monthly_near_target_and_avoids_earnings():
    today = date(2026, 1, 5)
    fridays = [today + timedelta(days=4 + 7 * i) for i in range(12)]
    cfg = BotConfig()
    chosen, _ = pick_expiration(fridays, today, cfg, None)
    assert is_monthly_expiration(chosen) and 30 <= dte(chosen, today) <= 60
    chosen, _ = pick_expiration(fridays, today, cfg, today + timedelta(days=40))
    assert chosen is not None and chosen < today + timedelta(days=40)
    none, why = pick_expiration(fridays, today, cfg, today + timedelta(days=25))
    assert none is None and "earnings" in why


@pytest.mark.parametrize("kind", list(StrategyKind))
def test_candidates_respect_the_rules(sim, config, kind):
    ctx = context(sim, config)
    candidates, notes = build_candidates(ctx, [kind])
    s = config.strategy
    for c in candidates:
        assert c.strategy is kind
        assert len({leg.contract.expiration for leg in c.legs}) == 1
        assert c.max_loss <= ctx.max_loss_per_contract
        assert c.width <= s.max_width_pct_of_spot * c.spot + 1e-9
        assert c.max_profit > 0 and c.max_loss > 0
        assert 0 <= c.pop <= 1
        if kind.is_credit:
            assert c.mid_value < 0 and c.expected_value > 0  # rich premium only
        else:
            assert c.mid_value > 0
    if not candidates:
        assert notes


def test_put_credit_spread_structure(sim, config):
    ctx = context(sim, config)
    candidates, _ = build_candidates(ctx, [StrategyKind.BULL_PUT_CREDIT])
    assert candidates, "the simulator prices IV 40% over realised vol, so put spreads should qualify"
    best = candidates[0]
    short = next(leg for leg in best.legs if leg.side is Side.SELL)
    long = next(leg for leg in best.legs if leg.side is Side.BUY)
    assert short.contract.type is long.contract.type is OptionType.PUT
    assert long.contract.strike < short.contract.strike < best.spot
    s = config.strategy
    assert s.credit_short_delta_min <= best.short_delta <= s.credit_short_delta_max
    assert best.credit / best.width >= s.min_credit_to_width
    assert candidates == sorted(candidates, key=lambda c: c.score, reverse=True)


def test_iron_condor_structure(sim, config):
    candidates, _ = build_candidates(context(sim, config), [StrategyKind.IRON_CONDOR])
    assert candidates
    c = candidates[0]
    puts = sorted(leg.contract.strike for leg in c.legs if leg.contract.type is OptionType.PUT)
    calls = sorted(leg.contract.strike for leg in c.legs if leg.contract.type is OptionType.CALL)
    assert puts[1] < c.spot < calls[0]
    assert puts[1] - puts[0] == pytest.approx(calls[1] - calls[0])
    assert len(c.breakevens) == 2


def test_risk_budget_limits_width(sim, config):
    candidates, _ = build_candidates(context(sim, config, budget=150.0), [StrategyKind.BULL_PUT_CREDIT])
    assert all(c.max_loss <= 150 for c in candidates)


def test_liquidity_filter():
    from optionsbot.occ import parse_occ

    c = parse_occ("SPY261218P00500000")
    cfg = LiquidityConfig()
    assert is_liquid(OptionQuote(c, bid=2.00, ask=2.04, open_interest=500), cfg)
    assert not is_liquid(OptionQuote(c, bid=2.00, ask=2.60, open_interest=500), cfg)  # wide market
    assert not is_liquid(OptionQuote(c, bid=0.0, ask=0.05, open_interest=500), cfg)  # no bid
    assert not is_liquid(OptionQuote(c, bid=2.00, ask=2.04, open_interest=10), cfg)  # thin open interest
