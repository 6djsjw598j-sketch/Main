"""Explainable factor model.

Each factor maps an indicator reading to a value in [-1, +1] (bearish .. bullish). The
composite score is the weighted mean of the available factors, scaled to [-100, +100]:

    score = 100 * sum(w_i * v_i) / sum(w_i)       (over factors with enough data)

The score is a summary of technical conditions. It is **not** calibrated and must not be
read as a probability that price will rise. All factors are computed only from data up
to and including each bar (no look-ahead), so the same series is used for backtests.

Trend factors use fixed reference averages (SMA 50/200, EMA 9/21); momentum and volume
factors use the user's indicator parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.indicators.core import ema, has_volume, sma
from app.indicators.momentum import macd, roc, rsi, stochastic
from app.indicators.params import IndicatorParams
from app.indicators.trend import adx, ichimoku
from app.indicators.volatility import atr
from app.indicators.volume import mfi, obv


@dataclass(frozen=True)
class FactorSpec:
    key: str
    name: str
    group: str
    weight: float
    formula: str


FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec("price_vs_sma200", "Price vs SMA 200", "trend", 1.5, "(close - SMA200) / (2 x ATR), clipped to [-1, 1]"),
    FactorSpec("sma50_vs_sma200", "SMA 50 vs SMA 200", "trend", 1.0, "+1 if SMA50 > SMA200, -1 if below"),
    FactorSpec("price_vs_sma50", "Price vs SMA 50", "trend", 1.0, "(close - SMA50) / (2 x ATR), clipped"),
    FactorSpec("ema_9_21", "EMA 9 vs EMA 21", "trend", 1.0, "(EMA9 - EMA21) / (0.5 x ATR), clipped"),
    FactorSpec("adx_dmi", "ADX / DMI direction", "trend", 1.0, "sign(+DI - -DI) x clip((ADX - 15) / 20, 0, 1)"),
    FactorSpec("ichimoku", "Ichimoku cloud position", "trend", 0.75, "+1 above the cloud, -1 below, 0 inside"),
    FactorSpec("rsi", "RSI", "momentum", 1.0, "(RSI - 50) / 20, clipped"),
    FactorSpec("macd", "MACD vs signal", "momentum", 1.0, "+1 above signal & > 0; +0.5 above signal & < 0; mirrored for bearish"),
    FactorSpec("stochastic", "Stochastic %K", "momentum", 0.5, "(%K - 50) / 30, clipped"),
    FactorSpec("roc", "Rate of change", "momentum", 0.5, "ROC% / (ATR% x sqrt(period)), clipped"),
    FactorSpec("obv", "On-Balance Volume trend", "volume", 0.75, "+1 if OBV > its 20-bar SMA, -1 if below"),
    FactorSpec("mfi", "Money Flow Index", "volume", 0.5, "(MFI - 50) / 20, clipped"),
    FactorSpec("updown_volume", "Up vs down volume (20 bars)", "volume", 0.5, "(up-bar volume - down-bar volume) / total"),
)
FACTOR_KEYS = tuple(f.key for f in FACTORS)


def parse_weights(text: str | None) -> dict[str, float]:
    """Parse ``"rsi:2,macd:0"`` into weight overrides (0 disables a factor)."""
    weights = {f.key: f.weight for f in FACTORS}
    if not text:
        return weights
    for part in text.split(","):
        if not part.strip():
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        if key not in weights:
            raise ValueError(f"Unknown factor '{key}'. Valid factors: {', '.join(FACTOR_KEYS)}")
        try:
            w = float(value)
        except ValueError as exc:
            raise ValueError(f"Weight for '{key}' must be a number") from exc
        if not 0 <= w <= 10:
            raise ValueError("Weights must be between 0 and 10")
        weights[key] = w
    if sum(weights.values()) <= 0:
        raise ValueError("At least one factor needs a positive weight")
    return weights


@dataclass
class SignalFrame:
    factors: pd.DataFrame
    raw: pd.DataFrame
    volume_available: bool
    params: IndicatorParams
    weights: dict[str, float] = field(default_factory=dict)

    def score(self) -> tuple[pd.Series, pd.Series]:
        """Composite score per bar and number of factors used."""
        w = pd.Series({k: self.weights.get(k, 0.0) for k in self.factors.columns})
        values = self.factors
        mask = values.notna() & (w > 0)
        num = (values.fillna(0.0) * w).where(mask, 0.0).sum(axis=1)
        den = mask.mul(w).sum(axis=1)
        score = 100.0 * num / den.replace(0.0, np.nan)
        return score, mask.sum(axis=1)


def _clip(series: pd.Series) -> pd.Series:
    return series.clip(lower=-1.0, upper=1.0)


def compute_signal_frame(frame: pd.DataFrame, params: IndicatorParams, weights: dict[str, float] | None = None) -> SignalFrame:
    close = frame["close"]
    atr_s = atr(frame, params.atr_period)
    atr_safe = atr_s.replace(0.0, np.nan)
    sma50, sma200 = sma(close, 50), sma(close, 200)
    ema9, ema21 = ema(close, 9), ema(close, 21)
    dmi = adx(frame, params.adx_period)
    ich = ichimoku(frame, params.ichimoku_tenkan, params.ichimoku_kijun, params.ichimoku_senkou_b, params.ichimoku_kijun)
    cloud_top = ich[["senkou_a", "senkou_b"]].max(axis=1, skipna=False)
    cloud_bottom = ich[["senkou_a", "senkou_b"]].min(axis=1, skipna=False)
    rsi_s = rsi(close, params.rsi_period)
    m = macd(close, params.macd_fast, params.macd_slow, params.macd_signal)
    st = stochastic(frame, params.stoch_k, params.stoch_smooth, params.stoch_d)
    roc_s = roc(close, params.roc_period)
    atr_pct = atr_s / close * 100.0

    f = pd.DataFrame(index=frame.index)
    f["price_vs_sma200"] = _clip((close - sma200) / (2 * atr_safe))
    f["sma50_vs_sma200"] = np.sign(sma50 - sma200).where(sma200.notna())
    f["price_vs_sma50"] = _clip((close - sma50) / (2 * atr_safe))
    f["ema_9_21"] = _clip((ema9 - ema21) / (0.5 * atr_safe))
    strength = ((dmi["adx"] - 15.0) / 20.0).clip(lower=0.0, upper=1.0)
    f["adx_dmi"] = (np.sign(dmi["plus_di"] - dmi["minus_di"]) * strength).where(dmi["adx"].notna())
    ich_val = pd.Series(np.where(close > cloud_top, 1.0, np.where(close < cloud_bottom, -1.0, 0.0)), index=frame.index)
    f["ichimoku"] = ich_val.where(cloud_top.notna())
    f["rsi"] = _clip((rsi_s - 50.0) / 20.0)
    above = m["macd"] > m["signal"]
    macd_val = np.where(above, np.where(m["macd"] > 0, 1.0, 0.5), np.where(m["macd"] < 0, -1.0, -0.5))
    f["macd"] = pd.Series(macd_val, index=frame.index).where(m["signal"].notna())
    f["stochastic"] = _clip((st["k"] - 50.0) / 30.0)
    f["roc"] = _clip(roc_s / (atr_pct * np.sqrt(params.roc_period)).replace(0.0, np.nan))

    raw = pd.DataFrame(
        {
            "close": close,
            "atr": atr_s,
            "atr_pct": atr_pct,
            "sma50": sma50,
            "sma200": sma200,
            "ema9": ema9,
            "ema21": ema21,
            "adx": dmi["adx"],
            "plus_di": dmi["plus_di"],
            "minus_di": dmi["minus_di"],
            "cloud_top": cloud_top,
            "cloud_bottom": cloud_bottom,
            "rsi": rsi_s,
            "macd": m["macd"],
            "macd_signal": m["signal"],
            "macd_hist": m["histogram"],
            "stoch_k": st["k"],
            "stoch_d": st["d"],
            "roc": roc_s,
        },
        index=frame.index,
    )

    volume_ok = has_volume(frame)
    if volume_ok:
        vol = frame["volume"].fillna(0.0)
        obv_s = obv(close, frame["volume"])
        obv_ma = sma(obv_s, 20)
        f["obv"] = np.sign(obv_s - obv_ma).where(obv_ma.notna())
        mfi_s = mfi(frame, params.mfi_period)
        f["mfi"] = _clip((mfi_s - 50.0) / 20.0)
        change = close.diff()
        up_vol = vol.where(change > 0, 0.0).rolling(20, min_periods=20).sum()
        down_vol = vol.where(change < 0, 0.0).rolling(20, min_periods=20).sum()
        total = vol.rolling(20, min_periods=20).sum().replace(0.0, np.nan)
        f["updown_volume"] = _clip((up_vol - down_vol) / total)
        raw["obv"], raw["obv_sma"], raw["mfi"] = obv_s, obv_ma, mfi_s
        raw["volume"], raw["volume_sma"] = frame["volume"], sma(vol, params.volume_sma_period)
        raw["updown"] = (up_vol - down_vol) / total
    else:
        for key in ("obv", "mfi", "updown_volume"):
            f[key] = np.nan
    return SignalFrame(f[list(FACTOR_KEYS)], raw, volume_ok, params, weights or {s.key: s.weight for s in FACTORS})
