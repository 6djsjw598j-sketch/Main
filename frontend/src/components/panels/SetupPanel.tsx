import type { SetupsResponse, TradeScenario } from "../../api/types";
import { formatPrice } from "../../lib/format";
import { ErrorState } from "../common/ErrorState";
import { Badge, Card, Details, Disclaimer, Skeleton, cx } from "../common/ui";

function Scenario({ s }: { s: TradeScenario }) {
  const bull = s.direction === "bullish";
  return (
    <div className={cx("rounded-md border p-2.5", bull ? "border-up/40" : "border-down/40")} data-testid={`scenario-${s.direction}`}>
      <div className="flex items-center justify-between gap-2">
        <span className={cx("text-sm font-semibold", bull ? "text-up" : "text-down")}>{bull ? "Bullish scenario" : "Bearish scenario"}</span>
        {s.available && s.alignment && (
          <Badge tone={s.alignment === "with_bias" ? "accent" : s.alignment === "counter_bias" ? "warn" : "neutral"}>
            {s.alignment === "with_bias" ? "With bias" : s.alignment === "counter_bias" ? "Counter-bias" : "Neutral bias"}
          </Badge>
        )}
      </div>
      {!s.available ? (
        <p className="mt-1 text-xs text-muted">Not available: {s.unavailable_reason}</p>
      ) : (
        <div className="mt-1 space-y-1 text-xs">
          <div className="text-muted">{s.setup_type}</div>
          <dl className="tabular grid grid-cols-2 gap-x-2 gap-y-0.5">
            <dt className="text-muted">Entry zone</dt>
            <dd className="text-right">{formatPrice(s.entry_zone_low)} – {formatPrice(s.entry_zone_high)}</dd>
            <dt className="text-muted">Invalidation</dt>
            <dd className="text-right text-down">{formatPrice(s.invalidation)}</dd>
            <dt className="text-muted">Risk / unit</dt>
            <dd className="text-right">{formatPrice(s.risk_per_unit)}</dd>
            {s.targets.map((t, i) => (
              <div key={t.price} className="contents" title={t.basis}>
                <dt className="text-muted">Target {i + 1}</dt>
                <dd className="text-right text-up">
                  {formatPrice(t.price)} <span className="text-muted">(R:R {t.reward_risk?.toFixed(2) ?? "n/a"})</span>
                </dd>
              </div>
            ))}
          </dl>
          {s.targets.length === 0 && <p className="text-muted">No level-based target identified.</p>}
          <Details summary="Confirmation / invalidation conditions">
            <p className="font-medium text-fg">Would confirm:</p>
            <ul className="list-inside list-disc">{s.confirmation_conditions.map((c) => <li key={c}>{c}</li>)}</ul>
            <p className="font-medium text-fg">Would invalidate:</p>
            <ul className="list-inside list-disc">{s.invalidation_conditions.map((c) => <li key={c}>{c}</li>)}</ul>
          </Details>
          {s.notes.map((n) => <p key={n} className="text-[11px] text-muted">{n}</p>)}
        </div>
      )}
    </div>
  );
}

export function SetupPanel({ query }: { query: { data?: SetupsResponse; error: unknown; isLoading: boolean; refetch: () => void } }) {
  const { data, error, isLoading } = query;
  return (
    <Card title="Technical scenarios" actions={<Badge tone="warn">Educational</Badge>}>
      {isLoading && !data && <Skeleton className="h-40 w-full" />}
      {error && !data ? <ErrorState error={error} onRetry={query.refetch} compact /> : null}
      {data && (
        <div className="space-y-2">
          <p className="text-[11px] text-muted">{data.label}. Model-generated from detected levels and ATR; observed inputs below.</p>
          <div className="tabular grid grid-cols-2 gap-x-2 rounded bg-surface-2 p-2 text-[11px]">
            <span className="text-muted">Last price (observed)</span>
            <span className="text-right">{formatPrice(data.observed.last_price)}</span>
            <span className="text-muted">ATR (computed)</span>
            <span className="text-right">{formatPrice(data.observed.atr)}</span>
          </div>
          <Scenario s={data.bullish} />
          <Scenario s={data.bearish} />
          <Details summary="Methodology & assumptions">
            {data.methodology.map((m) => <p key={m}>• {m}</p>)}
          </Details>
          <Disclaimer>{data.disclaimer}</Disclaimer>
        </div>
      )}
    </Card>
  );
}
