"""Shared fixtures. All upstream HTTP traffic is mocked with respx; tests never hit the network."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from app.config import Settings
from app.main import create_app
from tests.helpers import synthetic_price

COINBASE = "https://api.exchange.coinbase.com"
TWELVEDATA = "https://api.twelvedata.com"
FIXTURES = Path(__file__).parent / "fixtures"

PRODUCTS = [
    {"id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD", "status": "online"},
    {"id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD", "status": "online"},
    {"id": "SOL-USD", "base_currency": "SOL", "quote_currency": "USD", "status": "online"},
    {"id": "BTC-EUR", "base_currency": "BTC", "quote_currency": "EUR", "status": "online"},
    {"id": "OLD-USD", "base_currency": "OLD", "quote_currency": "USD", "status": "delisted"},
]
CURRENCIES = [{"id": "BTC", "name": "Bitcoin"}, {"id": "ETH", "name": "Ethereum"}, {"id": "SOL", "name": "Solana"}]


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _iso(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def coinbase_candles(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    gran = int(params["granularity"])
    start, end = _iso(params["start"]), _iso(params["end"])
    now = int(time.time())
    rows = []
    t = start - start % gran
    if t < start:
        t += gran
    while t <= min(end, now):
        o, c = synthetic_price(t), synthetic_price(t + gran)
        rows.append([t, min(o, c) - 0.3, max(o, c) + 0.3, o, c, 10.0 + (t // gran) % 7])
        t += gran
    rows.reverse()  # Coinbase returns newest first
    return httpx.Response(200, json=rows)


def coinbase_routes(router: respx.MockRouter) -> None:
    router.get(f"{COINBASE}/products").mock(return_value=httpx.Response(200, json=PRODUCTS))
    router.get(f"{COINBASE}/currencies").mock(return_value=httpx.Response(200, json=CURRENCIES))
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for pid in ("BTC-USD", "ETH-USD", "SOL-USD", "BTC-EUR"):
        router.get(f"{COINBASE}/products/{pid}/ticker").mock(
            return_value=httpx.Response(200, json={"price": "101.5", "bid": "101.4", "ask": "101.6", "volume": "50", "time": now})
        )
        router.get(f"{COINBASE}/products/{pid}/stats").mock(
            return_value=httpx.Response(200, json={"open": "100", "high": "103", "low": "99", "last": "101.5", "volume": "1234.5"})
        )
        router.get(f"{COINBASE}/products/{pid}/candles").mock(side_effect=coinbase_candles)


def twelvedata_series(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    interval = params["interval"]
    size = int(params["outputsize"])
    symbol = params["symbol"]
    if symbol == "SHORT":
        size = min(size, 10)
    values = []
    if interval in ("1day", "1week", "1month"):
        d = datetime.now(UTC).date()
        step = {"1day": 1, "1week": 7, "1month": 30}[interval]
        while len(values) < size:
            if interval != "1day" or d.weekday() < 5:
                ts = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
                o, c = synthetic_price(ts), synthetic_price(ts + 86400)
                values.append(
                    {
                        "datetime": d.isoformat(),
                        "open": f"{o:.4f}",
                        "high": f"{max(o, c) + 0.4:.4f}",
                        "low": f"{min(o, c) - 0.4:.4f}",
                        "close": f"{c:.4f}",
                        "volume": "1000000",
                    }
                )
            d -= timedelta(days=step)
    else:
        seconds = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800, "1h": 3600, "4h": 14400}[interval]
        t = int(time.time()) // seconds * seconds
        while len(values) < size:
            o, c = synthetic_price(t), synthetic_price(t + seconds)
            stamp = datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M:%S")
            values.append(
                {
                    "datetime": stamp,
                    "open": f"{o:.4f}",
                    "high": f"{max(o, c) + 0.2:.4f}",
                    "low": f"{min(o, c) - 0.2:.4f}",
                    "close": f"{c:.4f}",
                    "volume": "5000",
                }
            )
            t -= seconds
    values.reverse()
    return httpx.Response(
        200,
        json={
            "meta": {
                "symbol": symbol,
                "interval": interval,
                "currency": "USD",
                "exchange_timezone": "America/New_York",
                "exchange": "NASDAQ",
                "mic_code": "XNGS",
                "type": "Common Stock",
            },
            "values": values,
            "status": "ok",
        },
    )


def twelvedata_quote(request: httpx.Request) -> httpx.Response:
    symbol = request.url.params["symbol"]
    if symbol.startswith(("NOPE", "ZZZ")):
        return httpx.Response(200, json={"code": 404, "message": "**symbol** not found: NOPE", "status": "error"})
    body = load_fixture("twelvedata_quote_aapl.json")
    body["symbol"] = symbol
    body["timestamp"] = body["last_quote_at"] = int(time.time()) - 30
    return httpx.Response(200, json=body)


def twelvedata_routes(router: respx.MockRouter) -> None:
    router.get(f"{TWELVEDATA}/time_series").mock(side_effect=twelvedata_series)
    router.get(f"{TWELVEDATA}/quote").mock(side_effect=twelvedata_quote)
    router.get(f"{TWELVEDATA}/symbol_search").mock(return_value=httpx.Response(200, json=load_fixture("twelvedata_search_aapl.json")))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        redis_url=None,
        twelvedata_api_key="test-key",
        twelvedata_credits_per_minute=1000,
        twelvedata_credits_per_day=None,
        coinbase_requests_per_second=10,
        rate_limit_per_minute=10_000,
        log_json=False,
        log_level="WARNING",
        http_max_retries=1,
    )


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        twelvedata_routes(router)
        yield router


@pytest.fixture
async def client(settings: Settings, mock_upstream: respx.MockRouter) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def today_utc() -> date:
    return datetime.now(UTC).date()
