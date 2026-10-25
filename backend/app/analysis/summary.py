"""Market-bias summary built from the factor model, trend horizons and S/R context."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.analysis.signals import FACTORS, SignalFrame
from app.analysis.support_resistance import SRResult
from app.analysis.swings import find_swings
from app.indicators.engine import fmt_price
from app.indicators.volatility import bollinger
from app.models.analysis import (
    Condition,
    FactorContribution,
    HorizonTrend,
    KeyLevel,
    MomentumSummary,
    OverallBias,
    VolatilitySummary,
    VolumeSummary,
)

METHODOLOGY = [
    "Each factor converts one indicator reading into a value between -1 (bearish) and +1 (bullish); formulas are listed per factor.",
    "Score = 100 x weighted average of the available factors. Factors without enough history are excluded and listed as unavailable.",
    "Bias is bullish when the score is at or above the bullish threshold, bearish at or below the bearish threshold, otherwise neutral.",
    "The score is an uncalibrated summary of technical conditions, not a probability of a price move or of profit.",
    "Trend horizons: short = close vs EMA21, EMA9 vs EMA21, EMA21 5-bar slope; medium = close vs SMA50, SMA50 10-bar slope, MACD sign; "
    "long = close vs SMA200, SMA50 vs SMA200, SMA200 20-bar slope. Two of three agreeing votes set the direction.",
    "Volatility regime = percentile of ATR% (ATR / close) within the last 252 bars (or all available bars): <33 low, 33-67 moderate, >67 high.",
]


def _val(series: pd.Series, offset: int = 0) -> float | None:
    if len(series) <= offset:
        return None
    v = series.iloc[-1 - offset]
    return None if pd.isna(v) else float(v)


def _required_bars(key: str, sig: SignalFrame) -> int:
    p = sig.params
    return {
        "price_vs_sma200": 200,
        "sma50_vs_sma200": 200,
        "price_vs_sma50": 50,
        "ema_9_21": 21,
        "adx_dmi": 2 * p.adx_period + 1,
        "ichimoku": p.ichimoku_senkou_b + p.ichimoku_kijun - 1,
        "rsi": p.rsi_period + 1,
        "macd": p.macd_slow + p.macd_signal - 1,
        "stochastic": p.stoch_k + p.stoch_smooth + p.stoch_d - 2,
        "roc": max(p.roc_period + 1, p.atr_period),
        "obv": 20,
        "mfi": p.mfi_period + 1,
        "updown_volume": 21,
    }.get(key, 0)


def _factor_detail(key: str, sig: SignalFrame) -> str:
    r = sig.raw
    close = _val(r["close"]) or 0.0
    if key == "price_vs_sma200":
        s = _val(r["sma200"])
        return f"Close {fmt_price(close)} is {(close - s) / s * 100:+.2f}% vs SMA 200 ({fmt_price(s)})" if s else ""
    if key == "sma50_vs_sma200":
        a, b = _val(r["sma50"]), _val(r["sma200"])
        return f"SMA 50 ({fmt_price(a)}) is {'above' if a > b else 'below'} SMA 200 ({fmt_price(b)})" if a and b else ""
    if key == "price_vs_sma50":
        s = _val(r["sma50"])
        return f"Close is {(close - s) / s * 100:+.2f}% vs SMA 50 ({fmt_price(s)})" if s else ""
    if key == "ema_9_21":
        a, b = _val(r["ema9"]), _val(r["ema21"])
        return f"EMA 9 ({fmt_price(a)}) is {'above' if a > b else 'below'} EMA 21 ({fmt_price(b)})" if a and b else ""
    if key == "adx_dmi":
        a, pdi, mdi = _val(r["adx"]), _val(r["plus_di"]), _val(r["minus_di"])
        return f"ADX {a:.1f}, +DI {pdi:.1f}, -DI {mdi:.1f}" if None not in (a, pdi, mdi) else ""
    if key == "ichimoku":
        top, bottom = _val(r["cloud_top"]), _val(r["cloud_bottom"])
        if top is None or bottom is None:
            return ""
        where = "above" if close > top else "below" if close < bottom else "inside"
        return f"Close is {where} the cloud ({fmt_price(bottom)}–{fmt_price(top)})"
    if key == "rsi":
        v = _val(r["rsi"])
        return f"RSI {v:.1f}" if v is not None else ""
    if key == "macd":
        m, s = _val(r["macd"]), _val(r["macd_signal"])
        return f"MACD {m:.4g} {'above' if m > s else 'below'} signal {s:.4g}" if m is not None and s is not None else ""
    if key == "stochastic":
        v = _val(r["stoch_k"])
        return f"%K {v:.1f}" if v is not None else ""
    if key == "roc":
        v = _val(r["roc"])
        return f"ROC {v:+.2f}% over {sig.params.roc_period} bars" if v is not None else ""
    if key == "obv" and "obv" in r:
        a, b = _val(r["obv"]), _val(r["obv_sma"])
        return f"OBV is {'above' if a > b else 'below'} its 20-bar average" if a is not None and b is not None else ""
    if key == "mfi" and "mfi" in r:
        v = _val(r["mfi"])
        return f"MFI {v:.1f}" if v is not None else ""
    if key == "updown_volume" and "updown" in r:
        v = _val(r["updown"])
        return f"Up bars carried {(1 + v) / 2 * 100:.0f}% of volume over 20 bars" if v is not None else ""
    return ""


def overall_and_factors(
    sig: SignalFrame, bullish_threshold: float, bearish_threshold: float
) -> tuple[OverallBias, list[FactorContribution], list[str]]:
    latest = sig.factors.iloc[-1]
    weights = sig.weights
    available_weight = sum(weights[k] for k in latest.index if not pd.isna(latest[k]) and weights[k] > 0)
    contributions: list[FactorContribution] = []
    for spec in FACTORS:
        value = latest.get(spec.key)
        w = weights.get(spec.key, 0.0)
        if value is None or pd.isna(value):
            reason = (
                "No volume data from the provider"
                if spec.group == "volume" and not sig.volume_available
                else f"Needs at least {_required_bars(spec.key, sig)} bars"
            )
            contributions.append(
                FactorContribution(
                    key=spec.key,
                    name=spec.name,
                    group=spec.group,
                    weight=w,
                    value=None,
                    contribution=None,  # type: ignore[arg-type]
                    signal="insufficient_data",
                    detail=f"{reason}. Formula: {spec.formula}.",
                    available=False,
                )
            )
            continue
        v = float(value)
        contribution = 100.0 * w * v / available_weight if available_weight else 0.0
        signal = "bullish" if v >= 0.2 else "bearish" if v <= -0.2 else "neutral"
        detail = _factor_detail(spec.key, sig)
        contributions.append(
            FactorContribution(
                key=spec.key,
                name=spec.name,
                group=spec.group,
                weight=w,
                value=round(v, 3),  # type: ignore[arg-type]
                contribution=round(contribution, 2),
                signal=signal,
                available=w > 0,  # type: ignore[arg-type]
                detail=(detail + ". " if detail else "") + f"Formula: {spec.formula}." + ("" if w > 0 else " Disabled (weight 0)."),
            )
        )
    used = [c for c in contributions if c.available and c.value is not None]
    score = float(sum(c.contribution or 0.0 for c in used)) if used else 0.0
    if not used:
        bias = "neutral"
    elif score >= bullish_threshold:
        bias = "bullish"
    elif score <= bearish_threshold:
        bias = "bearish"
    else:
        bias = "neutral"
    bulls = [c.name for c in used if c.signal == "bullish"]
    bears = [c.name for c in used if c.signal == "bearish"]
    conflicts: list[str] = []
    if bias == "bullish":
        conflicts = [f"{name} is bearish against a bullish overall bias" for name in bears]
    elif bias == "bearish":
        conflicts = [f"{name} is bullish against a bearish overall bias" for name in bulls]
    elif bulls and bears:
        conflicts = [f"Mixed readings: {len(bulls)} bullish factor(s) ({', '.join(bulls[:4])}) vs {len(bears)} bearish ({', '.join(bears[:4])})"]
    overall = OverallBias(
        bias=bias,  # type: ignore[arg-type]
        score=round(score, 1),
        bullish_threshold=bullish_threshold,
        bearish_threshold=bearish_threshold,
        factors_used=len(used),
        factors_total=len(FACTORS),
        explanation=(
            f"Weighted factor score {score:+.0f} on a -100..+100 scale from {len(used)} of {len(FACTORS)} factors "
            f"({len(bulls)} bullish, {len(bears)} bearish). Bullish at ≥ {bullish_threshold:g}, bearish at ≤ {bearish_threshold:g}. "
            "This is a description of current technical conditions, not a forecast or probability."
        ),
    )
    return overall, contributions, conflicts


def _vote(values: list[float | None]) -> tuple[str, int]:
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in values):
        return "insufficient_data", 0
    total = sum(int(np.sign(v)) for v in values)  # type: ignore[arg-type]
    if total >= 2:
        return "bullish", total
    if total <= -2:
        return "bearish", total
    return "neutral", total


def trend_horizons(sig: SignalFrame) -> list[HorizonTrend]:
    r = sig.raw
    close = _val(r["close"]) or 0.0

    def diff(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else a - b

    ema21, ema21_5 = _val(r["ema21"]), _val(r["ema21"], 5)
    sma50, sma50_10 = _val(r["sma50"]), _val(r["sma50"], 10)
    sma200, sma200_20 = _val(r["sma200"]), _val(r["sma200"], 20)
    out = []
    specs = [
        (
            "short",
            [diff(close, ema21), diff(_val(r["ema9"]), ema21), diff(ema21, ema21_5)],
            ["close vs EMA 21", "EMA 9 vs EMA 21", "EMA 21 slope (5 bars)"],
            26,
        ),
        ("medium", [diff(close, sma50), diff(sma50, sma50_10), _val(r["macd"])], ["close vs SMA 50", "SMA 50 slope (10 bars)", "MACD sign"], 60),
        (
            "long",
            [diff(close, sma200), diff(sma50, sma200), diff(sma200, sma200_20)],
            ["close vs SMA 200", "SMA 50 vs SMA 200", "SMA 200 slope (20 bars)"],
            220,
        ),
    ]
    for horizon, values, labels, needed in specs:
        direction, _ = _vote(values)
        if direction == "insufficient_data":
            text = f"Needs about {needed} bars of history ({len(r)} available)."
        else:
            parts = [f"{label}: {'+' if v > 0 else '-' if v < 0 else '0'}" for label, v in zip(labels, values, strict=True)]  # type: ignore[operator]
            text = "; ".join(parts) + f" → {direction}."
        out.append(HorizonTrend(horizon=horizon, direction=direction, explanation=text))  # type: ignore[arg-type]
    return out


def momentum_summary(sig: SignalFrame) -> MomentumSummary:
    r = sig.raw
    rsi_v, hist, k, d, roc_v = _val(r["rsi"]), _val(r["macd_hist"]), _val(r["stoch_k"]), _val(r["stoch_d"]), _val(r["roc"])
    if None in (rsi_v, hist, k, d, roc_v):
        return MomentumSummary(state="insufficient_data", explanation="Not enough history for RSI, MACD, Stochastic and ROC.")
    votes = {
        f"RSI {rsi_v:.1f} {'>' if rsi_v > 50 else '<'} 50": rsi_v - 50,  # type: ignore[operator]
        f"MACD histogram {'positive' if hist > 0 else 'negative'}": hist,  # type: ignore[operator]
        f"Stochastic %K {'above' if k > d else 'below'} %D": k - d,  # type: ignore[operator]
        f"ROC {roc_v:+.2f}%": roc_v,
    }
    pos = sum(1 for v in votes.values() if v > 0)  # type: ignore[operator]
    neg = sum(1 for v in votes.values() if v < 0)  # type: ignore[operator]
    state = "positive" if pos >= 3 else "negative" if neg >= 3 else "mixed"
    return MomentumSummary(state=state, explanation="; ".join(votes) + f" ({pos} positive, {neg} negative).")  # type: ignore[arg-type]


def volatility_summary(sig: SignalFrame, frame: pd.DataFrame) -> VolatilitySummary:
    atr_pct = sig.raw["atr_pct"].dropna()
    atr_v = _val(sig.raw["atr"])
    baseline = atr_pct.tail(252)
    if len(baseline) < 50 or atr_v is None:
        return VolatilitySummary(
            state="insufficient_data",
            atr=atr_v,
            atr_pct=_val(sig.raw["atr_pct"]),
            percentile=None,
            baseline_bars=len(baseline),
            squeeze=False,
            explanation="At least 50 bars of ATR history are needed for a volatility baseline.",
        )
    current = float(baseline.iloc[-1])
    pct = float((baseline <= current).mean() * 100.0)
    state = "low" if pct < 33 else "high" if pct > 67 else "moderate"
    bw = bollinger(frame["close"], sig.params.bb_period, sig.params.bb_std)["bandwidth"].dropna().tail(126)
    squeeze = bool(len(bw) >= 40 and bw.iloc[-1] <= bw.quantile(0.10))
    return VolatilitySummary(
        state=state,  # type: ignore[arg-type]
        atr=atr_v,
        atr_pct=round(current, 4),
        percentile=round(pct, 1),
        baseline_bars=len(baseline),
        squeeze=squeeze,
        explanation=(
            f"ATR is {current:.2f}% of price, the {pct:.0f}th percentile of the last {len(baseline)} bars ({state})."
            + (" Bollinger Band width is in its lowest 10% (a squeeze), which often precedes larger moves in either direction." if squeeze else "")
        ),
    )


def volume_summary(sig: SignalFrame, last_bar_complete: bool) -> VolumeSummary:
    if not sig.volume_available:
        return VolumeSummary(state="unavailable", relative_volume=None, explanation="The provider does not supply volume for this instrument.")
    r = sig.raw
    vol, avg = _val(r["volume"]), _val(r["volume_sma"])
    rel = vol / avg if vol is not None and avg else None
    price_change = None if len(r) <= 20 else _val(r["close"]) - _val(r["close"], 20)  # type: ignore[operator]
    obv_change = None if len(r) <= 20 else (_val(r["obv"]) or 0.0) - (_val(r["obv"], 20) or 0.0)
    rel_text = "" if rel is None else f" Latest bar volume is {rel:.2f}× its average" + (" (bar still forming)." if not last_bar_complete else ".")
    if price_change is None or obv_change is None or price_change == 0:
        return VolumeSummary(state="neutral", relative_volume=rel, explanation="Not enough history to compare price and volume trends." + rel_text)
    if np.sign(price_change) == np.sign(obv_change) and obv_change != 0:
        state, text = (
            "confirming",
            f"OBV moved in the same direction as price over 20 bars ({'up' if price_change > 0 else 'down'}): volume supports the move.",
        )
    elif obv_change == 0:
        state, text = "neutral", "OBV was flat over 20 bars."
    else:
        state, text = (
            "diverging",
            f"Price moved {'up' if price_change > 0 else 'down'} over 20 bars but OBV moved the other way: the move lacks volume confirmation.",
        )
    return VolumeSummary(state=state, relative_volume=None if rel is None else round(rel, 3), explanation=text + rel_text)  # type: ignore[arg-type]


def _divergence(frame: pd.DataFrame, rsi_s: pd.Series) -> tuple[str | None, str]:
    window = frame.tail(90)
    swings = find_swings(window, 3)
    offset = len(frame) - len(window)
    highs = [s for s in swings if s.kind == "high"][-2:]
    lows = [s for s in swings if s.kind == "low"][-2:]
    if len(highs) == 2:
        r1, r2 = rsi_s.iloc[offset + highs[0].index], rsi_s.iloc[offset + highs[1].index]
        if highs[1].price > highs[0].price and not pd.isna(r1) and not pd.isna(r2) and r2 < r1 - 2 and len(window) - 1 - highs[1].index <= 15:
            return "bearish", (
                f"Bearish RSI divergence: price made a higher high ({fmt_price(highs[1].price)} vs {fmt_price(highs[0].price)}) "
                f"while RSI made a lower high ({r2:.1f} vs {r1:.1f})."
            )
    if len(lows) == 2:
        r1, r2 = rsi_s.iloc[offset + lows[0].index], rsi_s.iloc[offset + lows[1].index]
        if lows[1].price < lows[0].price and not pd.isna(r1) and not pd.isna(r2) and r2 > r1 + 2 and len(window) - 1 - lows[1].index <= 15:
            return "bullish", (
                f"Bullish RSI divergence: price made a lower low ({fmt_price(lows[1].price)} vs {fmt_price(lows[0].price)}) "
                f"while RSI made a higher low ({r2:.1f} vs {r1:.1f})."
            )
    return None, ""


def conditions_and_levels(
    frame: pd.DataFrame, sig: SignalFrame, sr: SRResult, bias: str, horizons: list[HorizonTrend]
) -> tuple[list[Condition], list[KeyLevel]]:
    atr_v = _val(sig.raw["atr"])
    res, sup = sr.nearest_resistance, sr.nearest_support
    conditions: list[Condition] = []
    recent_break_up = next((lv for lv in sr.levels if lv.status in ("breakout", "reclaimed")), None)
    recent_break_down = next((lv for lv in sr.levels if lv.status in ("breakdown", "lost")), None)

    if recent_break_up is not None:
        conditions.append(
            Condition(
                type="breakout",
                active=True,
                level=recent_break_up.zone_high,
                description=f"{recent_break_up.status_detail} Zone {fmt_price(recent_break_up.zone_low)}–{fmt_price(recent_break_up.zone_high)}.",
            )
        )
    elif res is not None:
        dist = res.distance / atr_v if atr_v else None
        near = f" ({dist:.1f} ATR away)" if dist is not None else ""
        conditions.append(
            Condition(
                type="breakout",
                active=False,
                level=res.zone_high,
                description=(
                    f"Nearest resistance {fmt_price(res.zone_low)}–{fmt_price(res.zone_high)}{near}. A close above {fmt_price(res.zone_high)}, "
                    "ideally on volume above its 20-bar average, would be a breakout condition."
                ),
            )
        )
    if recent_break_down is not None:
        conditions.append(
            Condition(
                type="breakdown",
                active=True,
                level=recent_break_down.zone_low,
                description=f"{recent_break_down.status_detail} Zone {fmt_price(recent_break_down.zone_low)}–{fmt_price(recent_break_down.zone_high)}.",
            )
        )
    elif sup is not None:
        dist = sup.distance / atr_v if atr_v else None
        near = f" ({dist:.1f} ATR away)" if dist is not None else ""
        conditions.append(
            Condition(
                type="breakdown",
                active=False,
                level=sup.zone_low,
                description=(
                    f"Nearest support {fmt_price(sup.zone_low)}–{fmt_price(sup.zone_high)}{near}. A close below {fmt_price(sup.zone_low)} "
                    "would be a breakdown condition."
                ),
            )
        )

    rsi_s = sig.raw["rsi"]
    kind, text = _divergence(frame, rsi_s)
    if kind:
        conditions.append(Condition(type=f"{kind}_reversal", active=True, level=None, description=text))  # type: ignore[arg-type]
    rsi_v = _val(rsi_s)
    p = sig.params
    trend_up = any(h.direction == "bullish" for h in horizons[:2])
    trend_down = any(h.direction == "bearish" for h in horizons[:2])
    if rsi_v is not None and rsi_v >= p.rsi_overbought:
        conditions.append(
            Condition(
                type="bearish_reversal",
                active=True,
                level=None,
                description=(
                    f"RSI {rsi_v:.1f} is overbought"
                    + (" within an uptrend: extended, but strong trends can stay overbought." if trend_up else ".")
                    + " A drop back below the overbought line would be an early sign of fading momentum."
                ),
            )
        )
    if rsi_v is not None and rsi_v <= p.rsi_oversold:
        conditions.append(
            Condition(
                type="bullish_reversal",
                active=True,
                level=None,
                description=(
                    f"RSI {rsi_v:.1f} is oversold"
                    + (" within a downtrend: stretched, but weak trends can stay oversold." if trend_down else ".")
                    + " A rise back above the oversold line would be an early sign of improving momentum."
                ),
            )
        )
    hist, hist_prev = _val(sig.raw["macd_hist"]), _val(sig.raw["macd_hist"], 1)
    if hist is not None and hist_prev is not None:
        if hist > 0 >= hist_prev and trend_down:
            conditions.append(
                Condition(
                    type="bullish_reversal",
                    active=True,
                    level=None,
                    description="MACD crossed above its signal line against the prevailing downtrend (possible early reversal; often fails without follow-through).",
                )
            )
        if hist < 0 <= hist_prev and trend_up:
            conditions.append(
                Condition(
                    type="bearish_reversal",
                    active=True,
                    level=None,
                    description="MACD crossed below its signal line against the prevailing uptrend (possible early reversal; often fails without follow-through).",
                )
            )

    key_levels: list[KeyLevel] = []
    if bias == "bullish":
        if res is not None:
            key_levels.append(
                KeyLevel(
                    role="confirmation",
                    price=res.zone_high,
                    description=f"A close above {fmt_price(res.zone_high)} (top of nearest resistance) would confirm continuation.",
                )
            )
        if sup is not None:
            key_levels.append(
                KeyLevel(
                    role="invalidation",
                    price=sup.zone_low,
                    description=f"A close below {fmt_price(sup.zone_low)} (bottom of nearest support) would invalidate the bullish read.",
                )
            )
    elif bias == "bearish":
        if sup is not None:
            key_levels.append(
                KeyLevel(
                    role="confirmation",
                    price=sup.zone_low,
                    description=f"A close below {fmt_price(sup.zone_low)} (bottom of nearest support) would confirm continuation.",
                )
            )
        if res is not None:
            key_levels.append(
                KeyLevel(
                    role="invalidation",
                    price=res.zone_high,
                    description=f"A close above {fmt_price(res.zone_high)} (top of nearest resistance) would invalidate the bearish read.",
                )
            )
    else:
        if res is not None:
            key_levels.append(
                KeyLevel(
                    role="bullish_trigger",
                    price=res.zone_high,
                    description=f"Neutral bias: a close above {fmt_price(res.zone_high)} would tilt conditions bullish.",
                )
            )
        if sup is not None:
            key_levels.append(
                KeyLevel(
                    role="bearish_trigger",
                    price=sup.zone_low,
                    description=f"Neutral bias: a close below {fmt_price(sup.zone_low)} would tilt conditions bearish.",
                )
            )
    return conditions, key_levels
