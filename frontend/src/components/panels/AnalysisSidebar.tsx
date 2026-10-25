import type { Level, SummaryResponse } from "../../api/types";
import { formatPct, formatPrice } from "../../lib/format";
import { ErrorState } from "../common/ErrorState";
import { Badge, Card, Details, Disclaimer, Skeleton, cx, directionTone } from "../common/ui";

const CAP = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");

function ScoreGauge({ score, bull, bear }: { score: number; bull: number; bear: number }) {
  const pos = (v: number) => `${((v + 100) / 200) * 100}%`;
  return (
    <div className="mt-2" aria-label={`Factor score ${score.toFixed(0)} on a -100 to +100 scale`}>
      <div className="relative h-2 rounded-full bg-gradient-to-r from-down via-surface-3 to-up">
        <span className="absolute top-[-3px] h-3.5 w-1 -translate-x-1/2 rounded bg-fg" style={{ left: pos(score) }} />
        <span className="absolute top-0 h-2 w-px bg-surface" style={{ left: pos(bear) }} />
        <span className="absolute top-0 h-2 w-px bg-surface" style={{ left: pos(bull) }} />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted">
        <span>−100 bearish</span>
        <span>neutral</span>
        <span>bullish +100</span>
      </div>
    </div>
  );
}

function LevelRow({ label, level, price }: { label: string; level: Level | null; price: number }) {
  if (!level) return <Row label={label} value={<span className="text-muted">none detected</span>} />;
  const up = level.kind === "support";
  return (
    <Row
      label={label}
      value={
        <span className="tabular">
          <span className={up ? "text-up" : "text-down"}>{formatPrice(level.zone_low)}–{formatPrice(level.zone_high)}</span>{" "}
          <span className="text-muted">({formatPct(up ? -level.distance_pct : level.distance_pct)}, {level.tier})</span>
        </span>
      }
      title={`${level.reactions} reactions, ${level.touches} touches, strength ${level.strength.toFixed(0)}. ${level.status_detail} Distance measured from ${formatPrice(price)} to the zone edge.`}
    />
  );
}

