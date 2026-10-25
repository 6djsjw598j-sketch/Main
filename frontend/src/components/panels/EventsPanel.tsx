import type { SummaryResponse, Timeframe } from "../../api/types";
import { formatPrice } from "../../lib/format";
import { formatBarTime } from "../../lib/time";
import { Badge, Skeleton, directionTone } from "../common/ui";

export function EventsPanel({ data, isLoading, timeframe, zone }: { data?: SummaryResponse; isLoading: boolean; timeframe: Timeframe; zone: string | undefined }) {
  if (isLoading && !data) return <Skeleton className="m-3 h-40" />;
  if (!data) return <p className="p-3 text-xs text-muted">Events are derived from the technical summary, which is unavailable.</p>;
  if (data.events.length === 0) return <p className="p-3 text-xs text-muted">No technical events in the last 60 bars.</p>;
  return (
    <ul className="divide-y divide-line p-1">
      {data.events.map((e, i) => (
        <li key={`${e.time}-${e.type}-${i}`} className="flex items-start justify-between gap-3 px-2 py-1.5 text-xs">
          <div className="flex min-w-0 items-start gap-2">
            <Badge tone={directionTone(e.direction)}>{e.direction}</Badge>
            <span>{e.description}</span>
          </div>
          <div className="tabular shrink-0 text-right text-muted">
            <div>{formatBarTime(e.time, timeframe, zone)}</div>
            {e.price !== null && <div>{formatPrice(e.price)}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}
