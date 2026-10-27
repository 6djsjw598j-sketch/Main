from datetime import UTC, date, datetime

import pytest

from optionsbot.broker.base import BrokerError, OrderState, OrderStatus
from optionsbot.config import ExecutionConfig
from optionsbot.execution import Executor, closing_legs, opening_legs, pay_prices
from optionsbot.journal import Journal, TradeStatus
from optionsbot.models import Candidate, Leg, OptionContract, OptionQuote, OptionType, Side, StrategyKind
from optionsbot.occ import format_occ

EXP = date(2026, 4, 17)
SHORT = OptionContract(format_occ("SPY", EXP, OptionType.PUT, 500), "SPY", EXP, 500, OptionType.PUT)
LONG = OptionContract(format_occ("SPY", EXP, OptionType.PUT, 495), "SPY", EXP, 495, OptionType.PUT)
LEGS = [Leg(SHORT, Side.SELL), Leg(LONG, Side.BUY)]
QUOTES = {SHORT.symbol: OptionQuote(SHORT, 2.40, 2.60), LONG.symbol: OptionQuote(LONG, 0.90, 1.10)}  # mid credit 1.50, natural 1.30


class FakeBroker:
    name = "fake"

    def __init__(self, threshold: float, partial: int | None = None, reject: int | None = None, stuck: bool = False, transport_error: bool = False):
        self.threshold = threshold  # fills when the price we pay is >= threshold
        self.partial = partial
        self.reject = reject
        self.stuck = stuck
        self.transport_error = transport_error
        self.orders: dict[str, OrderState] = {}
        self.submitted: list = []

    def get_option_quotes(self, symbols):
        return {s: QUOTES[s] for s in symbols}

    def submit_order(self, request):
        self.submitted.append(request)
        if self.transport_error:
            raise BrokerError("connection reset")
        if self.reject is not None:
            raise BrokerError("rejected", status=self.reject)
        oid = f"o{len(self.submitted)}"
        state = OrderState(oid, request.client_order_id, OrderStatus.NEW, request.qty, 0, None, request.limit_price)
        if request.limit_price >= self.threshold - 1e-9:
            qty = min(self.partial, request.qty) if self.partial else request.qty
            state.filled_qty, state.filled_price = qty, request.limit_price
            state.status = OrderStatus.FILLED if qty == request.qty else OrderStatus.PARTIALLY_FILLED
        self.orders[oid] = state
        return state

    def get_order(self, order_id):
        return self.orders[order_id]

    def cancel_order(self, order_id):
        state = self.orders[order_id]
        if not self.stuck and not state.status.is_terminal:
            state.status = OrderStatus.CANCELED


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t


def make(journal, broker, **cfg):
    clock = Clock()
    return Executor(broker, journal, ExecutionConfig(**cfg), sleep=clock.sleep, monotonic=clock.monotonic)


def new_trade(journal: Journal) -> int:
    c = Candidate("SPY", StrategyKind.BULL_PUT_CREDIT, EXP, 46, LEGS, 520, -1.5, -1.3, 150, 350, [498.5], 0.7, 0.7, 10, 20, 3, -8, 0.25)
    return journal.create_trade(c, 2, date(2026, 3, 2), 1.0, {})


def test_pay_prices_and_leg_intents():
    open_legs = opening_legs(LEGS)
    assert [(leg.side, leg.position_intent) for leg in open_legs] == [(Side.SELL, "sell_to_open"), (Side.BUY, "buy_to_open")]
    assert pay_prices(open_legs, QUOTES) == pytest.approx((-1.5, -1.3))
    close_legs = closing_legs(LEGS)
    assert [(leg.side, leg.position_intent) for leg in close_legs] == [(Side.BUY, "buy_to_close"), (Side.SELL, "sell_to_close")]
    assert pay_prices(close_legs, QUOTES) == pytest.approx((1.5, 1.7))


def test_walks_from_mid_towards_natural_until_filled(journal):
    broker = FakeBroker(threshold=-1.44)
    fills = []
    tid = new_trade(journal)
    result = make(journal, broker).work_order(
        tid, "open", opening_legs(LEGS), 2, steps=4, max_concession=0.4, on_fill=lambda q, p: fills.append((q, p))
    )
    prices = [r.limit_price for r in broker.submitted]
    assert prices == [-1.5, -1.47, -1.45, -1.42]  # mid, then 13%, 27%, 40% of the way to natural
    assert result.status == "filled" and result.filled_qty == 2 and fills == [(2, -1.42)]
    assert journal.unsettled_orders() == []


