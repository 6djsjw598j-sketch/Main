"""Backtest: runs the real engine, day by day, against the simulated broker.

Read the simulator's docstring before trusting the numbers. Option prices are modelled
(Black-Scholes on an assumed IV), not historical quotes, so a backtest validates the
bot's *behaviour* - entries, sizing, exits, breakers - and shows sensitivity to the
volatility premium you assume; it does not prove the strategy makes money.
"""

from __future__ import annotations

import math
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from optionsbot.broker.simulated import SimSettings, SimulatedBroker
from optionsbot.config import BotConfig
from optionsbot.earnings import EarningsCalendar, FileEarningsCalendar, NoEarningsSymbols
from optionsbot.engine import TradingEngine
from optionsbot.execution import Executor
from optionsbot.journal import Journal, TradeRecord
from optionsbot.notify import Notifier
from optionsbot.timeutil import ET

CYCLE_TIMES = (time(10, 30), time(15, 45))
WARMUP_BARS = 260


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def monotonic(self) -> float:
        return self.t


@dataclass
class BacktestResult:
    start: date
    end: date
    starting_equity: float
    equity: pd.Series
    trades: list[TradeRecord]
    fees: float
    events: list[str] = field(default_factory=list)

    @property
    def ending_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.starting_equity

    def stats(self) -> dict:
        eq = self.equity
        closed = [t for t in self.trades if t.status.value == "closed"]
        wins = [t.realized_pnl for t in closed if t.realized_pnl > 0]
        losses = [t.realized_pnl for t in closed if t.realized_pnl <= 0]
        daily = eq.pct_change().dropna()
        drawdown = (eq / eq.cummax() - 1).min() if len(eq) else 0.0
        years = max(len(eq) / 252, 1e-9)
        total = self.ending_equity / self.starting_equity - 1
        by_strategy: dict[str, dict] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
        for t in closed:
            row = by_strategy[t.strategy.value]
            row["trades"] += 1
            row["pnl"] += t.realized_pnl
            row["wins"] += t.realized_pnl > 0
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "starting_equity": round(self.starting_equity, 2),
            "ending_equity": round(self.ending_equity, 2),
            "total_return": total,
            "cagr": (1 + total) ** (1 / years) - 1 if total > -1 else -1.0,
            "max_drawdown": float(drawdown),
            "sharpe": float(daily.mean() / daily.std() * math.sqrt(252)) if len(daily) > 1 and daily.std() > 0 else 0.0,
            "trades": len(closed),
            "open_at_end": len(self.trades) - len(closed) - sum(1 for t in self.trades if t.status.value == "canceled"),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "avg_win": sum(wins) / len(wins) if wins else 0.0,
            "avg_loss": sum(losses) / len(losses) if losses else 0.0,
            "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else math.inf if wins else 0.0,
            "fees": round(self.fees, 2),
            "by_strategy": dict(by_strategy),
            "exit_reasons": dict(Counter(t.exit_reason or "?" for t in closed)),
        }

    def report(self) -> str:
        s = self.stats()
        lines = [
            f"Backtest {s['start']} -> {s['end']} (simulated option prices; see docs)",
            f"  equity        {s['starting_equity']:>12,.2f} -> {s['ending_equity']:,.2f}",
            f"  total return  {s['total_return']:>12.2%}   CAGR {s['cagr']:.2%}",
            f"  max drawdown  {s['max_drawdown']:>12.2%}   Sharpe {s['sharpe']:.2f}",
            f"  trades        {s['trades']:>12d}   win rate {s['win_rate']:.1%}   open at end {s['open_at_end']}",
            f"  avg win/loss  {s['avg_win']:>12,.2f} / {s['avg_loss']:,.2f}   profit factor {s['profit_factor']:.2f}",
            f"  fees          {s['fees']:>12,.2f}",
            "  by strategy:",
        ]
        for name, row in sorted(s["by_strategy"].items()):
            lines.append(f"    {name:<26} {row['trades']:>4} trades  win {row['wins'] / row['trades']:.0%}  P&L {row['pnl']:+,.2f}")
        lines.append("  exit reasons: " + ", ".join(f"{k} {v}" for k, v in sorted(s["exit_reasons"].items())))
        return "\n".join(lines)


def run_backtest(
    bars: dict[str, pd.DataFrame],
    config: BotConfig,
    start: date | None = None,
    end: date | None = None,
    sim: SimSettings | None = None,
    earnings_file: Path | None = None,
) -> BacktestResult:
    bars = {s.upper(): df for s, df in bars.items()}
    if config.market_symbol not in bars:
        raise ValueError(f"backtests need bars for the market symbol {config.market_symbol}")
    watchlist = [s for s in config.watchlist if s in bars]
    if not watchlist:
        raise ValueError("none of the watchlist symbols have bars")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = config.model_copy(deep=True, update={"dry_run": False, "mode": "paper", "watchlist": watchlist, "kill_switch_file": Path(tmp) / "STOP"})
        broker = SimulatedBroker(bars, sim)
        journal = Journal(":memory:")
        sources: list = [NoEarningsSymbols(list(bars))] if earnings_file is None else [FileEarningsCalendar(earnings_file)]
        clock = _FakeClock()
        executor = Executor(broker, journal, cfg.execution, sleep=clock.sleep, monotonic=clock.monotonic)
        engine = TradingEngine(cfg, broker, journal, EarningsCalendar(sources), Notifier(None), executor)

        days = broker.trading_days()
        first = start or days[min(WARMUP_BARS, max(0, len(days) - 1))]
        last = end or days[-1]
        run_days = [d for d in days if first <= d <= last]
        if not run_days:
            raise ValueError("no trading days in the requested range")
        equity: dict[date, float] = {}
        starting = broker.equity()
        events: list[str] = []
        for day in run_days:
            for at in CYCLE_TIMES:
                broker.set_time(datetime.combine(day, at, tzinfo=ET))
                report = engine.run_cycle()
                events.extend(f"{day} {m}" for m in report.entries + report.exits)
            broker.end_of_day()
            equity[day] = broker.equity()
        result = BacktestResult(
            start=run_days[0],
            end=run_days[-1],
            starting_equity=starting,
            equity=pd.Series(equity),
            trades=journal.all_trades(),
            fees=broker.fees_paid,
            events=events,
        )
        journal.close()
        return result


def load_csv_bars(directory: Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """``<SYMBOL>.csv`` files with date,open,high,low,close[,volume] columns."""
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = directory / f"{symbol.upper()}.csv"
        if not path.exists():
            raise FileNotFoundError(f"missing {path}")
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if "volume" not in df:
            df["volume"] = 0.0
        out[symbol.upper()] = df.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
    return out
