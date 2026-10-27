"""Simulated broker for backtests and tests.

It replays daily bars and prices a synthetic option chain with Black-Scholes. This makes
the *mechanics* testable end to end (selection, sizing, order walking, exits, circuit
breakers), but it is a model, not history:

* implied volatility is assumed to be ``iv_hv_ratio`` x a blend of trailing realised
  volatility, with a fixed put skew, so the volatility risk premium is an input, not a
  measurement;
* the session is sampled twice a day: before noon the underlying trades at the day's open,
  after noon at its close; nothing in between (stops can be hit late);
* positions held to expiration are cash-settled at the closing price.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd

from optionsbot.broker.base import Account, BrokerError, BrokerPosition, Clock, OrderRequest, OrderState, OrderStatus
from optionsbot.indicators import historical_volatility
from optionsbot.models import OptionChain, OptionContract, OptionQuote, OptionType, Side
from optionsbot.occ import format_occ, parse_occ
from optionsbot.pricing import bs_greeks
from optionsbot.timeutil import ET, et_date, to_et, years_to_expiry


@dataclass
class SimSettings:
    starting_cash: float = 100_000.0
    iv_hv_ratio: float = 1.10
    iv_floor: float = 0.08
    skew: float = 2.0
    # half of the bid/ask spread = base + pct x theoretical price, capped
    half_spread_base: float = 0.01
    half_spread_pct: float = 0.01
    max_half_spread: float = 0.25
    # share of the mid -> natural gap a limit order must give up before it fills
    fill_concession: float = 0.25
    fee_per_contract: float = 0.05
    risk_free_rate: float = 0.04
    expiration_weeks: int = 10
    open_interest: int = 5000
    options_level: int = 3


@dataclass
class SimPosition:
    contract: OptionContract
    qty: int
    avg_price: float


def strike_step(spot: float) -> float:
    if spot < 30:
        return 0.5
    if spot < 300:
        return 1.0
    return 2.5


class SimulatedBroker:
    name = "simulated"

    def __init__(self, bars: dict[str, pd.DataFrame], settings: SimSettings | None = None) -> None:
        if not bars:
            raise ValueError("the simulator needs daily bars for at least one symbol")
        self.settings = settings or SimSettings()
        self.bars = {s.upper(): df.sort_index() for s, df in bars.items()}
        self._vol: dict[str, pd.Series] = {}
        for symbol, df in self.bars.items():
            hv20 = historical_volatility(df["close"], 20)
            hv60 = historical_volatility(df["close"], 60)
            blend = (0.6 * hv20 + 0.4 * hv60).fillna(hv20)
            # IV observed on day d may only use closes up to d-1
            self._vol[symbol] = blend.shift(1)
        self.cash = self.settings.starting_cash
        self.positions: dict[str, SimPosition] = {}
        self.orders: dict[str, OrderState] = {}
        self._requests: dict[str, OrderRequest] = {}
        self._ids = itertools.count(1)
        self.now = datetime.combine(self.trading_days()[0], time(10, 0), tzinfo=ET)
        self.last_equity = self.cash
        self.fees_paid = 0.0

    # ------------------------------------------------------------------ simulation control
    def trading_days(self) -> list[date]:
        first = next(iter(self.bars.values()))
        return list(first.index)

    def set_time(self, moment: datetime) -> None:
        self.now = to_et(moment)

    @property
    def today(self) -> date:
        return et_date(self.now)

    def end_of_day(self) -> None:
        """Settle contracts expiring today at the close and roll the previous-close equity."""
        today = self.today
        for symbol, pos in list(self.positions.items()):
            if pos.contract.expiration <= today:
                close = self._close_on(pos.contract.underlying, today)
                self.cash += pos.qty * pos.contract.intrinsic(close) * pos.contract.multiplier
                del self.positions[symbol]
        for order_id, state in self.orders.items():
            if not state.status.is_terminal:
                self.orders[order_id] = _with_status(state, OrderStatus.EXPIRED)
        self.last_equity = self.equity()

    # ------------------------------------------------------------------ prices
    def _bar(self, symbol: str, day: date) -> pd.Series:
        df = self.bars.get(symbol.upper())
        if df is None:
            raise BrokerError(f"simulator has no data for {symbol}")
        if day not in df.index:
            raise BrokerError(f"{symbol} has no bar on {day}")
        return df.loc[day]

    def _close_on(self, symbol: str, day: date) -> float:
        return float(self._bar(symbol, day)["close"])

    def get_spot(self, symbol: str) -> float:
        bar = self._bar(symbol, self.today)
        return float(bar["open"] if to_et(self.now).time() < time(12, 0) else bar["close"])

    def atm_iv(self, symbol: str) -> float:
        vol = self._vol[symbol.upper()]
        value = vol.get(self.today, np.nan)
        if pd.isna(value):
            value = 0.20
        return max(self.settings.iv_floor, float(value) * self.settings.iv_hv_ratio)

    def _quote(self, contract: OptionContract, spot: float, atm_iv: float) -> OptionQuote | None:
        if contract.expiration < self.today:
            return None
        t = years_to_expiry(contract.expiration, self.now)
        moneyness = math.log(contract.strike / spot)
        iv = atm_iv * min(2.5, max(0.7, 1.0 - self.settings.skew * moneyness))
        g = bs_greeks(spot, contract.strike, t, self.settings.risk_free_rate, iv, contract.type)
        half = min(self.settings.max_half_spread, self.settings.half_spread_base + g.price * self.settings.half_spread_pct)
        mid = max(g.price, half)
        bid = round(max(0.0, mid - half), 2)
        ask = round(mid + half, 2)
        return OptionQuote(
            contract=contract,
            bid=bid,
            ask=max(ask, bid + 0.01),
            bid_size=50,
            ask_size=50,
            iv=iv,
            delta=g.delta,
            gamma=g.gamma,
            theta=g.theta,
            vega=g.vega,
            open_interest=self.settings.open_interest,
            quote_time=self.now,
        )

    def get_option_chain(self, underlying: str, expiration_from: date, expiration_to: date, spot: float) -> OptionChain:
        underlying = underlying.upper()
        spot = self.get_spot(underlying)
        iv = self.atm_iv(underlying)
        step = strike_step(spot)
        lo = math.floor(spot * 0.8 / step) * step
        hi = math.ceil(spot * 1.2 / step) * step
        strikes = np.arange(lo, hi + step / 2, step)
        quotes: list[OptionQuote] = []
        day = self.today + timedelta(days=(4 - self.today.weekday()) % 7)  # next Friday (or today)
        last = self.today + timedelta(weeks=self.settings.expiration_weeks)
        while day <= last:
            if expiration_from <= day <= expiration_to:
                for k in strikes:
                    for ot in (OptionType.PUT, OptionType.CALL):
                        contract = OptionContract(format_occ(underlying, day, ot, float(k)), underlying, day, round(float(k), 2), ot)
                        q = self._quote(contract, spot, iv)
                        if q is not None:
                            quotes.append(q)
            day += timedelta(days=7)
        return OptionChain(underlying=underlying, spot=spot, as_of=self.now.astimezone(UTC), quotes=quotes)

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        out: dict[str, OptionQuote] = {}
        cache: dict[str, tuple[float, float]] = {}
        for symbol in set(symbols):
            contract = parse_occ(symbol)
            u = contract.underlying
            if u not in cache:
                cache[u] = (self.get_spot(u), self.atm_iv(u))
            q = self._quote(contract, *cache[u])
            if q is not None:
                out[symbol] = q
        return out

    # ------------------------------------------------------------------ account
    def _marks(self) -> dict[str, OptionQuote]:
        return self.get_option_quotes(list(self.positions))

    def equity(self) -> float:
        marks = self._marks()
        value = 0.0
        for symbol, pos in self.positions.items():
            q = marks.get(symbol)
            price = q.mid if q else pos.contract.intrinsic(self.get_spot(pos.contract.underlying))
            value += pos.qty * price * pos.contract.multiplier
        return self.cash + value

    def margin_requirement(self, positions: dict[str, SimPosition] | None = None) -> float:
        """Worst-case expiry loss per (underlying, expiration), ignoring premiums - the
        "universal spread rule" Alpaca documents for multi-leg positions."""
        groups: dict[tuple[str, date], list[SimPosition]] = {}
        for pos in (positions if positions is not None else self.positions).values():
            groups.setdefault((pos.contract.underlying, pos.contract.expiration), []).append(pos)
        total = 0.0
        for group in groups.values():
            strikes = sorted({p.contract.strike for p in group})
            points = [0.0, *strikes, strikes[-1] * 10]
            worst = min(sum(p.qty * p.contract.intrinsic(s) * p.contract.multiplier for p in group) for s in points)
            call_slope = sum(p.qty for p in group if p.contract.type is OptionType.CALL)
            if call_slope < 0:
                return math.inf
            total += max(0.0, -worst)
        return total

    def get_account(self) -> Account:
        equity = self.equity()
        bp = self.cash - self.margin_requirement()
        return Account(
            equity=equity,
            last_equity=self.last_equity,
            cash=self.cash,
            buying_power=bp,
            options_buying_power=bp,
            options_level=self.settings.options_level,
        )

    def get_clock(self) -> Clock:
        now = to_et(self.now)
        days = self.trading_days()
        today = now.date()
        is_day = today in set(days)
        open_t = datetime.combine(today, time(9, 30), tzinfo=ET)
        close_t = datetime.combine(today, time(16, 0), tzinfo=ET)
        later = [d for d in days if d > today]
        nxt = later[0] if later else today + timedelta(days=1)
        return Clock(
            timestamp=now,
            is_open=is_day and open_t <= now < close_t,
            next_open=open_t if is_day and now < open_t else datetime.combine(nxt, time(9, 30), tzinfo=ET),
            next_close=close_t if is_day and now < close_t else datetime.combine(nxt, time(16, 0), tzinfo=ET),
        )

    def get_positions(self) -> list[BrokerPosition]:
        marks = self._marks()
        out = []
        for symbol, pos in self.positions.items():
            q = marks.get(symbol)
            price = q.mid if q else None
            out.append(
                BrokerPosition(
                    symbol=symbol,
                    qty=pos.qty,
                    avg_entry_price=pos.avg_price,
                    current_price=price,
                    market_value=None if price is None else pos.qty * price * pos.contract.multiplier,
                    asset_class="us_option",
                )
            )
        return out

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        df = self.bars.get(symbol.upper())
        if df is None:
            raise BrokerError(f"simulator has no data for {symbol}")
        # only completed sessions: no look-ahead into today's close
        mask = (df.index >= start) & (df.index <= end) & (df.index < self.today)
        return df.loc[mask, ["open", "high", "low", "close", "volume"]]

    # ------------------------------------------------------------------ orders
    def _try_fill(self, order_id: str) -> None:
        state = self.orders[order_id]
        request = self._requests[order_id]
        if state.status.is_terminal:
            return
        quotes = self.get_option_quotes([leg.symbol for leg in request.legs])
        if len(quotes) != len(request.legs):
            return
        mid_cost = sum(leg.side.sign * leg.ratio * quotes[leg.symbol].mid for leg in request.legs)
        natural_cost = sum(leg.side.sign * leg.ratio * quotes[leg.symbol].price_for(leg.side) for leg in request.legs)
        threshold = mid_cost + self.settings.fill_concession * (natural_cost - mid_cost)
        if request.limit_price + 1e-9 < threshold:
            return
        new_positions = dict(self.positions)
        for leg in request.legs:
            contract = quotes[leg.symbol].contract
            delta_qty = leg.side.sign * leg.ratio * request.qty
            existing = new_positions.get(leg.symbol)
            qty = (existing.qty if existing else 0) + delta_qty
            if qty == 0:
                new_positions.pop(leg.symbol, None)
            else:
                avg = quotes[leg.symbol].mid if existing is None or (existing.qty > 0) != (qty > 0) else existing.avg_price
                new_positions[leg.symbol] = SimPosition(contract, qty, avg)
        fees = self.settings.fee_per_contract * sum(leg.ratio for leg in request.legs) * request.qty
        cash_after = self.cash - request.limit_price * 100 * request.qty - fees
        if cash_after - self.margin_requirement(new_positions) < 0:
            self.orders[order_id] = _with_status(state, OrderStatus.REJECTED)
            return
        self.cash = cash_after
        self.fees_paid += fees
        self.positions = new_positions
        state.status = OrderStatus.FILLED
        state.filled_qty = request.qty
        state.filled_price = request.limit_price

    def submit_order(self, request: OrderRequest) -> OrderState:
        if self.settings.options_level < 3 and len(request.legs) > 1:
            raise BrokerError("multi-leg orders need options level 3", status=403)
        for leg in request.legs:
            pos = self.positions.get(leg.symbol)
            if leg.position_intent.endswith("_to_close") and (pos is None or (pos.qty > 0) == (leg.side is Side.BUY)):
                raise BrokerError(f"no position to close for {leg.symbol}", status=422)
        order_id = f"sim-{next(self._ids)}"
        state = OrderState(
            id=order_id,
            client_order_id=request.client_order_id,
            status=OrderStatus.NEW,
            qty=request.qty,
            filled_qty=0,
            filled_price=None,
            limit_price=request.limit_price,
        )
        self.orders[order_id] = state
        self._requests[order_id] = request
        self._try_fill(order_id)
        return _copy(self.orders[order_id])

    def get_order(self, order_id: str) -> OrderState:
        if order_id not in self.orders:
            raise BrokerError(f"unknown order {order_id}", status=404)
        self._try_fill(order_id)
        return _copy(self.orders[order_id])

    def get_order_by_client_id(self, client_order_id: str) -> OrderState | None:
        for state in self.orders.values():
            if state.client_order_id == client_order_id:
                return _copy(state)
        return None

    def cancel_order(self, order_id: str) -> None:
        state = self.orders.get(order_id)
        if state is not None and not state.status.is_terminal:
            self.orders[order_id] = _with_status(state, OrderStatus.CANCELED)

    def list_open_orders(self) -> list[OrderState]:
        return [_copy(s) for s in self.orders.values() if not s.status.is_terminal]


def _copy(state: OrderState) -> OrderState:
    return OrderState(state.id, state.client_order_id, state.status, state.qty, state.filled_qty, state.filled_price, state.limit_price)


def _with_status(state: OrderState, status: OrderStatus) -> OrderState:
    copy = _copy(state)
    copy.status = status
    return copy