def test_never_pays_worse_than_the_bound(journal):
    broker = FakeBroker(threshold=-1.40)
    result = make(journal, broker).work_order(
        new_trade(journal), "open", opening_legs(LEGS), 1, steps=4, max_concession=0.4, on_fill=lambda q, p: None, worst_price=-1.46
    )
    assert result.status == "unfilled"
    assert max(r.limit_price for r in broker.submitted) == pytest.approx(-1.46)


def test_partial_fill_then_rest(journal):
    broker = FakeBroker(threshold=-1.5, partial=1)
    fills = []
    result = make(journal, broker).work_order(
        new_trade(journal), "open", opening_legs(LEGS), 2, steps=3, max_concession=0.4, on_fill=lambda q, p: fills.append(q)
    )
    assert fills == [1, 1] and result.status == "filled"
    assert [r.qty for r in broker.submitted] == [2, 1]


def test_rejection_and_unknown_outcome(journal):
    result = make(journal, FakeBroker(threshold=0, reject=422)).work_order(
        new_trade(journal), "open", opening_legs(LEGS), 1, 3, 0.4, lambda q, p: None
    )
    assert result.status == "rejected" and journal.unsettled_orders() == []
    result = make(journal, FakeBroker(threshold=0, transport_error=True)).work_order(
        new_trade(journal), "open", opening_legs(LEGS), 1, 3, 0.4, lambda q, p: None
    )
    assert result.status == "pending"
    assert len(journal.unsettled_orders()) == 1  # left for reconciliation, not assumed rejected


def test_unconfirmed_cancel_is_left_pending(journal):
    broker = FakeBroker(threshold=99, stuck=True)
    result = make(journal, broker).work_order(new_trade(journal), "open", opening_legs(LEGS), 1, 3, 0.4, lambda q, p: None)
    assert result.status == "pending" and len(broker.submitted) == 1


def test_journal_entry_exit_and_pnl(journal):
    tid = new_trade(journal)
    at = datetime(2026, 3, 2, 15, tzinfo=UTC)
    journal.apply_entry_fill(tid, 1, -1.50, at)
    journal.apply_entry_fill(tid, 1, -1.40, at)
    journal.set_status(tid, TradeStatus.OPEN)
    trade = journal.get_trade(tid)
    assert trade.qty == 2 and trade.entry_value == pytest.approx(-1.45)
    assert trade.unrealized_pnl(-0.70) == pytest.approx(150.0)
    trade = journal.apply_exit_fill(tid, 1, -0.75, at, "profit_target")
    assert trade.status is TradeStatus.OPEN and trade.qty == 1 and trade.realized_pnl == pytest.approx(70.0)
    trade = journal.apply_exit_fill(tid, 1, -0.65, at, "profit_target")
    assert trade.status is TradeStatus.CLOSED and trade.realized_pnl == pytest.approx(150.0) and trade.exit_value == pytest.approx(-0.70)
    assert journal.closed_trades()[0].id == tid


def test_journal_transaction_rolls_back(journal):
    tid = new_trade(journal)
    with pytest.raises(RuntimeError), journal.transaction():
        journal.apply_entry_fill(tid, 1, -1.5, datetime(2026, 3, 2, tzinfo=UTC))
        raise RuntimeError("boom")
    assert journal.get_trade(tid).qty == 0


def test_journal_state_and_iv_history(tmp_path):
    j = Journal(tmp_path / "x.db")
    j.set_state("k", {"a": 1})
    j.record_iv("spy", date(2026, 1, 2), 0.2)
    j.record_iv("SPY", date(2026, 1, 3), 0.25)
    j.close()
    j = Journal(tmp_path / "x.db")
    assert j.get_state("k") == {"a": 1} and j.get_state("missing", 5) == 5
    assert j.iv_history("SPY", date(2026, 1, 3)) == [0.2]
    assert j.has_iv("SPY", date(2026, 1, 3))
