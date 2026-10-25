import { useState } from "react";

import type { Candle, CandlesResponse, IndicatorsResponse, SupportResistanceResponse } from "../../api/types";
import { TIMEFRAMES } from "../../api/types";
import { resolveParams } from "../../lib/catalog";
import { formatCompact, formatPct, formatPrice } from "../../lib/format";
import { displayZone, formatBarTime, zoneLabel } from "../../lib/time";
import { usePreferences } from "../../state/preferences";
import { ErrorState } from "../common/ErrorState";
import { IconRefresh } from "../common/icons";
import { Badge, Button, Skeleton, Spinner, cx } from "../common/ui";
import { IndicatorControls } from "./IndicatorControls";
import { PriceChart } from "./PriceChart";

interface Props {
  symbol: string;
  candlesQuery: { data?: CandlesResponse; error: unknown; isLoading: boolean; isFetching: boolean; refetch: () => void };
  indicators?: IndicatorsResponse;
  indicatorsError?: unknown;
  levels?: SupportResistanceResponse;
  onRefresh: () => void;
}

function Legend({ candle, prev, tfLabel }: { candle: Candle | undefined; prev: Candle | undefined; tfLabel: string }) {
  if (!candle) return null;
  const change = prev ? ((candle.close - prev.close) / prev.close) * 100 : null;
  const tone = candle.close >= candle.open ? "text-up" : "text-down";
  return (
    <div className="tabular pointer-events-none flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]">
      <span className="text-muted">{tfLabel}</span>
      <span>O <b className={tone}>{formatPrice(candle.open)}</b></span>
      <span>H <b className={tone}>{formatPrice(candle.high)}</b></span>
      <span>L <b className={tone}>{formatPrice(candle.low)}</b></span>
      <span>C <b className={tone}>{formatPrice(candle.close)}</b></span>
      {change !== null && <span className={change >= 0 ? "text-up" : "text-down"}>{formatPct(change)}</span>}
      <span>Vol <b>{candle.volume === null ? "n/a" : formatCompact(candle.volume)}</b></span>
    </div>
  );
}

