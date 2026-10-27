"""Reads an underlying the way a discretionary options trader would before picking a
strategy: trend and its strength, whether the move is stretched, realised volatility,
and whether implied volatility is rich or cheap.

IV rank needs a history of implied volatility. The bot records one ATM IV reading per
symbol per day; until ``iv_rank_min_history`` readings exist it falls back to ranking
today's IV against the past year's 20-day realised volatility (``hv_proxy``), and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

import pandas as pd

from optionsbot.indicators import adx, atr, beta, historical_volatility, last, rsi, sma
from optionsbot.models import OptionChain, OptionType
from optionsbot.pricing import implied_vol
from optionsbot.timeutil import dte, years_to_expiry


class Bias(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class Regime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"
    CRISIS = "crisis"


@dataclass
class UnderlyingView:
    symbol: str
    as_of: date
    price: float
    bars: int
    prev_close: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    rsi14: float | None
    adx14: float | None
    atr14: float | None
    hv20: float | None
    hv60: float | None
    hv252: float | None
    trend_score: int  # -4 .. +4
    bias: Bias
    strong_trend: bool
    vol_forecast: float | None
    beta: float = 1.0
    atm_iv: float | None = None
    iv_rank: float | None = None
    iv_percentile: float | None = None
    iv_rank_source: str | None = None
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.symbol} {self.price:.2f}", f"{self.bias.value}{' (strong)' if self.strong_trend else ''} trend {self.trend_score:+d}"]
        if self.rsi14 is not None:
            parts.append(f"RSI {self.rsi14:.0f}")
        if self.adx14 is not None:
            parts.append(f"ADX {self.adx14:.0f}")
        if self.atm_iv is not None:
            parts.append(f"IV {self.atm_iv:.1%}")
        if self.iv_rank is not None:
            parts.append(f"IVR {self.iv_rank:.0f} ({self.iv_rank_source})")
        if self.vol_forecast is not None:
            parts.append(f"vol fcst {self.vol_forecast:.1%}")
        return ", ".join(parts)


def completed_bars(bars: pd.DataFrame, today: date) -> pd.DataFrame:
    """Drop today's still-forming daily bar."""
    return bars[bars.index < today]


@dataclass(frozen=True)
class BarStats:
    """Everything derived from completed daily bars; it changes once a day."""

    bars: int
    prev_close: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    slope50: float | None
    rsi14: float | None
    adx14: float | None
    atr14: float | None
    hv20: float | None
    hv60: float | None
    hv252: float | None
    hv20_year: tuple[float, ...]
    beta: float


def bar_stats(bars: pd.DataFrame, today: date, market_bars: pd.DataFrame | None = None) -> BarStats:
    df = completed_bars(bars, today)
    close = df["close"]
    sma50 = sma(close, 50)
    hv20 = historical_volatility(close, 20)
    b = 1.0
    if market_bars is not None:
        measured = beta(close, completed_bars(market_bars, today)["close"])
        if measured is not None:
            b = min(3.0, max(0.2, measured))
    return BarStats(
        bars=len(df),
        prev_close=float(close.iloc[-1]) if len(close) else None,
        sma20=last(sma(close, 20)),
        sma50=last(sma50),
        sma200=last(sma(close, 200)),
        slope50=float(sma50.iloc[-1] - sma50.iloc[-11]) if len(sma50.dropna()) > 10 else None,
        rsi14=last(rsi(close, 14)),
        adx14=last(adx(df, 14)) if len(df) > 30 else None,
        atr14=last(atr(df, 14)),
        hv20=last(hv20),
        hv60=last(historical_volatility(close, 60)),
        hv252=last(historical_volatility(close, 252)),
        hv20_year=tuple(hv20.dropna().tail(252)),
        beta=b,
    )


