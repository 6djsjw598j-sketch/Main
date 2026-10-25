"""Async SQLAlchemy engine/session setup (PostgreSQL in production, SQLite for local dev)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def create_engine(database_url: str) -> AsyncEngine:
    url = make_url(database_url)
    kwargs: dict = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=1800)
    return create_async_engine(database_url, **kwargs)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
