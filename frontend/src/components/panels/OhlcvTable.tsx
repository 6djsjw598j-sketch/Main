import type { CandlesResponse } from "../../api/types";
import { formatCompact, formatPct, formatPrice } from "../../lib/format";
import { formatBarTime } from "../../lib/time";

export function OhlcvTable({ data, zone }: { data?: CandlesResponse; zone: string | undefined }) {
  if (!data) return <p className="p-3 text-xs text-muted">No candle data loaded.</p>;
  const rows = data.candles.slice(-150).reverse();
  const csv = () => {
    const header = "time_utc,open,high,low,close,volume\n";
    const body = data.candles
      .map((c) => `${new Date(c.time * 1000).toISOString()},${c.open},${c.high},${c.low},${c.close},${c.volume ?? ""}`)
      .join("\n");
    const url = URL.createObjectURL(new Blob([header + body], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${data.symbol}_${data.timeframe}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="p-3">
      <div className="mb-2 flex items-center justify-between text-[11px] text-muted">
        <span>Latest {rows.length} of {data.count} {data.timeframe} bars (newest first). Source: {data.provider}.</span>
        <button onClick={csv} className="rounded border border-line px-2 py-0.5 hover:bg-surface-3">Download CSV</button>
      </div>
      <div className="scroll-thin max-h-96 overflow-auto">
        <table className="tabular w-full min-w-[560px] text-xs">
          <thead className="sticky top-0 bg-surface">
            <tr className="text-left text-muted">
              <th className="py-1 font-normal">Time</th>
              <th className="py-1 text-right font-normal">Open</th>
              <th className="py-1 text-right font-normal">High</th>
              <th className="py-1 text-right font-normal">Low</th>
              <th className="py-1 text-right font-normal">Close</th>
              <th className="py-1 text-right font-normal">Change</th>
              <th className="py-1 text-right font-normal">Volume</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c, i) => {
              const prev = rows[i + 1];
              const change = prev ? ((c.close - prev.close) / prev.close) * 100 : null;
              return (
                <tr key={c.time} className="border-t border-line">
                  <td className="py-1">{formatBarTime(c.time, data.timeframe, zone)}{i === 0 && !data.last_bar_complete ? " *" : ""}</td>
                  <td className="py-1 text-right">{formatPrice(c.open)}</td>
                  <td className="py-1 text-right">{formatPrice(c.high)}</td>
                  <td className="py-1 text-right">{formatPrice(c.low)}</td>
                  <td className="py-1 text-right">{formatPrice(c.close)}</td>
                  <td className={`py-1 text-right ${change === null ? "" : change >= 0 ? "text-up" : "text-down"}`}>{formatPct(change)}</td>
                  <td className="py-1 text-right">{c.volume === null ? "n/a" : formatCompact(c.volume)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!data.last_bar_complete && <p className="mt-1 text-[11px] text-muted">* bar still forming</p>}
    </div>
  );
}