export function ChartPanel({ symbol, candlesQuery, indicators, indicatorsError, levels, onRefresh }: Props) {
  const { prefs, update } = usePreferences();
  const [hovered, setHovered] = useState<Candle | null>(null);
  const data = candlesQuery.data;
  const candles = data?.candles ?? [];
  const params = resolveParams(prefs.indicator_params);
  const zone = displayZone(prefs.timezone_mode, data?.timezone ?? "UTC");
  const volumeAvailable = candles.some((c) => c.volume !== null && c.volume > 0);
  const shown = hovered ?? candles[candles.length - 1];
  const shownIndex = shown ? candles.findIndex((c) => c.time === shown.time) : -1;
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  return (
    <section className="rounded-lg border border-line bg-surface" aria-label="Price chart">
      <div className="flex flex-wrap items-center gap-1 border-b border-line px-2 py-1.5">
        <div className="flex flex-wrap items-center gap-0.5" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((tf) => (
            <Button key={tf} active={prefs.timeframe === tf} onClick={() => update({ timeframe: tf })} ariaPressed={prefs.timeframe === tf}>
              {tf}
            </Button>
          ))}
        </div>
        <span className="mx-1 hidden h-4 w-px bg-line sm:inline-block" />
        <div className="flex items-center gap-0.5" role="group" aria-label="Chart type">
          {(["candles", "bars", "line"] as const).map((t) => (
            <Button key={t} active={prefs.chart_type === t} onClick={() => update({ chart_type: t })} ariaPressed={prefs.chart_type === t}>
              {t === "candles" ? "Candles" : t === "bars" ? "OHLC" : "Line"}
            </Button>
          ))}
        </div>
        <span className="mx-1 hidden h-4 w-px bg-line sm:inline-block" />
        <IndicatorControls volumeAvailable={volumeAvailable} isIntraday={["1m", "5m", "15m", "30m", "1h", "4h"].includes(prefs.timeframe)} />
        <div className="ml-auto flex items-center gap-1">
          <select
            aria-label="Display time zone"
            className="rounded border border-line bg-surface-2 px-1.5 py-1 text-xs"
            value={prefs.timezone_mode}
            onChange={(e) => update({ timezone_mode: e.target.value as typeof prefs.timezone_mode })}
          >
            <option value="local">Local time</option>
            <option value="exchange">Exchange time</option>
            <option value="utc">UTC</option>
          </select>
          <Button
            onClick={() => update({ auto_refresh: !prefs.auto_refresh })}
            active={prefs.auto_refresh}
            ariaPressed={prefs.auto_refresh}
            title="Automatically refresh data at the provider-appropriate interval"
          >
            Auto
          </Button>
          <Button onClick={onRefresh} ariaLabel="Refresh chart data" title="Refresh now">
            {candlesQuery.isFetching ? <Spinner label="Refreshing" /> : <IconRefresh />}
          </Button>
        </div>
      </div>

      <div className="relative">
        {candlesQuery.isLoading && !data && (
          <div className="p-3">
            <Skeleton className="h-[420px] w-full" />
          </div>
        )}
        {!!candlesQuery.error && !data && (
          <div className="p-3">
            <ErrorState error={candlesQuery.error} onRetry={candlesQuery.refetch} />
          </div>
        )}
        {data && candles.length === 0 && (
          <div className="p-6 text-center text-sm text-muted">The provider returned no {prefs.timeframe} candles for {symbol}.</div>
        )}
        {data && candles.length > 0 && (
          <>
            <div className="absolute left-2 top-1.5 z-10 max-w-[calc(100%-5rem)] rounded bg-surface/80 px-1.5 py-0.5 backdrop-blur-sm">
              <Legend candle={shown} prev={shownIndex > 0 ? candles[shownIndex - 1] : undefined} tfLabel={`${data.display_symbol} · ${data.timeframe}`} />
              {shown && <div className="text-[10px] text-muted">{formatBarTime(shown.time, data.timeframe, zone)}</div>}
            </div>
            <PriceChart
              candles={candles}
              timeframe={data.timeframe}
              resetKey={`${data.symbol}|${data.timeframe}`}
              chartType={prefs.chart_type}
              indicators={indicators}
              overlays={prefs.overlays.filter((o) => o !== "vwap" || ["1m", "5m", "15m", "30m", "1h", "4h"].includes(data.timeframe))}
              panels={prefs.panels}
              params={params}
              levels={levels}
              theme={prefs.theme}
              zone={zone}
              volumeAvailable={volumeAvailable}
              height={isMobile ? 340 : 470}
              onHover={setHovered}
              showVolume={prefs.show_volume && volumeAvailable}
              showLevels={prefs.show_levels}
              showReference={prefs.show_reference_levels}
              showPivots={prefs.show_pivots}
              showFibonacci={prefs.show_fibonacci}
              showTrendlines={prefs.show_trendlines}
            />
          </>
        )}
      </div>

      {data && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-1.5 text-[11px] text-muted">
          <Badge tone={data.freshness.is_stale ? "down" : data.freshness.status === "real_time" ? "up" : "warn"} title={data.freshness.detail}>
            {data.freshness.label}
          </Badge>
          <span>{data.count} bars · {data.provider === "coinbase" ? "Coinbase Exchange" : "Twelve Data"}</span>
          {data.aggregated_from && <span>aggregated from {data.aggregated_from}</span>}
          <span>times in {zoneLabel(prefs.timezone_mode, data.timezone)}{["1d", "1w", "1M"].includes(data.timeframe) ? " (daily+ bars labelled by session date)" : ""}</span>
          {data.cache.stale && <Badge tone="warn">{data.cache.warning}</Badge>}
          {!!indicatorsError && <span className={cx("text-down")}>Indicators unavailable</span>}
        </div>
      )}
    </section>
  );
}
