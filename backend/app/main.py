"""FastAPI application factory. Run with ``uvicorn app.main:create_app --factory``."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import __version__
from app.analysis.service import AnalysisService
from app.api import analysis as analysis_routes
from app.api import market as market_routes
from app.api import system as system_routes
from app.api import user as user_routes
from app.cache import CacheBackend, CachedFetcher, create_cache_backend
from app.config import Settings, get_settings
from app.db.base import Base, create_engine, create_sessionmaker
from app.errors import AppError
from app.logging_config import configure_logging, request_id_var
from app.market.service import MarketDataService
from app.providers.registry import ProviderRegistry
from app.ratelimit import SlidingWindowLimiter

logger = logging.getLogger("app")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
}


def _error_response(status: int, body: dict, retry_after: float | None = None) -> JSONResponse:
    headers = {"Retry-After": str(max(1, int(retry_after + 0.999)))} if retry_after else None
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    cache_backend: CacheBackend | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    cache_backend = cache_backend or create_cache_backend(settings.redis_url, settings.memory_cache_max_entries)
    registry = registry or ProviderRegistry(settings)
    market = MarketDataService(registry, CachedFetcher(cache_backend), settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        if not settings.is_production:
            # Production uses Alembic migrations (see backend/docker-entrypoint.sh).
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        logger.info(
            "startup",
            extra={
                "version": __version__,
                "env": settings.app_env,
                "cache": cache_backend.name,
                "providers": {p.name: p.configured for p in registry.providers},
            },
        )
        if settings.is_production and settings.debug:
            logger.warning("DEBUG is enabled in production; disable it")
        yield
        await registry.close()
        await cache_backend.close()
        await engine.dispose()

    docs = settings.enable_api_docs
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description=(
            "Market data, technical indicators, support/resistance detection and explainable technical summaries. "
            "All analysis is educational and not financial advice."
        ),
        debug=settings.debug and not settings.is_production,
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url="/api/redoc" if docs else None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.cache_backend = cache_backend
    app.state.market = market
    app.state.analysis = AnalysisService(market)
    app.state.api_limiter = SlidingWindowLimiter(settings.rate_limit_per_minute, 60.0)
    app.state.workspace_limiter = SlidingWindowLimiter(settings.workspace_creation_per_hour, 3600.0)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "X-Workspace-Token", "X-Request-ID"],
            max_age=600,
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        client = request.client.host if request.client else "unknown"
        try:
            path = request.url.path
            if path.startswith("/api/") and path != "/api/health":
                wait = app.state.api_limiter.hit(client)
                if wait:
                    response: Response = _error_response(
                        429,
                        {"error": {"code": "rate_limited", "message": "Too many requests; slow down.", "retry_after": round(wait, 1)}},
                        wait,
                    )
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client": client,
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        level = logging.WARNING if exc.status_code >= 500 or exc.status_code == 429 else logging.INFO
        logger.log(level, "app error", extra={"code": exc.code, "status": exc.status_code, "error": exc.message})
        return _error_response(exc.status_code, exc.to_dict(), exc.retry_after)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
        return JSONResponse(
            {"error": {"code": "validation_error", "message": "Invalid request parameters.", "details": {"errors": jsonable_encoder(errors)}}},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse({"error": {"code": "internal_error", "message": "An unexpected error occurred."}}, status_code=500)

    for router in (system_routes.router, market_routes.router, analysis_routes.router, user_routes.router):
        app.include_router(router, prefix="/api")
    return app
