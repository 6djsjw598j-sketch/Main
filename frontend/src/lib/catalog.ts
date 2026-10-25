import type { IndicatorParams, OverlayKey, PanelKey } from "../api/types";

export interface LineSpec {
  key: string;
  label: string;
  color: string;
  width?: 1 | 2 | 3;
  dashed?: boolean;
  histogram?: boolean;
}

export interface GuideSpec {
  value: number;
  label: string;
}

export interface OverlayDef {
  key: OverlayKey;
  label: string;
  description: string;
  lines: (p: IndicatorParams) => LineSpec[];
}

export interface PanelDef {
  key: PanelKey;
  label: string;
  description: string;
  lines: (p: IndicatorParams) => LineSpec[];
  guides?: (p: IndicatorParams) => GuideSpec[];
  needsVolume?: boolean;
}

const MA_COLORS = ["#f5a524", "#e040fb", "#29b6f6", "#ff7043", "#9ccc65", "#ffee58"];

export const OVERLAYS: OverlayDef[] = [
  {
    key: "sma",
    label: "SMA",
    description: "Simple moving averages",
    lines: (p) => p.sma_periods.map((n, i) => ({ key: `sma_${n}`, label: `SMA ${n}`, color: MA_COLORS[i % MA_COLORS.length] })),
  },
  {
    key: "ema",
    label: "EMA",
    description: "Exponential moving averages",
    lines: (p) =>
      p.ema_periods.map((n, i) => ({ key: `ema_${n}`, label: `EMA ${n}`, color: MA_COLORS[(i + 2) % MA_COLORS.length], dashed: true })),
  },
  {
    key: "vwap",
    label: "VWAP",
    description: "Session VWAP (intraday only)",
    lines: () => [{ key: "vwap", label: "VWAP", color: "#00bcd4", width: 2 }],
  },
  {
    key: "bbands",
    label: "Bollinger",
    description: "Bollinger Bands",
    lines: () => [
      { key: "bb_upper", label: "BB upper", color: "#7e57c2" },
      { key: "bb_middle", label: "BB mid", color: "#7e57c2", dashed: true },
      { key: "bb_lower", label: "BB lower", color: "#7e57c2" },
    ],
  },
  {
    key: "keltner",
    label: "Keltner",
    description: "Keltner Channels",
    lines: () => [
      { key: "kc_upper", label: "KC upper", color: "#26c6da", dashed: true },
      { key: "kc_middle", label: "KC mid", color: "#26c6da", dashed: true },
      { key: "kc_lower", label: "KC lower", color: "#26c6da", dashed: true },
    ],
  },
  {
    key: "ichimoku",
    label: "Ichimoku",
    description: "Ichimoku Cloud",
    lines: () => [
      { key: "ichimoku_tenkan", label: "Tenkan", color: "#2196f3" },
      { key: "ichimoku_kijun", label: "Kijun", color: "#b71c1c" },
      { key: "ichimoku_senkou_a", label: "Span A", color: "#43a047" },
      { key: "ichimoku_senkou_b", label: "Span B", color: "#e53935" },
      { key: "ichimoku_chikou", label: "Chikou", color: "#9e9e9e", dashed: true },
    ],
  },
];

export const PANELS: PanelDef[] = [
  {
    key: "rsi",
    label: "RSI",
    description: "Relative Strength Index",
    lines: (p) => [{ key: "rsi", label: `RSI ${p.rsi_period}`, color: "#ab47bc", width: 2 }],
    guides: (p) => [
      { value: p.rsi_overbought, label: "OB" },
      { value: 50, label: "" },
      { value: p.rsi_oversold, label: "OS" },
    ],
  },
  {
    key: "macd",
    label: "MACD",
    description: "MACD line, signal and histogram",
    lines: () => [
      { key: "macd_histogram", label: "Hist", color: "#26a69a", histogram: true },
      { key: "macd", label: "MACD", color: "#2962ff", width: 2 },
      { key: "macd_signal", label: "Signal", color: "#ff6d00" },
    ],
    guides: () => [{ value: 0, label: "" }],
  },
  {
    key: "stoch",
    label: "Stochastic",
    description: "Stochastic oscillator",
    lines: () => [
      { key: "stoch_k", label: "%K", color: "#2962ff", width: 2 },
      { key: "stoch_d", label: "%D", color: "#ff6d00" },
    ],
    guides: () => [
      { value: 80, label: "" },
      { value: 20, label: "" },
    ],
  },
  {
    key: "adx",
    label: "ADX/DMI",
    description: "Average Directional Index with +DI/-DI",
    lines: () => [
      { key: "adx", label: "ADX", color: "#f5a524", width: 2 },
      { key: "plus_di", label: "+DI", color: "#26a69a" },
      { key: "minus_di", label: "−DI", color: "#ef5350" },
    ],
    guides: () => [{ value: 25, label: "" }],
  },
  {
    key: "atr",
    label: "ATR",
    description: "Average True Range",
    lines: (p) => [{ key: "atr", label: `ATR ${p.atr_period}`, color: "#ef6c00", width: 2 }],
  },
  {
    key: "roc",
    label: "ROC",
    description: "Rate of Change (%)",
    lines: (p) => [{ key: "roc", label: `ROC ${p.roc_period}`, color: "#00897b", width: 2 }],
    guides: () => [{ value: 0, label: "" }],
  },
  {
    key: "willr",
    label: "Williams %R",
    description: "Williams %R",
    lines: (p) => [{ key: "willr", label: `%R ${p.willr_period}`, color: "#8e24aa", width: 2 }],
    guides: () => [
      { value: -20, label: "" },
      { value: -80, label: "" },
    ],
  },
  {
    key: "obv",
    label: "OBV",
    description: "On-Balance Volume",
    needsVolume: true,
    lines: () => [{ key: "obv", label: "OBV", color: "#42a5f5", width: 2 }],
  },
  {
    key: "mfi",
    label: "MFI",
    description: "Money Flow Index",
    needsVolume: true,
    lines: (p) => [{ key: "mfi", label: `MFI ${p.mfi_period}`, color: "#7cb342", width: 2 }],
    guides: () => [
      { value: 80, label: "" },
      { value: 20, label: "" },
    ],
  },
  {
    key: "ad",
    label: "A/D",
    description: "Accumulation/Distribution line",
    needsVolume: true,
    lines: () => [{ key: "ad", label: "A/D", color: "#5c6bc0", width: 2 }],
  },
  {
    key: "hv",
    label: "Hist. vol",
    description: "Historical volatility (annualized %)",
    lines: (p) => [{ key: "hv", label: `HV ${p.hv_period}`, color: "#ec407a", width: 2 }],
  },
];

