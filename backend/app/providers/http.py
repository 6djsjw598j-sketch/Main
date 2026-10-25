"""Shared HTTP helper with bounded retries for transient upstream failures."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app import __version__
from app.errors import ProviderBadResponseError, ProviderUnavailableError

logger = logging.getLogger(__name__)

RETRY_STATUSES = {500, 502, 503, 504}


class ProviderHTTP:
    def __init__(
        self,
        provider: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.provider = provider
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": f"MarketLens/{__version__}", "Accept": "application/json"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._client.get(path, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self.max_retries:
                    raise ProviderUnavailableError(
                        f"Could not reach {self.provider}: {type(exc).__name__}.",
                        provider=self.provider,
                        retry_after=10,
                    ) from exc
            else:
                if response.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                    if response.status_code in RETRY_STATUSES:
                        raise ProviderUnavailableError(
                            f"{self.provider} returned HTTP {response.status_code}.",
                            provider=self.provider,
                            retry_after=15,
                        )
                    return response
            attempt += 1
            delay = 0.4 * (2 ** (attempt - 1))
            logger.info("retrying provider request", extra={"provider": self.provider, "path": path, "attempt": attempt})
            await asyncio.sleep(delay)

    def parse_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderBadResponseError(
                f"{self.provider} returned a non-JSON response (HTTP {response.status_code}).",
                provider=self.provider,
            ) from exc

    async def close(self) -> None:
        await self._client.aclose()
