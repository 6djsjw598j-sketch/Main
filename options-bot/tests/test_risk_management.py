from datetime import UTC, date, datetime, timedelta

import pytest

from optionsbot.broker.base import Account
from optionsbot.journal import TradeStatus
from optionsbot.management import ExitReason, check_exit
from optionsbot.models import Candidate, Leg, OptionContract, OptionQuote, OptionType, Side, StrategyKind
from optionsbot.occ import format_occ
from optionsbot.risk import RiskManager

TODAY = date(2026, 3, 2)
EXP = date(2026, 4, 17)


def contract(strike: float, option_type: OptionType, underlying: str = "SPY") -> OptionContract:
    return OptionContract(format_occ(underlying, EXP, option_type, strike), underlying, EXP, strike, option_type)


def put_spread(underlying: str = "SPY", mid: float = -1.5, natural: float = -1.3, delta: float = 20.0) -> Candidate:
    legs = [Leg(contract(500, OptionType.PUT, underlying), Side.SELL), Leg(contract(495, OptionType.PUT, underlying), Side.BUY)]
    return Candidate(
        underlying=underlying, strategy=StrategyKind.BULL_PUT_CREDIT, expiration=EXP, dte=46, legs=legs, spot=520.0,
        mid_value=mid, natural_value=natural, max_profit=-mid * 100, max_loss=(5 + mid) * 100, breakevens=[498.5],
        pop=0.7, pop_forecast=0.75, expected_value=20.0, net_delta=delta, net_theta=3.0, net_vega=-8.0, short_delta=0.25,
    )  # fmt: skip


def account(equity: float = 100_000, last: float | None = None, bp: float | None = None) -> Account:
    return Account(
        equity=equity, last_equity=last or equity, cash=equity, buying_power=bp or equity, options_buying_power=bp or equity, options_level=3
    )


def open_trade(journal, candidate: Candidate, qty: int = 1, opened: date = TODAY):
    tid = journal.create_trade(candidate, qty, opened, 1.0, {"summary": "test"})
    journal.apply_entry_fill(tid, qty, candidate.mid_value, datetime(2026, 3, 2, 15, tzinfo=UTC))
    journal.set_status(tid, TradeStatus.OPEN)
    return journal.get_trade(tid)


def test_size_uses_risk_budget_with_slippage(config, journal):
    risk = RiskManager(config, journal)
    c = put_spread()  # max loss 350 + 0.4 * 0.2 * 100 = 358 per contract
    d = risk.size(c, account(), [], 1.0, 1.0, 0.0)
    assert d.risk_per_contract == pytest.approx(358.0)
    assert d.contracts == 5  # floor(2000 / 358)
    assert risk.size(c, account(), [], 0.5, 1.0, 0.0).contracts == 2  # risk-off halves the budget


def test_size_respects_portfolio_bp_contract_and_delta_caps(config, journal):
    risk = RiskManager(config, journal)
    c = put_spread()
    existing = open_trade(journal, put_spread("QQQ"), qty=70)  # 70 * 350 = 24,500 of a 25,000 budget
    assert risk.size(c, account(), [existing], 1.0, 1.0, 0.0).contracts == 1
    assert risk.size(c, account(bp=21_000), [], 1.0, 1.0, 0.0).contracts == 2  # (21000 - 20000 reserve) / 358
    capped = config.model_copy(update={"risk": config.risk.model_copy(update={"max_contracts_per_trade": 3})})
    assert RiskManager(capped, journal).size(c, account(), [], 1.0, 1.0, 0.0).contracts == 3
    # 20 deltas * 520 = 10,400 SPY-dollars per contract against a 100,000 cap already at 95,000
    assert risk.size(c, account(), [], 1.0, 1.0, 95_000.0).contracts == 0
    # a trade that reduces exposure is always allowed
    assert risk.size(put_spread(delta=-20.0), account(), [], 1.0, 1.0, 150_000.0).contracts == 5


def test_drawdown_halts_and_resume_clears(config, journal):
    risk = RiskManager(config, journal)
    risk.update(account(100_000), TODAY)
    assert risk.entry_block_reason(account(100_000), TODAY, []) is None
    messages = risk.update(account(84_000, last=85_000), TODAY)
    assert messages and "drawdown" in messages[0]
    assert "halted" in risk.entry_block_reason(account(84_000), TODAY, [])
    risk.resume()
    assert risk.halted() is None


def test_daily_loss_limit_pauses_until_tomorrow(config, journal):
    risk = RiskManager(config, journal)
    risk.update(account(96_000, last=100_000), TODAY)
    assert risk.entry_block_reason(account(96_000, last=100_000), TODAY, []) == "daily loss limit reached"
    assert risk.entry_block_reason(account(96_000), TODAY + timedelta(days=1), []) is None