export const DEFAULT_PARAMS: IndicatorParams = {
  sma_periods: [20, 50, 100, 200],
  ema_periods: [9, 12, 21, 50, 200],
  rsi_period: 14,
  rsi_overbought: 70,
  rsi_oversold: 30,
  macd_fast: 12,
  macd_slow: 26,
  macd_signal: 9,
  stoch_k: 14,
  stoch_smooth: 3,
  stoch_d: 3,
  roc_period: 12,
  willr_period: 14,
  bb_period: 20,
  bb_std: 2,
  atr_period: 14,
  kc_period: 20,
  kc_atr_period: 10,
  kc_mult: 2,
  hv_period: 20,
  adx_period: 14,
  ichimoku_tenkan: 9,
  ichimoku_kijun: 26,
  ichimoku_senkou_b: 52,
  volume_sma_period: 20,
  mfi_period: 14,
};

export interface ParamField {
  key: keyof IndicatorParams;
  label: string;
  min: number;
  max: number;
  step?: number;
  list?: boolean;
}

export const PARAM_GROUPS: { title: string; fields: ParamField[] }[] = [
  {
    title: "Moving averages",
    fields: [
      { key: "sma_periods", label: "SMA periods", min: 2, max: 400, list: true },
      { key: "ema_periods", label: "EMA periods", min: 2, max: 400, list: true },
    ],
  },
  {
    title: "Momentum",
    fields: [
      { key: "rsi_period", label: "RSI period", min: 2, max: 100 },
      { key: "rsi_overbought", label: "RSI overbought", min: 50, max: 95 },
      { key: "rsi_oversold", label: "RSI oversold", min: 5, max: 50 },
      { key: "macd_fast", label: "MACD fast", min: 2, max: 100 },
      { key: "macd_slow", label: "MACD slow", min: 3, max: 200 },
      { key: "macd_signal", label: "MACD signal", min: 2, max: 50 },
      { key: "stoch_k", label: "Stoch %K", min: 2, max: 100 },
      { key: "stoch_smooth", label: "Stoch smoothing", min: 1, max: 20 },
      { key: "stoch_d", label: "Stoch %D", min: 1, max: 20 },
      { key: "roc_period", label: "ROC period", min: 1, max: 200 },
      { key: "willr_period", label: "Williams %R period", min: 2, max: 100 },
    ],
  },
  {
    title: "Trend",
    fields: [
      { key: "adx_period", label: "ADX period", min: 2, max: 100 },
      { key: "ichimoku_tenkan", label: "Ichimoku tenkan", min: 2, max: 100 },
      { key: "ichimoku_kijun", label: "Ichimoku kijun", min: 2, max: 200 },
      { key: "ichimoku_senkou_b", label: "Ichimoku senkou B", min: 2, max: 300 },
    ],
  },
  {
    title: "Volatility",
    fields: [
      { key: "bb_period", label: "Bollinger period", min: 2, max: 200 },
      { key: "bb_std", label: "Bollinger std dev", min: 0.5, max: 5, step: 0.1 },
      { key: "atr_period", label: "ATR period", min: 2, max: 100 },
      { key: "kc_period", label: "Keltner EMA", min: 2, max: 200 },
      { key: "kc_atr_period", label: "Keltner ATR", min: 2, max: 100 },
      { key: "kc_mult", label: "Keltner multiplier", min: 0.5, max: 5, step: 0.1 },
      { key: "hv_period", label: "Hist. vol period", min: 5, max: 250 },
    ],
  },
  {
    title: "Volume",
    fields: [
      { key: "volume_sma_period", label: "Volume average", min: 2, max: 200 },
      { key: "mfi_period", label: "MFI period", min: 2, max: 100 },
    ],
  },
];

export function resolveParams(overrides: Partial<IndicatorParams>): IndicatorParams {
  return { ...DEFAULT_PARAMS, ...overrides };
}

/** Query-string form of the params that differ from the defaults (keeps URLs short and cache keys stable). */
export function paramsQuery(overrides: Partial<IndicatorParams>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(overrides)) {
    const def = DEFAULT_PARAMS[key as keyof IndicatorParams];
    if (value === undefined || JSON.stringify(value) === JSON.stringify(def)) continue;
    out[key] = Array.isArray(value) ? value.join(",") : (value as number);
  }
  return out;
}

export const POPULAR = [
  { symbol: "BTC-USD", type: "crypto" },
  { symbol: "ETH-USD", type: "crypto" },
  { symbol: "SOL-USD", type: "crypto" },
  { symbol: "AAPL", type: "stock" },
  { symbol: "TSLA", type: "stock" },
  { symbol: "NVDA", type: "stock" },
  { symbol: "MSFT", type: "stock" },
  { symbol: "AMZN", type: "stock" },
  { symbol: "SPY", type: "etf" },
  { symbol: "QQQ", type: "etf" },
] as const;
