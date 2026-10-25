// TypeScript mirrors of the backend's Pydantic response models (backend/app/models/*.py).

export type AssetType = "auto" | "stock" | "etf" | "index" | "crypto" | "forex" | "fund" | "other";
export type Timeframe = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w" | "1M";
export const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"];
export const INTRADAY: Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h"];

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface AssetInfo {
  symbol: string;
  display_symbol: string;
  name: string | null;
  asset_type: AssetType;
  exchange: string | null;
  mic_code: string | null;
  country: string | null;
  currency: string | null;
  timezone: string;
  provider: string;
}

export interface SearchResponse {
  query: string;
  results: AssetInfo[];
  notes: string[];
}

export type FreshnessStatus = "real_time" | "delayed" | "end_of_day" | "stale";

export interface Freshness {
  as_of: string;
  age_seconds: number;
  status: FreshnessStatus;
  label: string;
  is_stale: boolean;
  detail: string;
}

export interface MarketSession {
  status: "open" | "closed" | "pre_market" | "after_hours" | "always_open" | "unknown";
  label: string;
  timezone: string;
  is_open: boolean;
  note: string | null;
}

export interface CacheInfo {
  hit: boolean;
  stale: boolean;
  age_seconds: number;
  warning: string | null;
}

export interface Quote {
  symbol: string;
  display_symbol: string;
  name: string | null;
  asset_type: AssetType;
  exchange: string | null;
  currency: string | null;
  price: number;
  change: number | null;
  change_percent: number | null;
  change_basis: "previous_close" | "rolling_24h";
  open: number | null;
  high: number | null;
  low: number | null;
  previous_close: number | null;
  volume: number | null;
  volume_label: string | null;
  bid: number | null;
  ask: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  as_of: string;
  provider: string;
  timezone: string;
  market_open_flag: boolean | null;
}

export interface QuoteResponse {
  quote: Quote;
  session: MarketSession;
  freshness: Freshness;
  cache: CacheInfo;
  refresh_seconds: number;
  source_note: string;
}

export interface CandlesResponse {
  symbol: string;
  display_symbol: string;
  asset_type: AssetType;
  provider: string;
  timeframe: Timeframe;
  timezone: string;
  candles: Candle[];
  count: number;
  aggregated_from: string | null;
  last_bar_complete: boolean;
  freshness: Freshness;
  cache: CacheInfo;
  refresh_seconds: number;
  limitations: string[];
}

export interface OverviewItem {
  symbol: string;
  quote: Quote | null;
  freshness: Freshness | null;
  error: { code: string; message: string } | null;
}

export interface OverviewResponse {
  items: OverviewItem[];
}

// ------------------------------------------------------------------ analysis
export type IndicatorState =
  | "bullish"
  | "bearish"
  | "neutral"
  | "overbought"
  | "oversold"
  | "high"
  | "low"
  | "insufficient_data"
  | "unavailable";

export interface IndicatorReading {
  key: string;
  name: string;
  category: "trend" | "momentum" | "volatility" | "volume";
  values: Record<string, number | null>;
  display: string;
  state: IndicatorState;
  interpretation: string;
  params: Record<string, number | string>;
  placement: "overlay" | "panel";
  series_keys: string[];
}

export interface AnalysisContext {
  symbol: string;
  display_symbol: string;
  asset_type: AssetType;
  provider: string;
  timeframe: Timeframe;
  bars: number;
  as_of: string;
  last_price: number;
  last_bar_complete: boolean;
}

export interface IndicatorsResponse {
  context: AnalysisContext;
  times: number[];
  series: Record<string, (number | null)[]>;
  future_times: number[];
  future_series: Record<string, (number | null)[]>;
  readings: IndicatorReading[];
  notes: string[];
  disclaimer: string;
}

export type LevelStatus = "intact" | "testing" | "breakout" | "breakdown" | "reclaimed" | "lost";

