"""SQLite trade journal and bot state.

Every trade, order, exit reason and decision is recorded, so the bot can recover after a
restart and you can audit why it did what it did.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from optionsbot.broker.base import OrderStatus
from optionsbot.models import Candidate, Leg, StrategyKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying TEXT NOT NULL,
    strategy TEXT NOT NULL,
    expiration TEXT NOT NULL,
    legs TEXT NOT NULL,
    status TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0,
    target_qty INTEGER NOT NULL,
    entry_value REAL,
    entry_time TEXT,
    opened_on TEXT NOT NULL,
    max_profit REAL NOT NULL,
    max_loss REAL NOT NULL,
    beta REAL NOT NULL DEFAULT 1.0,
    closed_qty INTEGER NOT NULL DEFAULT 0,
    exit_value REAL,
    exit_time TEXT,
    exit_reason TEXT,
    realized_pnl REAL NOT NULL DEFAULT 0,
    rationale TEXT NOT NULL DEFAULT '{}',
    note TEXT
);
CREATE INDEX IF NOT EXISTS trades_status ON trades(status);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    broker_order_id TEXT,
    trade_id INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    qty INTEGER NOT NULL,
    limit_price REAL NOT NULL,
    status TEXT NOT NULL,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    filled_price REAL,
    applied INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iv_history (
    symbol TEXT NOT NULL,
    day TEXT NOT NULL,
    atm_iv REAL NOT NULL,
    PRIMARY KEY (symbol, day)
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT
);
"""


class TradeStatus(StrEnum):
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    CANCELED = "canceled"
    NEEDS_ATTENTION = "needs_attention"


ACTIVE_STATUSES = (TradeStatus.OPENING, TradeStatus.OPEN, TradeStatus.CLOSING, TradeStatus.NEEDS_ATTENTION)


@dataclass
class TradeRecord:
    id: int
    underlying: str
    strategy: StrategyKind
    expiration: date
    legs: list[Leg]
    status: TradeStatus
    qty: int
    target_qty: int
    entry_value: float | None
    entry_time: datetime | None
    opened_on: date
    max_profit: float  # $ per contract
    max_loss: float  # $ per contract
    beta: float
    closed_qty: int
    exit_value: float | None
    exit_time: datetime | None
    exit_reason: str | None
    realized_pnl: float
    rationale: dict = field(default_factory=dict)
    note: str | None = None

    @property
    def is_credit(self) -> bool:
        return self.strategy.is_credit

    def unrealized_pnl(self, value: float) -> float:
        if self.entry_value is None:
            return 0.0
        return (value - self.entry_value) * 100 * self.qty

    @property
    def open_risk(self) -> float:
        return self.max_loss * self.qty


@dataclass
class OrderRecord:
    client_order_id: str
    broker_order_id: str | None
    trade_id: int
    purpose: str
    qty: int
    limit_price: float
    status: OrderStatus
    filled_qty: int
    filled_price: float | None
    applied: bool


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


