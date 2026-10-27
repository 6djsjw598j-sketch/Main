"""The full engine against the simulated broker."""

from datetime import datetime, time, timedelta

import pytest

from optionsbot.backtest import run_backtest
from optionsbot.broker.base import OrderRequest, OrderStatus
from optionsbot.broker.simulated import SimPosition, SimSettings, SimulatedBroker
from optionsbot.config import BotConfig
from optionsbot.earnings import EarningsCalendar, NoEarningsSymbols
from optionsbot.engine import TradingEngine
from optionsbot.execution import Executor, opening_legs
from optionsbot.journal import Journal, TradeStatus
from optionsbot.management import ExitReason
from optionsbot.models import Candidate, Leg, OptionType, Side, StrategyKind, combo_value
from optionsbot.notify import Notifier
from optionsbot.timeutil import ET
from tests.helpers import synthetic_bars


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t


def make_engine(sim: SimulatedBroker, config: BotConfig, journal: Journal) -> TradingEngine:
    clock = Clock()
    executor = Executor(sim, journal, config.execution, sleep=clock.sleep, monotonic=clock.monotonic)
    earnings = EarningsCalendar([NoEarningsSymbols(config.no_earnings_symbols)])
    return TradingEngine(config, sim, journal, earnings, Notifier(None), executor)


def at(sim: SimulatedBroker, day_index: int, hour: int = 10, minute: int = 30) -> None:
    sim.set_time(datetime.combine(sim.trading_days()[day_index], time(hour, minute), tzinfo=ET))


def run_until(engine, sim, start: int, days: int, predicate) -> int:
    for i in range(start, start + days):
        for hour, minute in ((10, 30), (15, 45)):
            at(sim, i, hour, minute)
            engine.run_cycle()
            if predicate():
                return i
        sim.end_of_day()
    raise AssertionError("condition never met")


def test_opens_manages_and_accounts_exactly(sim, config, journal):
    engine = make_engine(sim, config, journal)
    start_equity = sim.equity()
    day = run_until(engine, sim, 300, 40, lambda: bool(journal.all_trades()) and journal.all_trades()[0].status is not TradeStatus.OPENING)
    first = journal.all_trades()[0]
    assert first.status in (TradeStatus.OPEN, TradeStatus.CLOSED)
    assert first.rationale["summary"] and first.rationale["why"]
    assert first.max_loss * first.qty <= config.risk.max_risk_per_trade_pct * start_equity * 1.05
    run_until(engine, sim, day + 1, 80, lambda: journal.get_trade(first.id).status is TradeStatus.CLOSED)
    closed = journal.get_trade(first.id)
    assert closed.exit_reason in {r.value for r in ExitReason}
    # the journal's P&L must reconcile with the broker's cash to the cent (fees aside)
    marks = sim.get_option_quotes([leg.symbol for t in journal.active_trades() for leg in t.legs])
    unrealized = sum(t.unrealized_pnl(combo_value(t.legs, marks, "mid")) for t in journal.active_trades())
    realized = sum(t.realized_pnl for t in journal.all_trades())
    assert sim.equity() - start_equity == pytest.approx(realized + unrealized - sim.fees_paid, abs=0.01)


def test_dry_run_sends_no_orders(sim, config, journal):
    engine = make_engine(sim, config.model_copy(update={"dry_run": True}), journal)
    report = engine.run_cycle()
    assert report.block_reason is None
    assert sim.orders == {} and journal.all_trades() == []
    assert any(e.startswith("(dry run)") for e in report.entries) or report.scans


def test_entry_window_and_kill_switch(sim, config, journal):
    engine = make_engine(sim, config, journal)
    at(sim, 300, 9, 45)
    assert engine.run_cycle().block_reason == "outside the entry window"
    at(sim, 300, 10, 30)
    config.kill_switch_file.write_text("")
    assert "kill-switch" in engine.run_cycle().block_reason
    at(sim, 300, 8, 0)
    assert engine.run_cycle().market_open is False


def test_external_close_is_reconciled(sim, config, journal):
    engine = make_engine(sim, config, journal)
    run_until(engine, sim, 300, 40, lambda: any(t.status is TradeStatus.OPEN for t in journal.all_trades()))
    trade = next(t for t in journal.all_trades() if t.status is TradeStatus.OPEN)
    for leg in trade.legs:
        sim.positions.pop(leg.symbol, None)  # closed by hand in the broker's app
    engine.reconcile(sim.now, sim.today)
    closed = journal.get_trade(trade.id)
    assert closed.status is TradeStatus.CLOSED and closed.exit_reason == ExitReason.EXTERNAL.value


