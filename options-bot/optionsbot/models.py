"""Core domain types shared by every module.

Sign convention used everywhere in the bot: a position's *value* per share is the sum of
its legs' prices, long legs positive and short legs negative. A credit spread therefore has
a negative value (it is a liability) and a debit spread a positive one.

* opening a position costs ``value`` per share (positive = debit paid, negative = credit
  received), which is exactly Alpaca's multi-leg ``limit_price`` convention;
* closing it costs ``-value``;
* P&L per share = current value - entry value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class StrategyKind(StrEnum):
    BULL_PUT_CREDIT = "bull_put_credit_spread"
    BEAR_CALL_CREDIT = "bear_call_credit_spread"
    IRON_CONDOR = "iron_condor"
    BULL_CALL_DEBIT = "bull_call_debit_spread"
    BEAR_PUT_DEBIT = "bear_put_debit_spread"

    @property
    def is_credit(self) -> bool:
        return self in (StrategyKind.BULL_PUT_CREDIT, StrategyKind.BEAR_CALL_CREDIT, StrategyKind.IRON_CONDOR)

    @property
    def direction(self) -> int:
        """+1 bullish, -1 bearish, 0 neutral."""
        if self in (StrategyKind.BULL_PUT_CREDIT, StrategyKind.BULL_CALL_DEBIT):
            return 1
        if self in (StrategyKind.BEAR_CALL_CREDIT, StrategyKind.BEAR_PUT_DEBIT):
            return -1
        return 0


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    expiration: date
    strike: float
    type: OptionType
    multiplier: int = 100

    def intrinsic(self, spot: float) -> float:
        if self.type is OptionType.CALL:
            return max(0.0, spot - self.strike)
        return max(0.0, self.strike - spot)

    def is_itm(self, spot: float) -> bool:
        return self.intrinsic(spot) > 0


@dataclass
class OptionQuote:
    contract: OptionContract
    bid: float
    ask: float
    bid_size: int = 0
    ask_size: int = 0
    last: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    open_interest: int | None = None
    quote_time: datetime | None = None

    @property
    def symbol(self) -> str:
        return self.contract.symbol

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def is_valid(self) -> bool:
        return math.isfinite(self.bid) and math.isfinite(self.ask) and self.ask > 0 and self.bid >= 0 and self.ask >= self.bid

    def price_for(self, side: Side) -> float:
        """Natural (worst) price: pay the ask when buying, receive the bid when selling."""
        return self.ask if side is Side.BUY else self.bid


@dataclass
class OptionChain:
    underlying: str
    spot: float
    as_of: datetime
    quotes: list[OptionQuote]

    def expirations(self) -> list[date]:
        return sorted({q.contract.expiration for q in self.quotes})

    def side(self, expiration: date, option_type: OptionType) -> list[OptionQuote]:
        """Quotes of one expiration and type, sorted by strike."""
        rows = [q for q in self.quotes if q.contract.expiration == expiration and q.contract.type is option_type]
        return sorted(rows, key=lambda q: q.contract.strike)

    def by_symbol(self) -> dict[str, OptionQuote]:
        return {q.symbol: q for q in self.quotes}


@dataclass(frozen=True)
class Leg:
    contract: OptionContract
    side: Side
    ratio: int = 1

    @property
    def symbol(self) -> str:
        return self.contract.symbol

    @property
    def sign(self) -> int:
        return self.side.sign * self.ratio

    def to_dict(self) -> dict:
        c = self.contract
        return {
            "symbol": c.symbol,
            "underlying": c.underlying,
            "expiration": c.expiration.isoformat(),
            "strike": c.strike,
            "type": c.type.value,
            "side": self.side.value,
            "ratio": self.ratio,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Leg:
        contract = OptionContract(
            symbol=data["symbol"],
            underlying=data["underlying"],
            expiration=date.fromisoformat(data["expiration"]),
            strike=float(data["strike"]),
            type=OptionType(data["type"]),
        )
        return cls(contract=contract, side=Side(data["side"]), ratio=int(data.get("ratio", 1)))


def combo_value(legs: list[Leg], quotes: dict[str, OptionQuote], how: str = "mid") -> float | None:
    """Per-share value of a combination at mid, or at the natural price to *open* it
    (``how="open"``) or to *close* it (``how="close"``). None if a quote is missing."""
    total = 0.0
    for leg in legs:
        q = quotes.get(leg.symbol)
        if q is None or not q.is_valid:
            return None
        if how == "mid":
            price = q.mid
        elif how == "open":
            price = q.price_for(leg.side)
        elif how == "close":
            # closing reverses the side; express the result as position value
            price = q.price_for(leg.side.opposite)
        else:
            raise ValueError(how)
        total += leg.sign * price
    return total


@dataclass
class Candidate:
    """A fully priced, evaluated trade idea for one underlying."""

    underlying: str
    strategy: StrategyKind
    expiration: date
    dte: int
    legs: list[Leg]
    spot: float
    mid_value: float  # per share, signed (credit negative)
    natural_value: float  # per share, worst price to open
    max_profit: float  # $ per contract
    max_loss: float  # $ per contract (positive number)
    breakevens: list[float]
    pop: float  # probability of profit at expiry under implied vol
    pop_forecast: float  # same, under the bot's realised-vol forecast
    expected_value: float  # $ per contract at mid, under the forecast distribution
    net_delta: float  # shares per contract
    net_theta: float  # $ per day per contract
    net_vega: float  # $ per vol point per contract
    short_delta: float  # |delta| of the (first) short leg
    score: float = 0.0
    rationale: list[str] = field(default_factory=list)

    @property
    def credit(self) -> float:
        return -self.mid_value

    @property
    def width(self) -> float:
        strikes_by_type: dict[OptionType, list[float]] = {}
        for leg in self.legs:
            strikes_by_type.setdefault(leg.contract.type, []).append(leg.contract.strike)
        return max((max(s) - min(s) for s in strikes_by_type.values() if len(s) > 1), default=0.0)

    def describe(self) -> str:
        parts = []
        for leg in sorted(self.legs, key=lambda leg: (leg.contract.type.value, leg.contract.strike)):
            c = leg.contract
            parts.append(f"{'+' if leg.side is Side.BUY else '-'}{c.strike:g}{c.type.value[0].upper()}")
        price = f"{self.credit:.2f} credit" if self.mid_value < 0 else f"{self.mid_value:.2f} debit"
        return f"{self.underlying} {self.strategy.value} {self.expiration.isoformat()} ({self.dte} DTE) {' '.join(parts)} @ {price}"
