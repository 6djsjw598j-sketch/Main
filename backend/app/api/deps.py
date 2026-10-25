"""FastAPI dependencies: services from app state, DB sessions, workspace auth, shared params."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.service import AnalysisService
from app.analysis.signals import parse_weights
from app.db import repository
from app.db.models import Workspace
from app.errors import InvalidParameterError
from app.market.service import MarketDataService
from app.models.market import AssetType, Timeframe
from app.symbols import normalize_symbol


def market_service(request: Request) -> MarketDataService:
    return request.app.state.market


def analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analysis


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


async def current_workspace(
    session: Annotated[AsyncSession, Depends(db_session)],
    x_workspace_token: Annotated[str | None, Header(description="Token from POST /api/workspaces")] = None,
) -> Workspace:
    return await repository.get_workspace(session, x_workspace_token)


def symbol_path(symbol: Annotated[str, Path(min_length=1, max_length=40, description="e.g. AAPL, SPY, BTC-USD")]) -> str:
    return normalize_symbol(symbol).symbol


def weights_param(
    weights: Annotated[str | None, Query(max_length=400, description="Factor weight overrides, e.g. 'rsi:2,macd:0'")] = None,
) -> dict[str, float]:
    try:
        return parse_weights(weights)
    except ValueError as exc:
        raise InvalidParameterError(str(exc)) from exc


Symbol = Annotated[str, Depends(symbol_path)]
TimeframeQ = Annotated[Timeframe, Query(description="1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M")]
AssetTypeQ = Annotated[AssetType, Query(description="Routing hint; 'auto' detects crypto pairs")]
LimitQ = Annotated[int, Query(ge=50, le=2000, description="Number of bars")]
Weights = Annotated[dict[str, float], Depends(weights_param)]
Market = Annotated[MarketDataService, Depends(market_service)]
Analysis = Annotated[AnalysisService, Depends(analysis_service)]
DbSession = Annotated[AsyncSession, Depends(db_session)]
CurrentWorkspace = Annotated[Workspace, Depends(current_workspace)]