def analyze_underlying(symbol: str, stats: BarStats, spot: float, today: date) -> UnderlyingView:
    notes: list[str] = []
    s20, s50, s200, rsi14, adx14 = stats.sma20, stats.sma50, stats.sma200, stats.rsi14, stats.adx14

    score = 0
    if s50 is not None:
        score += 1 if spot > s50 else -1
    if s20 is not None and s50 is not None:
        score += 1 if s20 > s50 else -1
    if s50 is not None and s200 is not None:
        score += 1 if s50 > s200 else -1
    elif s200 is None:
        notes.append("fewer than 200 bars: long-term trend unknown")
    if stats.slope50 is not None:
        score += 1 if stats.slope50 > 0 else -1

    if score >= 2:
        bias = Bias.BULLISH
    elif score <= -2:
        bias = Bias.BEARISH
    else:
        bias = Bias.NEUTRAL
    strong = abs(score) >= 3 and adx14 is not None and adx14 >= 25

    # Don't chase: a stretched move is treated as neutral until it cools off
    if rsi14 is not None:
        if bias is Bias.BULLISH and rsi14 >= 75:
            notes.append(f"RSI {rsi14:.0f}: overbought, waiting for a pullback before adding bullish risk")
            bias, strong = Bias.NEUTRAL, False
        elif bias is Bias.BEARISH and rsi14 <= 25:
            notes.append(f"RSI {rsi14:.0f}: oversold, waiting for a bounce before adding bearish risk")
            bias, strong = Bias.NEUTRAL, False
        elif bias is Bias.BULLISH and rsi14 <= 45:
            notes.append(f"RSI {rsi14:.0f}: pullback within an uptrend")
        elif bias is Bias.BEARISH and rsi14 >= 55:
            notes.append(f"RSI {rsi14:.0f}: bounce within a downtrend")

    # Volatility forecast: a blend of short, medium and long realised vol, never below the
    # most recent 20-day reading (volatility clusters, so a spike is respected).
    vol_forecast = None
    hv20, hv60, hv252 = stats.hv20, stats.hv60, stats.hv252
    if hv20 is not None:
        blend = 0.5 * hv20 + 0.3 * (hv60 if hv60 is not None else hv20) + 0.2 * (hv252 if hv252 is not None else hv20)
        vol_forecast = max(hv20, blend)

    return UnderlyingView(
        symbol=symbol,
        as_of=today,
        price=spot,
        bars=stats.bars,
        prev_close=stats.prev_close,
        sma20=s20,
        sma50=s50,
        sma200=s200,
        rsi14=rsi14,
        adx14=adx14,
        atr14=stats.atr14,
        hv20=hv20,
        hv60=hv60,
        hv252=hv252,
        trend_score=score,
        bias=bias,
        strong_trend=strong,
        vol_forecast=vol_forecast,
        beta=stats.beta,
        notes=notes,
    )


def atm_implied_vol(chain: OptionChain, now: datetime, today: date, target_dte: int = 30, r: float = 0.04) -> float | None:
    """Average call/put IV at the strike nearest the money, for the expiry nearest ``target_dte``."""
    expirations = [e for e in chain.expirations() if dte(e, today) >= 7]
    if not expirations:
        return None
    expiration = min(expirations, key=lambda e: abs(dte(e, today) - target_dte))
    ivs: list[float] = []
    for option_type in (OptionType.CALL, OptionType.PUT):
        side = [q for q in chain.side(expiration, option_type) if q.is_valid and q.bid > 0]
        if not side:
            continue
        q = min(side, key=lambda q: abs(q.contract.strike - chain.spot))
        iv = q.iv
        if not iv or iv <= 0:
            iv = implied_vol(q.mid, chain.spot, q.contract.strike, years_to_expiry(expiration, now), r, option_type)
        if iv:
            ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


def iv_rank_from_history(current: float, history: list[float]) -> tuple[float, float]:
    lo, hi = min(history), max(history)
    rank = 50.0 if hi - lo < 1e-9 else (current - lo) / (hi - lo) * 100
    percentile = sum(1 for h in history if h < current) / len(history) * 100
    return max(0.0, min(100.0, rank)), percentile


def attach_iv(view: UnderlyingView, atm_iv: float | None, iv_history: list[float], min_history: int, stats: BarStats) -> None:
    view.atm_iv = atm_iv
    if atm_iv is None:
        view.notes.append("no implied-volatility reading")
        return
    if len(iv_history) >= min_history:
        view.iv_rank, view.iv_percentile = iv_rank_from_history(atm_iv, iv_history[-252:])
        view.iv_rank_source = "iv_history"
        return
    if len(stats.hv20_year) >= 60:
        view.iv_rank, view.iv_percentile = iv_rank_from_history(atm_iv, list(stats.hv20_year))
        view.iv_rank_source = "hv_proxy"
        view.notes.append(f"IV rank approximated from realised volatility ({len(iv_history)}/{min_history} days of IV history)")


@dataclass
class MarketRegime:
    regime: Regime
    size_multiplier: float
    reasons: list[str]

    @property
    def allows_entries(self) -> bool:
        return self.regime is not Regime.CRISIS


def market_regime(view: UnderlyingView | None, crisis_iv: float, crisis_move: float, risk_off_multiplier: float) -> MarketRegime:
    if view is None:
        return MarketRegime(Regime.NEUTRAL, risk_off_multiplier, ["market data unavailable: trading at reduced size"])
    if view.atm_iv is not None and view.atm_iv >= crisis_iv:
        return MarketRegime(Regime.CRISIS, 0.0, [f"{view.symbol} implied vol {view.atm_iv:.0%} >= {crisis_iv:.0%}: no new risk"])
    if view.prev_close:
        move = view.price / view.prev_close - 1
        if abs(move) >= crisis_move:
            return MarketRegime(Regime.CRISIS, 0.0, [f"{view.symbol} moved {move:+.1%} today: no new risk"])
    below_200 = view.sma200 is not None and view.price < view.sma200
    if below_200 and view.trend_score <= -2:
        return MarketRegime(Regime.RISK_OFF, risk_off_multiplier, [f"{view.symbol} below its 200-day average in a downtrend"])
    if view.trend_score >= 2 and not below_200:
        return MarketRegime(Regime.RISK_ON, 1.0, [f"{view.symbol} in an uptrend"])
    return MarketRegime(Regime.NEUTRAL, 1.0, [f"{view.symbol} trend mixed"])
