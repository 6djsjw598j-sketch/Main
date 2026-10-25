"""Typed application errors and their JSON representation.

Every error that reaches a client has the shape::

    {"error": {"code": "...", "message": "...", "details": {...}, "retry_after": 12}}
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    #: Whether a cached (stale) copy may be served instead of failing.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        if self.retry_after is not None:
            body["retry_after"] = round(self.retry_after, 1)
        return {"error": body}


class InvalidSymbolError(AppError):
    status_code = 422
    code = "invalid_symbol"


class SymbolNotFoundError(AppError):
    status_code = 404
    code = "symbol_not_found"


class UnsupportedTimeframeError(AppError):
    status_code = 422
    code = "unsupported_timeframe"


class InvalidParameterError(AppError):
    status_code = 422
    code = "invalid_parameter"


class InsufficientDataError(AppError):
    status_code = 422
    code = "insufficient_data"


class ProviderNotConfiguredError(AppError):
    status_code = 503
    code = "provider_not_configured"


class ProviderError(AppError):
    """Base class for upstream market-data failures."""

    status_code = 502
    code = "provider_error"

    def __init__(self, message: str, *, provider: str, **kwargs: Any) -> None:
        details = kwargs.pop("details", None) or {}
        details.setdefault("provider", provider)
        super().__init__(message, details=details, **kwargs)
        self.provider = provider


class ProviderRateLimitedError(ProviderError):
    status_code = 429
    code = "provider_rate_limited"
    retryable = True


class ProviderUnavailableError(ProviderError):
    status_code = 503
    code = "provider_unavailable"
    retryable = True


class ProviderAuthError(ProviderError):
    status_code = 502
    code = "provider_auth_failed"


class ProviderPlanError(ProviderError):
    status_code = 403
    code = "provider_plan_restriction"


class ProviderBadResponseError(ProviderError):
    status_code = 502
    code = "provider_bad_response"
    retryable = True


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
