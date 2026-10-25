import { expect, test, type Page, type Route } from "@playwright/test";

const DAY = 86_400;

function candles(count: number, base: number) {
  const start = Math.floor(Date.now() / 1000 / DAY) * DAY - (count - 1) * DAY;
  return Array.from({ length: count }, (_, i) => {
    const open = base + Math.sin(i / 6) * base * 0.05 + i * base * 0.001;
    const close = open + Math.cos(i / 3) * base * 0.01;
    return { time: start + i * DAY, open, high: Math.max(open, close) * 1.01, low: Math.min(open, close) * 0.99, close, volume: 1000 + i * 10 };
  });
}

function quote(symbol: string, price: number) {
  const now = new Date().toISOString();
  return {
    quote: {
      symbol, display_symbol: symbol, name: symbol === "ETH-USD" ? "Ethereum / USD" : "Bitcoin / USD", asset_type: "crypto",
      exchange: "Coinbase", currency: "USD", price, change: 12.5, change_percent: 0.54, change_basis: "rolling_24h", open: price - 12.5,
      high: price * 1.02, low: price * 0.98, previous_close: price - 12.5, volume: 1000, volume_label: "24h volume", bid: price - 0.01,
      ask: price + 0.01, fifty_two_week_high: null, fifty_two_week_low: null, as_of: now, provider: "coinbase", timezone: "UTC", market_open_flag: null,
    },
    session: { status: "always_open", label: "24/7 market", timezone: "UTC", is_open: true, note: null },
    freshness: { as_of: now, age_seconds: 1, status: "real_time", label: "Real-time", is_stale: false, detail: "Latest exchange data." },
    cache: { hit: false, stale: false, age_seconds: 0, warning: null },
    refresh_seconds: 5,
    source_note: "Coinbase Exchange public market data.",
  };
}

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const symbol = decodeURIComponent(path.split("/")[3] ?? "");
    const base = symbol === "ETH-USD" ? 2300 : 65000;
    if (path === "/api/health") {
      return json({ status: "ok", version: "1.0.0", environment: "test", time: new Date().toISOString(), database: { ok: true, detail: "ok" }, cache: { ok: true, detail: "memory" }, providers: [] });
    }
    if (path === "/api/meta") return json({ app_name: "MarketLens", version: "1.0.0", timeframes: [], asset_types: [], default_indicator_params: {}, factors: [], providers: [], disclaimer: "Educational." });
    if (path === "/api/workspaces") return json({ token: "e2e-token", created_at: new Date().toISOString() }, 201);
    if (path === "/api/watchlist") return json({ items: [], max_items: 50 });
    if (path === "/api/preferences") return json({});
    if (path === "/api/assets/search") {
      return json({
        query: url.searchParams.get("q"),
        results: [
          { symbol: "ETH-USD", display_symbol: "ETH-USD", name: "Ethereum / USD", asset_type: "crypto", exchange: "Coinbase", mic_code: null, country: null, currency: "USD", timezone: "UTC", provider: "coinbase" },
        ],
        notes: [],
      });
    }
    if (path.endsWith("/quote")) return json(quote(symbol, base));
    if (path.endsWith("/candles")) {
      const list = candles(Number(url.searchParams.get("limit") ?? 300), base);
      return json({
        symbol, display_symbol: symbol, asset_type: "crypto", provider: "coinbase", timeframe: url.searchParams.get("timeframe") ?? "1d",
        timezone: "UTC", candles: list, count: list.length, aggregated_from: null, last_bar_complete: false,
        freshness: { as_of: new Date().toISOString(), age_seconds: 0, status: "real_time", label: "Live bar", is_stale: false, detail: "current" },
        cache: { hit: false, stale: false, age_seconds: 0, warning: null }, refresh_seconds: 300, limitations: [],
      });
    }
    return json({ error: { code: "provider_unavailable", message: "Not mocked in this test." } }, 503);
  });
}

test("search for a symbol and display its chart", async ({ page }) => {
  test.skip(!!process.env.E2E_LIVE, "mocked test");
  await mockApi(page);
  await page.goto("/?symbol=BTC-USD");
  await expect(page.getByTestId("asset-symbol")).toHaveText("BTC-USD");

  const search = page.getByRole("combobox", { name: "Search symbol" });
  await search.fill("eth");
  const option = page.getByRole("option", { name: /ETH-USD/ });
  await expect(option).toBeVisible();
  await option.click();

  await expect(page).toHaveURL(/symbol=ETH-USD/);
  await expect(page.getByTestId("asset-symbol")).toHaveText("ETH-USD");
  await expect(page.getByTestId("asset-price")).toHaveText("2,300.00");
  const chart = page.getByTestId("price-chart");
  await expect(chart).toBeVisible();
  await expect(chart.locator("canvas").first()).toBeVisible();
  const box = await chart.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThan(300);
  await expect(page.getByText(/bars · Coinbase Exchange/)).toBeVisible();
});

test("live stack: real backend and Coinbase data", async ({ page }) => {
  test.skip(!process.env.E2E_LIVE, "set E2E_LIVE=1 with the backend running on :8000");
  await page.goto("/");
  const search = page.getByRole("combobox", { name: "Search symbol" });
  await search.fill("BTC-USD");
  await page.getByRole("option", { name: /BTC-USD/ }).first().click();
  await expect(page.getByTestId("asset-symbol")).toHaveText("BTC-USD");
  await expect(page.getByTestId("asset-price")).not.toHaveText("—");
  await expect(page.getByTestId("price-chart").locator("canvas").first()).toBeVisible();
  await expect(page.getByText("Market bias")).toBeVisible();
  await expect(page.getByTestId("scenario-bullish")).toBeVisible();
});
