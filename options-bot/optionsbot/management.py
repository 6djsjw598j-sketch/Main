"""Exit rules for open trades - the part of trading most beginners skip.

Credit spreads / iron condors:
  * take profit at ``credit_profit_target_pct`` of the maximum profit (default 50%);
  * stop out when the loss reaches ``credit_stop_loss_multiple`` x the credit (default 2x);
  * optionally close when the underlying trades through a short strike;
  * close at ``credit_time_exit_dte`` days to expiry (default 21) to avoid gamma risk.
Debit spreads:
  * take profit at ``debit_profit_target_pct`` of the maximum profit (default 50%);
  * stop out after losing ``debit_stop_loss_pct`` of the debit (default 50%);
  * close at ``debit_time_exit_dte`` days to expiry (default 14).
Always:
  * close an in-the-money short leg with almost no time value left (early-assignment risk,
    e.g. ahead of a dividend);
  * close before an earnings report that falls before expiration;
  * never hold into expiration day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from optionsbot.config import ManagementConfig
from optionsbot.journal import TradeRecord
from optionsbot.models import OptionQuote, OptionType, Side, combo_value
from optionsbot.timeutil import dte


class ExitReason(StrEnum):
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    SHORT_STRIKE_BREACHED = "short_strike_breached"
    TIME_EXIT = "time_exit"
    ASSIGNMENT_RISK = "assignment_risk"
    EARNINGS = "earnings_ahead"
    EXPIRATION = "expiration_day"
    MANUAL = "manual"
    EXTERNAL = "closed_outside_bot"


@dataclass
class ExitSignal:
    reason: ExitReason
    urgent: bool
    detail: str


def check_exit(
    trade: TradeRecord,
    quotes: dict[str, OptionQuote],
    spot: float | None,
    today: date,
    cfg: ManagementConfig,
    earnings_date: date | None = None,
) -> ExitSignal | None:
    if trade.entry_value is None or trade.qty <= 0:
        return None
    days = dte(trade.expiration, today)
    if days <= 0:
        return ExitSignal(ExitReason.EXPIRATION, True, "expiration day: never hold into the close")
    if earnings_date is not None and earnings_date <= trade.expiration and (earnings_date - today).days <= cfg.close_before_earnings_days:
        return ExitSignal(ExitReason.EARNINGS, True, f"earnings on {earnings_date.isoformat()} before expiration")

    value = combo_value(trade.legs, quotes, "mid")
    if value is None:
        return None
    pnl = value - trade.entry_value  # per share
    max_profit = trade.max_profit / 100

    if spot is not None:
        for leg in trade.legs:
            if leg.side is not Side.SELL or not leg.contract.is_itm(spot):
                continue
            q = quotes[leg.symbol]
            extrinsic = q.mid - leg.contract.intrinsic(spot)
            if extrinsic < cfg.min_extrinsic_itm_short:
                return ExitSignal(
                    ExitReason.ASSIGNMENT_RISK,
                    True,
                    f"short {leg.contract.strike:g}{leg.contract.type.value[0].upper()} is in the money with {extrinsic:.2f} of time value left",
                )

    if trade.is_credit:
        credit = -trade.entry_value
        if pnl <= -cfg.credit_stop_loss_multiple * credit:
            return ExitSignal(ExitReason.STOP_LOSS, True, f"loss {-pnl:.2f}/share >= {cfg.credit_stop_loss_multiple:g}x the {credit:.2f} credit")
        if cfg.close_on_short_strike_breach and spot is not None:
            for leg in trade.legs:
                if leg.side is Side.SELL and leg.contract.is_itm(spot):
                    side = "below" if leg.contract.type is OptionType.PUT else "above"
                    return ExitSignal(ExitReason.SHORT_STRIKE_BREACHED, True, f"price {spot:.2f} is {side} the short {leg.contract.strike:g} strike")
        if pnl >= cfg.credit_profit_target_pct * max_profit:
            return ExitSignal(ExitReason.PROFIT_TARGET, False, f"captured {pnl / max_profit:.0%} of max profit")
        if days <= cfg.credit_time_exit_dte:
            return ExitSignal(ExitReason.TIME_EXIT, False, f"{days} DTE <= {cfg.credit_time_exit_dte}: closing before gamma risk grows")
    else:
        debit = trade.entry_value
        if pnl <= -cfg.debit_stop_loss_pct * debit:
            return ExitSignal(ExitReason.STOP_LOSS, True, f"lost {-pnl / debit:.0%} of the {debit:.2f} debit")
        if pnl >= cfg.debit_profit_target_pct * max_profit:
            return ExitSignal(ExitReason.PROFIT_TARGET, False, f"captured {pnl / max_profit:.0%} of max profit")
        if days <= cfg.debit_time_exit_dte:
            return ExitSignal(ExitReason.TIME_EXIT, False, f"{days} DTE <= {cfg.debit_time_exit_dte}: closing before time decay accelerates")
    return None
