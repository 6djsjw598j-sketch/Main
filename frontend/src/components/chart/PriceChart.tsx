import {
  BarSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineWidth,
  type MouseEventParams,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";

import type {
  Candle,
  IndicatorParams,
  IndicatorsResponse,
  OverlayKey,
  PanelKey,
  SupportResistanceResponse,
  Timeframe,
} from "../../api/types";
import { OVERLAYS, PANELS, type LineSpec } from "../../lib/catalog";
import { formatPrice, pricePrecision } from "../../lib/format";
import { formatBarTime, formatTick } from "../../lib/time";
import { ZonesPrimitive, type Zone } from "./zonesPrimitive";

export interface ChartToggles {
  showVolume: boolean;
  showLevels: boolean;
  showReference: boolean;
  showPivots: boolean;
  showFibonacci: boolean;
  showTrendlines: boolean;
}

export interface PriceChartProps extends ChartToggles {
  candles: Candle[];
  timeframe: Timeframe;
  /** Changes when symbol/timeframe change; used to reset the zoom. */
  resetKey: string;
  chartType: "candles" | "bars" | "line";
  indicators?: IndicatorsResponse;
  overlays: OverlayKey[];
  panels: PanelKey[];
  params: IndicatorParams;
  levels?: SupportResistanceResponse;
  theme: "dark" | "light";
  zone: string | undefined;
  volumeAvailable: boolean;
  height: number;
  onHover?: (candle: Candle | null) => void;
}

const PALETTE = {
  dark: { bg: "#131722", text: "#b2b5be", grid: "#1c2130", border: "#2a2f3d", up: "#26a69a", down: "#ef5350", line: "#3d74ff", guide: "#5d6475" },
  light: { bg: "#ffffff", text: "#3b4252", grid: "#eef1f6", border: "#dfe3ec", up: "#089981", down: "#e5303f", line: "#2962ff", guide: "#9aa1b1" },
};

const PANEL_HEIGHT = 110;

function toTime(t: number): Time {
  return t as UTCTimestamp;
}

function lineData(times: number[], values: (number | null)[] | undefined, futureTimes: number[] = [], future: (number | null)[] = []) {
  const out: { time: Time; value?: number }[] = [];
  if (!values) return out;
  for (let i = 0; i < times.length; i++) {
    const v = values[i];
    out.push(v === null || v === undefined ? { time: toTime(times[i]) } : { time: toTime(times[i]), value: v });
  }
  for (let i = 0; i < futureTimes.length; i++) {
    const v = future[i];
    out.push(v === null || v === undefined ? { time: toTime(futureTimes[i]) } : { time: toTime(futureTimes[i]), value: v });
  }
  return out;
}

function hexAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

interface SeriesEntry {
  spec: LineSpec;
  series: ISeriesApi<SeriesType>;
}

export function PriceChart(props: PriceChartProps) {
  const {
    candles, timeframe, resetKey, chartType, indicators, overlays, panels, params, levels, theme, zone,
    volumeAvailable, height, onHover, showVolume, showLevels, showReference, showPivots, showFibonacci, showTrendlines,
  } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceRef = useRef<ISeriesApi<SeriesType> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRef = useRef<SeriesEntry[]>([]);
  const panelRef = useRef<{ key: PanelKey; entries: SeriesEntry[]; guides: IPriceLine[] }[]>([]);
  const trendRef = useRef<ISeriesApi<"Line">[]>([]);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const zonesRef = useRef<ZonesPrimitive | null>(null);
  const lastResetKey = useRef<string | null>(null);
  const candleMap = useRef(new Map<number, Candle>());
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;

  const colors = PALETTE[theme];
  const precision = pricePrecision(candles.length ? candles[candles.length - 1].close : 1);
  const activePanels = useMemo(
    () => panels.filter((p) => !PANELS.find((d) => d.key === p)?.needsVolume || volumeAvailable),
    [panels, volumeAvailable],
  );
  const structureKey = JSON.stringify({
    chartType,
    showVolume,
    overlays: overlays.map((o) => OVERLAYS.find((d) => d.key === o)?.lines(params).map((l) => l.key)),
    panels: activePanels,
    theme,
    precision,
    showTrendlines,
    trendCount: levels?.trendlines.length ?? 0,
  });

  // Create the chart once.
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: true },
      timeScale: { borderVisible: true, rightOffset: 6, timeVisible: true, secondsVisible: false },
      layout: { attributionLogo: true },
    });
    chartRef.current = chart;
    const handler = (param: MouseEventParams<Time>) => {
      if (!hoverRef.current) return;
      if (param.time === undefined) {
        hoverRef.current(null);
        return;
      }
      hoverRef.current(candleMap.current.get(param.time as number) ?? null);
    };
    chart.subscribeCrosshairMove(handler);
    return () => {
      chart.unsubscribeCrosshairMove(handler);
      chart.remove();
      // Series belong to the removed chart; forget them so a re-mount starts clean.
      chartRef.current = null;
      priceRef.current = null;
      volumeRef.current = null;
      zonesRef.current = null;
      overlayRef.current = [];
      panelRef.current = [];
      trendRef.current = [];
      priceLinesRef.current = [];
      lastResetKey.current = null;
    };
  }, []);

  // Theme + time formatting.
  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: colors.bg }, textColor: colors.text, panes: { separatorColor: colors.border } },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.border },
      timeScale: {
        borderColor: colors.border,
        timeVisible: ["1m", "5m", "15m", "30m", "1h", "4h"].includes(timeframe),
        tickMarkFormatter: (time: Time, tickType: number) => formatTick(time as number, timeframe, zone, tickType),
      },
      localization: { timeFormatter: (time: Time) => formatBarTime(time as number, timeframe, zone) },
    });
  }, [colors, timeframe, zone]);

  // (Re)build series when the chart structure changes.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const removeAll = () => {
      const all: ISeriesApi<SeriesType>[] = [
        ...(priceRef.current ? [priceRef.current] : []),
        ...(volumeRef.current ? [volumeRef.current] : []),
        ...overlayRef.current.map((e) => e.series),
        ...panelRef.current.flatMap((p) => p.entries.map((e) => e.series)),
        ...trendRef.current,
      ];
      for (const s of all) chart.removeSeries(s);
      priceRef.current = null;
      volumeRef.current = null;
      overlayRef.current = [];
      panelRef.current = [];
      trendRef.current = [];
      priceLinesRef.current = [];
      zonesRef.current = null;
    };
    removeAll();

    const priceFormat = { type: "price" as const, precision, minMove: 1 / 10 ** precision };
    let price: ISeriesApi<SeriesType>;
    if (chartType === "line") {
      price = chart.addSeries(LineSeries, { color: colors.line, lineWidth: 2, priceFormat });
    } else if (chartType === "bars") {
      price = chart.addSeries(BarSeries, { upColor: colors.up, downColor: colors.down, priceFormat, thinBars: false });
    } else {
      price = chart.addSeries(CandlestickSeries, {
        upColor: colors.up,
        downColor: colors.down,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
        borderVisible: false,
        priceFormat,
      });
    }
    priceRef.current = price;
    const zones = new ZonesPrimitive();
    price.attachPrimitive(zones);
    zonesRef.current = zones;

    if (showVolume) {
      const volume = chart.addSeries(HistogramSeries, {
        priceScaleId: "volume",
        priceFormat: { type: "volume" },
        lastValueVisible: false,
        priceLineVisible: false,
      });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volumeRef.current = volume;
    }

    const lineOptions = (spec: LineSpec, extra: object = {}) => ({
      color: spec.color,
      lineWidth: (spec.width ?? 1) as LineWidth,
      lineStyle: spec.dashed ? LineStyle.Dashed : LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: "",
      ...extra,
    });

    for (const key of overlays) {
      const def = OVERLAYS.find((d) => d.key === key);
      if (!def) continue;
      for (const spec of def.lines(params)) {
        overlayRef.current.push({ spec, series: chart.addSeries(LineSeries, lineOptions(spec, { priceFormat })) });
      }
    }

    activePanels.forEach((key, i) => {
      const def = PANELS.find((d) => d.key === key);
      if (!def) return;
      const paneIndex = i + 1;
      const entries: SeriesEntry[] = def.lines(params).map((spec) => {
        const format = key === "obv" || key === "ad" ? { type: "volume" as const } : { type: "price" as const, precision: key === "atr" ? precision : 2, minMove: key === "atr" ? 1 / 10 ** precision : 0.01 };
        const series = spec.histogram
          ? chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false, priceFormat: format }, paneIndex)
          : chart.addSeries(LineSeries, lineOptions(spec, { lastValueVisible: true, priceFormat: format }), paneIndex);
        return { spec, series: series as ISeriesApi<SeriesType> };
      });
      const guides = (def.guides?.(params) ?? []).map((g) =>
        entries[entries.length - 1].series.createPriceLine({
          price: g.value,
          color: colors.guide,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: false,
          title: g.label,
        }),
      );
      panelRef.current.push({ key, entries, guides });
    });
    // Stretch factors are relative, so they hold regardless of when the container gets its size.
    const panes = chart.panes();
    panes[0]?.setStretchFactor(height);
    for (let i = 1; i < panes.length; i++) panes[i].setStretchFactor(PANEL_HEIGHT);

    if (showTrendlines && levels) {
      for (const tl of levels.trendlines) {
        trendRef.current.push(
          chart.addSeries(LineSeries, {
            color: tl.kind === "support" ? colors.up : colors.down,
            lineWidth: 2,
            lineStyle: tl.broken ? LineStyle.Dotted : LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            priceFormat,
          }),
        );
      }
    }
  }, [structureKey, height]);

  // Push data into the series.
  useEffect(() => {
    const chart = chartRef.current;
    const price = priceRef.current;
    if (!chart || !price) return;
    candleMap.current = new Map(candles.map((c) => [c.time, c]));

    if (chartType === "line") {
      price.setData(candles.map((c) => ({ time: toTime(c.time), value: c.close })));
    } else {
      price.setData(candles.map((c) => ({ time: toTime(c.time), open: c.open, high: c.high, low: c.low, close: c.close })));
    }
    volumeRef.current?.setData(
      candles.map((c) =>
        c.volume === null
          ? { time: toTime(c.time) }
          : { time: toTime(c.time), value: c.volume, color: hexAlpha(c.close >= c.open ? colors.up : colors.down, 0.45) },
      ),
    );

    // Indicator series only when they belong to the same candle set.
    const sameSet = indicators && indicators.times.length > 0 && candles.length > 0 && indicators.times[indicators.times.length - 1] === candles[candles.length - 1].time;
    const times = sameSet ? indicators!.times : [];
    for (const { spec, series } of overlayRef.current) {
      const isSpan = spec.key === "ichimoku_senkou_a" || spec.key === "ichimoku_senkou_b";
      series.setData(
        sameSet
          ? lineData(times, indicators!.series[spec.key], isSpan ? indicators!.future_times : [], isSpan ? indicators!.future_series[spec.key] ?? [] : [])
          : [],
      );
    }
    for (const panel of panelRef.current) {
      for (const { spec, series } of panel.entries) {
        if (!sameSet) {
          series.setData([]);
          continue;
        }
        const values = indicators!.series[spec.key];
        if (spec.histogram && values) {
          series.setData(
            times.map((t, i) => {
              const v = values[i];
              return v === null ? { time: toTime(t) } : { time: toTime(t), value: v, color: hexAlpha(v >= 0 ? colors.up : colors.down, 0.6) };
            }),
          );
        } else {
          series.setData(lineData(times, values));
        }
      }
    }

    // Levels: zones, nearest S/R, reference levels, pivots, Fibonacci, trendlines.
    for (const line of priceLinesRef.current) price.removePriceLine(line);
    priceLinesRef.current = [];
    const addLine = (value: number, color: string, title: string, style: LineStyle, axis = true) => {
      priceLinesRef.current.push(price.createPriceLine({ price: value, color, lineWidth: 1, lineStyle: style, axisLabelVisible: axis, title }));
    };
    const zones: Zone[] = [];
    if (levels && showLevels) {
      const nearestIds = new Set([levels.nearest_support?.id, levels.nearest_resistance?.id]);
      for (const lv of levels.levels) {
        const base = lv.kind === "support" ? colors.up : colors.down;
        // Label only major and nearest zones to keep the chart readable; the Levels tab lists all.
        const labelled = lv.tier === "major" || nearestIds.has(lv.id);
        zones.push({
          low: lv.zone_low,
          high: lv.zone_high,
          fill: hexAlpha(base, lv.tier === "major" ? 0.14 : 0.07),
          border: hexAlpha(base, lv.tier === "major" ? 0.5 : 0.25),
          label: labelled ? `${lv.id} · ${lv.tier} · ${Math.round(lv.strength)}` : "",
        });
      }
      if (levels.nearest_support) addLine(levels.nearest_support.zone_high, colors.up, "Support", LineStyle.Solid);
      if (levels.nearest_resistance) addLine(levels.nearest_resistance.zone_low, colors.down, "Resistance", LineStyle.Solid);
    }
    zonesRef.current?.setZones(zones);
    if (levels && showReference) {
      const short: Record<string, string> = {
        prev_day_high: "PDH", prev_day_low: "PDL", prev_week_high: "PWH", prev_week_low: "PWL", prev_month_high: "PMH", prev_month_low: "PML",
      };
      for (const ref of levels.reference_levels) addLine(ref.price, colors.guide, short[ref.key] ?? ref.name, LineStyle.LargeDashed);
    }
    if (levels?.pivots && showPivots) {
      for (const [name, value] of Object.entries(levels.pivots.levels)) addLine(value, "#ab47bc", name, LineStyle.Dotted);
    }
    if (levels?.fibonacci && showFibonacci) {
      for (const lv of levels.fibonacci.levels) addLine(lv.price, "#f5a524", `Fib ${(lv.ratio * 100).toFixed(1)}%`, LineStyle.Dotted);
    }
    if (levels && showTrendlines) {
      levels.trendlines.forEach((tl, i) => {
        trendRef.current[i]?.setData(tl.points.map((p) => ({ time: toTime(p.time), value: p.price })));
      });
    }

    // Reset the zoom only when the symbol or timeframe changes; keep the user's zoom on refresh.
    if (lastResetKey.current !== resetKey && candles.length) {
      lastResetKey.current = resetKey;
      const n = candles.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 6 });
    }
  }, [candles, indicators, levels, structureKey, showLevels, showReference, showPivots, showFibonacci, showTrendlines, resetKey, chartType, colors]);

  return (
    <div
      ref={containerRef}
      className="w-full"
      style={{ height: height + activePanels.length * PANEL_HEIGHT }}
      data-testid="price-chart"
      aria-label={`Price chart, ${candles.length} bars. Last close ${candles.length ? formatPrice(candles[candles.length - 1].close) : "n/a"}`}
      role="img"
    />
  );
}
