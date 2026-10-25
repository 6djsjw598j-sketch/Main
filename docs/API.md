# MarketLens REST API

Base path: `/api`. Interactive documentation generated from the code is served at
**`/api/docs`** (Swagger UI) and **`/api/redoc`**. The OpenAPI schema is at `/api/openapi.json`.
All responses are JSON. Times are ISO-8601 UTC strings or UNIX seconds (`time` fields in
candles and series).

## Conventions

**Symbols** (path segment `{symbol}`, case-insensitive):

| Kind | Format | Examples |
|---|---|---|
| US stocks, ETFs, indices | ticker | `AAPL`, `BRK.B`, `SPY`, `QQQ`, `SPX` |
| Non-US listing | `TICKER:EXCHANGE` (as returned by search) | `VOD:LSE` |
| Crypto / FX pairs | `BASE-QUOTE` (`/` is accepted and converted) | `BTC-USD`, `ETH-EUR`, `EUR-USD` |

Allowed characters: letters, digits and `. - _ ^ = :` (max 32). Anything else returns `422 invalid_symbol`.

**`asset_type`** (query, optional): `auto` (default), `stock`, `etf`, `index`, `crypto`,
`forex`, `fund`, `other`. `auto` sends Coinbase-listed pairs to Coinbase and everything else
to Twelve Data. `crypto` with a bare ticker (`BTC`) means `BTC-USD`.

**`timeframe`**: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w`, `1M` (case-sensitive: `1M` is monthly).

**Errors** always look like:

```json
{"error": {"code": "provider_rate_limited", "message": "Twelve Data per-minute credit limit (8/min) reached. Data will refresh automatically.", "details": {"provider": "twelvedata"}, "retry_after": 7.5}}
```

| HTTP | `code` | Meaning |
|---|---|---|
| 401 | `unauthorized` | Missing/unknown `X-Workspace-Token` (watchlist/preferences) |
| 403 | `provider_plan_restriction` | Symbol not included in the configured data plan |
| 404 | `symbol_not_found`, `not_found` | Unknown symbol / watchlist item |
| 409 | `conflict` | Symbol already on the watchlist |
| 422 | `validation_error`, `invalid_symbol`, `invalid_parameter`, `insufficient_data` | Bad input, or not enough history for the requested analysis |
| 429 | `rate_limited`, `provider_rate_limited` | This API's per-IP limit, or the provider's credit limit (`Retry-After` header set) |
| 502 | `provider_auth_failed`, `provider_bad_response` | Server's provider key rejected, or the provider returned malformed data |
| 503 | `provider_not_configured`, `provider_unavailable` | Provider needs an API key, or is down (after retries / circuit breaker) |

When a provider is rate-limited or down and a cached copy exists, the API returns `200` with
`cache.stale = true` and a `cache.warning` instead of an error.

Every response carries `X-Request-ID`, which you can match against the server logs.

---

## System

### `GET /api/health`
Liveness and readiness. Returns `200` unless the database is unreachable (`503`). `HEAD` is also supported.

```json
{"status": "ok", "version": "1.0.0", "environment": "production", "time": "2026-10-25T20:22:16Z",
 "database": {"ok": true, "detail": "connected"}, "cache": {"ok": true, "detail": "redis"},
 "providers": [{"name": "coinbase", "status": "ok", "configured": true, "limits": "10 requests/second per IP ...", ...},
               {"name": "twelvedata", "status": "not_configured", "configured": false, "credits_remaining_today": null, ...}]}
```

### `GET /api/meta`
Timeframes, asset types, default indicator parameters, the factor model (keys, weights,
formulas), provider capabilities (timeframe support, notes), and the disclaimer.

## Market data

### `GET /api/assets/search?q=...&asset_type=auto`
Searches Coinbase products (crypto) and Twelve Data's symbol search (when configured). Results
are ranked with exact symbol matches first. `notes` explains disabled or failing sources.

```bash
curl 'https://HOST/api/assets/search?q=sol'
```
```json
{"query": "sol", "results": [{"symbol": "SOL-USD", "display_symbol": "SOL-USD", "name": "Solana / USD", "asset_type": "crypto",
  "exchange": "Coinbase", "currency": "USD", "timezone": "UTC", "provider": "coinbase"}, ...], "notes": []}
```

### `GET /api/market/{symbol}/quote?asset_type=auto`
Latest quote, session status, freshness and cache info.

```json
{"quote": {"symbol": "AAPL", "price": 347.73, "change": -0.49, "change_percent": -0.14, "change_basis": "previous_close",
           "open": 349.12, "high": 351.73, "low": 345.75, "previous_close": 348.22, "volume": 39496500,
           "fifty_two_week_high": 351.73, "fifty_two_week_low": 243.42, "as_of": "2026-10-23T19:59:00Z", "provider": "twelvedata", ...},
 "session": {"status": "closed", "label": "Market closed", "timezone": "America/New_York", "is_open": false},
 "freshness": {"status": "end_of_day", "label": "Last close", "age_seconds": 171474, "is_stale": false,
               "detail": "Market closed. Showing the most recent session data (47.6 h old)."},
 "cache": {"hit": false, "stale": false, "age_seconds": 0.0, "warning": null},
 "refresh_seconds": 300, "source_note": "Twelve Data; change is measured from the previous regular-session close."}
