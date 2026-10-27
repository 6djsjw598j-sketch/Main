"""Position sizing and the circuit breakers that stop the bot from digging a hole.

Sizing: risk a fixed fraction of equity per trade (the spread's max loss, plus the slippage
we might concede), capped by total open risk, buying power, contract limits and net
beta-weighted delta.

Breakers that block *new* entries (exits always keep running):
  * manual halt (``optionsbot halt``) or the kill-switch file;
  * drawdown from the equity high-water mark beyond ``max_drawdown_pct`` (needs ``resume``);
  * daily loss beyond ``daily_loss_limit_pct`` (resets the next trading day);
  * a streak of ``max_consecutive_losses`` losing trades (cool-down period);
  * too many open trades or too many new trades today.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from optionsbot.broker.base import Account
from optionsbot.config import BotConfig
from optionsbot.journal import Journal, TradeRecord, TradeStatus
from optionsbot.models import Candidate

HALT_KEY = "halt"
HWM_KEY = "high_water_mark"
DAILY_PAUSE_KEY = "daily_loss_pause"


@dataclass
class SizeDecision:
    contracts: int
    risk_per_contract: float
    limits: list[str]


class RiskManager:
    def __init__(self, config: BotConfig, journal: Journal) -> None:
        self.config = config
        self.journal = journal

    # ------------------------------------------------------------------ breakers
    def halt(self, reason: str) -> None:
        self.journal.set_state(HALT_KEY, {"halted": True, "reason": reason})
        self.journal.log_event("CRITICAL", "halt", reason)

    def resume(self) -> None:
        self.journal.set_state(HALT_KEY, {"halted": False, "reason": ""})
        self.journal.log_event("INFO", "resume", "trading resumed manually")

    def halted(self) -> str | None:
        state = self.journal.get_state(HALT_KEY, {}) or {}
        if not state.get("halted"):
            return None
        return state.get("reason") or "halted"

    def update(self, account: Account, today: date) -> list[str]:
        """Track the high-water mark and trip breakers. Returns messages for newly tripped breakers."""
        messages: list[str] = []
        risk = self.config.risk
        hwm = max(float(self.journal.get_state(HWM_KEY, 0.0) or 0.0), account.equity)
        self.journal.set_state(HWM_KEY, hwm)
        if hwm > 0 and account.equity <= hwm * (1 - risk.max_drawdown_pct) and not self.halted():
            msg = f"drawdown {1 - account.equity / hwm:.1%} from high-water mark {hwm:,.0f} exceeds {risk.max_drawdown_pct:.0%}: new entries halted until `optionsbot resume`"
            self.halt(msg)
            messages.append(msg)
        if account.last_equity > 0 and account.equity <= account.last_equity * (1 - risk.daily_loss_limit_pct):
            paused = self.journal.get_state(DAILY_PAUSE_KEY)
            if paused != today.isoformat():
                self.journal.set_state(DAILY_PAUSE_KEY, today.isoformat())
                msg = f"down {1 - account.equity / account.last_equity:.1%} today (limit {risk.daily_loss_limit_pct:.0%}): no new entries until tomorrow"
                self.journal.log_event("WARNING", "daily_loss_limit", msg)
                messages.append(msg)
        return messages

    def loss_streak_block(self, today: date) -> str | None:
        n = self.config.risk.max_consecutive_losses
        recent = self.journal.closed_trades(limit=n)
        if len(recent) < n or any(t.realized_pnl >= 0 for t in recent):
            return None
        last_loss = max((t.exit_time.date() for t in recent if t.exit_time), default=today)
        until = last_loss + timedelta(days=self.config.risk.loss_streak_cooldown_days)
        if today < until:
            return f"{n} losing trades in a row: cooling off until {until.isoformat()}"
        return None

    def entry_block_reason(self, account: Account, today: date, active: list[TradeRecord]) -> str | None:
        risk = self.config.risk
        if reason := self.halted():
            return f"halted: {reason}"
        if self.config.kill_switch_file.exists():
            return f"kill-switch file {self.config.kill_switch_file} exists"
        if account.trading_blocked:
            return "the broker reports trading is blocked on this account"
        if self.journal.get_state(DAILY_PAUSE_KEY) == today.isoformat():
            return "daily loss limit reached"
        if reason := self.loss_streak_block(today):
            return reason
        if len(active) >= risk.max_open_trades:
            return f"{len(active)} open trades (max {risk.max_open_trades})"
        if self.journal.trades_opened_on(today) >= risk.max_new_trades_per_day:
            return f"already opened {risk.max_new_trades_per_day} trades today"
        return None

    def symbol_block_reason(self, symbol: str, active: list[TradeRecord], today: date) -> str | None:
        risk = self.config.risk
        same = [t for t in active if t.underlying == symbol]
        if len(same) >= risk.max_trades_per_underlying:
            return f"already {len(same)} open trade(s) on {symbol}"
        if any(t.status is TradeStatus.NEEDS_ATTENTION for t in same):
            return f"a {symbol} trade needs manual attention"
        group = self.config.group_of(symbol)
        if group is not None:
            in_group = [t for t in active if self.config.group_of(t.underlying) == group]
            if len(in_group) >= risk.max_trades_per_group:
                return f"{len(in_group)} open trades in correlated group {group!r} (max {risk.max_trades_per_group})"
        for trade in self.journal.closed_trades(limit=50):
            if trade.underlying != symbol or trade.exit_time is None:
                continue
            if trade.realized_pnl < 0 and (today - trade.exit_time.date()).days < risk.reentry_cooldown_days_after_loss:
                return f"lost on {symbol} {(today - trade.exit_time.date()).days} day(s) ago: no revenge trades"
            break
        return None

    # ------------------------------------------------------------------ sizing
    def size(
        self,
        candidate: Candidate,
        account: Account,
        active: list[TradeRecord],
        size_multiplier: float,
        beta: float,
        portfolio_beta_delta: float,
    ) -> SizeDecision:
        risk = self.config.risk
        limits: list[str] = []
        slippage = max(0.0, candidate.natural_value - candidate.mid_value) * self.config.execution.entry_max_concession * 100
        per_contract = candidate.max_loss + slippage
        if per_contract <= 0:
            return SizeDecision(0, per_contract, ["non-positive risk per contract"])

        budget = account.equity * risk.max_risk_per_trade_pct * size_multiplier
        n = math.floor(budget / per_contract)
        limits.append(f"risk budget ${budget:,.0f} -> {n}")

        open_risk = sum(t.open_risk for t in active)
        remaining = account.equity * risk.max_portfolio_risk_pct - open_risk
        by_portfolio = max(0, math.floor(remaining / per_contract))
        if by_portfolio < n:
            limits.append(f"portfolio risk ${open_risk:,.0f} of ${account.equity * risk.max_portfolio_risk_pct:,.0f} used -> {by_portfolio}")
            n = by_portfolio

        usable_bp = account.options_buying_power - account.equity * risk.buying_power_reserve_pct
        by_bp = max(0, math.floor(usable_bp / per_contract))
        if by_bp < n:
            limits.append(f"buying power ${account.options_buying_power:,.0f} (keeping {risk.buying_power_reserve_pct:.0%} reserve) -> {by_bp}")
            n = by_bp

        if n > risk.max_contracts_per_trade:
            limits.append(f"max {risk.max_contracts_per_trade} contracts per trade")
            n = risk.max_contracts_per_trade

        per_delta = candidate.net_delta * candidate.spot * beta
        cap = risk.max_net_beta_delta_pct * account.equity
        before = n
        while n > 0:
            after = portfolio_beta_delta + n * per_delta
            if abs(after) <= cap or abs(after) <= abs(portfolio_beta_delta):
                break
            n -= 1
        if n < before:
            limits.append(f"net beta-weighted delta ${portfolio_beta_delta:,.0f} near the ${cap:,.0f} cap -> {n}")
        return SizeDecision(max(0, n), per_contract, limits)
