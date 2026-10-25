import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export interface Zone {
  low: number;
  high: number;
  fill: string;
  border: string;
  label: string;
}

/**
 * Series primitive that shades horizontal price zones (support in green, resistance in red)
 * across the whole pane and labels them at the left edge.
 */
export class ZonesPrimitive implements ISeriesPrimitive<Time> {
  private zones: Zone[] = [];
  private series: ISeriesApi<SeriesType> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view: ZonesView;

  constructor() {
    this.view = new ZonesView(this);
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.requestUpdate = null;
  }

  setZones(zones: Zone[]): void {
    this.zones = zones;
    this.requestUpdate?.();
  }

  getZones(): Zone[] {
    return this.zones;
  }

  getSeries(): ISeriesApi<SeriesType> | null {
    return this.series;
  }

  updateAllViews(): void {
    /* coordinates are computed at draw time */
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }
}

class ZonesView implements IPrimitivePaneView {
  private readonly source: ZonesPrimitive;

  constructor(source: ZonesPrimitive) {
    this.source = source;
  }

  zOrder(): PrimitivePaneViewZOrder {
    return "bottom";
  }

  renderer(): IPrimitivePaneRenderer {
    return new ZonesRenderer(this.source);
  }
}

class ZonesRenderer implements IPrimitivePaneRenderer {
  private readonly source: ZonesPrimitive;

  constructor(source: ZonesPrimitive) {
    this.source = source;
  }

  draw(target: CanvasRenderingTarget2D): void {
    const series = this.source.getSeries();
    if (!series) return;
    const zones = this.source.getZones();
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const width = scope.bitmapSize.width;
      const ratio = scope.verticalPixelRatio;
      ctx.save();
      ctx.font = `${Math.round(10 * ratio)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textBaseline = "bottom";
      for (const zone of zones) {
        const yHigh = series.priceToCoordinate(zone.high);
        const yLow = series.priceToCoordinate(zone.low);
        if (yHigh === null || yLow === null) continue;
        const top = Math.round(Math.min(yHigh, yLow) * ratio);
        const height = Math.max(Math.round(Math.abs(yLow - yHigh) * ratio), Math.round(2 * ratio));
        ctx.fillStyle = zone.fill;
        ctx.fillRect(0, top, width, height);
        ctx.fillStyle = zone.border;
        ctx.fillRect(0, top, width, Math.max(1, Math.round(ratio)));
        ctx.fillRect(0, top + height - Math.max(1, Math.round(ratio)), width, Math.max(1, Math.round(ratio)));
        if (zone.label) ctx.fillText(zone.label, Math.round(6 * scope.horizontalPixelRatio), top - Math.round(2 * ratio));
      }
      ctx.restore();
    });
  }
}
