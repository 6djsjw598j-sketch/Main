import type { QuoteResponse } from "../../api/types";
import { changeTone, formatCompact, formatPct, formatPrice, formatSigned } from "../../lib/format";
import { formatDateTime } from "../../lib/time";
import { ErrorState } from "../common/ErrorState";
import { IconStar } from "../common/icons";
import { Badge, Button, Skeleton, Stat, cx, type Tone } from "../common/ui";

const FRESHNESS_TONE: Record<string, Tone> = { real_time: "up", delayed: "warn", end_of_day: "accent", stale: "down" };
const SESSION_TONE: Record<string, Tone> = {
  open: "up", always_open: "up", pre_market: "warn", after_hours: "warn", closed: "neutral", unknown: "neutral",
};

export function AssetSummary({
  symbol,
  query,
  inWatchlist,
  onToggleWatchlist,
  watchlistBusy,
}: {
  symbol: string;
  query: { data?: QuoteResponse; error: unknown; isLoading: boolean; refetch: () => void };
  inWatchlist: boolean;
  onToggleWatchlist: () => void;
  watchlistBusy: boolean;
}) {
  const { data, error, isLoading } = query;
  if (isLoading && !data) {
    return (
      <section className="rounded-lg border border-line bg-surface p-3" aria-busy="true" aria-label="Loading quote">
        <Skeleton className="mb-2 h-5 w-40" />
        <Skeleton className="h-8 w-56" />
      </section>
    );
  }
  if (error && !data) {
    return <ErrorState error={error} onRetry={query.refetch} />;
  }
  if (!data) return null;
  const q = data.quote;
  const tone = changeTone(q.change);
  const isCrypto = q.change_basis === "rolling_24h";
  return (
    <section className="rounded-lg border border-line bg-surface p-3" aria-label={`${q.display_symbol} quote`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight" data-testid="asset-symbol">{q.display_symbol}</h1>
            <Badge>{q.asset_type.toUpperCase()}</Badge>
            {q.exchange && <span className="text-xs text-muted">{q.exchange}</span>}
            <Button
              onClick={onToggleWatchlist}
              disabled={watchlistBusy}
              ariaPressed={inWatchlist}
              ariaLabel={inWatchlist ? `Remove ${q.display_symbol} from watchlist` : `Add ${q.display_symbol} to watchlist`}
              className={cx(inWatchlist ? "text-warn" : "text-muted")}
            >
              <IconStar filled={inWatchlist} />
            </Button>
          </div>
          <div className="truncate text-xs text-muted">{q.name ?? symbol}</div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="tabular text-3xl font-semibold" data-testid="asset-price">
              {formatPrice(q.price)}
            </span>
            {q.currency && <span className="text-xs text-muted">{q.currency}</span>}
            <span className={cx("tabular text-base font-medium", tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-muted")}>
              {formatSigned(q.change)} ({formatPct(q.change_percent)})
            </span>
            <span className="text-xs text-muted">{isCrypto ? "24h" : "vs prev. close"}</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 text-right">
          <div className="flex flex-wrap justify-end gap-1">
            <Badge tone={SESSION_TONE[data.session.status]} title={data.session.note ?? undefined}>{data.session.label}</Badge>
            <Badge tone={FRESHNESS_TONE[data.freshness.status]} title={data.freshness.detail}>{data.freshness.label}</Badge>
          </div>
          <div className="text-[11px] text-muted" title={data.freshness.detail}>As of {formatDateTime(q.as_of)}</div>
          <div className="max-w-xs text-[11px] text-muted">{data.freshness.detail}</div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3 border-t border-line pt-2 sm:grid-cols-4 lg:grid-cols-7">
        <Stat label={isCrypto ? "24h open" : "Open"} value={formatPrice(q.open)} />
        <Stat label={isCrypto ? "24h high" : "High"} value={formatPrice(q.high)} />
        <Stat label={isCrypto ? "24h low" : "Low"} value={formatPrice(q.low)} />
        <Stat label={isCrypto ? "Price 24h ago" : "Prev. close"} value={formatPrice(q.previous_close)} />
        <Stat label={q.volume_label ?? "Volume"} value={q.volume === null ? "n/a" : formatCompact(q.volume)} />
        {q.bid !== null && q.ask !== null ? (
          <Stat label="Bid / Ask" value={`${formatPrice(q.bid)} / ${formatPrice(q.ask)}`} />
        ) : (
          <Stat label="52w range" value={q.fifty_two_week_low !== null ? `${formatPrice(q.fifty_two_week_low)} – ${formatPrice(q.fifty_two_week_high)}` : "n/a"} />
        )}
        <Stat label="Source" value={q.provider === "coinbase" ? "Coinbase" : "Twelve Data"} title={data.source_note} />
      </div>
      {data.cache.stale && (
        <p role="status" className="mt-2 rounded bg-warn-soft px-2 py-1 text-xs text-warn">{data.cache.warning}</p>
      )}
    </section>
  );
}