class Journal:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several writes atomically (re-entrant: nested calls join the outer one)."""
        with self._lock:
            if self._db.in_transaction:
                yield
                return
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # ------------------------------------------------------------------ trades
    @staticmethod
    def _trade(row: sqlite3.Row) -> TradeRecord:
        return TradeRecord(
            id=row["id"],
            underlying=row["underlying"],
            strategy=StrategyKind(row["strategy"]),
            expiration=date.fromisoformat(row["expiration"]),
            legs=[Leg.from_dict(d) for d in json.loads(row["legs"])],
            status=TradeStatus(row["status"]),
            qty=row["qty"],
            target_qty=row["target_qty"],
            entry_value=row["entry_value"],
            entry_time=_dt(row["entry_time"]),
            opened_on=date.fromisoformat(row["opened_on"]),
            max_profit=row["max_profit"],
            max_loss=row["max_loss"],
            beta=row["beta"],
            closed_qty=row["closed_qty"],
            exit_value=row["exit_value"],
            exit_time=_dt(row["exit_time"]),
            exit_reason=row["exit_reason"],
            realized_pnl=row["realized_pnl"],
            rationale=json.loads(row["rationale"] or "{}"),
            note=row["note"],
        )

    def create_trade(self, candidate: Candidate, target_qty: int, opened_on: date, beta: float, rationale: dict) -> int:
        cur = self._exec(
            """INSERT INTO trades (underlying, strategy, expiration, legs, status, qty, target_qty, opened_on,
                                   max_profit, max_loss, beta, rationale)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)""",
            (
                candidate.underlying,
                candidate.strategy.value,
                candidate.expiration.isoformat(),
                json.dumps([leg.to_dict() for leg in candidate.legs]),
                TradeStatus.OPENING.value,
                target_qty,
                opened_on.isoformat(),
                candidate.max_profit,
                candidate.max_loss,
                beta,
                json.dumps(rationale, default=str),
            ),
        )
        return int(cur.lastrowid)

    def get_trade(self, trade_id: int) -> TradeRecord:
        row = self._exec("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise KeyError(trade_id)
        return self._trade(row)

    def active_trades(self) -> list[TradeRecord]:
        marks = ",".join("?" * len(ACTIVE_STATUSES))
        rows = self._exec(f"SELECT * FROM trades WHERE status IN ({marks}) ORDER BY id", tuple(s.value for s in ACTIVE_STATUSES)).fetchall()
        return [self._trade(r) for r in rows]

    def closed_trades(self, limit: int | None = None) -> list[TradeRecord]:
        sql = "SELECT * FROM trades WHERE status = ? ORDER BY exit_time DESC, id DESC"
        params: tuple = (TradeStatus.CLOSED.value,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        return [self._trade(r) for r in self._exec(sql, params).fetchall()]

    def all_trades(self) -> list[TradeRecord]:
        return [self._trade(r) for r in self._exec("SELECT * FROM trades ORDER BY id").fetchall()]

    def trades_opened_on(self, day: date) -> int:
        row = self._exec("SELECT COUNT(*) FROM trades WHERE opened_on = ? AND status != ?", (day.isoformat(), TradeStatus.CANCELED.value)).fetchone()
        return int(row[0])

    def set_status(self, trade_id: int, status: TradeStatus, note: str | None = None) -> None:
        if note is None:
            self._exec("UPDATE trades SET status = ? WHERE id = ?", (status.value, trade_id))
        else:
            self._exec("UPDATE trades SET status = ?, note = ? WHERE id = ?", (status.value, note, trade_id))

    def apply_entry_fill(self, trade_id: int, qty: int, value: float, at: datetime) -> None:
        trade = self.get_trade(trade_id)
        total = trade.qty + qty
        avg = value if trade.entry_value is None or trade.qty == 0 else (trade.entry_value * trade.qty + value * qty) / total
        self._exec(
            "UPDATE trades SET qty = ?, entry_value = ?, entry_time = COALESCE(entry_time, ?) WHERE id = ?",
            (total, avg, _iso(at), trade_id),
        )

    def set_risk(self, trade_id: int, max_profit: float, max_loss: float) -> None:
        self._exec("UPDATE trades SET max_profit = ?, max_loss = ? WHERE id = ?", (max_profit, max_loss, trade_id))

    def apply_exit_fill(self, trade_id: int, qty: int, value: float, at: datetime, reason: str) -> TradeRecord:
        """Record closing ``qty`` contracts at position value ``value`` per share."""
        trade = self.get_trade(trade_id)
        qty = min(qty, trade.qty)
        pnl = (value - (trade.entry_value or 0.0)) * 100 * qty
        closed = trade.closed_qty + qty
        avg_exit = value if trade.exit_value is None or trade.closed_qty == 0 else (trade.exit_value * trade.closed_qty + value * qty) / closed
        remaining = trade.qty - qty
        status = TradeStatus.CLOSED if remaining == 0 else trade.status
        self._exec(
            """UPDATE trades SET qty = ?, closed_qty = ?, exit_value = ?, exit_time = ?, exit_reason = ?,
                                 realized_pnl = realized_pnl + ?, status = ? WHERE id = ?""",
            (remaining, closed, avg_exit, _iso(at), reason, pnl, status.value, trade_id),
        )
        return self.get_trade(trade_id)

    # ------------------------------------------------------------------ orders
    def record_order(
        self, client_order_id: str, broker_order_id: str | None, trade_id: int, purpose: str, qty: int, limit_price: float, status: OrderStatus
    ) -> None:
        now = _iso(datetime.now(UTC))
        self._exec(
            """INSERT OR REPLACE INTO orders (client_order_id, broker_order_id, trade_id, purpose, qty, limit_price, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_order_id, broker_order_id, trade_id, purpose, qty, limit_price, status.value, now, now),
        )

    def update_order(
        self, client_order_id: str, status: OrderStatus, filled_qty: int, filled_price: float | None, broker_order_id: str | None = None
    ) -> None:
        self._exec(
            """UPDATE orders SET status = ?, filled_qty = ?, filled_price = ?, broker_order_id = COALESCE(?, broker_order_id), updated_at = ?
               WHERE client_order_id = ?""",
            (status.value, filled_qty, filled_price, broker_order_id, _iso(datetime.now(UTC)), client_order_id),
        )

    def mark_order_applied(self, client_order_id: str) -> None:
        self._exec("UPDATE orders SET applied = 1 WHERE client_order_id = ?", (client_order_id,))

    @staticmethod
    def _order(row: sqlite3.Row) -> OrderRecord:
        return OrderRecord(
            client_order_id=row["client_order_id"],
            broker_order_id=row["broker_order_id"],
            trade_id=row["trade_id"],
            purpose=row["purpose"],
            qty=row["qty"],
            limit_price=row["limit_price"],
            status=OrderStatus.parse(row["status"]),
            filled_qty=row["filled_qty"],
            filled_price=row["filled_price"],
            applied=bool(row["applied"]),
        )

    def unsettled_orders(self) -> list[OrderRecord]:
        """Orders whose final state has not been applied to their trade yet."""
        return [self._order(r) for r in self._exec("SELECT * FROM orders WHERE applied = 0 ORDER BY created_at").fetchall()]

    def orders_for(self, trade_id: int) -> list[OrderRecord]:
        return [self._order(r) for r in self._exec("SELECT * FROM orders WHERE trade_id = ? ORDER BY created_at", (trade_id,)).fetchall()]

    # ------------------------------------------------------------------ IV history
    def record_iv(self, symbol: str, day: date, atm_iv: float) -> None:
        self._exec("INSERT OR REPLACE INTO iv_history (symbol, day, atm_iv) VALUES (?, ?, ?)", (symbol.upper(), day.isoformat(), atm_iv))

    def iv_history(self, symbol: str, before: date, limit: int = 252) -> list[float]:
        rows = self._exec(
            "SELECT atm_iv FROM iv_history WHERE symbol = ? AND day < ? ORDER BY day DESC LIMIT ?",
            (symbol.upper(), before.isoformat(), limit),
        ).fetchall()
        return [r[0] for r in reversed(rows)]

    def has_iv(self, symbol: str, day: date) -> bool:
        return self._exec("SELECT 1 FROM iv_history WHERE symbol = ? AND day = ?", (symbol.upper(), day.isoformat())).fetchone() is not None

    # ------------------------------------------------------------------ state and events
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self._exec("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_state(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, json.dumps(value, default=str)))

    def log_event(self, level: str, kind: str, message: str, data: dict | None = None, at: datetime | None = None) -> None:
        self._exec(
            "INSERT INTO events (at, level, kind, message, data) VALUES (?, ?, ?, ?, ?)",
            (_iso(at or datetime.now(UTC)), level, kind, message, json.dumps(data, default=str) if data else None),
        )

    def recent_events(self, limit: int = 50) -> list[dict]:
        rows = self._exec("SELECT at, level, kind, message FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
