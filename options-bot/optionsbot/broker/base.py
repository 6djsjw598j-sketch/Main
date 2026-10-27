"""Broker interface. The engine only talks to this protocol, so the same trading logic
runs against Alpaca (paper or live) and against the simulator used for backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

import pandas as pd

from optionsbot.models import OptionChain, OptionQuote, Side


class BrokerError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Account:
    equity: float
    last_equity: float  # equity at the previous close
    cash: float
    buying_power: float
    options_buying_power: float
    options_level: int
    trading_blocked: bool = False
    status: str = "ACTIVE"


@dataclass
class Clock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass
class BrokerPosition:
    symbol: str
    qty: int  # signed: negative = short
    avg_entry_price: float
    current_price: float | None
    market_value: float | None
    asset_class: str  # "us_option" or "us_equity"


class OrderStatus(StrEnum):
    NEW = "new"
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    DONE_FOR_DAY = "done_for_day"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REPLACED = "replaced"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    STOPPED = "stopped"
    CALCULATED = "calculated"
    HELD = "held"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: str | None) -> OrderStatus:
        try:
            return cls((value or "").lower())
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
            OrderStatus.REPLACED,
            OrderStatus.DONE_FOR_DAY,
        )


@dataclass(frozen=True)
class OrderLeg:
    symbol: str
    side: Side
    position_intent: str  # buy_to_open, sell_to_open, buy_to_close, sell_to_close
    ratio: int = 1


@dataclass
class OrderRequest:
    legs: list[OrderLeg]
    qty: int
    limit_price: float  # per share; positive = debit paid, negative = credit received
    client_order_id: str
    time_in_force: str = "day"


@dataclass
class OrderState:
    id: str
    client_order_id: str
    status: OrderStatus
    qty: int
    filled_qty: int
    filled_price: float | None  # net per share, same sign convention as limit_price
    limit_price: float | None = None
    raw: dict = field(default_factory=dict, repr=False)


class Broker(Protocol):
    name: str

    def get_account(self) -> Account: ...

    def get_clock(self) -> Clock: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Daily bars indexed by date with open/high/low/close/volume columns."""
        ...

    def get_spot(self, symbol: str) -> float: ...

    def get_option_chain(self, underlying: str, expiration_from: date, expiration_to: date, spot: float) -> OptionChain: ...

    def get_option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]: ...

    def submit_order(self, request: OrderRequest) -> OrderState: ...

    def get_order(self, order_id: str) -> OrderState: ...

    def get_order_by_client_id(self, client_order_id: str) -> OrderState | None: ...

    def cancel_order(self, order_id: str) -> None: ...

    def list_open_orders(self) -> list[OrderState]: ...
