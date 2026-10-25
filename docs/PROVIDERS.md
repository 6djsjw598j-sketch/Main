# Market-data providers

## Choice

The goal was the simplest combination that is reliable, legal to use, cheap, and covers
stocks, ETFs and crypto on every required timeframe.

| Need | Chosen | Why |
|---|---|---|
| Crypto | **Coinbase Exchange public market data** | No key, free, real-time trades/ticker/24h stats, OHLCV at 1m–1d. Publicly documented rate limits (10 req/s per IP, bursts to 15). Widely accessible (Binance's API is geo-restricted in many regions, including some cloud regions). |
| Stocks, ETFs, indices, FX | **Twelve Data** | One REST API with quotes, all intraday-to-monthly intervals (1min…1month, up to 5,000 bars per call) and symbol search. Its free Basic plan is enough for personal use. |

Alternatives considered:

- **Finnhub**: generous free real-time US quotes, but historical candles are a paid feature,
  so charts would need a second provider.
- **Alpha Vantage**: free tier is a small number of requests per day, too few for a
  multi-timeframe dashboard.
- **Polygon.io (now Massive)**: excellent data, but its free tier is end-of-day only and
  rate-limited, and real-time needs a paid tier.

Twelve Data can also serve crypto. MarketLens prefers Coinbase for Coinbase-listed pairs and
falls back to Twelve Data for other pairs (e.g. FX like `EUR-USD`, or crypto not listed on
Coinbase) when a key is configured.

## What each provider supplies

### Coinbase Exchange (`providers/coinbase.py`)

| Data | Endpoint | Notes |
|---|---|---|
| Product list (search, validation) | `GET /products` | Cached 6 h. Delisted products are excluded |
| Currency names | `GET /currencies` | Cached 24 h |
| Last trade, bid/ask | `GET /products/{id}/ticker` | Trade timestamp drives freshness |
| 24h open/high/low/volume | `GET /products/{id}/stats` | Change is measured over a **rolling 24 h** window (crypto has no close) |
| Candles | `GET /products/{id}/candles` | Granularities 60/300/900/3600/21600/86400 s; **max 300 per request**, so MarketLens pages back automatically (up to ~7,200 source candles) |

Limitations:

- The price is Coinbase's own last trade, not a cross-exchange index.
- No candle is published for intervals without trades; gaps stay empty.
- About 5 days of 1-minute history per request window.
- Monthly history begins at the product's listing on Coinbase.

### Twelve Data (`providers/twelvedata.py`)

| Data | Endpoint | Credits |
|---|---|---|
| Quote (price, OHLC, previous close, volume, 52-week range, market-open flag) | `GET /quote` | 1 per symbol |
| OHLCV time series | `GET /time_series` (`interval` 1min…1month, `outputsize` ≤ 5000) | 1 |
| Search | `GET /symbol_search` | counted as 1 (conservative) |

- The key goes in the `Authorization: apikey …` header, never in URLs or logs.
- Intraday bars are requested in UTC; daily+ bars use exchange-local session dates.
- Error codes are mapped to typed errors: 401 → `provider_auth_failed`, 403 → `provider_plan_restriction`,
  404/"not found" → `symbol_not_found`, 429 → `provider_rate_limited`, 5xx → `provider_unavailable`.
- Local rationing: `TWELVEDATA_CREDITS_PER_MINUTE` (token bucket) and `TWELVEDATA_CREDITS_PER_DAY`
  (reset 00:00 UTC). Requests over budget fail fast with `retry_after` instead of reaching the API.

Typical credit use: opening a stock costs about **3 credits** (quote + candles + daily bars for
prior-period levels). All analysis endpoints reuse those cached candles. Polling then costs
about one credit per minute while the market is open, and far less when it is closed, because
cache TTLs stretch to an hour. Watchlist quotes cost one credit per stock symbol per refresh
(every 60 s).

## Plans and what works on free tiers

From each provider's public page on **2026-10-25** (verify before purchasing):

| Plan | Price | Limits | Works for |
|---|---|---|---|
| Coinbase public data | Free | 10 req/s per IP | All crypto features |
| Twelve Data Basic | Free | 8 credits/min, 800/day; the pricing page lists real-time US equities and ETFs | One user, a handful of stock symbols |
| Twelve Data Grow | $79/mo ($66/mo yearly) | 55+ credits/min, no daily limit, more markets | Several users, bigger watchlists |
| Twelve Data Pro | $229/mo ($191/mo yearly) | 610+ credits/min, WebSocket streaming, pre/post-market | Heavy use, extended hours |

What changes on the free Twelve Data plan:

- When the limit is hit you will see "rate limit reached" messages with a countdown. Cached
  data stays visible and is labelled stale.
- Some indices (e.g. `SPX`, `NDX`) and most non-US exchanges may return
  `provider_plan_restriction`. Use ETF proxies such as `SPY` and `QQQ`.
- Extended-hours prices are not shown on any plan in this version (regular session only).

Real-time vs delayed is **never assumed from the plan**. Each quote is labelled from the age
of its timestamp (see METHODOLOGY.md §1).

Without a Twelve Data key, MarketLens runs crypto-only. Stock symbols show a setup message
explaining how to add the key. No synthetic or demo prices are ever shown.

## Licensing

Free API plans are generally for personal or internal use. Showing exchange data publicly to
anonymous visitors can require a redistribution licence. Read Twelve Data's and Coinbase's
terms before opening the site to the public, and consider the Nginx basic-auth option in the
deployment guide for private deployments.

## Adding another provider

1. Subclass `MarketDataProvider` (`providers/base.py`) and implement `resolve`, `get_quote`,
   `get_candles`, `search` and `status`. Return normalized `Quote`, `Candle` and `AssetInfo`
   models, and raise the typed errors from `app/errors.py`.
2. Use `ProviderHTTP` for retries, `TokenBucket`/`DailyQuota` for rate limits, and
   `ProviderHealth` for the circuit breaker.
3. If the provider lacks a timeframe, fetch a lower one and call `market.aggregation.aggregate`.
4. Register it in `providers/registry.py` (routing order) and add settings in `config.py`.
5. Add respx-mocked tests like `tests/test_providers.py`.
