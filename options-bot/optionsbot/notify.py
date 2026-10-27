"""Optional chat notifications through an incoming webhook (Slack or Discord)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, webhook_url: str | None, prefix: str = "optionsbot", client: httpx.Client | None = None) -> None:
        self.webhook_url = webhook_url
        self.prefix = prefix
        self._client = client or (httpx.Client(timeout=5) if webhook_url else None)
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        text = f"[{self.prefix}] {message}"
        self.sent.append(text)
        if not self.webhook_url or self._client is None:
            return
        try:
            # "text" is read by Slack, "content" by Discord
            self._client.post(self.webhook_url, json={"text": text, "content": text}).raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("notification failed: %s", type(exc).__name__)
