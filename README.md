# MarketLens: technical analysis for stocks, ETFs and crypto

MarketLens is a self-hosted market-analysis web app. Enter a ticker (AAPL, SPY, QQQ, BTC-USD,
ETH-USD…) to get:

- the latest price and session data
- an interactive candlestick chart with volume and indicator overlays
- automatically detected support/resistance zones
- 25+ technical indicators with plain-language readings
- an explainable trend/momentum summary
- educational trade scenarios and a no-look-ahead backtest

It uses **real market data only**: Coinbase Exchange public data for crypto, and Twelve Data
for stocks/ETFs/indices when you add an API key. When a provider isn't configured, the app
says so and shows setup instructions. It never substitutes invented prices.

> **Not financial advice.** Everything MarketLens computes (levels, scores, scenarios,
> backtests) is an algorithmic, educational description of historical prices. No level is
> guaranteed to hold, and no score is a probability of profit.

---

## Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Market-data providers and their limits](#market-data-providers)
4. [Run it on a Mac (development)](#run-it-on-a-mac-development)
5. [Run the production stack locally with Docker](#run-the-production-stack-with-docker)
6. [Deploy to AWS EC2](#deploy-to-aws-ec2)
7. [Tests](#tests)
8. [Project structure](#project-structure)
9. [Configuration reference](#configuration-reference)
10. [Security and operations](#security-and-operations)
11. [Status checklist](#status-checklist)

Detailed documents:

- [docs/DEPLOYMENT_AWS_EC2.md](docs/DEPLOYMENT_AWS_EC2.md): step-by-step deployment from a Mac, troubleshooting, costs
- [docs/API.md](docs/API.md): REST API reference (live Swagger UI at `/api/docs`)
- [docs/METHODOLOGY.md](docs/METHODOLOGY.md): every formula, the S/R scoring, the factor model, the backtest design
- [docs/PROVIDERS.md](docs/PROVIDERS.md): provider choice, free vs paid plans, adding a provider
- [docs/SECURITY.md](docs/SECURITY.md): security model and hardening checklist

## Features

| Area | What you get |
|---|---|
| **Quotes** | Last price, absolute and % change, open/high/low, previous close (or price 24h ago for crypto), volume, bid/ask, 52-week range, session status (pre-market, open, after-hours, closed, 24/7), and a freshness label (real-time / delayed / last close / stale) **measured from the data's own timestamp** |
| **Chart** | TradingView Lightweight Charts: candles, OHLC bars or line; zoom, pan, crosshair with OHLCV legend; volume; 9 timeframes (1m–1M); indicator overlays and separate oscillator panes; S/R zones shaded green/red with labels; trendlines; previous day/week/month highs and lows; pivot points; Fibonacci; local, exchange or UTC time |
| **Support/resistance** | Swing-point clustering into zones with touches, reactions, a documented 0–100 strength score, major/minor tiers, distance in price and %, breakout/breakdown/reclaimed/lost detection, nearest support and resistance, configurable lookback |
| **Indicators** | SMA 20/50/100/200, EMA 9/12/21/50/200, session VWAP, ADX/+DI/−DI, Ichimoku, RSI, MACD, Stochastic, ROC, Williams %R, Bollinger Bands, ATR, Keltner, historical volatility, volume average, OBV, MFI, Accumulation/Distribution. Each has a latest value, interpretation, overbought/oversold state, toggles and editable parameters. Missing history shows as "insufficient data" and is never filled in |
| **Analysis** | Weighted factor score (−100…+100) with every factor's value, weight and contribution; conflicting signals; short/medium/long trend; momentum; volatility regime vs a 252-bar baseline; volume confirmation; breakout, breakdown and reversal conditions (incl. RSI divergence); confirmation and invalidation levels; recent technical events |
| **Scenarios** | Bullish and bearish educational scenarios: entry zone, invalidation, level-based targets, reward/risk, and confirming/invalidating conditions. Shown only when the data supports them |
| **Backtest** | Factor-score rule tested with next-bar-open execution, commission and slippage, no look-ahead, compared against buy-and-hold |
| **Watchlist & preferences** | Add, remove and reorder (drag or arrows) with compact live quotes. Timeframe, chart type, indicators, parameters, theme and time zone persist on the server (anonymous workspace token) and in the browser |
| **Ops** | Docker Compose (PostgreSQL, Redis, API, Nginx), structured JSON logs, health endpoint, per-IP rate limits, provider credit rationing, caching with stale-on-error fallback, circuit breaker, backups, health-check timer |

## Architecture

```
Browser (React + TypeScript + Tailwind + Lightweight Charts)
   │  HTTPS
   ▼
Host Nginx  ── TLS (Let's Encrypt), rate limiting, HSTS          [EC2, ports 80/443]
   │  http://127.0.0.1:8080
   ▼
frontend container (unprivileged Nginx) ── static SPA, CSP, proxies /api
   │  http://backend:8000 (Docker network only)
   ▼
backend container (FastAPI, Python 3.12)
   ├── providers/   Coinbase Exchange (crypto) · Twelve Data (stocks/ETFs/indices/FX)
   ├── market/      routing, caching, aggregation, sessions, freshness
   ├── indicators/  pandas/NumPy indicator engine
   ├── analysis/    S/R, levels, factor model, events, scenarios, backtest
   ├── api/         REST routes (OpenAPI at /api/docs)
   ├── Redis        response cache (in-memory fallback in development)
   └── PostgreSQL   workspaces, watchlists, preferences (SQLite in development)
```

Design decisions:

- **API keys never reach the browser.** The frontend only talks to `/api` on its own origin.
- **One cached candle fetch feeds every analysis endpoint.** Single-flight locking means five
  parallel dashboard requests cost one provider call.
- **Freshness is measured, not assumed.** A quote is labelled real-time only if its timestamp
  is under 2 minutes old during an open session.
- **Graceful degradation.** Rate limits and outages serve the last good data, clearly
  flagged as stale. Otherwise you get a typed error (`provider_rate_limited`,
  `symbol_not_found`, `provider_not_configured`, …) that the UI explains.

## Market-data providers

| Asset class | Provider | Key needed | What the free tier gives | Main limitations |
|---|---|---|---|---|
| Crypto (all Coinbase pairs, e.g. BTC-USD, ETH-USD, SOL-USD) | **Coinbase Exchange** public market data | No | Real-time trades, ticker, 24h stats, candles 1m/5m/15m/1h/6h/1d | 10 req/s per IP; 300 candles per request; Coinbase prices only (not a cross-exchange index); no candle for minutes without trades |
| US stocks, ETFs; indices and FX where your plan allows | **Twelve Data** | Yes (free) | Basic plan: 8 API credits/min, 800/day; the pricing page lists real-time US equities and ETFs | Credits are tight for several users; regular-session bars only; some indices and non-US exchanges need paid plans (the app shows the provider's message) |

- 30m, 4h, 1w and 1M crypto candles are **aggregated** from Coinbase 15m/1h/1d candles
  (UTC-aligned, weeks start Monday). Twelve Data serves all nine timeframes natively.
- Prices, plans and real-time scope come from the providers' public pages as of 2026-10-25.
  Verify them before relying on them. See [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Run it on a Mac (development)

Prerequisites: [Homebrew](https://brew.sh), then:

```bash
brew install python@3.12 node@22 git
# make sure python3.12 and node 22 are on your PATH (brew prints the exact commands)
```

```bash
git clone <your-repo-url> marketlens && cd marketlens

# 1) Backend (FastAPI) - terminal tab 1
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # optional: add TWELVEDATA_API_KEY for stocks
.venv/bin/uvicorn app.main:create_app --factory --reload --port 8000

# 2) Frontend (Vite) - terminal tab 2
cd frontend
npm ci
npm run dev                     # http://localhost:5173 (proxies /api to :8000)
```

Open <http://localhost:5173>. Crypto works immediately. For stocks, get a free key at
<https://twelvedata.com/pricing>, put it in `backend/.env` as `TWELVEDATA_API_KEY=...`, and
restart the backend.

Swagger UI: <http://localhost:8000/api/docs>. Shortcuts: `make setup`, `make backend`,
`make frontend`, `make test`.

## Run the production stack with Docker

With Docker Desktop (Mac) or Docker Engine (Linux):

```bash
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD (openssl rand -hex 24) and optionally TWELVEDATA_API_KEY
docker compose up -d --build
open http://127.0.0.1:8080      # macOS; use your browser on Linux
```

The stack is PostgreSQL 17, Redis 7.4, the API (Alembic migrations run automatically), and the
Nginx-served frontend. Only `127.0.0.1:8080` is published. On a server, the host's Nginx adds
TLS in front.

## Deploy to AWS EC2

Follow **[docs/DEPLOYMENT_AWS_EC2.md](docs/DEPLOYMENT_AWS_EC2.md)**. It is written for a Mac
user and covers instance choice, security groups, SSH, Docker, secrets, Nginx, Let's Encrypt,
systemd, backups, monitoring, troubleshooting and costs. Short version:

```bash
# on the EC2 instance (Ubuntu 24.04)
git clone <your-repo-url> ~/marketlens && cd ~/marketlens
sudo ./deployment/scripts/bootstrap_ec2.sh      # Docker, Nginx, Certbot, UFW, fail2ban, swap
exit                                              # log out/in so the docker group applies
./deployment/scripts/init_env.sh                  # creates .env with a random DB password
./deployment/scripts/deploy.sh                    # build + start + health check
sudo ./deployment/scripts/setup_nginx.sh your.domain.com you@example.com   # HTTPS
```

## Tests

| Suite | Command | Covers |
|---|---|---|
| Backend (148 tests) | `cd backend && .venv/bin/pytest` | Indicators vs hand-computed values, independent reference loops and the `ta` library; S/R detection, breakouts, reclaims; pivots, Fibonacci, trendlines; timeframe aggregation; freshness and sessions; cache TTL/LRU, single-flight, stale fallback, negative caching; rate limiters; provider parsing and errors (invalid symbols, empty responses, 401/403/404/429/5xx, timeouts, circuit breaker); API validation; watchlist/preferences; no-look-ahead property of the factor model; backtest execution and costs |
| Backend lint | `.venv/bin/ruff check app tests` | |
| Frontend unit (23 tests) | `cd frontend && npm test` | Formatting, API client errors and retries, workspace token recovery, loading/error/empty states, watchlist, dashboard flows |
| Type check | `npm run typecheck` | |
| End-to-end | `npm run test:e2e` | Playwright on desktop and mobile: search a symbol, select it, and the chart renders (mocked API). `E2E_LIVE=1 npm run test:e2e -- --project=desktop` runs the same flow against a live backend |

All external market-data APIs are mocked in the automated tests (`respx`, Playwright routes).
Test fixtures include real recorded provider responses.

## Project structure

```
backend/
  app/
    main.py            app factory, middleware (request id, JSON logs, rate limit, security headers)
    config.py          settings from environment variables
    cache.py           memory/Redis cache, single-flight, stale-on-error, negative caching
    ratelimit.py       token bucket, daily quota, per-IP sliding window
    symbols.py         symbol validation/normalization
    providers/         base.py (abstraction), coinbase.py, twelvedata.py, registry.py, http.py
    market/            service.py, aggregation.py, sessions.py, freshness.py
    indicators/        core.py, trend.py, momentum.py, volatility.py, volume.py, engine.py, params.py
    analysis/          swings, support_resistance, levels, trendlines, signals, summary, events, setups, backtest, service
    api/               market.py, analysis.py, user.py, system.py, deps.py
    db/                SQLAlchemy models, repository
    models/            Pydantic request/response models
  alembic/             database migrations
  tests/               pytest suite + recorded fixtures
  Dockerfile, docker-entrypoint.sh, requirements*.txt (fully pinned)
frontend/
  src/
    api/               typed client, TanStack Query hooks, types mirroring the backend
    components/chart/  PriceChart (Lightweight Charts), S/R zone primitive, toolbar, indicator controls
    components/panels/ summary, sidebar, scenarios, readings, levels, events, OHLCV, backtest, watchlist, data source
    components/layout/ top navigation, asset search
    state/             preferences (local + server sync)
    lib/               formatting, time zones, indicator catalog
  e2e/                 Playwright tests
  Dockerfile, nginx.conf
deployment/
  nginx/marketlens.conf       host Nginx site (TLS via Certbot)
  scripts/                    bootstrap_ec2, init_env, deploy, setup_nginx, backup/restore, healthcheck
  systemd/                    boot service, backup and health-check timers
docker-compose.yml, .env.example, Makefile, .github/workflows/ci.yml
docs/                         deployment, API, methodology, providers, security
```

The repository also contains [`options-bot/`](options-bot/README.md), a separate project: a
rules-based, risk-first options trading bot for Alpaca (paper trading by default). It has its own
README, tests and CI workflow.

## Configuration reference

All settings are environment variables (see `.env.example` and `backend/.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `TWELVEDATA_API_KEY` | empty | Enables stocks/ETFs/indices/FX. Sent only in an `Authorization` header from the server |
| `TWELVEDATA_CREDITS_PER_MINUTE` / `_PER_DAY` | 8 / 800 | Match your Twelve Data plan; requests beyond them get a clean `provider_rate_limited` error instead of an upstream ban |
| `COINBASE_ENABLED` | true | Crypto via Coinbase Exchange public data |
| `DATABASE_URL` | SQLite in dev | `postgresql+asyncpg://…` in production (set by compose) |
| `REDIS_URL` | empty | Redis cache; empty means an in-process LRU cache |
| `RATE_LIMIT_PER_MINUTE` | 240 | Per-IP API limit (Nginx also limits) |
| `QUOTE_TTL_CRYPTO` / `QUOTE_TTL_EQUITY` | 4 / 60 s | Quote cache lifetimes (longer when US markets are closed) |
| `STALE_TTL` | 21600 s | How long a stale copy may be served during an outage |
| `ENABLE_API_DOCS` | true | Swagger/ReDoc at `/api/docs`, `/api/redoc` |
| `CORS_ORIGINS` | localhost dev origins | Only needed if the UI is served from another origin |
| `LOG_LEVEL`, `LOG_JSON` | INFO, true | Structured logging |
| `APP_ENV` | development | `production` disables auto table creation (Alembic runs instead) and debug |

## Security and operations

- Secrets live only in `.env` on the server (mode 600, git-ignored). The key is never logged
  or sent to the browser.
- Only ports 22 (your IP), 80 and 443 are open. App containers bind to `127.0.0.1` or the
  internal Docker network.
- Containers run as non-root with read-only filesystems and `no-new-privileges`.
- Strict CSP, HSTS, `X-Frame-Options`, `nosniff`, and rate limits in Nginx and the API.
- Watchlists use **anonymous workspace tokens**, not user accounts (no passwords are
  handled). To make a deployment private, enable the Nginx basic-auth lines. For real
  multi-user accounts, put an identity provider in front (see `docs/SECURITY.md`).
- Daily database backups (optionally to S3), a 5-minute health-check timer with automatic
  restart, Docker log rotation and automatic Ubuntu security updates.

## Status checklist

Implemented and verified in this repository (automated tests plus manual runs against live
Coinbase data, and Twelve Data's `demo` key for AAPL):

- [x] Provider abstraction; Coinbase Exchange and Twelve Data integrations; routing; symbol validation
- [x] Quotes with session status and measured freshness; candles for all 9 timeframes; correct aggregation
- [x] Caching (memory/Redis), single-flight, stale fallback, negative caching, credit rationing, circuit breaker, retries
- [x] Indicator engine (all requested indicators) with parameters, interpretations and insufficient-data handling
- [x] Support/resistance zones, scoring, breakouts/reclaims, nearest levels, prior-period highs/lows, pivots, Fibonacci, trendlines
- [x] Explainable factor model, trend horizons, momentum/volatility/volume summaries, conditions, key levels, events
- [x] Educational scenarios; backtest with costs, slippage and next-bar execution
- [x] Watchlist (add/remove/reorder/overview) and persisted preferences
- [x] Responsive dark/light dashboard with the full chart and every panel
- [x] Backend, frontend and end-to-end tests; CI workflow
- [x] Dockerfiles, Compose, Nginx (container + host), scripts, systemd units; production stack built and run locally with Docker
- [x] Deployment, API, methodology, provider and security documentation

Deliberately not included (possible extensions):

- [ ] Personal user accounts with login. Anonymous workspaces are used instead (see SECURITY.md for adding OIDC)
- [ ] WebSocket streaming. Efficient polling with server-side caching is used. Coinbase offers a public WebSocket feed; Twelve Data WebSockets need the Pro plan
- [ ] Extended-hours (pre/post-market) equity prices
- [ ] Price alerts / notifications
- [ ] Multi-worker deployments with shared (Redis-based) provider rate limiting. The API runs one async worker by design

## License

Provided as-is for the repository owner. Check each market-data provider's terms before
redistributing their data publicly. Lightweight Charts™ is © TradingView, Inc. (Apache-2.0);
its attribution logo stays enabled as its license requires.
