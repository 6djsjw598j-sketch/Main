import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import { PreferencesProvider } from "../state/preferences";

export function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PreferencesProvider syncWithServer={false}>{ui}</PreferencesProvider>
    </QueryClientProvider>,
  );
}

interface MockResult {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}
type Handler = (url: URL, init?: RequestInit) => MockResult | undefined;

/** Routes fetch() calls to handlers keyed by path prefix; unknown paths return 404. */
export function mockFetch(routes: Record<string, Handler | MockResult>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const entry = Object.entries(routes)
      .sort((a, b) => b[0].length - a[0].length)
      .find(([prefix]) => url.pathname.startsWith(prefix));
    const result = entry ? (typeof entry[1] === "function" ? entry[1](url, init) : entry[1]) : undefined;
    const status = result?.status ?? (result ? 200 : 404);
    const body = result?.body ?? (result ? {} : { error: { code: "not_found", message: `No mock for ${url.pathname}` } });
    return new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...(result?.headers ?? {}) },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const NOW_ISO = new Date().toISOString();

export function quoteResponse(overrides: Record<string, unknown> = {}) {
  return {
    quote: {
      symbol: "BTC-USD", display_symbol: "BTC-USD", name: "Bitcoin / USD", asset_type: "crypto", exchange: "Coinbase",
      currency: "USD", price: 65432.1, change: 1234.5, change_percent: 1.92, change_basis: "rolling_24h", open: 64197.6,
      high: 66000, low: 63900, previous_close: 64197.6, volume: 12345, volume_label: "24h volume (BTC)", bid: 65432,
      ask: 65432.2, fifty_two_week_high: null, fifty_two_week_low: null, as_of: NOW_ISO, provider: "coinbase", timezone: "UTC",
      market_open_flag: null,
      ...overrides,
    },
    session: { status: "always_open", label: "24/7 market", timezone: "UTC", is_open: true, note: null },
    freshness: { as_of: NOW_ISO, age_seconds: 2, status: "real_time", label: "Real-time", is_stale: false, detail: "Latest exchange data, updated 2s ago." },
    cache: { hit: false, stale: false, age_seconds: 0, warning: null },
    refresh_seconds: 5,
    source_note: "Coinbase Exchange public market data.",
  };
}

export function candlesResponse(count = 50, symbol = "BTC-USD") {
  const start = Math.floor(Date.now() / 1000 / 86400) * 86400 - (count - 1) * 86400;
  return {
    symbol, display_symbol: symbol, asset_type: "crypto", provider: "coinbase", timeframe: "1d", timezone: "UTC",
    candles: Array.from({ length: count }, (_, i) => ({
      time: start + i * 86400, open: 100 + i, high: 102 + i, low: 99 + i, close: 101 + i, volume: 1000 + i,
    })),
    count, aggregated_from: null, last_bar_complete: false,
    freshness: { as_of: NOW_ISO, age_seconds: 0, status: "real_time", label: "Live bar", is_stale: false, detail: "current bar" },
    cache: { hit: false, stale: false, age_seconds: 0, warning: null },
    refresh_seconds: 300,
    limitations: ["Coinbase publishes no candle for intervals without trades; such gaps are left empty."],
  };
}
