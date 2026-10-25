import type { Preferences, Timeframe } from "../api/types";
import { INTRADAY } from "../api/types";

export type TimezoneMode = Preferences["timezone_mode"];

/** IANA zone used for display; `undefined` means the browser's local zone. */
export function displayZone(mode: TimezoneMode, exchangeTz: string): string | undefined {
  if (mode === "utc") return "UTC";
  if (mode === "exchange") return exchangeTz || "UTC";
  return undefined;
}

export function isIntraday(tf: Timeframe): boolean {
  return INTRADAY.includes(tf);
}

const cache = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: string | undefined, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${zone ?? "local"}|${JSON.stringify(options)}`;
  let fmt = cache.get(key);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", { ...options, timeZone: zone });
    cache.set(key, fmt);
  }
  return fmt;
}

/**
 * Formats a bar time. Daily and longer bars are session dates (stored at 00:00 UTC), so
 * they are always formatted in UTC to avoid showing the previous day west of Greenwich.
 */
export function formatBarTime(unixSeconds: number, tf: Timeframe, zone: string | undefined): string {
  const date = new Date(unixSeconds * 1000);
  if (!isIntraday(tf)) {
    const opts: Intl.DateTimeFormatOptions =
      tf === "1M" ? { year: "numeric", month: "short" } : { year: "numeric", month: "short", day: "2-digit" };
    return formatter("UTC", opts).format(date);
  }
  return formatter(zone, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

export function formatTick(unixSeconds: number, tf: Timeframe, zone: string | undefined, tickType: number): string {
  const date = new Date(unixSeconds * 1000);
  const z = isIntraday(tf) ? zone : "UTC";
  switch (tickType) {
    case 0: // year
      return formatter(z, { year: "numeric" }).format(date);
    case 1: // month
      return formatter(z, { month: "short" }).format(date);
    case 2: // day of month
      return formatter(z, { month: "short", day: "numeric" }).format(date);
    default:
      return formatter(z, { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }
}

export function formatDateTime(iso: string | number, zone?: string): string {
  const date = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return formatter(zone, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(date);
}

export function zoneLabel(mode: TimezoneMode, exchangeTz: string): string {
  if (mode === "utc") return "UTC";
  if (mode === "exchange") return exchangeTz;
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Local";
}
