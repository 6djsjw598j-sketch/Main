"""Provider integrations against mocked upstream HTTP APIs."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.config import Settings
from app.errors import (
    ProviderAuthError,
    ProviderNotConfiguredError,
    ProviderPlanError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from app.models.market import AssetType, Timeframe
from app.providers.coinbase import CoinbaseProvider
from app.providers.registry import ProviderRegistry
from app.providers.twelvedata import TwelveDataProvider
from app.symbols import normalize_symbol
from tests.conftest import COINBASE, TWELVEDATA, coinbase_routes, load_fixture, twelvedata_routes


@pytest.fixture
def cb(settings: Settings) -> CoinbaseProvider:
    return CoinbaseProvider(settings)


@pytest.fixture
def td(settings: Settings) -> TwelveDataProvider:
    return TwelveDataProvider(settings)


# ----------------------------------------------------------------------------- Coinbase
async def test_coinbase_resolve_and_quote(cb, mock_upstream):
    asset = await cb.resolve(normalize_symbol("btc-usd"), AssetType.AUTO)
    assert asset is not None and asset.name == "Bitcoin / USD" and asset.asset_type is AssetType.CRYPTO
    quote = await cb.get_quote(asset)
    assert quote.price == 101.5
    assert quote.change == pytest.approx(1.5) and quote.change_percent == pytest.approx(1.5)
    assert quote.change_basis == "rolling_24h"
    assert quote.previous_close == 100 and quote.volume == 1234.5 and quote.bid == 101.4


async def test_coinbase_crypto_hint_and_unknown(cb, mock_upstream):
    assert (await cb.resolve(normalize_symbol("ETH"), AssetType.CRYPTO)).symbol == "ETH-USD"
    assert await cb.resolve(normalize_symbol("ZZZ-USD"), AssetType.AUTO) is None
    assert await cb.resolve(normalize_symbol("OLD-USD"), AssetType.AUTO) is None  # delisted
    assert await cb.resolve(normalize_symbol("AAPL"), AssetType.AUTO) is None


async def test_coinbase_candles_parse_field_order(cb):
    now = int(time.time())
    current_hour = now - now % 3600
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        router.get(f"{COINBASE}/products/BTC-USD/candles").mock(
            return_value=httpx.Response(
                200,
                json=[
                    [current_hour, 90.0, 110.0, 95.0, 105.0, 12.5],  # time, low, high, open, close, volume
                ],
            )
        )
        asset = await cb.resolve(normalize_symbol("BTC-USD"), AssetType.AUTO)
        series = await cb.get_candles(asset, Timeframe.H1, 1)
    candle = series.candles[0]
    assert (candle.open, candle.high, candle.low, candle.close, candle.volume) == (95.0, 110.0, 90.0, 105.0, 12.5)


async def test_coinbase_paginates_and_aggregates_4h(cb, mock_upstream):
    asset = await cb.resolve(normalize_symbol("BTC-USD"), AssetType.AUTO)
    series = await cb.get_candles(asset, Timeframe.H4, 200)
    assert len(series.candles) == 200
    assert series.aggregated_from == "1h"
    times = [c.time for c in series.candles]
    assert times == sorted(times) and all(t % 14400 == 0 for t in times)
    calls = [c for c in mock_upstream.calls if "/candles" in c.request.url.path]
    assert len(calls) >= 3  # 200 x 4 hourly candles need several 300-candle pages
    for call in calls:
        params = call.request.url.params
        span = datetime.fromisoformat(params["end"]) - datetime.fromisoformat(params["start"])
        assert span.total_seconds() / int(params["granularity"]) < 300


async def test_coinbase_404_and_rate_limit(cb):
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        router.get(f"{COINBASE}/products/BTC-USD/ticker").mock(return_value=httpx.Response(404, json={"message": "NotFound"}))
        asset = await cb.resolve(normalize_symbol("BTC-USD"), AssetType.AUTO)
        with pytest.raises(SymbolNotFoundError):
            await cb.get_quote(asset)
        router.get(f"{COINBASE}/products/BTC-USD/ticker").mock(return_value=httpx.Response(429, json={"message": "slow down"}))
        with pytest.raises(ProviderRateLimitedError):
            await cb.get_quote(asset)


async def test_coinbase_outage_retries_then_opens_circuit(cb):
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        route = router.get(f"{COINBASE}/products/BTC-USD/stats").mock(return_value=httpx.Response(503))
        router.get(f"{COINBASE}/products/BTC-USD/ticker").mock(return_value=httpx.Response(503))
        asset = await cb.resolve(normalize_symbol("BTC-USD"), AssetType.AUTO)
        with pytest.raises(ProviderUnavailableError):
            await cb.get_quote(asset)
        await asyncio.sleep(0.05)
        assert route.call_count == 2  # one retry (http_max_retries=1 in tests)
        for _ in range(3):
            with pytest.raises(ProviderUnavailableError):
                await cb.get_quote(asset)
        assert cb.health.status == "down"
        calls_before = route.call_count
        with pytest.raises(ProviderUnavailableError, match="temporarily unavailable"):
            await cb.get_quote(asset)
        assert route.call_count == calls_before  # circuit open: no upstream call


async def test_coinbase_timeout_maps_to_unavailable(cb):
    with respx.mock(assert_all_called=False) as router:
        coinbase_routes(router)
        router.get(f"{COINBASE}/products/BTC-USD/ticker").mock(side_effect=httpx.ConnectTimeout("timeout"))
        asset = await cb.resolve(normalize_symbol("BTC-USD"), AssetType.AUTO)
        with pytest.raises(ProviderUnavailableError):
            await cb.get_quote(asset)


async def test_coinbase_search(cb, mock_upstream):
    results = await cb.search("btc", AssetType.AUTO)
    assert [r.symbol for r in results][:2] == ["BTC-USD", "BTC-EUR"]
    assert await cb.search("btc", AssetType.STOCK) == []
    assert [r.symbol for r in await cb.search("solana", AssetType.AUTO)] == ["SOL-USD"]


# ----------------------------------------------------------------------------- Twelve Data
async def test_twelvedata_quote_uses_header_auth(td, mock_upstream):
    asset = await td.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
    quote = await td.get_quote(asset)
    fixture = load_fixture("twelvedata_quote_aapl.json")
    assert quote.price == pytest.approx(float(fixture["close"]))
    assert quote.previous_close == pytest.approx(float(fixture["previous_close"]))
    assert quote.change_basis == "previous_close"
    assert quote.fifty_two_week_high == pytest.approx(float(fixture["fifty_two_week"]["high"]))
    request = mock_upstream.calls.last.request
    assert request.headers["Authorization"] == "apikey test-key"
    assert "apikey" not in str(request.url) and "test-key" not in str(request.url)


async def test_twelvedata_time_series_parsing(td, mock_upstream):
    asset = await td.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
    daily = await td.get_candles(asset, Timeframe.D1, 50)
    assert len(daily.candles) == 50
    assert all(datetime.fromtimestamp(c.time, UTC).hour == 0 for c in daily.candles)
    assert daily.asset.timezone == "America/New_York" and daily.asset.asset_type is AssetType.STOCK
    intraday = await td.get_candles(asset, Timeframe.M5, 30)
    assert all(c.time % 300 == 0 for c in intraday.candles)
    params = mock_upstream.calls.last.request.url.params
    assert params["interval"] == "5min" and params["timezone"] == "UTC"


async def test_twelvedata_real_fixture_parsing(td):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TWELVEDATA}/time_series").mock(return_value=httpx.Response(200, json=load_fixture("twelvedata_aapl_daily.json")))
        asset = await td.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
        series = await td.get_candles(asset, Timeframe.D1, 60)
    raw = load_fixture("twelvedata_aapl_daily.json")["values"]
    assert len(series.candles) == len(raw)
    newest = max(raw, key=lambda r: r["datetime"])
    assert series.candles[-1].close == pytest.approx(float(newest["close"]))


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"code": 401, "message": "bad key", "status": "error"}, 401, ProviderAuthError),
        ({"code": 429, "message": "limit", "status": "error"}, 429, ProviderRateLimitedError),
        ({"code": 404, "message": "not found", "status": "error"}, 404, SymbolNotFoundError),
        ({"code": 400, "message": "**symbol** not found: XYZ", "status": "error"}, 200, SymbolNotFoundError),
        ({"code": 403, "message": "available on Pro plan", "status": "error"}, 403, ProviderPlanError),
        ({"code": 500, "message": "boom", "status": "error"}, 200, ProviderUnavailableError),
    ],
)
async def test_twelvedata_error_mapping(td, body, status, error):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TWELVEDATA}/quote").mock(return_value=httpx.Response(status, json=body))
        asset = await td.resolve(normalize_symbol("XYZ"), AssetType.AUTO)
        with pytest.raises(error):
            await td.get_quote(asset)


async def test_twelvedata_empty_values(td):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{TWELVEDATA}/time_series").mock(return_value=httpx.Response(200, json={"meta": {}, "values": [], "status": "ok"}))
        asset = await td.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
        series = await td.get_candles(asset, Timeframe.D1, 10)
    assert series.candles == []


async def test_twelvedata_local_credit_limit_blocks_before_http(settings):
    provider = TwelveDataProvider(settings.model_copy(update={"twelvedata_credits_per_minute": 2}))
    with respx.mock(assert_all_called=False) as router:
        twelvedata_routes(router)
        asset = await provider.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
        await provider.get_quote(asset)
        await provider.get_quote(asset)
        with pytest.raises(ProviderRateLimitedError) as info:
            await provider.get_quote(asset)
        assert info.value.retry_after and info.value.retry_after > 1
        assert router.calls.call_count == 2


async def test_twelvedata_daily_quota(settings):
    provider = TwelveDataProvider(settings.model_copy(update={"twelvedata_credits_per_day": 1}))
    with respx.mock(assert_all_called=False) as router:
        twelvedata_routes(router)
        asset = await provider.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
        await provider.get_quote(asset)
        with pytest.raises(ProviderRateLimitedError, match="daily"):
            await provider.get_quote(asset)


async def test_twelvedata_not_configured(settings):
    provider = TwelveDataProvider(settings.model_copy(update={"twelvedata_api_key": None}))
    asset = await provider.resolve(normalize_symbol("AAPL"), AssetType.AUTO)
    with pytest.raises(ProviderNotConfiguredError, match="TWELVEDATA_API_KEY"):
        await provider.get_quote(asset)


async def test_twelvedata_search_maps_types(td, mock_upstream):
    results = await td.search("AAPL", AssetType.AUTO)
    assert results[0].symbol == "AAPL" and results[0].asset_type is AssetType.STOCK
    assert any(":" in r.symbol for r in results[1:])  # non-US listings carry an exchange suffix


# ----------------------------------------------------------------------------- registry routing
async def test_registry_routing(settings, mock_upstream):
    registry = ProviderRegistry(settings)
    provider, asset = await registry.resolve("BTC-USD")
    assert provider.name == "coinbase"
    provider, asset = await registry.resolve("AAPL")
    assert provider.name == "twelvedata"
    provider, asset = await registry.resolve("EUR/USD")
    assert provider.name == "twelvedata" and asset.asset_type is AssetType.FOREX and asset.display_symbol == "EUR/USD"
    await registry.close()


async def test_registry_without_twelvedata_key(settings, mock_upstream):
    registry = ProviderRegistry(settings.model_copy(update={"twelvedata_api_key": None}))
    with pytest.raises(ProviderNotConfiguredError):
        await registry.resolve("AAPL")
    with pytest.raises(SymbolNotFoundError):
        await registry.resolve("ZZZ", AssetType.CRYPTO)
    results, notes = await registry.search("btc", AssetType.AUTO)
    assert results and any("Twelve Data" in n for n in notes)
    await registry.close()


def test_fixture_files_are_valid_json():
    for name in ("twelvedata_quote_aapl.json", "twelvedata_search_aapl.json", "twelvedata_aapl_daily.json", "coinbase_btc_daily_candles.json"):
        assert json.dumps(load_fixture(name))
