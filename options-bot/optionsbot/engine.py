"""The trading loop.

Every cycle (default 5 minutes) while the market is open:
  1. update the high-water mark and circuit breakers;
  2. settle orders left over from earlier cycles or a restart;
  3. reconcile the journal with the broker's positions;
  4. manage open trades (profit targets, stops, time exits, assignment and earnings risk);
  5. if nothing blocks new risk: read each underlying, pick a structure from the playbook,
     build and rank spreads, size them, and work the best ones as limit orders.
"""

from __future__ import annotations

import logging
import signal
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from types import FrameType

import numpy as np
import pandas as pd

from optionsbot.analysis import BarStats, MarketRegime, UnderlyingView, analyze_underlying, atm_implied_vol, attach_iv, bar_stats, market_regime
from optionsbot.broker.base import Account, Broker, BrokerError, OrderStatus
from optionsbot.config import BotConfig
from optionsbot.earnings import EarningsCalendar
from optionsbot.execution import Executor, closing_legs, opening_legs
from optionsbot.journal import Journal, TradeRecord, TradeStatus
from optionsbot.management import ExitReason, ExitSignal, check_exit
from optionsbot.models import Candidate, OptionChain, Side, StrategyKind, combo_value
from optionsbot.notify import Notifier
from optionsbot.occ import is_occ, parse_occ
from optionsbot.pricing import payoff_at_expiry, payoff_extremes
from optionsbot.risk import RiskManager
from optionsbot.strategies import BuildContext, build_candidates, choose_playbook
from optionsbot.timeutil import ET, et_date, to_et

logger = logging.getLogger(__name__)

SESSION_OPEN = dtime(9, 30)


