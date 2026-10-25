// Number and time formatting helpers.

export function pricePrecision(value: number | null | undefined): number {
  const v = Math.abs(value ?? 0);
  if (v >= 1) return 2;
  if (v >= 0.01) return 4;
  if (v === 0) return 2;
  return 8;
}

export function formatPrice(value: number | null | undefined, precision?: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const digits = precision ?? pricePrecision(value);
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatSigned(value: number | null | undefined, precision?: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const text = formatPrice(Math.abs(value), precision ?? pricePrecision(value));
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${text}`;
}

export function formatPct(value: number | null | undefined, digits = 2, signed = true): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : "";
  return `${sign}${Math.abs(value).toFixed(digits)}%`;
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} days`;
}

export function changeTone(value: number | null | undefined): "up" | "down" | "flat" {
  if (value === null || value === undefined || value === 0 || !Number.isFinite(value)) return "flat";
  return value > 0 ? "up" : "down";
}
