"""Rate limiting primitives.

* ``TokenBucket`` protects upstream providers from being called faster than their plan
  allows (e.g. Twelve Data Basic: 8 credits/minute).
* ``DailyQuota`` tracks a per-UTC-day credit allowance.
* ``SlidingWindowLimiter`` limits how often a single client IP may call this API.

These are process-local; run a single application worker (the default) so the limits
are enforced exactly. The app is async, so one worker handles many concurrent requests.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime


class TokenBucket:
    def __init__(
        self,
        rate_per_second: float,
        capacity: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate_per_second
        self.capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens

    def wait_time(self, tokens: float = 1.0) -> float:
        self._refill()
        if self._tokens >= tokens:
            return 0.0
        return (tokens - self._tokens) / self.rate

    async def acquire(self, tokens: float = 1.0, max_wait: float = 0.0) -> float:
        """Take ``tokens``; wait at most ``max_wait`` seconds.

        Returns 0 on success, otherwise the number of seconds until enough tokens exist
        (nothing is consumed in that case).
        """
        async with self._lock:
            wait = self.wait_time(tokens)
            if wait > max_wait:
                return wait
            if wait > 0:
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= tokens
            return 0.0


class DailyQuota:
    def __init__(self, limit: int | None, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.limit = limit
        self._now = now
        self._day = self._now().date()
        self.used = 0

    def _roll(self) -> None:
        today = self._now().date()
        if today != self._day:
            self._day = today
            self.used = 0

    def remaining(self) -> int | None:
        self._roll()
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    def try_consume(self, credits: int = 1) -> bool:
        self._roll()
        if self.limit is not None and self.used + credits > self.limit:
            return False
        self.used += credits
        return True

    def seconds_until_reset(self) -> float:
        now = self._now()
        tomorrow = datetime(now.year, now.month, now.day, tzinfo=UTC).timestamp() + 86400
        return max(0.0, tomorrow - now.timestamp())


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float:
        """Record a request. Returns 0 if allowed, else seconds until a slot frees up."""
        now = self._clock()
        bucket = self._hits.setdefault(key, deque())
        while bucket and bucket[0] <= now - self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return bucket[0] + self.window - now
        bucket.append(now)
        if len(self._hits) > 50_000:  # bound memory under abuse
            self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - self.window}
        return 0.0