@dataclass
class SymbolScan:
    symbol: str
    view: UnderlyingView | None = None
    candidates: list[Candidate] = field(default_factory=list)
    contracts: int = 0
    sizing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def chosen(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


@dataclass
class CycleReport:
    at: datetime
    market_open: bool
    block_reason: str | None = None
    regime: MarketRegime | None = None
    exits: list[str] = field(default_factory=list)
    entries: list[str] = field(default_factory=list)
    scans: list[SymbolScan] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


class TradingEngine:
    def __init__(
        self,
        config: BotConfig,
        broker: Broker,
        journal: Journal,
        earnings: EarningsCalendar,
        notifier: Notifier | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.journal = journal
        self.earnings = earnings
        self.notifier = notifier or Notifier(None)
        self.executor = executor or Executor(broker, journal, config.execution)
        self.risk = RiskManager(config, journal)
        self._bars: dict[str, tuple[date, pd.DataFrame]] = {}
        self._stats: dict[str, tuple[date, BarStats]] = {}
        self._chains: dict[str, OptionChain] = {}
        self._spots: dict[str, float] = {}
        self._views: dict[str, UnderlyingView] = {}
        self._unmanaged: set[str] = set()
        self.portfolio_beta_delta = 0.0
        self._stop = False

    # ------------------------------------------------------------------ helpers
    def _say(self, report: CycleReport | None, level: int, message: str, notify: bool = False) -> None:
        logger.log(level, message)
        if report is not None:
            report.messages.append(message)
        if notify:
            self.notifier.send(message)

    def _now(self) -> datetime:
        return self.broker.get_clock().timestamp

    def bars(self, symbol: str, today: date) -> pd.DataFrame:
        cached = self._bars.get(symbol)
        if cached is None or cached[0] != today:
            frame = self.broker.get_daily_bars(symbol, today - timedelta(days=self.config.data.history_days), today)
            self._bars[symbol] = (today, frame)
        return self._bars[symbol][1]

    def spot(self, symbol: str) -> float:
        if symbol not in self._spots:
            self._spots[symbol] = self.broker.get_spot(symbol)
        return self._spots[symbol]

    def chain(self, symbol: str, today: date) -> OptionChain:
        if symbol not in self._chains:
            s = self.config.strategy
            self._chains[symbol] = self.broker.get_option_chain(
                symbol, today + timedelta(days=min(20, s.min_dte)), today + timedelta(days=s.max_dte), self.spot(symbol)
            )
        return self._chains[symbol]

    def analyze(self, symbol: str, now: datetime, today: date) -> UnderlyingView:
        if symbol in self._views:
            return self._views[symbol]
        cached = self._stats.get(symbol)
        if cached is None or cached[0] != today:
            market = self.config.market_symbol
            market_bars = self.bars(market, today) if symbol != market else None
            self._stats[symbol] = (today, bar_stats(self.bars(symbol, today), today, market_bars))
        stats = self._stats[symbol][1]
        view = analyze_underlying(symbol, stats, self.spot(symbol), today)
        iv = atm_implied_vol(self.chain(symbol, today), now, today, r=self.config.data.risk_free_rate)
        attach_iv(view, iv, self.journal.iv_history(symbol, today), self.config.data.iv_rank_min_history, stats)
        if iv is not None and not self.journal.has_iv(symbol, today):
            self.journal.record_iv(symbol, today, iv)
        self._views[symbol] = view
        return view

    def earnings_date(self, symbol: str, today: date) -> tuple[bool, date | None]:
        if symbol in self.config.no_earnings_symbols:
            return True, None
        info = self.earnings.lookup(symbol, today)
        return info.known, info.next_date

    def _session(self, now: datetime, next_close: datetime) -> tuple[datetime, datetime]:
        local = to_et(now)
        return datetime.combine(local.date(), SESSION_OPEN, tzinfo=ET), to_et(next_close)

    # ------------------------------------------------------------------ cycle
    def run_cycle(self) -> CycleReport:
        clock = self.broker.get_clock()
        now = clock.timestamp
        today = et_date(now)
        report = CycleReport(at=now, market_open=clock.is_open)
        self._chains.clear()
        self._spots.clear()
        self._views.clear()
        if not clock.is_open:
            return report

        session_open, session_close = self._session(now, clock.next_close)
        sched = self.config.schedule
        in_exit_window = (
            session_open + timedelta(minutes=sched.exit_start_minutes_after_open)
            <= now
            <= session_close - timedelta(minutes=sched.exit_stop_minutes_before_close)
        )
        in_entry_window = (
            session_open + timedelta(minutes=sched.entry_start_minutes_after_open)
            <= now
            <= session_close - timedelta(minutes=sched.entry_stop_minutes_before_close)
        )

        account = self.broker.get_account()
        for message in self.risk.update(account, today):
            self._say(report, logging.WARNING, message, notify=True)

        self.resolve_orders(now, report)
        self.reconcile(now, today, report)
        if in_exit_window:
            self.manage(now, today, report, account)

        active = self.journal.active_trades()
        block = self.risk.entry_block_reason(account, today, active)
        if block is None and not in_entry_window:
            block = "outside the entry window"
        if block is None and account.options_level < 3:
            block = f"options trading level {account.options_level}: spreads need level 3"
        if block is None and self.config.mode == "live" and self.broker.name != "alpaca":
            block = "live mode requires the Alpaca broker"

        if block is None:
            report.regime = self.regime(now, today, report)
            if not report.regime.allows_entries:
                block = "; ".join(report.regime.reasons)
        report.block_reason = block

        if block is None:
            self.enter(now, today, account, report.regime, report)
        if in_entry_window:
            self.record_iv_snapshots(now, today)
        return report

    def regime(self, now: datetime, today: date, report: CycleReport | None = None) -> MarketRegime:
        r = self.config.risk
        market = self.config.market_symbol
        try:
            view = self.analyze(market, now, today)
        except BrokerError as exc:
            self._say(report, logging.WARNING, f"market regime unavailable ({exc}); trading at reduced size")
            view = None
        return market_regime(view, r.crisis_iv, r.crisis_daily_move_pct, r.risk_off_size_multiplier)

    def record_iv_snapshots(self, now: datetime, today: date) -> None:
        for symbol in self.config.watchlist:
            if self.journal.has_iv(symbol, today):
                continue
            try:
                iv = atm_implied_vol(self.chain(symbol, today), now, today, r=self.config.data.risk_free_rate)
            except BrokerError as exc:
                logger.warning("IV snapshot for %s failed: %s", symbol, exc)
                continue
            if iv is not None:
                self.journal.record_iv(symbol, today, iv)

    # ------------------------------------------------------------------ orders and reconciliation
    def _apply_fill(self, trade_id: int, purpose: str, qty: int, price: float, now: datetime) -> None:
        if purpose == "open":
            self.journal.apply_entry_fill(trade_id, qty, price, now)
        else:
            reason = self.journal.get_trade(trade_id).note or "exit"
            self.journal.apply_exit_fill(trade_id, qty, -price, now, reason)

    def resolve_orders(self, now: datetime, report: CycleReport | None = None) -> None:
        """Apply the final state of orders from earlier cycles; cancel stale working orders."""
        for order in self.journal.unsettled_orders():
            try:
                if order.broker_order_id:
                    state = self.broker.get_order(order.broker_order_id)
                else:
                    state = self.broker.get_order_by_client_id(order.client_order_id)
            except BrokerError as exc:
                logger.warning("could not look up order %s: %s", order.client_order_id, exc)
                continue
            if state is None:
                self.journal.update_order(order.client_order_id, OrderStatus.REJECTED, 0, None)
                self.journal.mark_order_applied(order.client_order_id)
                continue
            if not state.status.is_terminal:
                self.broker.cancel_order(state.id)
                self.journal.update_order(order.client_order_id, state.status, state.filled_qty, state.filled_price, state.id)
                continue
            with self.journal.transaction():
                self.journal.update_order(order.client_order_id, state.status, state.filled_qty, state.filled_price, state.id)
                if state.filled_qty:
                    price = state.filled_price if state.filled_price is not None else order.limit_price
                    self._apply_fill(order.trade_id, order.purpose, state.filled_qty, price, now)
                self.journal.mark_order_applied(order.client_order_id)
            self._say(report, logging.INFO, f"settled order {order.client_order_id}: {state.status.value}, {state.filled_qty} filled")

        pending = {o.trade_id for o in self.journal.unsettled_orders()}
        for trade in self.journal.active_trades():
            if trade.id in pending:
                continue
            if trade.status is TradeStatus.OPENING:
                if trade.qty > 0:
                    self._finish_open(trade.id)
                else:
                    self.journal.set_status(trade.id, TradeStatus.CANCELED, "entry order never filled")
            elif trade.status is TradeStatus.CLOSING and trade.qty > 0:
                self.journal.set_status(trade.id, TradeStatus.OPEN)

    def reconcile(self, now: datetime, today: date, report: CycleReport | None = None) -> None:
        try:
            positions = self.broker.get_positions()
        except BrokerError as exc:
            logger.warning("positions unavailable: %s", exc)
            return
        held = {p.symbol: p.qty for p in positions if p.asset_class == "us_option" or is_occ(p.symbol)}
        active = self.journal.active_trades()
        expected: dict[str, int] = defaultdict(int)
        for trade in active:
            if trade.qty > 0:
                for leg in trade.legs:
                    expected[leg.symbol] += leg.sign * trade.qty
        mismatched = {s for s in set(expected) | set(held) if expected.get(s, 0) != held.get(s, 0)}

        for trade in active:
            if trade.status is not TradeStatus.OPEN or trade.qty <= 0:
                continue
            symbols = [leg.symbol for leg in trade.legs]
            if all(held.get(s, 0) == 0 for s in symbols):
                self._settle_vanished(trade, now, today, report)
                mismatched -= set(symbols)
            elif any(s in mismatched for s in symbols):
                # a short leg that shrank at the broker without the bot closing it was most likely assigned
                assigned = any(leg.side is Side.SELL and held.get(leg.symbol, 0) > expected[leg.symbol] for leg in trade.legs)
                what = "a short leg is gone: possible assignment" if assigned else "broker positions don't match the journal"
                msg = f"trade {trade.id} ({trade.underlying} {trade.strategy.value}): {what}. Needs manual review (`optionsbot resolve {trade.id}`)"
                self.journal.set_status(trade.id, TradeStatus.NEEDS_ATTENTION, msg)
                if assigned:
                    self.risk.halt(msg)
                self._say(report, logging.CRITICAL if assigned else logging.ERROR, msg, notify=True)

        unmanaged = {s for s in mismatched if expected.get(s, 0) == 0 and held.get(s, 0) != 0}
        underlyings = set()
        for symbol in unmanaged:
            try:
                underlyings.add(parse_occ(symbol).underlying)
            except ValueError:
                continue
        if underlyings - self._unmanaged:
            self._say(
                report,
                logging.WARNING,
                f"option positions not opened by the bot on {sorted(underlyings)}: leaving them alone and not trading those underlyings",
            )
        self._unmanaged = underlyings

    def _settle_vanished(self, trade: TradeRecord, now: datetime, today: date, report: CycleReport | None) -> None:
        """Legs are gone at the broker: expired, assigned or closed by hand."""
        value = None
        if trade.expiration < today:
            try:
                bars = self.bars(trade.underlying, today)
                close = float(bars.loc[trade.expiration, "close"]) if trade.expiration in bars.index else None
            except BrokerError:
                close = None
            if close is not None:
                value = float(payoff_at_expiry(trade.legs, np.array([close]))[0])
        if value is None:
            try:
                value = combo_value(trade.legs, self.broker.get_option_quotes([leg.symbol for leg in trade.legs]), "mid")
            except BrokerError:
                value = None
        estimated = value if value is not None else trade.entry_value or 0.0
        updated = self.journal.apply_exit_fill(trade.id, trade.qty, estimated, now, ExitReason.EXTERNAL.value)
        self.journal.log_event("WARNING", "external_close", f"trade {trade.id} legs no longer held; P&L estimated", {"value": estimated})
        self._say(
            report,
            logging.WARNING,
            f"trade {trade.id} {trade.underlying} {trade.strategy.value} closed outside the bot (expired/assigned/manual); estimated P&L {updated.realized_pnl:+,.0f}",
            notify=True,
        )

    # ------------------------------------------------------------------ exits
    def manage(self, now: datetime, today: date, report: CycleReport | None = None, account: Account | None = None) -> None:
        trades = [t for t in self.journal.active_trades() if t.status is TradeStatus.OPEN and t.qty > 0]
        self.portfolio_beta_delta = 0.0
        if not trades:
            return
        try:
            quotes = self.broker.get_option_quotes([leg.symbol for t in trades for leg in t.legs])
        except BrokerError as exc:
            self._say(report, logging.WARNING, f"cannot manage positions, quotes unavailable: {exc}")
            return
        for trade in trades:
            try:
                spot = self.spot(trade.underlying)
            except BrokerError:
                spot = None
            for leg in trade.legs:
                q = quotes.get(leg.symbol)
                if q is not None and q.delta is not None and spot is not None:
                    self.portfolio_beta_delta += leg.sign * q.delta * 100 * trade.qty * spot * trade.beta
            _, earnings = self.earnings_date(trade.underlying, today)
            signal_ = check_exit(trade, quotes, spot, today, self.config.management, earnings)
            if signal_ is None:
                continue
            if not signal_.urgent and self._would_day_trade(trade, today, account):
                logger.info("trade %s: %s, but closing today would be a day trade on a sub-$25k account; waiting", trade.id, signal_.reason.value)
                continue
            self.close_trade(trade, signal_, now, report)

    def _would_day_trade(self, trade: TradeRecord, today: date, account: Account | None) -> bool:
        """Under the pattern-day-trader rule, accounts below the threshold get restricted after
        four day trades in five days. Non-urgent exits on a position opened today wait a day."""
        threshold = self.config.management.pdt_equity_threshold
        if account is None or account.equity >= threshold or trade.entry_time is None:
            return False
        return et_date(trade.entry_time) == today

    def close_trade(self, trade: TradeRecord, signal_: ExitSignal, now: datetime, report: CycleReport | None = None) -> None:
        label = f"trade {trade.id} {trade.underlying} {trade.strategy.value}"
        if self.config.dry_run:
            self._say(report, logging.INFO, f"DRY RUN: would close {label}: {signal_.reason.value} ({signal_.detail})")
            return
        ex = self.config.execution
        self.journal.set_status(trade.id, TradeStatus.CLOSING, signal_.reason.value)
        result = self.executor.work_order(
            trade.id,
            "close",
            closing_legs(trade.legs),
            trade.qty,
            steps=ex.exit_steps,
            max_concession=ex.urgent_exit_max_concession if signal_.urgent else ex.exit_max_concession,
            on_fill=lambda qty, price: self.journal.apply_exit_fill(trade.id, qty, -price, now, signal_.reason.value),
        )
        updated = self.journal.get_trade(trade.id)
        if updated.status is TradeStatus.CLOSED:
            msg = f"closed {label} ({signal_.reason.value}: {signal_.detail}); P&L {updated.realized_pnl:+,.2f}"
            if report is not None:
                report.exits.append(msg)
            self.journal.log_event("INFO", "exit", msg, {"trade": trade.id})
            self._say(report, logging.INFO, msg, notify=True)
            return
        if result.status == "pending":
            return
        self.journal.set_status(trade.id, TradeStatus.OPEN)
        level = logging.ERROR if result.status in ("rejected", "error") else logging.WARNING
        self._say(
            report,
            level,
            f"could not fully close {label} ({signal_.reason.value}): {result.status} {result.message}; {updated.qty} contract(s) remain, retrying next cycle",
            notify=signal_.urgent,
        )

    def close_all(self, reason: str = "manual") -> list[str]:
        now = self._now()
        out = []
        for trade in self.journal.active_trades():
            if trade.status is TradeStatus.OPEN and trade.qty > 0:
                self.close_trade(trade, ExitSignal(ExitReason.MANUAL, True, reason), now)
                out.append(f"trade {trade.id}: {self.journal.get_trade(trade.id).status.value}")
        return out

    # ------------------------------------------------------------------ entries
    def scan(self, now: datetime, today: date, account: Account, regime: MarketRegime) -> list[SymbolScan]:
        active = self.journal.active_trades()
        out: list[SymbolScan] = []
        for symbol in self.config.watchlist:
            scan = SymbolScan(symbol)
            out.append(scan)
            if symbol in self._unmanaged:
                scan.notes.append("has option positions the bot didn't open")
                continue
            if reason := self.risk.symbol_block_reason(symbol, active, today):
                scan.notes.append(reason)
                continue
            known, earnings = self.earnings_date(symbol, today)
            if not known and not self.config.allow_unknown_earnings:
                scan.notes.append("next earnings date unknown: skipping (add an earnings source or the no_earnings_symbols list)")
                continue
            try:
                view = self.analyze(symbol, now, today)
            except BrokerError as exc:
                scan.notes.append(f"data unavailable: {exc}")
                continue
            scan.view = view
            scan.notes.extend(view.notes)
            playbook = choose_playbook(view, regime, self.config.strategy)
            scan.notes.extend(playbook.reasons)
            if not playbook.strategies:
                continue
            budget = account.equity * self.config.risk.max_risk_per_trade_pct * regime.size_multiplier
            ctx = BuildContext(self.chain(symbol, today), view, now, today, self.config, budget, earnings)
            candidates, notes = build_candidates(ctx, playbook.strategies)
            scan.notes.extend(notes)
            scan.candidates = candidates
            if candidates:
                decision = self.risk.size(candidates[0], account, active, regime.size_multiplier, view.beta, self.portfolio_beta_delta)
                scan.contracts = decision.contracts
                scan.sizing = decision.limits
        return out

    def enter(self, now: datetime, today: date, account: Account, regime: MarketRegime, report: CycleReport) -> None:
        scans = self.scan(now, today, account, regime)
        report.scans = scans
        ranked = sorted((s for s in scans if s.chosen is not None and s.contracts > 0), key=lambda s: s.chosen.score, reverse=True)
        for scan in ranked:
            active = self.journal.active_trades()
            if reason := self.risk.entry_block_reason(account, today, active):
                self._say(report, logging.INFO, f"stopping entries: {reason}")
                break
            if reason := self.risk.symbol_block_reason(scan.symbol, active, today):
                continue
            candidate = scan.chosen
            decision = self.risk.size(candidate, account, active, regime.size_multiplier, scan.view.beta, self.portfolio_beta_delta)
            if decision.contracts <= 0:
                continue
            opened = self.open_trade(candidate, decision.contracts, scan, regime, now, today, report)
            if opened:
                self.portfolio_beta_delta += candidate.net_delta * candidate.spot * scan.view.beta * opened
                account = self.broker.get_account()

    def _rationale(self, candidate: Candidate, scan: SymbolScan, regime: MarketRegime, contracts: int) -> dict:
        view = scan.view
        return {
            "summary": candidate.describe(),
            "view": view.summary() if view else None,
            "market_regime": f"{regime.regime.value}: {'; '.join(regime.reasons)}",
            "why": scan.notes,
            "pop": round(candidate.pop, 3),
            "pop_forecast": round(candidate.pop_forecast, 3),
            "edge_per_contract": round(candidate.expected_value, 2),
            "max_profit_per_contract": round(candidate.max_profit, 2),
            "max_loss_per_contract": round(candidate.max_loss, 2),
            "breakevens": candidate.breakevens,
            "net_delta": round(candidate.net_delta, 1),
            "net_theta": round(candidate.net_theta, 2),
            "net_vega": round(candidate.net_vega, 2),
            "contracts": contracts,
            "sizing": scan.sizing,
        }

    def _entry_bound(self, candidate: Candidate) -> float:
        """The worst price (paid per share) the strategy rules accept for this entry."""
        s = self.config.strategy
        bound = candidate.mid_value + self.config.execution.entry_max_concession * (candidate.natural_value - candidate.mid_value)
        if candidate.strategy.is_credit:
            ratio = s.min_condor_credit_to_width if candidate.strategy is StrategyKind.IRON_CONDOR else s.min_credit_to_width
            bound = min(bound, -ratio * candidate.width)
        else:
            bound = min(bound, s.max_debit_to_width * candidate.width)
        return bound

    def open_trade(
        self, candidate: Candidate, contracts: int, scan: SymbolScan, regime: MarketRegime, now: datetime, today: date, report: CycleReport
    ) -> int:
        rationale = self._rationale(candidate, scan, regime, contracts)
        summary = f"{contracts}x {candidate.describe()} | POP {candidate.pop:.0%}, edge ${candidate.expected_value:+.0f}/contract, max loss ${candidate.max_loss:,.0f}/contract"
        if self.config.dry_run:
            self._say(report, logging.INFO, f"DRY RUN: would open {summary}")
            report.entries.append(f"(dry run) {summary}")
            return 0
        beta = scan.view.beta if scan.view else 1.0
        trade_id = self.journal.create_trade(candidate, contracts, today, beta, rationale)
        ex = self.config.execution
        result = self.executor.work_order(
            trade_id,
            "open",
            opening_legs(candidate.legs),
            contracts,
            steps=ex.entry_steps,
            max_concession=ex.entry_max_concession,
            on_fill=lambda qty, price: self.journal.apply_entry_fill(trade_id, qty, price, now),
            worst_price=self._entry_bound(candidate),
        )
        trade = self.journal.get_trade(trade_id)
        if result.status == "pending":
            self._say(report, logging.WARNING, f"entry order for trade {trade_id} still working; will settle next cycle")
            return 0
        if trade.qty <= 0:
            self.journal.set_status(trade_id, TradeStatus.CANCELED, f"not filled: {result.status} {result.message}".strip())
            self._say(report, logging.INFO, f"no fill for {candidate.describe()} ({result.status} {result.message})".strip())
            return 0
        self._finish_open(trade_id)
        trade = self.journal.get_trade(trade_id)
        msg = f"opened trade {trade_id}: {trade.qty}x {candidate.describe()} filled at {trade.entry_value:+.2f}/share (max loss ${trade.max_loss * trade.qty:,.0f})"
        report.entries.append(msg)
        self.journal.log_event("INFO", "entry", msg, rationale)
        self._say(report, logging.INFO, msg, notify=True)
        return trade.qty

    def _finish_open(self, trade_id: int) -> None:
        """Mark a filled entry open and re-measure its risk at the actual fill price."""
        trade = self.journal.get_trade(trade_id)
        if trade.entry_value is not None:
            lo, hi, _ = payoff_extremes(trade.legs)
            self.journal.set_risk(trade_id, (hi - trade.entry_value) * 100, (trade.entry_value - lo) * 100)
        self.journal.set_status(trade_id, TradeStatus.OPEN)

    # ------------------------------------------------------------------ main loop
    def request_stop(self, *_: object) -> None:
        self._stop = True

    def run_forever(self) -> None:
        def handler(signum: int, frame: FrameType | None) -> None:
            logger.info("signal %s received: finishing the current cycle and stopping", signum)
            self.request_stop()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        self.notifier.send(
            f"started in {self.config.mode}{' dry-run' if self.config.dry_run else ''} mode, watching {', '.join(self.config.watchlist)}"
        )
        failures = 0
        while not self._stop:
            delay = float(self.config.schedule.cycle_seconds)
            try:
                report = self.run_cycle()
                failures = 0
                if not report.market_open:
                    clock = self.broker.get_clock()
                    delay = min(3600.0, max(60.0, (clock.next_open - clock.timestamp).total_seconds() - 60))
                    logger.info("market closed; next open %s, sleeping %.0f s", clock.next_open.isoformat(), delay)
                else:
                    logger.info(
                        "cycle done: %d exit(s), %d entr(y/ies)%s",
                        len(report.exits),
                        len(report.entries),
                        f"; entries blocked: {report.block_reason}" if report.block_reason else "",
                    )
            except BrokerError as exc:
                failures += 1
                delay = min(900.0, 30.0 * 2 ** min(failures, 5))
                logger.error("cycle failed (%s); retrying in %.0f s", exc, delay)
                if failures == 3:
                    self.notifier.send(f"three consecutive cycle failures: {exc}")
            end = time.monotonic() + delay
            while not self._stop and time.monotonic() < end:
                time.sleep(min(5.0, end - time.monotonic()))
        self.notifier.send("stopped")
