import type { IndicatorsResponse } from "../../api/types";
import { ErrorState } from "../common/ErrorState";
import { Badge, Skeleton, directionTone } from "../common/ui";

const ORDER = ["trend", "momentum", "volatility", "volume"] as const;
const STATE_LABEL: Record<string, string> = {
  insufficient_data: "Insufficient data",
  unavailable: "Unavailable",
};

export function IndicatorReadings({ query }: { query: { data?: IndicatorsResponse; error: unknown; isLoading: boolean; refetch: () => void } }) {
  const { data, error, isLoading } = query;
  if (isLoading && !data) return <Skeleton className="m-3 h-48" />;
  if (error && !data) return <ErrorState error={error} onRetry={query.refetch} className="m-3" />;
  if (!data) return null;
  return (
    <div className="p-3">
      {!data.context.last_bar_complete && (
        <p className="mb-2 text-[11px] text-muted">Latest values include the still-forming {data.context.timeframe} bar and will change until it closes.</p>
      )}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {ORDER.map((category) => (
          <div key={category}>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{category}</h3>
            <ul className="divide-y divide-line rounded border border-line">
              {data.readings
                .filter((r) => r.category === category)
                .map((r) => (
                  <li key={r.key} className="p-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{r.name}</span>
                      <span className="flex items-center gap-2">
                        <span className="tabular">{r.display}</span>
                        <Badge tone={directionTone(r.state)}>{STATE_LABEL[r.state] ?? r.state.replace("_", " ")}</Badge>
                      </span>
                    </div>
                    <p className="mt-0.5 leading-relaxed text-muted">{r.interpretation}</p>
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </div>
      {data.notes.map((n) => <p key={n} className="mt-2 text-[11px] text-muted">{n}</p>)}
      <p className="mt-2 text-[11px] text-muted">No single indicator is a buy or sell signal. {data.disclaimer}</p>
    </div>
  );
}