export interface Level {
  id: string;
  kind: "support" | "resistance";
  tier: "major" | "minor";
  price: number;
  zone_low: number;
  zone_high: number;
  touches: number;
  reactions: number;
  strength: number;
  score_breakdown: Record<string, number>;
  first_seen: string;
  last_reaction: string;
  distance: number;
  distance_pct: number;
  status: LevelStatus;
  status_detail: string;
  status_changed_at: string | null;
  historical_role: "support" | "resistance" | "mixed";
}

export interface ReferenceLevel {
  key: string;
  name: string;
  price: number;
  period_start: string;
  period_end: string;
  distance: number;
  distance_pct: number;
  position: "above" | "below" | "at";
  source: "observed";
}

export interface PivotLevels {
  basis: string;
  method: string;
  levels: Record<string, number>;
}

export interface FibonacciLevels {
  direction: "up" | "down";
  swing_high: number;
  swing_high_time: string;
  swing_low: number;
  swing_low_time: string;
  levels: { ratio: number; price: number }[];
  note: string;
}

export interface TrendPoint {
  time: number;
  price: number;
}

export interface Trendline {
  kind: "support" | "resistance";
  points: TrendPoint[];
  anchors: TrendPoint[];
  slope_per_bar: number;
  current_value: number;
  touches: number;
  broken: boolean;
  description: string;
}

export interface SupportResistanceResponse {
  context: AnalysisContext;
  params: { lookback: number; swing_window: number; tolerance: number; major_threshold: number; max_levels: number };
  atr: number | null;
  levels: Level[];
  nearest_support: Level | null;
  nearest_resistance: Level | null;
  reference_levels: ReferenceLevel[];
  pivots: PivotLevels | null;
  fibonacci: FibonacciLevels | null;
  trendlines: Trendline[];
  methodology: string[];
  disclaimer: string;
}

export type Direction = "bullish" | "bearish" | "neutral" | "insufficient_data";

export interface FactorContribution {
  key: string;
  name: string;
  group: "trend" | "momentum" | "volume";
  weight: number;
  value: number | null;
  contribution: number | null;
  signal: Direction;
  detail: string;
  available: boolean;
}

export interface TechnicalEvent {
  time: number;
  type: string;
  direction: "bullish" | "bearish" | "neutral";
  description: string;
  price: number | null;
}

export interface SummaryResponse {
  context: AnalysisContext;
  overall: {
    bias: "bullish" | "bearish" | "neutral";
    score: number;
    bullish_threshold: number;
    bearish_threshold: number;
    factors_used: number;
    factors_total: number;
    explanation: string;
  };
  factors: FactorContribution[];
  conflicts: string[];
  trend: { horizon: "short" | "medium" | "long"; direction: Direction; explanation: string }[];
  momentum: { state: "positive" | "negative" | "mixed" | "insufficient_data"; explanation: string };
  volatility: {
    state: "low" | "moderate" | "high" | "insufficient_data";
    atr: number | null;
    atr_pct: number | null;
    percentile: number | null;
    baseline_bars: number;
    squeeze: boolean;
    explanation: string;
  };
  volume: { state: "confirming" | "diverging" | "neutral" | "unavailable"; relative_volume: number | null; explanation: string };
  nearest_support: Level | null;
  nearest_resistance: Level | null;
  conditions: { type: string; active: boolean; level: number | null; description: string }[];
  key_levels: { role: "confirmation" | "invalidation" | "bullish_trigger" | "bearish_trigger"; price: number; description: string }[];
  events: TechnicalEvent[];
  methodology: string[];
  disclaimer: string;
}

export interface TradeScenario {
  direction: "bullish" | "bearish";
  available: boolean;
  unavailable_reason: string | null;
  setup_type: string | null;
  alignment: "with_bias" | "counter_bias" | "neutral_bias" | null;
  entry_zone_low: number | null;
  entry_zone_high: number | null;
  entry_reference: number | null;
  invalidation: number | null;
  risk_per_unit: number | null;
  targets: { price: number; basis: string; reward_risk: number | null }[];
  reward_risk: number | null;
  confirmation_conditions: string[];
  invalidation_conditions: string[];
  notes: string[];
}

export interface SetupsResponse {
  context: AnalysisContext;
  observed: Record<string, number | null>;
  bullish: TradeScenario;
  bearish: TradeScenario;
  methodology: string[];
  label: string;
  disclaimer: string;
}

