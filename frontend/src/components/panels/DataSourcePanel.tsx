import { useMeta } from "../../api/hooks";
import type { CandlesResponse, QuoteResponse } from "../../api/types";
import { formatAge } from "../../lib/format";
import { formatDateTime } from "../../lib/time";
import { Badge } from "../common/ui";

export function DataSourcePanel({ quote, candles }: { quote?: QuoteResponse; candles?: CandlesResponse }) {
  const meta = useMeta();
  const provider = meta.data?.providers.find((p) => p.name === (candles?.provider ?? quote?.quote.provider));
  return (
    <div className="grid grid-cols-1 gap-4 p-3 text-xs lg:grid-cols-2">
      <div className="space-y-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">This view</h3>
        {quote && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted">Quote source</dt>
            <dd>{quote.source_note}</dd>
            <dt className="text-muted">Quote time</dt>
            <dd>{formatDateTime(quote.quote.as_of)} ({formatAge(quote.freshness.age_seconds)} old)</dd>
            <dt className="text-muted">Freshness</dt>
            <dd><Badge>{quote.freshness.label}</Badge> {quote.freshness.detail}</dd>
            <dt className="text-muted">Session</dt>
            <dd>{quote.session.label}{quote.session.note ? ` — ${quote.session.note}` : ""} ({quote.session.timezone})</dd>
            <dt className="text-muted">Server cache</dt>
            <dd>{quote.cache.hit ? `hit (${formatAge(quote.cache.age_seconds)} old)` : "fresh fetch"}{quote.cache.stale ? " — stale fallback" : ""}</dd>
            <dt className="text-muted">Refresh</dt>
            <dd>every {quote.refresh_seconds}s while auto-refresh is on</dd>
          </dl>
        )}
        {candles && (
          <>
            <h3 className="pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted">Candle limitations</h3>
            <ul className="list-inside list-disc space-y-0.5 text-muted">
              {candles.limitations.map((l) => <li key={l}>{l}</li>)}
              <li>Exchange time zone: {candles.timezone}.</li>
            </ul>
          </>
        )}
      </div>
      <div className="space-y-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">Providers</h3>
        {meta.data?.providers.map((p) => (
          <div key={p.name} className="rounded border border-line p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium">{p.display_name}</span>
              <Badge tone={p.configured ? "up" : "neutral"}>{p.configured ? "configured" : "not configured"}</Badge>
            </div>
            <p className="mt-1 text-muted">{p.data_type}</p>
            <p className="text-muted">Limits: {p.limits}</p>
            <ul className="mt-1 list-inside list-disc text-muted">{p.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </div>
        ))}
        {provider && (
          <details className="text-muted">
            <summary className="cursor-pointer">Timeframe support ({provider.display_name})</summary>
            <ul className="mt-1 grid grid-cols-2 gap-x-3">
              {provider.timeframes.map((t) => (
                <li key={t.timeframe}>{t.timeframe}: {t.native ? "native" : t.note}</li>
              ))}
            </ul>
          </details>
        )}
        {meta.data && <p className="text-[11px] text-muted">{meta.data.disclaimer}</p>}
      </div>
    </div>
  );
}
