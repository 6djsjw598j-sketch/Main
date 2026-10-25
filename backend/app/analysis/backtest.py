"""Historical performance of the factor-score signal.

Design choices that avoid look-ahead bias:

* The score at bar t uses only data up to the close of bar t (all indicators are causal).
* Signals are evaluated at the close of bar t and executed at the **open of bar t+1**.
* Only completed bars are used; a still-forming last bar is dropped.
* Evaluation starts once every non-volume factor has enough history, so the model does
  not change composition mid-test.

Costs: ``commission_bps`` per side and ``slippage_bps`` per side are applied to every
fill (buy fills higher, sell fills lower). Returns are compounded; no leverage; a
position is either fully in or out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.signals import SignalFrame
from app.errors import InsufficientDataError
from app.models.analysis import BacktestAssumptions, BacktestMetrics, BacktestTrade, TrendlinePoint

MIN_EVAL_BARS = 100
NON_VOLUME = ("price_vs_sma200", "sma50_vs_sma200", "price_vs_sma50", "ema_9_21", "adx_dmi", "ichimoku", "rsi", "macd", "stochastic", "roc")

CAVEATS = [
    "In-sample test on the same history the chart shows; parameters were not optimized out-of-sample.",
    "Past behaviour of a technical rule does not predict future results.",
    "Small trade counts make statistics unreliable; treat fewer than ~30 trades as anecdotal.",
    "Gaps, partial fills, borrow costs (for shorts), taxes and funding are not modelled.",
    "The score thresholds are user settings, not calibrated probabilities.",
]


def _downsample(times: list[int], values: np.ndarray, max_points: int = 300) -> list[TrendlinePoint]:
    n = len(times)
    step = max(1, int(np.ceil(n / max_points)))
    idx = list(range(0, n, step))
    if idx and idx[-1] != n - 1:
        idx.append(n - 1)
    return [TrendlinePoint(time=times[i], price=round(float(values[i]), 6)) for i in idx]


def run_backtest(
    frame: pd.DataFrame,
    sig: SignalFrame,
    *,
    last_bar_complete: bool,
    entry_threshold: float,
    exit_threshold: float,
    commission_bps: float,
    slippage_bps: float,
    direction: str = "long_only",
) -> tuple[BacktestAssumptions, BacktestMetrics, list[BacktestTrade], list[TrendlinePoint], list[TrendlinePoint]]:
    score, _ = sig.score()
    if not last_bar_complete:
        frame, score = frame.iloc[:-1], score.iloc[:-1]
    factors = sig.factors.loc[frame.index]
    active_keys = [k for k in NON_VOLUME if sig.weights.get(k, 0) > 0]
    ready = factors[active_keys].notna().all(axis=1) if active_keys else score.notna()
    if not ready.any():
        raise InsufficientDataError("Not enough history for the factor model (SMA 200 needs 200+ bars).")
    start = int(np.argmax(ready.to_numpy()))
    n = len(frame)
    if n - start < MIN_EVAL_BARS:
        raise InsufficientDataError(
            f"Only {n - start} bars are available after the indicator warm-up; at least {MIN_EVAL_BARS} are needed. "
            "Request more bars (limit) or use a shorter timeframe.",
            details={"available": max(0, n - start), "required": MIN_EVAL_BARS},
        )

    opens = frame["open"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    sc = score.to_numpy(dtype=float)
    times = [int(ts.timestamp()) for ts in frame.index]
    cost = (commission_bps + slippage_bps) / 10_000.0

    position = 0  # +1 long, -1 short, 0 flat
    entry_price = 0.0
    entry_i = 0
    trades: list[BacktestTrade] = []
    equity = np.ones(n)
    cash_equity = 1.0  # equity at last trade close
    in_market = 0

    def close_trade(i_exec: int, price: float, open_flag: bool = False) -> None:
        nonlocal cash_equity
        if position > 0:
            exit_fill = price * (1 - cost) if not open_flag else price
            ret = exit_fill / entry_price - 1.0
        else:
            exit_fill = price * (1 + cost) if not open_flag else price
            ret = entry_price / exit_fill - 1.0
        cash_equity *= 1.0 + ret
        trades.append(
            BacktestTrade(
                direction="long" if position > 0 else "short",
                entry_time=times[entry_i],
                entry_price=round(entry_price, 10),
                exit_time=times[i_exec],
                exit_price=round(exit_fill, 10),
                return_pct=round(ret * 100.0, 4),
                bars_held=i_exec - entry_i,
                open=open_flag,
            )
        )

    for t in range(start, n):
        # Mark-to-market at this bar's close.
        if position > 0:
            equity[t] = cash_equity * (closes[t] / entry_price)
            in_market += 1
        elif position < 0:
            equity[t] = cash_equity * (entry_price / closes[t])
            in_market += 1
        else:
            equity[t] = cash_equity
        if t == n - 1 or np.isnan(sc[t]):
            continue
        nxt = t + 1
        if (position > 0 and sc[t] <= exit_threshold) or (position < 0 and sc[t] >= -exit_threshold):
            close_trade(nxt, opens[nxt])
            position = 0
        if position == 0:
            if sc[t] >= entry_threshold:
                position, entry_i = 1, nxt
                entry_price = opens[nxt] * (1 + cost)
            elif direction == "long_short" and sc[t] <= -entry_threshold:
                position, entry_i = -1, nxt
                entry_price = opens[nxt] * (1 - cost)
    if position != 0:
        close_trade(n - 1, closes[-1], open_flag=True)
        equity[-1] = cash_equity

    eq = equity[start:]
    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min() * 100.0) if len(eq) else 0.0
    bench = closes[start:] / closes[start]
    returns = np.array([tr.return_pct for tr in trades])
    wins, losses = returns[returns > 0], returns[returns < 0]
    metrics = BacktestMetrics(
        trades=len(trades),
        win_rate=round(float(len(wins) / len(returns) * 100.0), 2) if len(returns) else None,
        avg_return_pct=round(float(returns.mean()), 4) if len(returns) else None,
        median_return_pct=round(float(np.median(returns)), 4) if len(returns) else None,
        best_return_pct=round(float(returns.max()), 4) if len(returns) else None,
        worst_return_pct=round(float(returns.min()), 4) if len(returns) else None,
        profit_factor=round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
        total_return_pct=round((float(eq[-1]) - 1.0) * 100.0, 4),
        buy_and_hold_return_pct=round((float(bench[-1]) - 1.0) * 100.0, 4),
        max_drawdown_pct=round(max_dd, 4),
        exposure_pct=round(in_market / len(eq) * 100.0, 2) if len(eq) else 0.0,
        avg_bars_held=round(float(np.mean([tr.bars_held for tr in trades])), 2) if trades else None,
    )
    assumptions = BacktestAssumptions(
        direction=direction,  # type: ignore[arg-type]
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        execution="Signal at bar close; fill at next bar open +/- slippage; commission per side; open trade marked at last close.",
        evaluation_start=times[start],
        evaluation_end=times[-1],
        bars_evaluated=n - start,
    )
    eval_times = times[start:]
    return assumptions, metrics, trades, _downsample(eval_times, eq), _downsample(eval_times, bench)
