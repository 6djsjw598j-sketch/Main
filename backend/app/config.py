"""Application settings loaded from environment variables (or a local .env file).

Secrets (API keys, database passwords) are only ever read from the environment and are
wrapped in ``SecretStr`` so they are never printed in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General -------------------------------------------------------------------
    app_name: str = "MarketLens"
    app_env: Literal["development", "production", "test"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = True
    enable_api_docs: bool = True
    # Comma-separated list of origins allowed by CORS. In production the frontend is
    # served from the same origin behind Nginx, so this can usually stay empty.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Storage -------------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/marketlens.db"
    redis_url: str | None = None
    memory_cache_max_entries: int = 2000

    # --- API protection --------------------------------------------------------------
    rate_limit_per_minute: int = Field(default=240, ge=10, le=10_000)
    workspace_creation_per_hour: int = Field(default=20, ge=1, le=1000)

    # --- Market data providers -------------------------------------------------------
    http_timeout_seconds: float = 10.0
    http_max_retries: int = Field(default=2, ge=0, le=5)

    coinbase_enabled: bool = True
    coinbase_base_url: str = "https://api.exchange.coinbase.com"
    # Coinbase documents 10 req/s per IP for public endpoints; stay below it.
    coinbase_requests_per_second: float = Field(default=8.0, gt=0, le=10)

    twelvedata_api_key: SecretStr | None = None
    twelvedata_base_url: str = "https://api.twelvedata.com"
    # Basic (free) plan: 8 credits/minute, 800 credits/day. Raise on paid plans.
    twelvedata_credits_per_minute: int = Field(default=8, ge=1)
    twelvedata_credits_per_day: int | None = Field(default=800, ge=1)

    # --- Cache TTLs (seconds) --------------------------------------------------------
    quote_ttl_crypto: int = 4
    quote_ttl_equity: int = 60
    search_ttl: int = 6 * 3600
    stale_ttl: int = 6 * 3600  # how long a stale copy may be served during an outage

    @field_validator("twelvedata_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("redis_url", mode="before")
    @classmethod
    def _empty_redis_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def twelvedata_configured(self) -> bool:
        return self.twelvedata_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
