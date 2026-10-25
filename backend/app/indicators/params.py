"""User-configurable indicator parameters (validated query parameters)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _parse_periods(value: object, name: str) -> list[int]:
    raw_items = value if isinstance(value, (list, tuple)) else [value]
    items = [p for item in raw_items for p in str(item).replace(" ", "").split(",") if p]
    try:
        periods = sorted({int(p) for p in items})
    except ValueError as exc:
        raise ValueError(f"{name} must be a comma-separated list of integers") from exc
    if not periods:
        raise ValueError(f"{name} needs at least one period")
    if len(periods) > 6:
        raise ValueError(f"{name} accepts at most 6 periods")
    if any(p < 2 or p > 400 for p in periods):
        raise ValueError(f"{name} periods must be between 2 and 400")
    return periods


class IndicatorParams(BaseModel):
    # Unknown keys are ignored so the model can be used directly as a query-parameter model.
    model_config = ConfigDict(extra="ignore")

    sma_periods: list[int] = Field(default=[20, 50, 100, 200], description="Comma-separated, e.g. 20,50,100,200")
    ema_periods: list[int] = Field(default=[9, 12, 21, 50, 200], description="Comma-separated, e.g. 9,12,21,50,200")
    rsi_period: int = Field(14, ge=2, le=100)
    rsi_overbought: float = Field(70.0, ge=50, le=95)
    rsi_oversold: float = Field(30.0, ge=5, le=50)
    macd_fast: int = Field(12, ge=2, le=100)
    macd_slow: int = Field(26, ge=3, le=200)
    macd_signal: int = Field(9, ge=2, le=50)
    stoch_k: int = Field(14, ge=2, le=100)
    stoch_smooth: int = Field(3, ge=1, le=20)
    stoch_d: int = Field(3, ge=1, le=20)
    roc_period: int = Field(12, ge=1, le=200)
    willr_period: int = Field(14, ge=2, le=100)
    bb_period: int = Field(20, ge=2, le=200)
    bb_std: float = Field(2.0, ge=0.5, le=5.0)
    atr_period: int = Field(14, ge=2, le=100)
    kc_period: int = Field(20, ge=2, le=200)
    kc_atr_period: int = Field(10, ge=2, le=100)
    kc_mult: float = Field(2.0, ge=0.5, le=5.0)
    hv_period: int = Field(20, ge=5, le=250)
    adx_period: int = Field(14, ge=2, le=100)
    ichimoku_tenkan: int = Field(9, ge=2, le=100)
    ichimoku_kijun: int = Field(26, ge=2, le=200)
    ichimoku_senkou_b: int = Field(52, ge=2, le=300)
    volume_sma_period: int = Field(20, ge=2, le=200)
    mfi_period: int = Field(14, ge=2, le=100)

    @field_validator("sma_periods", mode="before")
    @classmethod
    def _sma(cls, v: object) -> list[int]:
        return _parse_periods(v, "sma_periods")

    @field_validator("ema_periods", mode="before")
    @classmethod
    def _ema(cls, v: object) -> list[int]:
        return _parse_periods(v, "ema_periods")

    @model_validator(mode="after")
    def _check(self) -> IndicatorParams:
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be smaller than macd_slow")
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("rsi_oversold must be below rsi_overbought")
        if not self.ichimoku_tenkan < self.ichimoku_kijun < self.ichimoku_senkou_b:
            raise ValueError("Ichimoku periods must satisfy tenkan < kijun < senkou_b")
        return self
