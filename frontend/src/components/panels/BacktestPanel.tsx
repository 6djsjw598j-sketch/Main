import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { useBacktest, type BacktestSettings } from "../../api/hooks";
import type { AssetType, BacktestResponse, Timeframe } from "../../api/types";
import { formatNumber, formatPct, formatPrice } from "../../lib/format";
import { formatBarTime } from "../../lib/time";
import { ErrorState } from "../common/ErrorState";
import { Button, Details, Spinner, Stat } from "../common/ui";

function EquityChart({ data, theme }: { data: BacktestResponse; theme: "dark" | "light" }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const dark = theme === "dark";
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: dark ? "#131722" : "#ffffff" }, textColor: dark ? "#b2b5be" : "#3b4252" },
      grid: { vertLines: { visible: false }, horzLines: { color: dark ? "#1c2130" : "#eef1f6" } },
      timeScale: { borderVisible: false },
      rightPriceScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    });
    const toData = (pts: { time: number; price: number }[]) => pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.price }));
    chart.addSeries(LineSeries, { color: "#3d74ff", lineWidth: 2, title: "Strategy", priceLineVisible: false }).setData(toData(data.equity_curve));
    chart.addSeries(LineSeries, { color: "#8b93a7", lineWidth: 1, title: "Buy & hold", priceLineVisible: false }).setData(toData(data.benchmark_curve));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, theme]);
  return <div ref={ref} className="h-48 w-full" aria-label="Equity curve versus buy and hold" role="img" />;
}

export function BacktestPanel({ symbol, assetType, timeframe, theme, zone }: {
  symbol: string;
  assetType: AssetType;
  timeframe: Timeframe;
  theme: "dark" | "light";
  zone: string | undefined;
}) {
  const [settings, setSettings] = useState<BacktestSettings>({
    entry_threshold: 25, exit_threshold: 0, commission_bps: 10, slippage_bps: 5, direction: "long_only", limit: 1000,
  });
  const [draft, setDraft] = useState(settings);
  const [run, setRun] = useState(false);
  const query = useBacktest({ symbol, assetType, timeframe }, settings, run);
  const data = query.data;
  const field = (key: keyof BacktestSettings, label: string, min: number, max: number) => (
    <label className="flex flex-col gap-0.5 text-[11px] text-muted">
      {label}
      <input
        type="number"
        min={min}
        max={max}
        className="tabular w-24 rounded border border-line bg-surface-2 px-2 py-1 text-xs text-fg"
        value={draft[key] as number}
        onChange={(e) => setDraft((d) => ({ ...d, [key]: Number(e.target.value) }))}
      />
    </label>
  );

  return (
    <div className="space-y-3 p-3">
      <p className="text-xs text-muted">
        Tests the factor score as a rule: enter when the score crosses the entry threshold at a bar's close, fill at the next bar's open,
        exit when it falls to the exit threshold. Costs and slippage are charged on every fill. No look-ahead: each bar's score only uses data up to that bar.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        {field("entry_threshold", "Entry score ≥", 5, 100)}
        {field("exit_threshold", "Exit score ≤", -100, 100)}
        {field("commission_bps", "Commission (bps/side)", 0, 200)}
        {field("slippage_bps", "Slippage (bps/side)", 0, 200)}
        {field("limit", "Bars", 300, 5000)}
        <label className="flex flex-col gap-0.5 text-[11px] text-muted">
          Direction
          <select
            className="rounded border border-line bg-surface-2 px-2 py-1 text-xs text-fg"
            value={draft.direction}
            onChange={(e) => setDraft((d) => ({ ...d, direction: e.target.value as BacktestSettings["direction"] }))}
          >
            <option value="long_only">Long only</option>
            <option value="long_short">Long & short</option>
          </select>
        </label>
        <Button active onClick={() => { setSettings(draft); setRun(true); }}>
          {query.isFetching ? <Spinner label="Running backtest" /> : "Run backtest"}
        </Button>
      </div>
      {!run && <p className="text-xs text-muted">Run the backtest to see historical results for {symbol} on {timeframe} bars.</p>}
      {query.error ? <ErrorState error={query.error} onRetry={() => query.refetch()} compact /> : null}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 rounded border border-line p-2 sm:grid-cols-4 lg:grid-cols-6">
            <Stat label="Trades" value={data.metrics.trades} />
            <Stat label="Win rate" value={data.metrics.win_rate === null ? "n/a" : `${data.metrics.win_rate.toFixed(1)}%`} />
            <Stat label="Strategy return" value={formatPct(data.metrics.total_return_pct)} tone={data.metrics.total_return_pct >= 0 ? "up" : "down"} />
            <Stat label="Buy & hold" value={formatPct(data.metrics.buy_and_hold_return_pct)} tone={data.metrics.buy_and_hold_return_pct >= 0 ? "up" : "down"} />
            <Stat label="Max drawdown" value={formatPct(data.metrics.max_drawdown_pct)} tone="down" />
            <Stat label="Exposure" value={`${data.metrics.exposure_pct.toFixed(0)}%`} />
            <Stat label="Avg trade" value={formatPct(data.metrics.avg_return_pct)} />
            <Stat label="Median trade" value={formatPct(data.metrics.median_return_pct)} />
            <Stat label="Best / worst" value={`${formatPct(data.metrics.best_return_pct, 1)} / ${formatPct(data.metrics.worst_return_pct, 1)}`} />
            <Stat label="Profit factor" value={formatNumber(data.metrics.profit_factor)} />
            <Stat label="Avg bars held" value={formatNumber(data.metrics.avg_bars_held, 1)} />
            <Stat label="Bars tested" value={data.assumptions.bars_evaluated} />
          </div>
          <EquityChart data={data} theme={theme} />
          <Details summary={`Trades (${data.trades.length})`}>
            <div className="scroll-thin max-h-64 overflow-auto">
              <table className="tabular w-full min-w-[520px] text-[11px]">
                <thead>
                  <tr className="text-left">
                    <th className="font-normal">Side</th><th className="font-normal">Entry</th><th className="text-right font-normal">Price</th>
                    <th className="font-normal">Exit</th><th className="text-right font-normal">Price</th><th className="text-right font-normal">Return</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trades.slice().reverse().map((t) => (
                    <tr key={`${t.entry_time}-${t.direction}`} className="border-t border-line text-fg">
                      <td>{t.direction}{t.open ? " (open)" : ""}</td>
                      <td>{formatBarTime(t.entry_time, timeframe, zone)}</td>
                      <td className="text-right">{formatPrice(t.entry_price)}</td>
                      <td>{formatBarTime(t.exit_time, timeframe, zone)}</td>
                      <td className="text-right">{formatPrice(t.exit_price)}</td>
                      <td className={`text-right ${t.return_pct >= 0 ? "text-up" : "text-down"}`}>{formatPct(t.return_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Details>
          <div className="rounded bg-warn-soft p-2 text-[11px] text-warn">
            <p className="font-semibold">Caveats</p>
            <ul className="list-inside list-disc">{data.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
            <p className="mt-1">{data.assumptions.execution}</p>
          </div>
        </>
      )}
    </div>
  );
}