def test_kill_switch_and_trade_count_limits(config, journal):
    risk = RiskManager(config, journal)
    config.kill_switch_file.write_text("stop")
    assert "kill-switch" in risk.entry_block_reason(account(), TODAY, [])
    config.kill_switch_file.unlink()
    for _ in range(3):
        open_trade(journal, put_spread("IWM"))
    assert "already opened 3" in risk.entry_block_reason(account(), TODAY, journal.active_trades())


def test_symbol_blocks_group_and_revenge_trades(config, journal):
    risk = RiskManager(config, journal)
    spy = open_trade(journal, put_spread("SPY"))
    assert "already 1 open" in risk.symbol_block_reason("SPY", [spy], TODAY)
    qqq = open_trade(journal, put_spread("QQQ"))
    assert "correlated group" in risk.symbol_block_reason("IWM", [spy, qqq], TODAY)
    loser = open_trade(journal, put_spread("GLD"))
    journal.apply_exit_fill(loser.id, 1, -3.0, datetime(2026, 3, 2, 18, tzinfo=UTC), "stop_loss")
    assert "revenge" in risk.symbol_block_reason("GLD", [], TODAY + timedelta(days=1))
    assert risk.symbol_block_reason("GLD", [], TODAY + timedelta(days=5)) is None


def test_loss_streak_cooldown(config, journal):
    risk = RiskManager(config, journal)
    for i in range(4):
        t = open_trade(journal, put_spread(f"S{i}"))
        journal.apply_exit_fill(t.id, 1, -2.5, datetime(2026, 3, 2, 18, tzinfo=UTC), "stop_loss")
    assert "losing trades in a row" in risk.entry_block_reason(account(), TODAY, [])
    assert risk.loss_streak_block(TODAY + timedelta(days=3)) is None


def quotes(short_bid, short_ask, long_bid, long_ask, short_strike=500, long_strike=495, option_type=OptionType.PUT):
    s, lg = contract(short_strike, option_type), contract(long_strike, option_type)
    return {s.symbol: OptionQuote(s, short_bid, short_ask), lg.symbol: OptionQuote(lg, long_bid, long_ask)}


@pytest.mark.parametrize(
    ("q", "spot", "today", "reason"),
    [
        (quotes(1.00, 1.10, 0.30, 0.34), 525, TODAY, ExitReason.PROFIT_TARGET),  # worth 0.73 vs 1.50 credit
        (quotes(4.80, 5.00, 0.15, 0.25), 501, TODAY, ExitReason.STOP_LOSS),  # worth 4.70: loss 3.20 > 2x credit
        (quotes(1.70, 1.80, 0.60, 0.70), 515, EXP - timedelta(days=20), ExitReason.TIME_EXIT),
        (quotes(5.00, 5.10, 0.30, 0.40), 495, TODAY, ExitReason.ASSIGNMENT_RISK),  # ITM short, ~0.05 extrinsic
        (quotes(1.70, 1.80, 0.60, 0.70), 515, EXP, ExitReason.EXPIRATION),
        (quotes(1.70, 1.80, 0.60, 0.70), 515, TODAY, None),
    ],
)
def test_credit_spread_exits(config, journal, q, spot, today, reason):
    trade = open_trade(journal, put_spread())
    signal = check_exit(trade, q, spot, today, config.management)
    assert (signal.reason if signal else None) == reason


def test_breach_rule_is_opt_in_and_earnings_exit(config, journal):
    trade = open_trade(journal, put_spread())
    q = quotes(2.20, 2.30, 0.80, 0.90)
    assert check_exit(trade, q, 499, TODAY, config.management) is None
    strict = config.management.model_copy(update={"close_on_short_strike_breach": True})
    assert check_exit(trade, q, 499, TODAY, strict).reason is ExitReason.SHORT_STRIKE_BREACHED
    assert check_exit(trade, q, 515, TODAY, config.management, earnings_date=TODAY + timedelta(days=1)).reason is ExitReason.EARNINGS


def test_debit_spread_exits(config, journal):
    legs = [Leg(contract(500, OptionType.CALL), Side.BUY), Leg(contract(505, OptionType.CALL), Side.SELL)]
    c = put_spread()
    c.legs, c.strategy, c.mid_value, c.max_profit, c.max_loss = legs, StrategyKind.BULL_CALL_DEBIT, 2.5, 250.0, 250.0
    trade = open_trade(journal, c)
    # helper order is (500 strike bid/ask, 505 strike bid/ask): long 500C, short 505C
    up = quotes(5.40, 5.50, 0.90, 1.00, short_strike=500, long_strike=505, option_type=OptionType.CALL)  # worth 4.50
    assert check_exit(trade, up, 504, TODAY, config.management).reason is ExitReason.PROFIT_TARGET
    down = quotes(1.30, 1.40, 0.15, 0.25, short_strike=500, long_strike=505, option_type=OptionType.CALL)  # worth 1.15
    assert check_exit(trade, down, 495, TODAY, config.management).reason is ExitReason.STOP_LOSS