def test_unmanaged_positions_block_that_underlying(sim, config, journal):
    engine = make_engine(sim, config, journal)
    chain = sim.get_option_chain("SPY", sim.today + timedelta(days=30), sim.today + timedelta(days=60), sim.get_spot("SPY"))
    q = chain.quotes[0]
    sim.positions[q.symbol] = SimPosition(q.contract, -1, q.mid)
    report = engine.run_cycle()
    spy = next(s for s in report.scans if s.symbol == "SPY")
    assert "didn't open" in spy.notes[0]
    assert not any(t.underlying == "SPY" for t in journal.all_trades())


def test_single_stock_without_earnings_data_is_skipped(market_bars, config, journal):
    bars = dict(market_bars, ACME=synthetic_bars(80, 420, 0.2, 0.3, seed=12))
    sim = SimulatedBroker(bars, SimSettings(iv_hv_ratio=1.4))
    at(sim, 300)
    engine = make_engine(sim, config.model_copy(update={"watchlist": ["ACME"]}), journal)
    report = engine.run_cycle()
    assert "earnings date unknown" in report.scans[0].notes[0]


def test_orders_left_over_from_a_crash_are_settled(sim, config, journal):
    engine = make_engine(sim, config, journal)
    chain = sim.get_option_chain("SPY", sim.today + timedelta(days=30), sim.today + timedelta(days=60), sim.get_spot("SPY"))
    puts = [q for q in chain.side(chain.expirations()[-1], OptionType.PUT) if q.contract.strike < chain.spot]
    short, long = puts[-5], puts[-10]
    legs = [Leg(short.contract, Side.SELL), Leg(long.contract, Side.BUY)]
    candidate = Candidate(
        "SPY", StrategyKind.BULL_PUT_CREDIT, short.contract.expiration, 40, legs, chain.spot, -1.0, -0.9, 100, 400, [], 0.7, 0.7, 1, 20, 2, -5, 0.25
    )
    trade_id = journal.create_trade(candidate, 1, sim.today, 1.0, {"summary": "crash test"})
    # the bot submitted this order, then died before recording the outcome
    price = combo_value(legs, chain.by_symbol(), "open")  # marketable: sell the bid, pay the ask
    request = OrderRequest(opening_legs(legs), 1, round(price, 2), "ob-crash-1")
    state = sim.submit_order(request)
    assert state.status is OrderStatus.FILLED
    journal.record_order("ob-crash-1", None, trade_id, "open", 1, request.limit_price, OrderStatus.PENDING_NEW)
    engine.resolve_orders(sim.now)
    trade = journal.get_trade(trade_id)
    assert trade.status is TradeStatus.OPEN and trade.qty == 1 and trade.entry_value == pytest.approx(request.limit_price)
    assert journal.unsettled_orders() == []


def test_halt_on_drawdown_blocks_entries(sim, config, journal):
    engine = make_engine(sim, config, journal)
    journal.set_state("high_water_mark", sim.equity() * 1.25)
    report = engine.run_cycle()
    assert report.block_reason.startswith("halted")


def test_backtest_runs_and_reports(market_bars, config):
    result = run_backtest(market_bars, config, start=list(market_bars["SPY"].index)[300], sim=SimSettings(iv_hv_ratio=1.3))
    stats = result.stats()
    assert len(result.equity) == 120
    assert stats["trades"] >= 1 and 0 <= stats["win_rate"] <= 1
    assert "Backtest" in result.report()
    with pytest.raises(ValueError):
        run_backtest({"QQQ": market_bars["QQQ"]}, config)


def test_small_accounts_avoid_same_day_round_trips(market_bars, config, journal):
    from optionsbot.broker.base import Account

    sim = SimulatedBroker(market_bars, SimSettings(iv_hv_ratio=1.4))
    at(sim, 300)
    engine = make_engine(sim, config, journal)
    run_until(engine, sim, 300, 40, lambda: any(t.status is TradeStatus.OPEN for t in journal.all_trades()))
    trade = next(t for t in journal.all_trades() if t.status is TradeStatus.OPEN)
    today = trade.opened_on
    small = Account(10_000, 10_000, 10_000, 10_000, 10_000, 3)
    big = Account(50_000, 50_000, 50_000, 50_000, 50_000, 3)
    assert engine._would_day_trade(trade, today, small)
    assert not engine._would_day_trade(trade, today, big)
    assert not engine._would_day_trade(trade, today + timedelta(days=1), small)


def test_vanished_short_leg_flags_assignment_and_halts(sim, config, journal):
    engine = make_engine(sim, config, journal)
    run_until(engine, sim, 300, 40, lambda: any(t.status is TradeStatus.OPEN for t in journal.all_trades()))
    trade = next(t for t in journal.all_trades() if t.status is TradeStatus.OPEN)
    short = next(leg for leg in trade.legs if leg.side is Side.SELL)
    sim.positions.pop(short.symbol)  # assigned overnight
    engine.reconcile(sim.now, sim.today)
    assert journal.get_trade(trade.id).status is TradeStatus.NEEDS_ATTENTION
    assert "assignment" in engine.risk.halted()
