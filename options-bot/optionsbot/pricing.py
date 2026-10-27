"""Black-Scholes pricing, Greeks, implied volatility and payoff statistics.

Used to fill in missing Greeks/IV from the broker, to price the simulated chain in
backtests, and to measure a candidate's probability of profit and expected value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from optionsbot.models import Leg, OptionType

SQRT_2PI = math.sqrt(2 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point (0.01)


def _d1_d2(spot: float, strike: float, t: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vol_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / vol_t
    return d1, d1 - vol_t


def bs_price(spot: float, strike: float, t: float, r: float, sigma: float, option_type: OptionType, q: float = 0.0) -> float:
    if t <= 0 or sigma <= 0:
        intrinsic = spot - strike if option_type is OptionType.CALL else strike - spot
        return max(0.0, intrinsic)
    d1, d2 = _d1_d2(spot, strike, t, r, sigma, q)
    if option_type is OptionType.CALL:
        return spot * math.exp(-q * t) * norm_cdf(d1) - strike * math.exp(-r * t) * norm_cdf(d2)
    return strike * math.exp(-r * t) * norm_cdf(-d2) - spot * math.exp(-q * t) * norm_cdf(-d1)


def bs_greeks(spot: float, strike: float, t: float, r: float, sigma: float, option_type: OptionType, q: float = 0.0) -> Greeks:
    price = bs_price(spot, strike, t, r, sigma, option_type, q)
    if t <= 0 or sigma <= 0:
        itm = (spot > strike) if option_type is OptionType.CALL else (spot < strike)
        delta = (1.0 if option_type is OptionType.CALL else -1.0) if itm else 0.0
        return Greeks(price, delta, 0.0, 0.0, 0.0)
    d1, d2 = _d1_d2(spot, strike, t, r, sigma, q)
    sqrt_t = math.sqrt(t)
    disc_q = math.exp(-q * t)
    disc_r = math.exp(-r * t)
    gamma = disc_q * norm_pdf(d1) / (spot * sigma * sqrt_t)
    vega = spot * disc_q * norm_pdf(d1) * sqrt_t / 100
    common = -spot * disc_q * norm_pdf(d1) * sigma / (2 * sqrt_t)
    if option_type is OptionType.CALL:
        delta = disc_q * norm_cdf(d1)
        theta = common - r * strike * disc_r * norm_cdf(d2) + q * spot * disc_q * norm_cdf(d1)
    else:
        delta = disc_q * (norm_cdf(d1) - 1)
        theta = common + r * strike * disc_r * norm_cdf(-d2) - q * spot * disc_q * norm_cdf(-d1)
    return Greeks(price, delta, gamma, theta / 365, vega)


def implied_vol(price: float, spot: float, strike: float, t: float, r: float, option_type: OptionType, q: float = 0.0) -> float | None:
    """Implied volatility by bisection; None when the price is outside no-arbitrage bounds."""
    if price <= 0 or t <= 0 or spot <= 0 or strike <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if option_type is OptionType.CALL else (strike - spot))
    if price < intrinsic * math.exp(-r * t) - 1e-9:
        return None
    lo, hi = 1e-4, 5.0
    if bs_price(spot, strike, t, r, hi, option_type, q) < price:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(spot, strike, t, r, mid, option_type, q) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)


def payoff_at_expiry(legs: Sequence[Leg], prices: np.ndarray) -> np.ndarray:
    """Per-share intrinsic value of the combination at each terminal underlying price."""
    total = np.zeros_like(prices, dtype=float)
    for leg in legs:
        k = leg.contract.strike
        intrinsic = np.maximum(prices - k, 0.0) if leg.contract.type is OptionType.CALL else np.maximum(k - prices, 0.0)
        total += leg.sign * intrinsic
    return total


def payoff_extremes(legs: Sequence[Leg]) -> tuple[float, float, bool]:
    """(min payoff, max payoff, bounded) per share at expiry.

    Payoffs are piecewise linear with kinks at the strikes, so extremes occur at a strike,
    at zero, or as the price goes to infinity. ``bounded`` is False if the loss side is
    unlimited (net short calls), which the bot never trades.
    """
    strikes = sorted({leg.contract.strike for leg in legs})
    points = np.array([0.0, *strikes, strikes[-1] * 10 if strikes else 1.0])
    values = payoff_at_expiry(legs, points)
    call_slope = sum(leg.sign for leg in legs if leg.contract.type is OptionType.CALL)
    bounded = call_slope >= 0
    return float(values.min()), float(values.max()), bounded


def _pnl_points(legs: Sequence[Leg], entry_value: float) -> tuple[list[float], list[float]]:
    strikes = sorted({leg.contract.strike for leg in legs})
    xs = [0.0, *strikes, (strikes[-1] if strikes else 1.0) * 10]
    ys = list(payoff_at_expiry(legs, np.array(xs)) - entry_value)
    return xs, ys


def breakevens(legs: Sequence[Leg], entry_value: float) -> list[float]:
    """Underlying prices at expiry where P&L crosses zero (exact: P&L is piecewise linear)."""
    xs, ys = _pnl_points(legs, entry_value)
    out: list[float] = []
    for i in range(len(xs) - 1):
        x0, y0, x1, y1 = xs[i], ys[i], xs[i + 1], ys[i + 1]
        if y0 == 0 and x0 > 0:
            out.append(x0)
        elif y0 * y1 < 0:
            out.append(x0 + (x1 - x0) * y0 / (y0 - y1))
    return sorted({round(x, 4) for x in out})


def lognormal_cdf(x: float, spot: float, sigma: float, t: float, drift: float = 0.0) -> float:
    """P(S_T <= x) for a lognormal terminal price."""
    if x <= 0:
        return 0.0
    vol_t = max(sigma, 1e-6) * math.sqrt(max(t, 1e-6))
    return norm_cdf((math.log(x / spot) - (drift - 0.5 * sigma * sigma) * t) / vol_t)


def probability_of_profit(legs: Sequence[Leg], entry_value: float, spot: float, sigma: float, t: float, drift: float = 0.0) -> float:
    """Probability that the position is profitable at expiry under a lognormal model."""
    bes = breakevens(legs, entry_value)
    edges = [0.0, *bes, math.inf]
    total = 0.0
    for lo, hi in pairwise(edges):
        probe = (lo + hi) / 2 if math.isfinite(hi) else max(lo * 1.5, lo + 1.0)
        if float(payoff_at_expiry(legs, np.array([probe]))[0]) - entry_value > 0:
            upper = 1.0 if not math.isfinite(hi) else lognormal_cdf(hi, spot, sigma, t, drift)
            total += upper - lognormal_cdf(lo, spot, sigma, t, drift)
    return max(0.0, min(1.0, total))