function Row({ label, value, title }: { label: string; value: React.ReactNode; title?: string }) {
  return (
    <div className="flex items-start justify-between gap-2 py-1 text-xs" title={title}>
      <span className="shrink-0 text-muted">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}

export function AnalysisSidebar({ query }: { query: { data?: SummaryResponse; error: unknown; isLoading: boolean; refetch: () => void } }) {
  const { data, error, isLoading } = query;
  if (isLoading && !data) {
    return (
      <Card title="Market bias">
        <Skeleton className="mb-2 h-6 w-32" />
        <Skeleton className="mb-1 h-3 w-full" />
        <Skeleton className="h-3 w-3/4" />
      </Card>
    );
  }
  if (error && !data) {
    return (
      <Card title="Market bias">
        <ErrorState error={error} onRetry={query.refetch} compact />
      </Card>
    );
  }
  if (!data) return null;
  const { overall } = data;
  const price = data.context.last_price;
  return (
    <div className="space-y-3">
      <Card title="Market bias" actions={<span className="text-[10px] text-muted">{data.context.timeframe} · {data.context.bars} bars</span>}>
        <div className="flex items-center justify-between">
          <Badge tone={directionTone(overall.bias)} className="px-2 py-1 text-sm">{CAP(overall.bias)}</Badge>
          <span className="tabular text-sm font-semibold">{overall.score > 0 ? "+" : ""}{overall.score.toFixed(0)}</span>
        </div>
        <ScoreGauge score={overall.score} bull={overall.bullish_threshold} bear={overall.bearish_threshold} />
        <p className="mt-2 text-xs leading-relaxed text-muted">{overall.explanation}</p>
        {data.conflicts.length > 0 && (
          <div className="mt-2 rounded bg-warn-soft p-2 text-[11px] text-warn">
            <div className="font-semibold">Conflicting signals</div>
            <ul className="list-inside list-disc">
              {data.conflicts.slice(0, 6).map((c) => <li key={c}>{c}</li>)}
            </ul>
          </div>
        )}
        <Details summary={`Factor breakdown (${overall.factors_used}/${overall.factors_total} used)`}>
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-left text-muted">
                <th className="py-1 font-normal">Factor</th>
                <th className="py-1 text-right font-normal">Value</th>
                <th className="py-1 text-right font-normal">Weight</th>
                <th className="py-1 text-right font-normal">Points</th>
              </tr>
            </thead>
            <tbody>
              {data.factors.map((f) => (
                <tr key={f.key} className={cx("border-t border-line align-top", !f.available && "opacity-60")} title={f.detail}>
                  <td className="py-1">
                    <span className={cx(f.signal === "bullish" ? "text-up" : f.signal === "bearish" ? "text-down" : "text-fg")}>{f.name}</span>
                    <div className="text-[10px] text-muted">{f.detail.split(" Formula:")[0]}</div>
                  </td>
                  <td className="tabular py-1 text-right">{f.value === null ? "n/a" : f.value.toFixed(2)}</td>
                  <td className="tabular py-1 text-right">{f.weight}</td>
                  <td className="tabular py-1 text-right">{f.contribution === null ? "—" : f.contribution.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.methodology.map((m) => <p key={m}>• {m}</p>)}
        </Details>
      </Card>

      <Card title="Trend & momentum">
        {data.trend.map((t) => (
          <Row key={t.horizon} label={`${CAP(t.horizon)}-term trend`} value={<Badge tone={directionTone(t.direction)}>{CAP(t.direction)}</Badge>} title={t.explanation} />
        ))}
        <Row label="Momentum" value={<Badge tone={directionTone(data.momentum.state)}>{CAP(data.momentum.state)}</Badge>} title={data.momentum.explanation} />
        <Row
          label="Volatility"
          value={
            <span className="flex items-center gap-1">
              <Badge tone={data.volatility.state === "high" ? "warn" : "neutral"}>{CAP(data.volatility.state)}</Badge>
              {data.volatility.squeeze && <Badge tone="accent">Squeeze</Badge>}
            </span>
          }
          title={data.volatility.explanation}
        />
        <Row label="Volume" value={<Badge tone={directionTone(data.volume.state)}>{CAP(data.volume.state)}</Badge>} title={data.volume.explanation} />
        <Details summary="Explanations">
          {data.trend.map((t) => <p key={t.horizon}><b>{CAP(t.horizon)}:</b> {t.explanation}</p>)}
          <p><b>Momentum:</b> {data.momentum.explanation}</p>
          <p><b>Volatility:</b> {data.volatility.explanation}</p>
          <p><b>Volume:</b> {data.volume.explanation}</p>
        </Details>
      </Card>

      <Card title="Key levels">
        <LevelRow label="Nearest support" level={data.nearest_support} price={price} />
        <LevelRow label="Nearest resistance" level={data.nearest_resistance} price={price} />
        {data.key_levels.map((k) => (
          <Row
            key={k.role}
            label={CAP(k.role)}
            value={<span className={cx("tabular", k.role === "invalidation" || k.role === "bearish_trigger" ? "text-down" : "text-up")}>{formatPrice(k.price)}</span>}
            title={k.description}
          />
        ))}
        {data.key_levels.length > 0 && <p className="mt-1 text-[11px] text-muted">{data.key_levels.map((k) => k.description).join(" ")}</p>}
      </Card>

      <Card title="Conditions to watch">
        {data.conditions.length === 0 && <p className="text-xs text-muted">No breakout, breakdown or reversal conditions detected.</p>}
        <ul className="space-y-2">
          {data.conditions.map((c, i) => (
            <li key={`${c.type}-${i}`} className="text-xs">
              <div className="flex items-center gap-1">
                <Badge tone={c.type.startsWith("bull") || c.type === "breakout" ? "up" : "down"}>{CAP(c.type)}</Badge>
                {c.active ? <Badge tone="warn">Active</Badge> : <Badge>Watching</Badge>}
              </div>
              <p className="mt-0.5 leading-relaxed text-muted">{c.description}</p>
            </li>
          ))}
        </ul>
        <Disclaimer>{data.disclaimer}</Disclaimer>
      </Card>
    </div>
  );
}
