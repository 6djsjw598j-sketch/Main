import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "./App";
import { candlesResponse, mockFetch, quoteResponse, renderWithProviders } from "./test/utils";

// lightweight-charts draws on <canvas>, which jsdom does not implement.
vi.mock("./components/chart/PriceChart", () => ({
  PriceChart: (props: { candles: unknown[] }) => <div data-testid="price-chart">{props.candles.length} bars</div>,
}));

const HEALTH = {
  status: "ok", version: "1.0.0", environment: "test", time: new Date().toISOString(),
  database: { ok: true, detail: "connected" }, cache: { ok: true, detail: "memory" }, providers: [],
};

beforeEach(() => {
  window.history.replaceState({}, "", "/?symbol=BTC-USD");
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const workspace = {
  "/api/workspaces": { status: 201, body: { token: "tok", created_at: "2024-01-01T00:00:00Z" } },
  "/api/watchlist": { body: { items: [], max_items: 50 } },
  "/api/health": { body: HEALTH },
};

describe("Dashboard", () => {
  it("loads the quote and chart for the symbol in the URL", async () => {
    mockFetch({
      ...workspace,
      "/api/market/BTC-USD/quote": { body: quoteResponse() },
      "/api/market/BTC-USD/candles": { body: candlesResponse(60) },
      "/api/analysis": { status: 503, body: { error: { code: "provider_unavailable", message: "Analysis offline in this test." } } },
    });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByTestId("asset-price")).toHaveTextContent("65,432.10");
    expect(await screen.findByTestId("price-chart")).toHaveTextContent("60 bars");
    expect(screen.getByText(/60 bars · Coinbase Exchange/)).toBeInTheDocument();
  });

  it("shows a clear error for an unknown symbol", async () => {
    window.history.replaceState({}, "", "/?symbol=ZZZZ");
    const notFound = { status: 404, body: { error: { code: "symbol_not_found", message: "Twelve Data does not recognise this symbol." } } };
    mockFetch({ ...workspace, "/api/market/ZZZZ": notFound, "/api/analysis/ZZZZ": notFound });
    renderWithProviders(<Dashboard />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("Symbol not found"))).toBe(true);
  });

  it("shows an empty state when the provider returns no candles", async () => {
    mockFetch({
      ...workspace,
      "/api/market/BTC-USD/quote": { body: quoteResponse() },
      "/api/market/BTC-USD/candles": { body: { ...candlesResponse(0), candles: [], count: 0 } },
      "/api/analysis": { status: 422, body: { error: { code: "insufficient_data", message: "Only 0 bars are available." } } },
    });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByText(/returned no 1d candles/)).toBeInTheDocument();
  });

  it("switches timeframe and requests new candles", async () => {
    const fetchMock = mockFetch({
      ...workspace,
      "/api/market/BTC-USD/quote": { body: quoteResponse() },
      "/api/market/BTC-USD/candles": { body: candlesResponse(30) },
      "/api/analysis": { status: 503, body: { error: { code: "provider_unavailable", message: "offline" } } },
    });
    renderWithProviders(<Dashboard />);
    await screen.findByTestId("price-chart");
    await userEvent.click(screen.getByRole("button", { name: "4h" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/candles") && String(url).includes("timeframe=4h"))).toBe(true),
    );
  });
});