export interface BacktestResponse {
  context: AnalysisContext;
  assumptions: {
    direction: "long_only" | "long_short";
    entry_threshold: number;
    exit_threshold: number;
    commission_bps: number;
    slippage_bps: number;
    execution: string;
    evaluation_start: number;
    evaluation_end: number;
    bars_evaluated: number;
  };
  metrics: {
    trades: number;
    win_rate: number | null;
    avg_return_pct: number | null;
    median_return_pct: number | null;
    best_return_pct: number | null;
    worst_return_pct: number | null;
    profit_factor: number | null;
    total_return_pct: number;
    buy_and_hold_return_pct: number;
    max_drawdown_pct: number;
    exposure_pct: number;
    avg_bars_held: number | null;
  };
  trades: {
    direction: "long" | "short";
    entry_time: number;
    entry_price: number;
    exit_time: number;
    exit_price: number;
    return_pct: number;
    bars_held: number;
    open: boolean;
  }[];
  equity_curve: TrendPoint[];
  benchmark_curve: TrendPoint[];
  caveats: string[];
  disclaimer: string;
}

// ------------------------------------------------------------------ system
export interface TimeframeSupport {
  timeframe: Timeframe;
  native: boolean;
  source: string;
  note: string | null;
}

export interface ProviderStatus {
  name: string;
  display_name: string;
  asset_classes: string[];
  configured: boolean;
  status: "ok" | "degraded" | "down" | "not_configured" | "unknown";
  last_success_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  data_type: string;
  limits: string;
  credits_remaining_today: number | null;
  timeframes: TimeframeSupport[];
  notes: string[];
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error";
  version: string;
  environment: string;
  time: string;
  database: { ok: boolean; detail: string };
  cache: { ok: boolean; detail: string };
  providers: ProviderStatus[];
}

export interface IndicatorParams {
  sma_periods: number[];
  ema_periods: number[];
  rsi_period: number;
  rsi_overbought: number;
  rsi_oversold: number;
  macd_fast: number;
  macd_slow: number;
  macd_signal: number;
  stoch_k: number;
  stoch_smooth: number;
  stoch_d: number;
  roc_period: number;
  willr_period: number;
  bb_period: number;
  bb_std: number;
  atr_period: number;
  kc_period: number;
  kc_atr_period: number;
  kc_mult: number;
  hv_period: number;
  adx_period: number;
  ichimoku_tenkan: number;
  ichimoku_kijun: number;
  ichimoku_senkou_b: number;
  volume_sma_period: number;
  mfi_period: number;
}

export interface MetaResponse {
  app_name: string;
  version: string;
  timeframes: Timeframe[];
  asset_types: AssetType[];
  default_indicator_params: IndicatorParams;
  factors: { key: string; name: string; group: string; default_weight: number; formula: string }[];
  providers: ProviderStatus[];
  disclaimer: string;
}

// ------------------------------------------------------------------ user
export interface WatchlistItem {
  symbol: string;
  asset_type: AssetType;
  position: number;
  added_at: string;
}

export interface WatchlistResponse {
  items: WatchlistItem[];
  max_items: number;
}

export type OverlayKey = "sma" | "ema" | "vwap" | "bbands" | "keltner" | "ichimoku";
export type PanelKey = "rsi" | "macd" | "stoch" | "adx" | "atr" | "roc" | "willr" | "obv" | "mfi" | "ad" | "hv";

export interface Preferences {
  theme: "dark" | "light";
  timeframe: Timeframe;
  chart_type: "candles" | "bars" | "line";
  timezone_mode: "local" | "utc" | "exchange";
  last_symbol: string | null;
  last_asset_type: AssetType;
  bars: number;
  overlays: OverlayKey[];
  panels: PanelKey[];
  show_volume: boolean;
  show_levels: boolean;
  show_reference_levels: boolean;
  show_pivots: boolean;
  show_fibonacci: boolean;
  show_trendlines: boolean;
  auto_refresh: boolean;
  indicator_params: Partial<IndicatorParams>;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown>; retry_after?: number };
}
