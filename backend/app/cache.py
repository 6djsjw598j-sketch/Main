"""Caching: in-memory LRU or Redis, with single-flight fetches and stale fallback.

``CachedFetcher.get_or_fetch`` returns fresh data from the cache when possible, fetches
upstream otherwise, and - when the upstream fails with a retryable error (rate limit,
outage) - serves the last good copy explicitly flagged as stale.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.errors import AppError

logger = logging.getLogger(__name__)


class CacheBackend(Protocol):
    name: str

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class MemoryCache:
    """Process-local LRU cache with per-entry expiry."""

    name = "memory"

    def __init__(self, max_entries: int = 2000, clock: Callable[[], float] = time.monotonic) -> None:
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._max = max_entries
        self._clock = clock

    async def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < self._clock():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = (self._clock() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()


class RedisCache:
    name = "redis"

    def __init__(self, url: str | None = None, client: Any | None = None) -> None:
        if client is not None:
            self._redis = client
        else:
            from redis import asyncio as aioredis

            self._redis = aioredis.from_url(url or "redis://localhost:6379/0", encoding="utf-8", decode_responses=True)

    async def get(self, key: str) -> str | None:
        try:
            return await self._redis.get(key)
        except Exception as exc:  # cache outages must never break requests
            logger.warning("redis get failed", extra={"error": str(exc)})
            return None

    async def set(self, key: str, value: str, ttl: int) -> None:
        try:
            await self._redis.set(key, value, ex=max(1, int(ttl)))
        except Exception as exc:
            logger.warning("redis set failed", extra={"error": str(exc)})

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as exc:
            logger.warning("redis delete failed", extra={"error": str(exc)})

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()


@dataclass
class CacheResult:
    value: Any
    hit: bool
    stale: bool
    age_seconds: float
    warning: str | None = None


class CachedFetcher:
    """Read-through cache with single-flight and stale-on-error semantics."""

    def __init__(self, backend: CacheBackend, clock: Callable[[], float] = time.time) -> None:
        self.backend = backend
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) > 5000:
                self._locks = {k: v for k, v in self._locks.items() if v.locked()}
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def _read(self, key: str) -> tuple[float, Any] | None:
        raw = await self.backend.get(key)
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
            return float(envelope["t"]), envelope["v"]
        except (ValueError, KeyError, TypeError):
            return None

    async def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Awaitable[Any]],
        *,
        ttl: float,
        stale_ttl: int,
    ) -> CacheResult:
        """``fetch`` must return a JSON-serializable value."""
        cached = await self._read(key)
        now = self._clock()
        if cached is not None and now - cached[0] < ttl:
            return CacheResult(cached[1], hit=True, stale=False, age_seconds=now - cached[0])

        async with self._lock(key):
            # Another request may have refreshed the entry while we waited.
            cached = await self._read(key)
            now = self._clock()
            if cached is not None and now - cached[0] < ttl:
                return CacheResult(cached[1], hit=True, stale=False, age_seconds=now - cached[0])
            try:
                value = await fetch()
            except AppError as exc:
                if exc.retryable and cached is not None:
                    age = now - cached[0]
                    logger.warning(
                        "serving stale cache after provider error",
                        extra={"cache_key": key, "error_code": exc.code, "age_s": round(age, 1)},
                    )
                    return CacheResult(
                        cached[1],
                        hit=True,
                        stale=True,
                        age_seconds=age,
                        warning=f"{exc.message} Showing cached data from {int(age)}s ago.",
                    )
                raise
            await self.backend.set(key, json.dumps({"t": self._clock(), "v": value}), stale_ttl)
            return CacheResult(value, hit=False, stale=False, age_seconds=0.0)


def create_cache_backend(redis_url: str | None, max_entries: int) -> CacheBackend:
    if redis_url:
        return RedisCache(redis_url)
    return MemoryCache(max_entries=max_entries)
