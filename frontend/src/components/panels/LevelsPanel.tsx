import type { SupportResistanceResponse } from "../../api/types";
import { formatPct, formatPrice, pricePrecision } from "../../lib/format";
import { formatDateTime } from "../../lib/time";
import { ErrorState } from "../common/ErrorState";
import { Badge, Details, Skeleton, cx, directionTone } from "../common/ui";

export function LevelsPanel({ query }: { query: { data?: SupportResistanceResponse; error: unknown; isLoading: boolean; refetch: () => void } }) {
  const { data, error, isLoading } = query;
  if (isLoading && !data) return <Skeleton className="m-3 h-48" />;
  if (error && !data) return <ErrorState error={error} onRetry={query.refetch} className="m-3" />;
  if (!data) return null;
  const price = data.context.last_price;
  return (
    <div className="space-y-4 p-3">
      <div>
        <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
          Support / resistance zones <span className="normal-case">(algorithmic estimates)</span>
        </h3>
        {data.levels.length === 0 ? (
          <p className="text-xs text-muted">No swing-based zones were detected in the last {data.params.lookback} bars.</p>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="tabular w-full min-w-[640px] text-xs">
              <thead>
                <tr className="text-left text-muted">
                  <th className="py-1 font-normal">Zone</th>
                  <th className="py-1 font-normal">Type</th>
                  <th className="py-1 text-right font-normal">Range</th>
                  <th className="py-1 text-right font-normal">Distance</th>
                  <th className="py-1 text-right font-normal">Touches</th>
                  <th className="py-1 text-right font-normal">Reactions</th>
                  <th className="py-1 pl-4 font-normal">Strength</th>
                  <th className="py-1 font-normal">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.levels.map((lv) => (
                  <tr key={lv.id} className="border-t border-line" title={`${lv.status_detail} Score: ${Object.entries(lv.score_breakdown).map(([k, v]) => `${k} ${v}`).join(", ")}`}>
                    <td className="py-1">
                      {lv.id}
                      {(lv.id === data.nearest_support?.id || lv.id === data.nearest_resistance?.id) && <Badge tone="accent" className="ml-1">nearest</Badge>}
                    </td>
                    <td className="py-1">
                      <span className={lv.kind === "support" ? "text-up" : "text-down"}>{lv.kind}</span> <span className="text-muted">· {lv.tier}</span>
                    </td>
                    <td className="py-1 text-right">{formatPrice(lv.zone_low)} – {formatPrice(lv.zone_high)}</td>
                    <td className="py-1 text-right">
                      {formatPrice(lv.distance, pricePrecision(price))} <span className="text-muted">({formatPct(lv.distance_pct, 2, false)})</span>
                    </td>
                    <td className="py-1 text-right">{lv.touches}</td>
                    <td className="py-1 text-right">{lv.reactions}</td>
                    <td className="py-1 pl-4">
                      <div className="flex items-center gap-1">
                        <div className="h-1.5 w-16 rounded bg-surface-3">
                          <div className={cx("h-1.5 rounded", lv.kind === "support" ? "bg-up" : "bg-down")} style={{ width: `${lv.strength}%` }} />
                        </div>
                        <span>{lv.strength.toFixed(0)}</span>
                      </div>
                    </td>
                    <td className="py-1"><Badge tone={directionTone(lv.status)}>{lv.status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div>
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Prior period highs/lows <span className="normal-case">(observed)</span></h3>
          {data.reference_levels.length === 0 && <p className="text-xs text-muted">Daily history unavailable.</p>}
          <ul className="tabular space-y-0.5 text-xs">
            {data.reference_levels.map((r) => (
              <li key={r.key} className="flex justify-between gap-2" title={`${formatDateTime(r.period_start, "UTC").split(",")[0]} – ${formatDateTime(r.period_end, "UTC").split(",")[0]}`}>
                <span className="text-muted">{r.name}</span>
                <span>
                  {formatPrice(r.price)} <span className={r.position === "above" ? "text-down" : "text-up"}>({r.position}, {formatPct(r.distance_pct, 2, false)})</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Pivot points <span className="normal-case">(formula)</span></h3>
          {data.pivots ? (
            <>
              <p className="mb-1 text-[11px] text-muted">From {data.pivots.basis}</p>
              <ul className="tabular grid grid-cols-2 gap-x-3 text-xs">
                {Object.entries(data.pivots.levels).map(([k, v]) => (
                  <li key={k} className="flex justify-between">
                    <span className="text-muted">{k}</span>
                    <span className={v > price ? "text-down" : "text-up"}>{formatPrice(v)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-xs text-muted">Not enough completed periods.</p>
          )}
        </div>
        <div>
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Fibonacci retracement</h3>
          {data.fibonacci ? (
            <>
              <p className="mb-1 text-[11px] text-muted">
                Swing {formatPrice(data.fibonacci.swing_low)} → {formatPrice(data.fibonacci.swing_high)} ({data.fibonacci.direction})
              </p>
              <ul className="tabular grid grid-cols-2 gap-x-3 text-xs">
                {data.fibonacci.levels.map((l) => (
                  <li key={l.ratio} className="flex justify-between">
                    <span className="text-muted">{(l.ratio * 100).toFixed(1)}%</span>
                    <span>{formatPrice(l.price)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-xs text-muted">No clear swing range.</p>
          )}
        </div>
      </div>

      {data.trendlines.length > 0 && (
        <div>
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Trendlines</h3>
          <ul className="space-y-0.5 text-xs">
            {data.trendlines.map((t) => (
              <li key={t.kind}>
                <Badge tone={t.kind === "support" ? "up" : "down"}>{t.kind}</Badge> <span className="text-muted">{t.description} ({t.touches} touches)</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Details summary="How levels are calculated">
        {data.methodology.map((m) => <p key={m}>• {m}</p>)}
        <p>• Parameters: lookback {data.params.lookback} bars, swing window {data.params.swing_window}, zone tolerance {formatPrice(data.params.tolerance)}, major ≥ {data.params.major_threshold}.</p>
      </Details>
      <p className="text-[11px] text-muted">{data.disclaimer}</p>
    </div>
  );
}
