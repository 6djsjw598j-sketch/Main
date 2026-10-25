"""Anonymous workspaces, watchlist and preferences."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentWorkspace, DbSession, Symbol
from app.db import repository
from app.errors import RateLimitedError
from app.models.user import Preferences, WatchlistAdd, WatchlistOrder, WatchlistOut, WorkspaceCreated

router = APIRouter(tags=["watchlist & preferences"])


@router.post(
    "/workspaces", response_model=WorkspaceCreated, status_code=status.HTTP_201_CREATED, summary="Create an anonymous workspace and return its token"
)
async def create_workspace(request: Request, session: DbSession) -> WorkspaceCreated:
    limiter = request.app.state.workspace_limiter
    wait = limiter.hit(request.client.host if request.client else "unknown")
    if wait:
        raise RateLimitedError("Too many workspaces created from this address.", retry_after=wait)
    workspace, token = await repository.create_workspace(session)
    return WorkspaceCreated(token=token, created_at=workspace.created_at)


@router.get("/watchlist", response_model=WatchlistOut)
async def get_watchlist(session: DbSession, workspace: CurrentWorkspace) -> WatchlistOut:
    return await repository.list_watchlist(session, workspace)


@router.post("/watchlist", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def add_watchlist(body: WatchlistAdd, session: DbSession, workspace: CurrentWorkspace) -> WatchlistOut:
    return await repository.add_to_watchlist(session, workspace, body.symbol, body.asset_type)


@router.put("/watchlist/order", response_model=WatchlistOut, summary="Reorder the watchlist")
async def reorder_watchlist(body: WatchlistOrder, session: DbSession, workspace: CurrentWorkspace) -> WatchlistOut:
    return await repository.reorder_watchlist(session, workspace, body.symbols)


@router.delete("/watchlist/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(symbol: Symbol, session: DbSession, workspace: CurrentWorkspace) -> Response:
    await repository.remove_from_watchlist(session, workspace, symbol)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/preferences", response_model=Preferences)
async def get_preferences(session: DbSession, workspace: CurrentWorkspace) -> Preferences:
    return await repository.get_preferences(session, workspace)


@router.put("/preferences", response_model=Preferences)
async def put_preferences(body: Preferences, session: DbSession, workspace: CurrentWorkspace) -> Preferences:
    return await repository.save_preferences(session, workspace, body)
