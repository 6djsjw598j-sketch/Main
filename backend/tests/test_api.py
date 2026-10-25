"""HTTP API tests (upstream providers mocked with respx)."""

from __future__ import annotations

import math

import httpx
import pytest
import respx

from app.config import Settings
from app.main import create_app
from tests.conftest import COINBASE, TWELVEDATA, coinbase_routes, twelvedata_routes


def _assert_finite(obj) -> None:
    if isinstance(obj, float):
        assert math.isfinite(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _assert_finite(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_finite(v)


async def test_health_and_meta(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded") and body["database"]["ok"] is True
    assert {p["name"] for p in body["providers"]} == {"coinbase", "twelvedata"}
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Request-ID"]
    assert (await client.head("/api/health")).status_code == 200
    meta = (await client.get("/api/meta")).json()
    assert meta["timeframes"] == ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"]
    assert len(meta["factors"]) == 13 and "not financial advice" in meta["disclaimer"]


async def test_openapi_docs_available(client):
    r = await client.get("/api/openapi.json")
    paths = r.json()["paths"]
    for path in (
        "/api/health",
        "/api/assets/search",
        "/api/market/{symbol}/quote",
        "/api/market/{symbol}/candles",
        "/api/analysis/{symbol}/indicators",
        "/api/analysis/{symbol}/support-resistance",
        "/api/analysis/{symbol}/summary",
        "/api/analysis/{symbol}/setups",
    ):
        assert path in paths


async def test_crypto_quote(client):
    r = await client.get("/api/market/btc-usd/quote")
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["symbol"] == "BTC-USD" and body["quote"]["price"] == 101.5
    assert body["session"]["status"] == "always_open"
    assert body["freshness"]["status"] == "real_time"
    assert body["refresh_seconds"] == 5
    second = (await client.get("/api/market/BTC-USD/quote")).json()
    assert second["cache"]["hit"] is True


async def test_stock_quote_and_candles(client):
    q = (await client.get("/api/market/AAPL/quote")).json()
    assert q["quote"]["provider"] == "twelvedata" and q["quote"]["change_basis"] == "previous_close"
    c = await client.get("/api/market/AAPL/candles", params={"timeframe": "1d", "limit": 120})
    assert c.status_code == 200
    body = c.json()
    assert body["count"] == 120 and body["timezone"] == "America/New_York"
    times = [x["time"] for x in body["candles"]]
    assert times == sorted(times)


@pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"])
async def test_crypto_candles_all_timeframes(client, tf):
    r = await client.get("/api/market/ETH-USD/candles", params={"timeframe": tf, "limit": 60})
    assert r.status_code == 200, r.text
    body = r.json()
    assert 0 < body["count"] <= 60
    assert body["aggregated_from"] == {"30m": "15m", "4h": "1h", "1w": "1d", "1M": "1d"}.get(tf)


async def test_validation_errors(client):
    bad_tf = await client.get("/api/market/BTC-USD/candles", params={"timeframe": "2h"})
    assert bad_tf.status_code == 422 and bad_tf.json()["error"]["code"] == "validation_error"
    bad_limit = await client.get("/api/market/BTC-USD/candles", params={"limit": 0})
    assert bad_limit.status_code == 422
    bad_symbol = await client.get("/api/market/%3Cscript%3E/quote")
    assert bad_symbol.status_code == 422 and bad_symbol.json()["error"]["code"] == "invalid_symbol"
    bad_param = await client.get("/api/analysis/BTC-USD/indicators", params={"rsi_period": 1})
    assert bad_param.status_code == 422
    bad_weights = await client.get("/api/analysis/BTC-USD/summary", params={"weights": "magic:3"})
    assert bad_weights.status_code == 422 and bad_weights.json()["error"]["code"] == "invalid_parameter"
    bad_bt = await client.get("/api/analysis/BTC-USD/backtest", params={"entry_threshold": 20, "exit_threshold": 30})
    assert bad_bt.status_code == 422
    search = await client.get("/api/assets/search", params={"q": ""})
    assert search.status_code == 422


async def test_unknown_crypto_and_unknown_stock(client):
    r = await client.get("/api/market/ZZZ/quote", params={"asset_type": "crypto"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "symbol_not_found"
    r = await client.get("/api/market/NOPE/quote")
    assert r.status_code == 404


async def test_stock_requires_twelvedata_key(settings, mock_upstream):
    app = create_app(settings.model_copy(update={"twelvedata_api_key": None}))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/market/AAPL/quote")
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["code"] == "provider_not_configured" and "TWELVEDATA_API_KEY" in err["message"]
        crypto = await ac.get("/api/market/BTC-USD/quote")
        assert crypto.status_code == 200  # crypto keeps working without keys


async def test_provider_rate_limit_and_stale_fallback(settings, monkeypatch):
    # Weekends lengthen equity cache TTLs; pin the session so the test is deterministic.
    monkeypatch.setattr("app.market.service.equity_market_likely_closed", lambda *_: False)
    app = create_app(settings.model_copy(update={"quote_ttl_equity": 0}))
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        twelvedata_routes(router)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            first = await ac.get("/api/market/AAPL/quote")
            assert first.status_code == 200
            router.get(f"{TWELVEDATA}/quote").mock(
                return_value=httpx.Response(429, json={"code": 429, "message": "You have run out of API credits", "status": "error"})
            )
            stale = await ac.get("/api/market/AAPL/quote")
            assert stale.status_code == 200
            cache = stale.json()["cache"]
            assert cache["stale"] is True and "rate limit" in cache["warning"].lower()
            cold = await ac.get("/api/market/MSFT/quote")
            assert cold.status_code == 429
            assert cold.json()["error"]["code"] == "provider_rate_limited"
            assert int(cold.headers["Retry-After"]) >= 1


async def test_provider_outage_returns_503(settings):
    app = create_app(settings)
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        router.get(f"{COINBASE}/products/SOL-USD/ticker").mock(side_effect=httpx.ConnectError("down"))
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/market/SOL-USD/quote")
            assert r.status_code == 503 and r.json()["error"]["code"] == "provider_unavailable"


async def test_analysis_endpoints(client):
    base = {"timeframe": "1d", "limit": 400}
    ind = await client.get("/api/analysis/BTC-USD/indicators", params=base | {"sma_periods": "10,50", "rsi_period": 9})
    assert ind.status_code == 200, ind.text
    body = ind.json()
    assert len(body["times"]) == 400 and "sma_10" in body["series"] and "sma_200" not in body["series"]
    assert len(body["future_times"]) == 25
    assert any(r["name"] == "RSI (9)" for r in body["readings"])
    _assert_finite(body)

    sr = await client.get("/api/analysis/BTC-USD/support-resistance", params=base | {"lookback": 250})
    assert sr.status_code == 200, sr.text
    srb = sr.json()
    assert srb["params"]["lookback"] == 250 and srb["levels"]
    assert {lv["kind"] for lv in srb["levels"]} <= {"support", "resistance"}
    assert srb["fibonacci"] is not None and srb["methodology"]
    assert any(r["key"].startswith("prev_day") for r in srb["reference_levels"])
    _assert_finite(srb)

    summary = await client.get("/api/analysis/BTC-USD/summary", params=base | {"weights": "rsi:2"})
    assert summary.status_code == 200, summary.text
    sb = summary.json()
    assert sb["overall"]["bias"] in ("bullish", "bearish", "neutral")
    assert -100 <= sb["overall"]["score"] <= 100
    assert next(f for f in sb["factors"] if f["key"] == "rsi")["weight"] == 2
    assert {t["horizon"] for t in sb["trend"]} == {"short", "medium", "long"}
    assert "probability" in sb["overall"]["explanation"]
    _assert_finite(sb)

    setups = await client.get("/api/analysis/BTC-USD/setups", params=base)
    assert setups.status_code == 200
    stb = setups.json()
    assert stb["label"].startswith("Technical scenarios") and "not financial advice" in stb["disclaimer"]
    assert stb["observed"]["last_price"] > 0

    bt = await client.get("/api/analysis/BTC-USD/backtest", params={"timeframe": "1h", "limit": 1000})
    assert bt.status_code == 200, bt.text
    btb = bt.json()
    assert btb["assumptions"]["commission_bps"] == 10 and btb["caveats"]
    _assert_finite(btb)


async def test_insufficient_data(client):
    r = await client.get("/api/analysis/SHORT/summary", params={"timeframe": "1d"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "insufficient_data"
    ind = await client.get("/api/analysis/SHORT/indicators", params={"timeframe": "1d"})
    assert ind.status_code == 200
    states = {r["key"]: r["state"] for r in ind.json()["readings"]}
    assert states["sma_200"] == "insufficient_data"


async def test_search(client):
    r = await client.get("/api/assets/search", params={"q": "btc"})
    assert r.status_code == 200
    symbols = [x["symbol"] for x in r.json()["results"]]
    assert symbols[0] == "BTC-USD"
    r = await client.get("/api/assets/search", params={"q": "AAPL", "asset_type": "stock"})
    assert r.json()["results"][0]["symbol"] == "AAPL"


async def test_overview(client):
    r = await client.get("/api/market/overview", params={"symbols": "BTC-USD,AAPL,NOPE"})
    assert r.status_code == 200
    items = {i["symbol"]: i for i in r.json()["items"]}
    assert items["BTC-USD"]["quote"]["price"] == 101.5
    assert items["NOPE"]["error"]["code"] == "symbol_not_found"
    too_many = await client.get("/api/market/overview", params={"symbols": ",".join(f"S{i}" for i in range(21))})
    assert too_many.status_code == 422


async def test_watchlist_and_preferences_flow(client):
    assert (await client.get("/api/watchlist")).status_code == 401
    assert (await client.get("/api/watchlist", headers={"X-Workspace-Token": "bogus"})).status_code == 401
    created = await client.post("/api/workspaces")
    assert created.status_code == 201
    h = {"X-Workspace-Token": created.json()["token"]}

    assert (await client.post("/api/watchlist", json={"symbol": "btc/usd"}, headers=h)).status_code == 201
    assert (await client.post("/api/watchlist", json={"symbol": "AAPL", "asset_type": "stock"}, headers=h)).status_code == 201
    dup = await client.post("/api/watchlist", json={"symbol": "AAPL"}, headers=h)
    assert dup.status_code == 409
    bad = await client.post("/api/watchlist", json={"symbol": "<bad>"}, headers=h)
    assert bad.status_code == 422

    order = await client.put("/api/watchlist/order", json={"symbols": ["AAPL", "BTC-USD"]}, headers=h)
    assert [i["symbol"] for i in order.json()["items"]] == ["AAPL", "BTC-USD"]
    wrong = await client.put("/api/watchlist/order", json={"symbols": ["AAPL"]}, headers=h)
    assert wrong.status_code == 422
    assert (await client.delete("/api/watchlist/AAPL", headers=h)).status_code == 204
    assert (await client.delete("/api/watchlist/AAPL", headers=h)).status_code == 404
    items = (await client.get("/api/watchlist", headers=h)).json()["items"]
    assert [i["symbol"] for i in items] == ["BTC-USD"] and items[0]["added_at"].endswith(("Z", "+00:00"))

    defaults = (await client.get("/api/preferences", headers=h)).json()
    assert defaults["theme"] == "dark" and defaults["timeframe"] == "1d"
    saved = await client.put(
        "/api/preferences",
        headers=h,
        json=defaults
        | {"theme": "light", "timeframe": "4h", "panels": ["rsi", "adx"], "indicator_params": {"rsi_period": 7, "sma_periods": "10,20"}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["indicator_params"] == {"rsi_period": 7, "sma_periods": [10, 20]}
    assert (await client.get("/api/preferences", headers=h)).json()["theme"] == "light"
    invalid = await client.put("/api/preferences", headers=h, json={"panels": ["telepathy"]})
    assert invalid.status_code == 422
    invalid = await client.put("/api/preferences", headers=h, json={"unknown_field": 1})
    assert invalid.status_code == 422


async def test_api_rate_limit(settings: Settings, mock_upstream):
    app = create_app(settings.model_copy(update={"rate_limit_per_minute": 10}))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        statuses = [(await ac.get("/api/meta")).status_code for _ in range(12)]
        assert statuses[:10] == [200] * 10 and statuses[10] == 429
        assert (await ac.get("/api/health")).status_code == 200  # health is never rate limited


async def test_workspace_creation_rate_limit(settings: Settings, mock_upstream):
    app = create_app(settings.model_copy(update={"workspace_creation_per_hour": 2}))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        codes = [(await ac.post("/api/workspaces")).status_code for _ in range(3)]
        assert codes == [201, 201, 429]
