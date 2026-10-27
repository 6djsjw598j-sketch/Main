"""Order execution the way a careful trader works a spread: never a market order.

Each order starts at the mid price and, if it doesn't fill within ``step_timeout_seconds``,
is cancelled and re-priced a step closer to the natural price, up to a maximum
concession (entries are patient; stop-loss exits may go all the way to the natural price).
Quotes are refreshed before every step, and an entry never accepts a worse price than the
bound the strategy rules allow.

Prices here are what we *pay* per share (Alpaca's multi-leg convention): positive = debit,
negative = credit. Opening a position pays its value; closing it pays minus its value.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from optionsbot.broker.base import Broker, BrokerError, OrderLeg, OrderRequest, OrderState, OrderStatus
from optionsbot.config import ExecutionConfig
from optionsbot.journal import Journal
from optionsbot.models import Leg, OptionQuote

logger = logging.getLogger(__name__)


@dataclass
class FillResult:
    filled_qty: int
    avg_price: float | None  # paid per share, Alpaca convention
    status: str  # filled | partial | unfilled | rejected | pending | error
    message: str = ""


def pay_prices(legs: list[OrderLeg], quotes: dict[str, OptionQuote]) -> tuple[float, float] | None:
    """(mid, natural) price we would pay per share for one unit of the order."""
    mid = natural = 0.0
    for leg in legs:
        q = quotes.get(leg.symbol)
        if q is None or not q.is_valid:
            return None
        mid += leg.side.sign * leg.ratio * q.mid
        natural += leg.side.sign * leg.ratio * q.price_for(leg.side)
    return mid, natural


def opening_legs(legs: list[Leg]) -> list[OrderLeg]:
    return [OrderLeg(leg.symbol, leg.side, f"{leg.side.value}_to_open", leg.ratio) for leg in legs]


def closing_legs(legs: list[Leg]) -> list[OrderLeg]:
    return [OrderLeg(leg.symbol, leg.side.opposite, f"{leg.side.opposite.value}_to_close", leg.ratio) for leg in legs]


class Executor:
    def __init__(
        self,
        broker: Broker,
        journal: Journal,
        config: ExecutionConfig,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.broker = broker
        self.journal = journal
        self.config = config
        self.sleep = sleep
        self.monotonic = monotonic

    def _round(self, price: float) -> float:
        tick = self.config.tick
        rounded = round(round(price / tick) * tick, 2)
        if abs(rounded) < tick:
            rounded = tick if price >= 0 else -tick
        return rounded

    def _wait(self, state: OrderState, timeout: float) -> OrderState:
        deadline = self.monotonic() + timeout
        while not state.status.is_terminal and self.monotonic() < deadline:
            self.sleep(self.config.poll_seconds)
            state = self.broker.get_order(state.id)
        return state

    def _cancel(self, state: OrderState) -> OrderState:
        self.broker.cancel_order(state.id)
        state = self.broker.get_order(state.id)
        return self._wait(state, self.config.cancel_timeout_seconds)

    def work_order(
        self,
        trade_id: int,
        purpose: str,
        legs: list[OrderLeg],
        qty: int,
        steps: int,
        max_concession: float,
        on_fill: Callable[[int, float], None],
        worst_price: float | None = None,
    ) -> FillResult:
        """Work a limit order from mid towards natural. ``worst_price`` caps what we pay.

        ``on_fill(qty, price_paid)`` is called once per filled order, in the same database
        transaction that marks the order as applied, so a crash can't lose or double-count a fill."""
        remaining = qty
        filled = 0
        paid = 0.0
        symbols = [leg.symbol for leg in legs]
        for step in range(steps):
            try:
                quotes = self.broker.get_option_quotes(symbols)
            except BrokerError as exc:
                return self._result(filled, paid, qty, "error", f"quotes unavailable: {exc}")
            prices = pay_prices(legs, quotes)
            if prices is None:
                return self._result(filled, paid, qty, "error", "missing or invalid quotes")
            mid, natural = prices
            fraction = max_concession * step / (steps - 1) if steps > 1 else 0.0
            price = self._round(mid + fraction * (natural - mid))
            if worst_price is not None and price > worst_price + 1e-9:
                if step == 0:
                    return self._result(filled, paid, qty, "unfilled", f"mid {mid:.2f} is already worse than the {worst_price:.2f} limit")
                price = self._round(worst_price)
            client_id = f"ob-{trade_id}-{purpose}-{step}-{uuid.uuid4().hex[:8]}"
            request = OrderRequest(legs=legs, qty=remaining, limit_price=price, client_order_id=client_id)
            self.journal.record_order(client_id, None, trade_id, purpose, remaining, price, OrderStatus.PENDING_NEW)
            try:
                state = self.broker.submit_order(request)
            except BrokerError as exc:
                if exc.status is None:
                    # we don't know whether the order reached the broker: leave it for reconciliation
                    self.journal.update_order(client_id, OrderStatus.UNKNOWN, 0, None)
                    return self._result(filled, paid, qty, "pending", str(exc))
                self.journal.update_order(client_id, OrderStatus.REJECTED, 0, None)
                self.journal.mark_order_applied(client_id)
                status = "rejected" if exc.status in (400, 403, 422) else "error"
                return self._result(filled, paid, qty, status, str(exc))
            self.journal.update_order(client_id, state.status, state.filled_qty, state.filled_price, state.id)
            logger.info(
                "%s trade %s: %d @ %.2f (mid %.2f, natural %.2f, step %d/%d)", purpose, trade_id, remaining, price, mid, natural, step + 1, steps
            )

            state = self._wait(state, self.config.step_timeout_seconds)
            if not state.status.is_terminal:
                state = self._cancel(state)
            self.journal.update_order(client_id, state.status, state.filled_qty, state.filled_price, state.id)
            if not state.status.is_terminal:
                # cancel not confirmed: leave it for reconciliation rather than risk a double fill
                return self._result(filled, paid, qty, "pending", f"order {state.id} still working after cancel request")
            fill_price = state.filled_price if state.filled_price is not None else price
            with self.journal.transaction():
                if state.filled_qty:
                    on_fill(state.filled_qty, fill_price)
                self.journal.mark_order_applied(client_id)
            if state.filled_qty:
                filled += state.filled_qty
                paid += fill_price * state.filled_qty
                remaining -= state.filled_qty
            if remaining <= 0:
                break
            if state.status is OrderStatus.REJECTED:
                return self._result(filled, paid, qty, "rejected", "order rejected by the broker")
        return self._result(filled, paid, qty, "filled" if filled >= qty else ("partial" if filled else "unfilled"))

    @staticmethod
    def _result(filled: int, paid: float, qty: int, status: str, message: str = "") -> FillResult:
        if status in ("error", "rejected", "unfilled") and filled:
            status = "partial"
        return FillResult(filled, paid / filled if filled else None, status, message)
