import math
from datetime import date

import pytest

from optionsbot.models import Leg, OptionContract, OptionType, Side
from optionsbot.occ import format_occ, is_occ, parse_occ
from optionsbot.pricing import (
    breakevens,
    bs_greeks,
    bs_price,
    implied_vol,
    lognormal_cdf,
    payoff_extremes,
    probability_of_profit,
)


def contract(strike: float, option_type: OptionType, expiration: date = date(2026, 12, 18)) -> OptionContract:
    return OptionContract(format_occ("SPY", expiration, option_type, strike), "SPY", expiration, strike, option_type)


def test_occ_round_trip():
    symbol = format_occ("SPY", date(2026, 12, 18), OptionType.PUT, 552.5)
    assert symbol == "SPY261218P00552500"
    c = parse_occ(symbol)
    assert (c.underlying, c.expiration, c.strike, c.type) == ("SPY", date(2026, 12, 18), 552.5, OptionType.PUT)
    assert is_occ("AAPL240119C00100000")
    assert not is_occ("AAPL")
    with pytest.raises(ValueError):
        parse_occ("SPY")


def test_black_scholes_put_call_parity_and_greeks():
    s, k, t, r, vol = 100.0, 105.0, 0.25, 0.04, 0.3
    call = bs_price(s, k, t, r, vol, OptionType.CALL)
    put = bs_price(s, k, t, r, vol, OptionType.PUT)
    assert call - put == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)
    g_call = bs_greeks(s, k, t, r, vol, OptionType.CALL)
    g_put = bs_greeks(s, k, t, r, vol, OptionType.PUT)
    assert 0 < g_call.delta < 1 and -1 < g_put.delta < 0
    assert g_call.delta - g_put.delta == pytest.approx(1.0, abs=1e-9)
    assert g_call.gamma == pytest.approx(g_put.gamma) and g_call.vega > 0 and g_call.theta < 0


def test_implied_vol_recovers_input_and_rejects_bad_prices():
    price = bs_price(100, 95, 0.2, 0.04, 0.27, OptionType.PUT)
    assert implied_vol(price, 100, 95, 0.2, 0.04, OptionType.PUT) == pytest.approx(0.27, abs=1e-4)
    assert implied_vol(0.0, 100, 95, 0.2, 0.04, OptionType.PUT) is None
    assert implied_vol(1.0, 100, 50, 0.2, 0.04, OptionType.CALL) is None  # below intrinsic


def test_put_credit_spread_payoff_statistics():
    legs = [Leg(contract(100, OptionType.PUT), Side.SELL), Leg(contract(95, OptionType.PUT), Side.BUY)]
    entry = -1.5  # received 1.50
    lo, hi, bounded = payoff_extremes(legs)
    assert (lo, hi, bounded) == (-5.0, 0.0, True)
    assert breakevens(legs, entry) == [98.5]
    pop = probability_of_profit(legs, entry, 105, 0.2, 45 / 365)
    assert pop == pytest.approx(1 - lognormal_cdf(98.5, 105, 0.2, 45 / 365), abs=1e-9)


def test_iron_condor_has_two_breakevens_and_naked_call_is_unbounded():
    legs = [
        Leg(contract(90, OptionType.PUT), Side.BUY),
        Leg(contract(95, OptionType.PUT), Side.SELL),
        Leg(contract(105, OptionType.CALL), Side.SELL),
        Leg(contract(110, OptionType.CALL), Side.BUY),
    ]
    assert breakevens(legs, -1.0) == [94.0, 106.0]
    p = probability_of_profit(legs, -1.0, 100, 0.2, 30 / 365)
    expected = lognormal_cdf(106, 100, 0.2, 30 / 365) - lognormal_cdf(94, 100, 0.2, 30 / 365)
    assert p == pytest.approx(expected, abs=1e-9)
    assert payoff_extremes([Leg(contract(105, OptionType.CALL), Side.SELL)])[2] is False
