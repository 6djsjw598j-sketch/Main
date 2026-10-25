"""Health check and metadata (timeframes, factor model, provider capabilities)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.analysis.signals import FACTORS
from app.indicators.params import IndicatorParams
from app.models.analysis import DISCLAIMER
from app.models.market import AssetType, Timeframe
from app.providers.base import ProviderStatus

router = APIRouter(tags=["system"])


class ComponentHealth(BaseModel):
    ok: bool
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    time: datetime
    database: ComponentHealth
    cache: ComponentHealth
    providers: list[ProviderStatus]


class FactorInfo(BaseModel):
    key: str
    name: str
    group: str
    default_weight: float
    formula: str


class MetaResponse(BaseModel):
    app_name: str
    version: str
    timeframes: list[str]
    asset_types: list[str]
    default_indicator_params: IndicatorParams
    factors: list[FactorInfo]
    providers: list[ProviderStatus]
    disclaimer: str


@router.get("/health", response_model=HealthResponse, summary="Liveness/readiness check")
async def health(request: Request) -> JSONResponse:
    state = request.app.state
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db = ComponentHealth(ok=True, detail="connected")
    except Exception as exc:  # report, don't crash the health endpoint
        db = ComponentHealth(ok=False, detail=type(exc).__name__)
    cache_ok = await state.cache_backend.ping()
    providers = state.registry.statuses()
    degraded = any(p.status in ("down", "degraded") for p in providers if p.configured) or not cache_ok
    body = HealthResponse(
        status="error" if not db.ok else "degraded" if degraded else "ok",
        version=__version__,
        environment=state.settings.app_env,
        time=datetime.now(UTC),
        database=db,
        cache=ComponentHealth(ok=cache_ok, detail=state.cache_backend.name),
        providers=providers,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if db.ok else 503)


@router.get("/meta", response_model=MetaResponse, summary="Timeframes, factor model, provider capabilities")
async def meta(request: Request) -> MetaResponse:
    state = request.app.state
    return MetaResponse(
        app_name=state.settings.app_name,
        version=__version__,
        timeframes=[tf.value for tf in Timeframe],
        asset_types=[a.value for a in AssetType],
        default_indicator_params=IndicatorParams(),
        factors=[FactorInfo(key=f.key, name=f.name, group=f.group, default_weight=f.weight, formula=f.formula) for f in FACTORS],
        providers=state.registry.statuses(),
        disclaimer=DISCLAIMER,
    )
