"""Data access for workspaces, watchlists and preferences."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import Preference, WatchlistItem, Workspace
from app.errors import ConflictError, InvalidParameterError, NotFoundError, UnauthorizedError
from app.models.market import AssetType
from app.models.user import MAX_WATCHLIST, Preferences, WatchlistItemOut, WatchlistOut


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; every timestamp is stored in UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_workspace(session: AsyncSession) -> tuple[Workspace, str]:
    token = secrets.token_urlsafe(32)
    workspace = Workspace(token_hash=hash_token(token))
    session.add(workspace)
    await session.commit()
    return workspace, token


async def get_workspace(session: AsyncSession, token: str | None) -> Workspace:
    if not token or len(token) > 128:
        raise UnauthorizedError("Missing or invalid X-Workspace-Token header.")
    workspace = await session.scalar(select(Workspace).where(Workspace.token_hash == hash_token(token)))
    if workspace is None:
        raise UnauthorizedError("Unknown workspace token.")
    now = utcnow()
    if (now - _aware(workspace.last_seen_at)).total_seconds() > 3600:
        workspace.last_seen_at = now
        await session.commit()
    return workspace


async def list_watchlist(session: AsyncSession, workspace: Workspace) -> WatchlistOut:
    rows = (
        await session.scalars(
            select(WatchlistItem).where(WatchlistItem.workspace_id == workspace.id).order_by(WatchlistItem.position, WatchlistItem.id)
        )
    ).all()
    return WatchlistOut(
        items=[
            WatchlistItemOut(symbol=r.symbol, asset_type=AssetType(r.asset_type), position=r.position, added_at=_aware(r.created_at)) for r in rows
        ]
    )


async def add_to_watchlist(session: AsyncSession, workspace: Workspace, symbol: str, asset_type: AssetType) -> WatchlistOut:
    count = await session.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.workspace_id == workspace.id))
    if (count or 0) >= MAX_WATCHLIST:
        raise InvalidParameterError(f"A watchlist can hold at most {MAX_WATCHLIST} symbols.")
    max_pos = await session.scalar(select(func.max(WatchlistItem.position)).where(WatchlistItem.workspace_id == workspace.id))
    session.add(
        WatchlistItem(workspace_id=workspace.id, symbol=symbol, asset_type=asset_type.value, position=(max_pos if max_pos is not None else -1) + 1)
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(f"{symbol} is already on the watchlist.") from exc
    return await list_watchlist(session, workspace)


async def remove_from_watchlist(session: AsyncSession, workspace: Workspace, symbol: str) -> None:
    result = await session.execute(delete(WatchlistItem).where(WatchlistItem.workspace_id == workspace.id, WatchlistItem.symbol == symbol))
    await session.commit()
    if not result.rowcount:  # type: ignore[attr-defined]
        raise NotFoundError(f"{symbol} is not on the watchlist.")


async def reorder_watchlist(session: AsyncSession, workspace: Workspace, symbols: list[str]) -> WatchlistOut:
    rows = (await session.scalars(select(WatchlistItem).where(WatchlistItem.workspace_id == workspace.id))).all()
    by_symbol = {r.symbol: r for r in rows}
    if set(symbols) != set(by_symbol):
        raise InvalidParameterError("The new order must contain exactly the symbols currently on the watchlist.")
    for position, symbol in enumerate(symbols):
        by_symbol[symbol].position = position
    await session.commit()
    return await list_watchlist(session, workspace)


async def get_preferences(session: AsyncSession, workspace: Workspace) -> Preferences:
    row = await session.get(Preference, workspace.id)
    if row is None:
        return Preferences()
    try:
        return Preferences.model_validate(row.data)
    except ValueError:
        return Preferences()  # stored data from an older schema; fall back to defaults


async def save_preferences(session: AsyncSession, workspace: Workspace, prefs: Preferences) -> Preferences:
    row = await session.get(Preference, workspace.id)
    data = prefs.model_dump(mode="json")
    if row is None:
        session.add(Preference(workspace_id=workspace.id, data=data))
    else:
        row.data = data
        row.updated_at = utcnow()
    await session.commit()
    return prefs