```

For crypto, `change_basis` is `rolling_24h` and `previous_close` holds the price 24 hours ago.
`freshness.status` is one of `real_time`, `delayed`, `end_of_day`, `stale`, measured from
`as_of` (see METHODOLOGY.md).

### `GET /api/market/{symbol}/candles?timeframe=1h&limit=500&asset_type=auto`
`limit` 1–2000. Candles are ascending by `time` (bar open, UNIX seconds UTC; daily+ bars use
00:00 UTC of the session date). `volume` is `null` when the provider has none.

```json
{"symbol": "BTC-USD", "provider": "coinbase", "timeframe": "4h", "timezone": "UTC", "count": 500,
 "candles": [{"time": 1792944000, "open": 80527.74, "high": 80859.26, "low": 80421.18, "close": 80508.0, "volume": 263.03}, ...],
 "aggregated_from": "1h", "last_bar_complete": false,
 "freshness": {"status": "real_time", "label": "Live bar", ...}, "refresh_seconds": 120,
 "limitations": ["4h candles are aggregated from Coinbase 1h candles (buckets aligned to 00:00 UTC).", "The most recent bar is still forming ..."]}
```

### `GET /api/market/overview?symbols=BTC-USD,AAPL`
Compact quotes for up to 20 symbols (used by the watchlist). Each item has either `quote` +
`freshness` or an `error`.

## Analysis

All analysis endpoints accept `timeframe`, `limit` (50–2000, default 500) and `asset_type`.
They are computed from the same cached candles as `/candles`.

**Indicator parameters** (optional query parameters, validated): `sma_periods` (e.g.
`20,50,100,200`), `ema_periods`, `rsi_period`, `rsi_overbought`, `rsi_oversold`, `macd_fast`,
`macd_slow`, `macd_signal`, `stoch_k`, `stoch_smooth`, `stoch_d`, `roc_period`,
`willr_period`, `bb_period`, `bb_std`, `atr_period`, `kc_period`, `kc_atr_period`,
`kc_mult`, `hv_period`, `adx_period`, `ichimoku_tenkan`, `ichimoku_kijun`,
`ichimoku_senkou_b`, `volume_sma_period`, `mfi_period`. Defaults and ranges are in
`GET /api/meta` and Swagger.

### `GET /api/analysis/{symbol}/indicators`
```json
{"context": {"symbol": "ETH-USD", "timeframe": "1h", "bars": 500, "last_price": 2350.16, "last_bar_complete": false, ...},
 "times": [1791158400, ...],
 "series": {"sma_20": [null, ..., 2345.65], "rsi": [...], "macd": [...], "bb_upper": [...], "vwap": [...], ...},
 "future_times": [...25 projected bar times...], "future_series": {"ichimoku_senkou_a": [...], "ichimoku_senkou_b": [...]},
 "readings": [{"key": "rsi", "name": "RSI (14)", "category": "momentum", "values": {"rsi": 51.6}, "display": "51.6",
               "state": "bullish", "interpretation": "Above 50: average gains exceed average losses ...",
               "params": {"period": 14, "overbought": 70, "oversold": 30}, "placement": "panel", "series_keys": ["rsi"]}, ...],
 "notes": ["Latest values include the still-forming bar ..."], "disclaimer": "..."}
```
Values are `null` during warm-up. Insufficient history yields `state: "insufficient_data"`,
and missing volume yields `state: "unavailable"`.

### `GET /api/analysis/{symbol}/support-resistance`
Extra parameters: `lookback` (50–2000, default 300), `swing_window` (2–20, default by
timeframe), `major_threshold` (0–100, default 55), `max_levels` (2–30, default 12).

```json
{"params": {"lookback": 300, "swing_window": 5, "tolerance": 725.77, "major_threshold": 55, "max_levels": 12}, "atr": 2073.6,
 "levels": [{"id": "S5", "kind": "support", "tier": "major", "price": 79918.85, "zone_low": 79341.56, "zone_high": 80496.14,
             "touches": 17, "reactions": 3, "strength": 76.4,
             "score_breakdown": {"reactions": 21.0, "touches": 20.0, "recency": 18.1, "volume": 7.3, "confluence": 10.0},
             "distance": 7.69, "distance_pct": 0.0096, "status": "intact", "historical_role": "support", ...}],
 "nearest_support": {...}, "nearest_resistance": {...},
 "reference_levels": [{"key": "prev_day_high", "name": "Previous day high", "price": 81106.03, "position": "above", "source": "observed", ...}],
 "pivots": {"basis": "previous week (2026-10-12 to 2026-10-18)", "levels": {"R3": ..., "P": ..., "S3": ...}},
 "fibonacci": {"direction": "down", "swing_high": 97963.62, "swing_low": 57717.55, "levels": [{"ratio": 0.618, "price": 82589.62}, ...]},
 "trendlines": [{"kind": "support", "points": [{"time": ..., "price": ...}, ...], "current_value": 78763.62, "broken": false, ...}],
 "methodology": ["..."]}
