"""Freshness classification, trading sessions, symbol validation, caching and rate limiting."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from app.cache import CachedFetcher, MemoryCache, RedisCache
from app.errors import InvalidSymbolError, ProviderRateLimitedError, SymbolNotFoundError
from app.market.freshness import classify_candle_freshness, classify_freshness
from app.market.sessions import market_session, us_schedule_phase
from app.models.market import AssetType, MarketSession
from app.ratelimit import DailyQuota, SlidingWindowLimiter, TokenBucket
from app.symbols import normalize_symbol

NOW = datetime(2024, 3, 6, 15, 0, tzinfo=UTC)  # Wednesday 10:00 New York
OPEN = MarketSession(status="open", label="Market open", timezone="America/New_York", is_open=True)
CLOSED = MarketSession(status="closed", label="Market closed", timezone="America/New_York", is_open=False)
CRYPTO = MarketSession(status="always_open", label="24/7 market", timezone="UTC", is_open=True)


# ----------------------------------------------------------------------------- freshness
@pytest.mark.parametrize(
    ("age", "session", "crypto", "status", "stale"),
    [
        (30, CRYPTO, True, "real_time", False),
        (600, CRYPTO, True, "delayed", False),
        (7200, CRYPTO, True, "stale", True),
        (60, OPEN, False, "real_time", False),
        (20 * 60, OPEN, False, "delayed", False),
        (2 * 3600, OPEN, False, "stale", True),
        (2 * 86400, CLOSED, False, "end_of_day", False),
        (10 * 86400, CLOSED, False, "stale", True),
    ],
)
def test_classify_freshness(age, session, crypto, status, stale):
    f = classify_freshness(NOW - timedelta(seconds=age), is_crypto=crypto, session=session, now=NOW)
    assert f.status == status
    assert f.is_stale is stale
    assert f.age_seconds == pytest.approx(age)


def test_delayed_data_is_never_labelled_real_time():
    f = classify_freshness(NOW - timedelta(minutes=15), is_crypto=False, session=OPEN, now=NOW)
    assert f.status == "delayed" and "15 min" in f.label


def test_candle_freshness():
    bar_open = NOW - timedelta(minutes=30)
    live = classify_candle_freshness(bar_open, bar_open + timedelta(hours=1), is_crypto=True, session=CRYPTO, timeframe_label="1h", now=NOW)
    assert live.status == "real_time" and live.label == "Live bar"
    old_open = NOW - timedelta(days=3)
    stale = classify_candle_freshness(old_open, old_open + timedelta(hours=1), is_crypto=True, session=CRYPTO, timeframe_label="1h", now=NOW)
    assert stale.is_stale
    closed = classify_candle_freshness(old_open, old_open + timedelta(days=1), is_crypto=False, session=CLOSED, timeframe_label="1d", now=NOW)
    assert closed.status == "end_of_day"


# ----------------------------------------------------------------------------- sessions
@pytest.mark.parametrize(
    ("utc_time", "phase"),
    [
        ("2024-03-06 10:00", "pre_market"),  # 05:00 ET
        ("2024-03-06 15:00", "open"),  # 10:00 ET
        ("2024-03-06 21:30", "after_hours"),  # 16:30 ET
        ("2024-03-07 02:00", "closed"),  # 21:00 ET
        ("2024-03-09 15:00", "closed"),  # Saturday
    ],
)
def test_us_schedule(utc_time, phase):
    now = datetime.fromisoformat(utc_time).replace(tzinfo=UTC)
    assert us_schedule_phase(now) == phase


def test_provider_flag_detects_holiday():
    s = market_session(AssetType.STOCK, "America/New_York", NOW, provider_open_flag=False)
    assert s.status == "closed" and "holiday" in (s.note or "")


def test_crypto_and_foreign_sessions():
    assert market_session(AssetType.CRYPTO, "UTC", NOW).status == "always_open"
    assert market_session(AssetType.STOCK, "Europe/London", NOW, provider_open_flag=True).is_open
    assert market_session(AssetType.STOCK, "Europe/London", NOW).status == "unknown"
    saturday = datetime(2024, 3, 9, 12, 0, tzinfo=UTC)
    assert not market_session(AssetType.FOREX, "UTC", saturday).is_open


# ----------------------------------------------------------------------------- symbols
@pytest.mark.parametrize(
    ("raw", "symbol", "pair"),
    [
        ("aapl", "AAPL", False),
        (" btc/usd ", "BTC-USD", True),
        ("BRK.B", "BRK.B", False),
        ("vod:lse", "VOD:LSE", False),
        ("eth-usd", "ETH-USD", True),
        ("^GSPC", "^GSPC", False),
    ],
)
def test_normalize_symbol(raw, symbol, pair):
    parsed = normalize_symbol(raw)
    assert parsed.symbol == symbol and parsed.is_pair is pair


@pytest.mark.parametrize("raw", ["", "   ", "AAPL;DROP", "<script>", "A" * 40, "AAPL:", "a b c$"])
def test_invalid_symbols_rejected(raw):
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(raw)


# ----------------------------------------------------------------------------- cache
class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def test_memory_cache_expiry_and_lru():
    clock = FakeClock()
    cache = MemoryCache(max_entries=2, clock=clock)
    await cache.set("a", "1", ttl=10)
    await cache.set("b", "2", ttl=10)
    assert await cache.get("a") == "1"  # a becomes most recent
    await cache.set("c", "3", ttl=10)  # evicts b
    assert await cache.get("b") is None
    clock.t += 11
    assert await cache.get("a") is None


async def test_cached_fetcher_hit_and_single_flight():
    clock = FakeClock()
    fetcher = CachedFetcher(MemoryCache(clock=clock), clock=clock)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"v": calls}

    results = await asyncio.gather(*(fetcher.get_or_fetch("k", fetch, ttl=5, stale_ttl=100) for _ in range(5)))
    assert calls == 1
    assert sum(1 for r in results if not r.hit) == 1
    again = await fetcher.get_or_fetch("k", fetch, ttl=5, stale_ttl=100)
    assert again.hit and calls == 1


async def test_cached_fetcher_serves_stale_on_retryable_error():
    clock = FakeClock()
    fetcher = CachedFetcher(MemoryCache(clock=clock), clock=clock)

    async def ok():
        return [1, 2, 3]

    async def limited():
        raise ProviderRateLimitedError("limit", provider="x", retry_after=30)

    await fetcher.get_or_fetch("k", ok, ttl=5, stale_ttl=100)
    clock.t += 10
    result = await fetcher.get_or_fetch("k", limited, ttl=5, stale_ttl=100)
    assert result.stale and result.value == [1, 2, 3] and "cached data" in (result.warning or "")


async def test_cached_fetcher_propagates_non_retryable_and_cold_errors():
    clock = FakeClock()
    fetcher = CachedFetcher(MemoryCache(clock=clock), clock=clock)

    async def missing():
        raise SymbolNotFoundError("nope")

    async def limited():
        raise ProviderRateLimitedError("limit", provider="x", retry_after=30)

    with pytest.raises(ProviderRateLimitedError):
        await fetcher.get_or_fetch("cold", limited, ttl=5, stale_ttl=100)

    async def ok():
        return 1

    await fetcher.get_or_fetch("k", ok, ttl=5, stale_ttl=100)
    clock.t += 10
    with pytest.raises(SymbolNotFoundError):
        await fetcher.get_or_fetch("k", missing, ttl=5, stale_ttl=100)


async def test_permanent_errors_are_negatively_cached():
    clock = FakeClock()
    fetcher = CachedFetcher(MemoryCache(clock=clock), clock=clock, negative_ttl=60)
    calls = 0

    async def missing():
        nonlocal calls
        calls += 1
        raise SymbolNotFoundError("nope")

    for _ in range(3):
        with pytest.raises(SymbolNotFoundError):
            await fetcher.get_or_fetch("k", missing, ttl=5, stale_ttl=100)
    assert calls == 1
    clock.t += 61
    with pytest.raises(SymbolNotFoundError):
        await fetcher.get_or_fetch("k", missing, ttl=5, stale_ttl=100)
    assert calls == 2


async def test_redis_cache_backend():
    cache = RedisCache(client=fakeredis.FakeAsyncRedis(decode_responses=True))
    await cache.set("x", "value", ttl=60)
    assert await cache.get("x") == "value"
    assert await cache.ping()
    await cache.delete("x")
    assert await cache.get("x") is None


# ----------------------------------------------------------------------------- rate limiting
async def test_token_bucket_reports_wait_without_consuming():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_second=1.0, capacity=2, clock=clock)
    assert await bucket.acquire() == 0
    assert await bucket.acquire() == 0
    wait = await bucket.acquire(max_wait=0)
    assert wait == pytest.approx(1.0)
    clock.t += 1.0
    assert await bucket.acquire() == 0


def test_daily_quota_resets_at_utc_midnight():
    now = [datetime(2024, 3, 6, 23, 59, tzinfo=UTC)]
    quota = DailyQuota(2, now=lambda: now[0])
    assert quota.try_consume() and quota.try_consume()
    assert not quota.try_consume()
    assert quota.seconds_until_reset() == pytest.approx(60)
    now[0] = datetime(2024, 3, 7, 0, 1, tzinfo=UTC)
    assert quota.try_consume() and quota.remaining() == 1


def test_sliding_window_limiter():
    clock = FakeClock()
    limiter = SlidingWindowLimiter(2, 60, clock=clock)
    assert limiter.hit("ip") == 0 and limiter.hit("ip") == 0
    assert limiter.hit("ip") == pytest.approx(60)
    assert limiter.hit("other") == 0
    clock.t += 61
    assert limiter.hit("ip") == 0
