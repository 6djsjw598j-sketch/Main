import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { mockFetch, quoteResponse, renderWithProviders } from "../test/utils";
import { ErrorState } from "./common/ErrorState";
import { AssetSummary } from "./panels/AssetSummary";
import { WatchlistPanel } from "./panels/WatchlistPanel";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const baseQuery = { error: null, isLoading: false, refetch: () => undefined };

describe("AssetSummary", () => {
  it("shows a loading skeleton", () => {
    renderWithProviders(
      <AssetSummary symbol="BTC-USD" query={{ ...baseQuery, isLoading: true }} inWatchlist={false} onToggleWatchlist={() => {}} watchlistBusy={false} />,
    );
    expect(screen.getByLabelText("Loading quote")).toBeInTheDocument();
  });

  it("renders price, change, session and freshness", () => {
    renderWithProviders(
      <AssetSummary symbol="BTC-USD" query={{ ...baseQuery, data: quoteResponse() as never }} inWatchlist={false} onToggleWatchlist={() => {}} watchlistBusy={false} />,
    );
    expect(screen.getByTestId("asset-price")).toHaveTextContent("65,432.10");
    expect(screen.getByText(/\+1,234\.50/)).toBeInTheDocument();
    expect(screen.getByText("24/7 market")).toBeInTheDocument();
    expect(screen.getByText("Real-time")).toBeInTheDocument();
    expect(screen.getByText("Price 24h ago")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add BTC-USD to watchlist/ })).toBeInTheDocument();
  });

  it("shows a stale-cache warning when the provider failed", () => {
    const data = quoteResponse();
    data.cache = { hit: true, stale: true, age_seconds: 120, warning: "Twelve Data rate limit reached. Showing cached data from 120s ago." } as never;
    renderWithProviders(
      <AssetSummary symbol="BTC-USD" query={{ ...baseQuery, data: data as never }} inWatchlist onToggleWatchlist={() => {}} watchlistBusy={false} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/cached data from 120s ago/);
  });

  it("shows provider setup instructions when stock data is not configured", () => {
    const error = new ApiError(503, "provider_not_configured", "Stock, ETF and index data requires a Twelve Data API key.");
    renderWithProviders(
      <AssetSummary symbol="AAPL" query={{ ...baseQuery, error }} inWatchlist={false} onToggleWatchlist={() => {}} watchlistBusy={false} />,
    );
    expect(screen.getByText("Market-data provider not configured")).toBeInTheDocument();
    expect(screen.getByText(/TWELVEDATA_API_KEY=your_key_here/)).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("explains unknown symbols", () => {
    renderWithProviders(<ErrorState error={new ApiError(404, "symbol_not_found", "Twelve Data does not recognise this symbol.")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Symbol not found");
  });

  it("shows an automatic retry countdown for rate limits", () => {
    renderWithProviders(<ErrorState error={new ApiError(429, "provider_rate_limited", "Limit reached.", 30)} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/Retrying automatically in ~30s/);
  });
});

describe("WatchlistPanel", () => {
  it("shows an empty state with popular assets", async () => {
    mockFetch({
      "/api/workspaces": { status: 201, body: { token: "tok", created_at: "2024-01-01T00:00:00Z" } },
      "/api/watchlist": { body: { items: [], max_items: 50 } },
    });
    renderWithProviders(<WatchlistPanel onSelect={() => {}} current={null} autoRefresh={false} />);
    expect(await screen.findByTestId("watchlist-empty")).toHaveTextContent(/watchlist is empty/);
    expect(screen.getByRole("button", { name: "AAPL" })).toBeInTheDocument();
  });

  it("lists items with their latest quotes", async () => {
    mockFetch({
      "/api/workspaces": { status: 201, body: { token: "tok", created_at: "2024-01-01T00:00:00Z" } },
      "/api/watchlist": { body: { items: [{ symbol: "BTC-USD", asset_type: "crypto", position: 0, added_at: "2024-01-01T00:00:00Z" }], max_items: 50 } },
      "/api/market/overview": { body: { items: [{ symbol: "BTC-USD", quote: quoteResponse().quote, freshness: quoteResponse().freshness, error: null }] } },
    });
    renderWithProviders(<WatchlistPanel onSelect={() => {}} current={null} autoRefresh={false} />);
    expect(await screen.findByText("65,432.10")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove BTC-USD" })).toBeInTheDocument();
  });
});
