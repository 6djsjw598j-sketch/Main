"""Watchlist and preference schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.indicators.params import IndicatorParams
from app.models.market import AssetType, Timeframe
from app.symbols import normalize_symbol

MAX_WATCHLIST = 50

OVERLAY_KEYS = {
    "sma",
    "ema",
    "vwap",
    "bbands",
    "keltner",
    "ichimoku",
}
PANEL_KEYS = {"rsi", "macd", "stoch", "adx", "atr", "roc", "willr", "obv", "mfi", "ad", "hv"}


class WorkspaceCreated(BaseModel):
    token: str = Field(description="Bearer token for X-Workspace-Token. Shown once; store it client-side.")
    created_at: datetime


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    asset_type: AssetType = AssetType.AUTO

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        return normalize_symbol(v).symbol


class WatchlistOrder(BaseModel):
    symbols: list[str] = Field(max_length=MAX_WATCHLIST)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        out = [normalize_symbol(s).symbol for s in v]
        if len(set(out)) != len(out):
            raise ValueError("symbols must be unique")
        return out


class WatchlistItemOut(BaseModel):
    symbol: str
    asset_type: AssetType
    position: int
    added_at: datetime


class WatchlistOut(BaseModel):
    items: list[WatchlistItemOut]
    max_items: int = MAX_WATCHLIST


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["dark", "light"] = "dark"
    timeframe: Timeframe = Timeframe.D1
    chart_type: Literal["candles", "bars", "line"] = "candles"
    timezone_mode: Literal["local", "utc", "exchange"] = "local"
    last_symbol: str | None = None
    last_asset_type: AssetType = AssetType.AUTO
    bars: int = Field(500, ge=100, le=2000)
    overlays: list[str] = Field(default=["sma", "ema"], max_length=len(OVERLAY_KEYS))
    panels: list[str] = Field(default=["rsi", "macd"], max_length=len(PANEL_KEYS))
    show_volume: bool = True
    show_levels: bool = True
    show_reference_levels: bool = False
    show_pivots: bool = False
    show_fibonacci: bool = False
    show_trendlines: bool = True
    auto_refresh: bool = True
    indicator_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("last_symbol")
    @classmethod
    def _last_symbol(cls, v: str | None) -> str | None:
        return None if v is None else normalize_symbol(v).symbol

    @field_validator("overlays")
    @classmethod
    def _overlays(cls, v: list[str]) -> list[str]:
        unknown = set(v) - OVERLAY_KEYS
        if unknown:
            raise ValueError(f"Unknown overlays: {sorted(unknown)}")
        return list(dict.fromkeys(v))

    @field_validator("panels")
    @classmethod
    def _panels(cls, v: list[str]) -> list[str]:
        unknown = set(v) - PANEL_KEYS
        if unknown:
            raise ValueError(f"Unknown panels: {sorted(unknown)}")
        return list(dict.fromkeys(v))

    @field_validator("indicator_params")
    @classmethod
    def _params(cls, v: dict[str, Any]) -> dict[str, Any]:
        unknown = set(v) - set(IndicatorParams.model_fields)
        if unknown:
            raise ValueError(f"Unknown indicator parameters: {sorted(unknown)}")
        validated = IndicatorParams.model_validate(v)
        return {k: getattr(validated, k) for k in v}
