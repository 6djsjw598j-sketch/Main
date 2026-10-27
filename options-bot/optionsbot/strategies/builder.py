"""Builds and prices concrete spreads from an option chain.

Strike selection follows common practice: short strikes by delta, ~45 DTE (monthly
expirations preferred), no expirations that span an earnings report, liquid legs only,
and a minimum credit relative to the spread width.

Each candidate is scored by its *edge*: the model value of the position minus the price
we expect to pay, where the model prices every leg at the bot's realised-volatility
forecast scaled by the chain's own skew. Selling premium only makes sense when this is
positive, i.e. when options are priced above the volatility the bot expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from optionsbot.analysis import UnderlyingView
from optionsbot.config import BotConfig, LiquidityConfig
from optionsbot.models import Candidate, Leg, OptionChain, OptionQuote, OptionType, Side, StrategyKind, combo_value
from optionsbot.pricing import breakevens, bs_greeks, bs_price, implied_vol, payoff_extremes, probability_of_profit
from optionsbot.timeutil import dte, is_monthly_expiration, years_to_expiry

EXPECTED_FILL_CONCESSION = 0.25  # we usually fill a little worse than mid
EXPECTED_EXIT_CONCESSION = 0.50  # exits are worked harder (stops go to the natural price)
MAX_WIDTHS_PER_SHORT = 6
CONDOR_SIDES = 5
DEBIT_MIN_EDGE = -0.10  # debit spreads are directional bets; don't overpay by more than 10% of risk


@dataclass
class BuildContext:
    chain: OptionChain
    view: UnderlyingView
    now: datetime
    today: date
    config: BotConfig
    max_loss_per_contract: float
    earnings_date: date | None = None


def pick_expiration(expirations: list[date], today: date, config: BotConfig, earnings_date: date | None) -> tuple[date | None, str]:
    s = config.strategy
    window = [e for e in expirations if s.min_dte <= dte(e, today) <= s.max_dte]
    if not window:
        return None, f"no expiration between {s.min_dte} and {s.max_dte} DTE"
    if earnings_date is not None:
        window = [e for e in window if e < earnings_date]
        if not window:
            return None, f"earnings on {earnings_date.isoformat()} fall inside every expiration in the DTE window"
    if s.prefer_monthly:
        monthly = [e for e in window if is_monthly_expiration(e)]
        window = monthly or window
    best = min(window, key=lambda e: abs(dte(e, today) - s.target_dte))
    return best, ""


def is_liquid(q: OptionQuote, cfg: LiquidityConfig) -> bool:
    if not q.is_valid or q.bid <= 0:
        return False
    if q.spread > max(cfg.max_leg_spread_abs, cfg.max_leg_spread_pct * q.mid):
        return False
    return not (q.open_interest is not None and q.open_interest < cfg.min_open_interest)


class _Pricer:
    """Fills in IV/delta the feed did not provide and holds the model inputs."""

    def __init__(self, ctx: BuildContext, expiration: date) -> None:
        self.ctx = ctx
        self.spot = ctx.chain.spot
        self.t = years_to_expiry(expiration, ctx.now)
        self.r = ctx.config.data.risk_free_rate
        self.atm_iv = ctx.view.atm_iv
        self.forecast = ctx.view.vol_forecast or ctx.view.atm_iv or 0.2

    def iv(self, q: OptionQuote) -> float | None:
        if q.iv and q.iv > 0:
            return q.iv
        q.iv = implied_vol(q.mid, self.spot, q.contract.strike, self.t, self.r, q.contract.type)
        return q.iv

    def delta(self, q: OptionQuote) -> float | None:
        if q.delta is None:
            iv = self.iv(q)
            if iv is None:
                return None
            g = bs_greeks(self.spot, q.contract.strike, self.t, self.r, iv, q.contract.type)
            q.delta, q.gamma, q.theta, q.vega = g.delta, g.gamma, g.theta, g.vega
        return q.delta

    def skew_factor(self, q: OptionQuote) -> float:
        iv = self.iv(q)
        if not iv or not self.atm_iv:
            return 1.0
        return min(2.5, max(0.5, iv / self.atm_iv))

    def model_value(self, legs: list[Leg], quotes: dict[str, OptionQuote]) -> float:
        total = 0.0
        for leg in legs:
            q = quotes[leg.symbol]
            sigma = self.forecast * self.skew_factor(q)
            total += leg.sign * bs_price(self.spot, leg.contract.strike, self.t, self.r, sigma, leg.contract.type)
        return total


def _evaluate(strategy: StrategyKind, legs: list[Leg], quotes: dict[str, OptionQuote], pricer: _Pricer, ctx: BuildContext) -> Candidate | None:
    mid = combo_value(legs, quotes, "mid")
    natural = combo_value(legs, quotes, "open")
    if mid is None or natural is None:
        return None
    lo, hi, bounded = payoff_extremes(legs)
    if not bounded:
        return None  # never trade undefined risk
    max_profit = (hi - mid) * 100
    max_loss = (mid - lo) * 100
    if max_profit <= 0 or max_loss <= 0:
        return None
    if natural - mid > ctx.config.liquidity.max_combo_spread_pct * abs(mid):
        return None

    shorts = [leg for leg in legs if leg.side is Side.SELL]
    short_quotes = [quotes[leg.symbol] for leg in shorts] or [quotes[leg.symbol] for leg in legs]
    ivs = [pricer.iv(q) for q in short_quotes]
    implied = sum(v for v in ivs if v) / max(1, sum(1 for v in ivs if v)) or pricer.forecast
    skew = sum(pricer.skew_factor(q) for q in short_quotes) / len(short_quotes)
    pop = probability_of_profit(legs, mid, pricer.spot, implied, pricer.t, pricer.r)
    pop_fc = probability_of_profit(legs, mid, pricer.spot, pricer.forecast * skew, pricer.t, pricer.r)

    # edge net of the round trip: the entry concession plus what closing will likely cost
    expected_fill = mid + EXPECTED_FILL_CONCESSION * (natural - mid)
    exit_cost = EXPECTED_EXIT_CONCESSION * (natural - mid)
    edge = (pricer.model_value(legs, quotes) - expected_fill - exit_cost) * 100

    net_delta = net_theta = net_vega = 0.0
    for leg in legs:
        q = quotes[leg.symbol]
        net_delta += leg.sign * (pricer.delta(q) or 0.0) * 100
        net_theta += leg.sign * (q.theta or 0.0) * 100
        net_vega += leg.sign * (q.vega or 0.0) * 100
    short_delta = abs(pricer.delta(quotes[shorts[0].symbol]) or 0.0) if shorts else 0.0

    expiration = legs[0].contract.expiration
    return Candidate(
        underlying=ctx.chain.underlying,
        strategy=strategy,
        expiration=expiration,
        dte=dte(expiration, ctx.today),
        legs=legs,
        spot=pricer.spot,
        mid_value=mid,
        natural_value=natural,
        max_profit=max_profit,
        max_loss=max_loss,
        breakevens=breakevens(legs, mid),
        pop=pop,
        pop_forecast=pop_fc,
        expected_value=edge,
        net_delta=net_delta,
        net_theta=net_theta,
        net_vega=net_vega,
        short_delta=short_delta,
    )


def _expected_credit(c: Candidate) -> float:
    """Credit (negative for a debit) at the price we expect to fill, a little worse than mid."""
    return -(c.mid_value + EXPECTED_FILL_CONCESSION * (c.natural_value - c.mid_value))


def _otm(q: OptionQuote, spot: float) -> bool:
    return q.contract.strike < spot if q.contract.type is OptionType.PUT else q.contract.strike > spot


def _verticals(
    side: list[OptionQuote], pricer: _Pricer, ctx: BuildContext, delta_lo: float, delta_hi: float, max_width: float
) -> list[tuple[OptionQuote, OptionQuote]]:
    """(short, long) pairs for OTM credit verticals with the short |delta| in range."""
    spot = pricer.spot
    s = ctx.config.strategy
    pairs = []
    for short in side:
        d = pricer.delta(short)
        if d is None or not _otm(short, spot) or not delta_lo <= abs(d) <= delta_hi:
            continue
        if short.contract.type is OptionType.PUT:
            wings = [q for q in reversed(side) if q.contract.strike < short.contract.strike]
        else:
            wings = [q for q in side if q.contract.strike > short.contract.strike]
        wings = [q for q in wings if s.min_width - 1e-9 <= abs(q.contract.strike - short.contract.strike) <= max_width + 1e-9]
        pairs.extend((short, wing) for wing in wings[:MAX_WIDTHS_PER_SHORT])
    return pairs


def _credit_spreads(ctx: BuildContext, expiration: date, quotes: dict[str, OptionQuote], liquid: dict, option_type: OptionType) -> list[Candidate]:
    s = ctx.config.strategy
    pricer = _Pricer(ctx, expiration)
    strategy = StrategyKind.BULL_PUT_CREDIT if option_type is OptionType.PUT else StrategyKind.BEAR_CALL_CREDIT
    max_width = s.max_width_pct_of_spot * pricer.spot
    out = []
    for short, wing in _verticals(liquid[option_type], pricer, ctx, s.credit_short_delta_min, s.credit_short_delta_max, max_width):
        legs = [Leg(short.contract, Side.SELL), Leg(wing.contract, Side.BUY)]
        c = _evaluate(strategy, legs, quotes, pricer, ctx)
        if c is None or c.max_loss > ctx.max_loss_per_contract:
            continue
        if _expected_credit(c) / c.width < s.min_credit_to_width or c.pop < s.min_pop:
            continue
        if s.require_positive_edge and c.expected_value <= s.min_edge_pct_of_risk * c.max_loss:
            continue
        c.score = c.expected_value / c.max_loss - 0.3 * abs(c.short_delta - s.credit_short_delta)
        out.append(c)
    return out


def _iron_condors(ctx: BuildContext, expiration: date, quotes: dict[str, OptionQuote], liquid: dict) -> list[Candidate]:
    s = ctx.config.strategy
    pricer = _Pricer(ctx, expiration)
    max_width = s.max_width_pct_of_spot * pricer.spot

    def best_sides(option_type: OptionType) -> list[tuple[OptionQuote, OptionQuote]]:
        pairs = _verticals(liquid[option_type], pricer, ctx, s.condor_short_delta_min, s.condor_short_delta_max, max_width)
        pairs.sort(key=lambda p: abs(abs(pricer.delta(p[0]) or 0) - s.condor_short_delta))
        return pairs[: CONDOR_SIDES * MAX_WIDTHS_PER_SHORT]

    out = []
    for put_short, put_wing in best_sides(OptionType.PUT):
        put_width = put_short.contract.strike - put_wing.contract.strike
        for call_short, call_wing in best_sides(OptionType.CALL):
            call_width = call_wing.contract.strike - call_short.contract.strike
            if abs(call_width - put_width) > 1e-9:
                continue
            legs = [
                Leg(put_wing.contract, Side.BUY),
                Leg(put_short.contract, Side.SELL),
                Leg(call_short.contract, Side.SELL),
                Leg(call_wing.contract, Side.BUY),
            ]
            c = _evaluate(StrategyKind.IRON_CONDOR, legs, quotes, pricer, ctx)
            if c is None or c.max_loss > ctx.max_loss_per_contract:
                continue
            if _expected_credit(c) / c.width < s.min_condor_credit_to_width or c.pop < s.min_pop:
                continue
            if s.require_positive_edge and c.expected_value <= s.min_edge_pct_of_risk * c.max_loss:
                continue
            deltas = [abs(pricer.delta(put_short) or 0), abs(pricer.delta(call_short) or 0)]
            c.short_delta = max(deltas)
            c.score = c.expected_value / c.max_loss - 0.3 * sum(abs(d - s.condor_short_delta) for d in deltas) / 2
            out.append(c)
    return out


def _debit_spreads(ctx: BuildContext, expiration: date, quotes: dict[str, OptionQuote], liquid: dict, option_type: OptionType) -> list[Candidate]:
    s = ctx.config.strategy
    pricer = _Pricer(ctx, expiration)
    strategy = StrategyKind.BULL_CALL_DEBIT if option_type is OptionType.CALL else StrategyKind.BEAR_PUT_DEBIT
    max_width = s.max_width_pct_of_spot * pricer.spot
    side = liquid[option_type]
    target_short = (s.debit_short_delta_min + s.debit_short_delta_max) / 2
    out = []
    for long in side:
        d_long = pricer.delta(long)
        if d_long is None or not s.debit_long_delta_min <= abs(d_long) <= s.debit_long_delta_max:
            continue
        for short in side:
            d_short = pricer.delta(short)
            if d_short is None or not s.debit_short_delta_min <= abs(d_short) <= s.debit_short_delta_max:
                continue
            width = short.contract.strike - long.contract.strike if option_type is OptionType.CALL else long.contract.strike - short.contract.strike
            if not s.min_width - 1e-9 <= width <= max_width + 1e-9:
                continue
            legs = [Leg(long.contract, Side.BUY), Leg(short.contract, Side.SELL)]
            c = _evaluate(strategy, legs, quotes, pricer, ctx)
            if c is None or c.max_loss > ctx.max_loss_per_contract:
                continue
            if -_expected_credit(c) / c.width > s.max_debit_to_width:
                continue
            if c.expected_value < DEBIT_MIN_EDGE * c.max_loss:
                continue
            c.short_delta = abs(d_short)
            c.score = c.expected_value / c.max_loss - 0.3 * abs(abs(d_short) - target_short)
            out.append(c)
    return out


def build_candidates(ctx: BuildContext, strategies: list[StrategyKind]) -> tuple[list[Candidate], list[str]]:
    """All acceptable candidates for the given strategies, best first, plus notes on why
    nothing qualified when the list is empty."""
    notes: list[str] = []
    expiration, why = pick_expiration(ctx.chain.expirations(), ctx.today, ctx.config, ctx.earnings_date)
    if expiration is None:
        return [], [why]
    quotes = ctx.chain.by_symbol()
    liquid = {
        option_type: [q for q in ctx.chain.side(expiration, option_type) if is_liquid(q, ctx.config.liquidity)]
        for option_type in (OptionType.PUT, OptionType.CALL)
    }
    if not liquid[OptionType.PUT] and not liquid[OptionType.CALL]:
        return [], [f"no liquid contracts for {expiration.isoformat()}"]
    out: list[Candidate] = []
    for strategy in strategies:
        if strategy is StrategyKind.BULL_PUT_CREDIT:
            found = _credit_spreads(ctx, expiration, quotes, liquid, OptionType.PUT)
        elif strategy is StrategyKind.BEAR_CALL_CREDIT:
            found = _credit_spreads(ctx, expiration, quotes, liquid, OptionType.CALL)
        elif strategy is StrategyKind.IRON_CONDOR:
            found = _iron_condors(ctx, expiration, quotes, liquid)
        elif strategy is StrategyKind.BULL_CALL_DEBIT:
            found = _debit_spreads(ctx, expiration, quotes, liquid, OptionType.CALL)
        else:
            found = _debit_spreads(ctx, expiration, quotes, liquid, OptionType.PUT)
        if not found:
            notes.append(f"{strategy.value}: no spread on {expiration.isoformat()} met the credit/POP/edge/liquidity/risk rules")
        out.extend(found)
    out.sort(key=lambda c: c.score, reverse=True)
    return out, notes