```
`status` is one of `intact`, `testing`, `breakout`, `breakdown`, `reclaimed`, `lost`.

### `GET /api/analysis/{symbol}/summary`
Extra parameters: `weights` (e.g. `rsi:2,macd:0`, valid keys in `/api/meta`), `bullish_threshold`
(0–100, default 25), `bearish_threshold` (−100–0, default −25).

Returns `overall` (bias, score, thresholds, explanation), `factors` (value in [−1, 1], weight,
contribution in score points, signal, detail with formula), `conflicts`, `trend` (short /
medium / long), `momentum`, `volatility` (ATR %, percentile, squeeze), `volume`,
`nearest_support` / `nearest_resistance`, `conditions` (breakout / breakdown / reversal, `active`
flag), `key_levels` (confirmation / invalidation or triggers), `events` (recent crossovers,
level breaks, volume spikes), and `methodology`.

### `GET /api/analysis/{symbol}/setups`
Same parameters as summary. Returns `observed` inputs (last price, ATR, nearest zones, score)
and the `bullish` / `bearish` scenarios. Each has `available`, `setup_type`, `alignment`,
`entry_zone_low/high`, `entry_reference`, `invalidation`, `risk_per_unit`, `targets[]` (price,
basis, reward_risk), `confirmation_conditions`, `invalidation_conditions`, `notes`. If data is
insufficient or no level anchors a scenario, `available` is `false` with an
`unavailable_reason`.

### `GET /api/analysis/{symbol}/backtest`
Parameters: `limit` (300–5000, default 1000), `entry_threshold` (5–100, default 25),
`exit_threshold` (−100–100, default 0, must be below entry), `commission_bps` and
`slippage_bps` (per side, defaults 10 and 5), `direction` (`long_only` | `long_short`),
plus indicator params and `weights`. Returns `assumptions`, `metrics` (trades, win rate,
average/median/best/worst trade, profit factor, total vs buy-and-hold return, max drawdown,
exposure), `trades` (last 200), downsampled `equity_curve` and `benchmark_curve`, and `caveats`.

## Watchlist and preferences

These endpoints need an anonymous workspace token in the `X-Workspace-Token` header.

| Method & path | Body | Result |
|---|---|---|
| `POST /api/workspaces` | – | `201 {"token": "...", "created_at": ...}` (limited to 20 per hour per IP) |
| `GET /api/watchlist` | – | `{"items": [{"symbol", "asset_type", "position", "added_at"}], "max_items": 50}` |
| `POST /api/watchlist` | `{"symbol": "BTC-USD", "asset_type": "auto"}` | `201` + list; `409` if present |
| `PUT /api/watchlist/order` | `{"symbols": ["AAPL", "BTC-USD"]}` | Reordered list (must contain exactly the current symbols) |
| `DELETE /api/watchlist/{symbol}` | – | `204`; `404` if absent |
| `GET /api/preferences` | – | Saved preferences or defaults |
| `PUT /api/preferences` | Preferences JSON (unknown fields rejected; indicator params validated) | Saved preferences |

Example:

```bash
TOKEN=$(curl -s -X POST https://HOST/api/workspaces | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -s -X POST https://HOST/api/watchlist -H "X-Workspace-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"symbol":"ETH-USD"}'
```

## Rate limits and caching

- Per client IP: `RATE_LIMIT_PER_MINUTE` (default 240/min) in the API, plus Nginx (10 req/s,
  burst 40, on `/api`). `/api/health` is exempt in the API.
- Provider budgets: Coinbase ≤ 8 req/s; Twelve Data `TWELVEDATA_CREDITS_PER_MINUTE` and
  `_PER_DAY`. Exceeding them returns `429 provider_rate_limited` with `retry_after`. The
  upstream is never hammered.
- Cache TTLs: crypto quotes 4 s, equity quotes 60 s (≥ 10 min while US markets are closed);
  candles from 10 s (crypto 1m) to 1 h (equity weekly/monthly, or any equity timeframe while
  closed). Permanent errors (unknown symbol, bad key, plan restriction) are cached for 60 s.
