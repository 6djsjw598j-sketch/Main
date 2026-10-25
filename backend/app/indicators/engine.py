"""Computes every indicator for a candle frame and produces plain-language readings."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.indicators.core import ema, has_volume, sma
from app.indicators.momentum import macd, roc, rsi, stochastic, williams_r
from app.indicators.params import IndicatorParams
from app.indicators.trend import adx, ichimoku, vwap
from app.indicators.volatility import atr, bollinger, historical_volatility, keltner
from app.indicators.volume import accumulation_distribution, mfi, obv
from app.models.analysis import IndicatorReading
from app.models.market import AssetType, Timeframe


def fmt_price(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:.6g}"


def periods_per_year(timeframe: Timeframe, asset_type: AssetType) -> float:
    """Bars per year used to annualize volatility (documented in METHODOLOGY.md)."""
    if timeframe is Timeframe.MN1:
        return 12.0
    if timeframe is Timeframe.W1:
        return 52.0
    seconds = float(timeframe.seconds or 86400)
    if asset_type is AssetType.CRYPTO:
        return 365.0 * 86400.0 / seconds
    if asset_type is AssetType.FOREX:
        return 260.0 * 86400.0 / seconds
    if timeframe is Timeframe.D1:
        return 252.0
    # Regular US session = 6.5 hours.
    return 252.0 * max(1.0, 6.5 * 3600.0 / seconds)


def session_keys(index: pd.DatetimeIndex, timezone: str, asset_type: AssetType) -> pd.Series:
    tz = "UTC" if asset_type is AssetType.CRYPTO else (timezone or "UTC")
    try:
        local = index.tz_convert(tz)
    except Exception:  # unknown zone name
        local = index.tz_convert("UTC")
    return pd.Series(local.strftime("%Y-%m-%d"), index=index)


def future_bar_times(last_time: int, timeframe: Timeframe, asset_type: AssetType, count: int) -> list[int]:
    """Approximate timestamps of upcoming bars (used only to draw the projected Ichimoku cloud)."""
    out: list[int] = []
    skip_weekends = asset_type not in (AssetType.CRYPTO,)
    current = datetime.fromtimestamp(last_time, UTC)
    while len(out) < count:
        if timeframe is Timeframe.MN1:
            d = current.date()
            current = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=UTC)
        else:
            current = current + timedelta(seconds=timeframe.seconds or 86400)
        if skip_weekends and timeframe not in (Timeframe.W1, Timeframe.MN1) and current.weekday() >= 5:
            continue
        out.append(int(current.timestamp()))
    return out


@dataclass
class IndicatorSet:
    frame: pd.DataFrame
    params: IndicatorParams
    timeframe: Timeframe
    asset_type: AssetType
    columns: dict[str, pd.Series]
    volume_available: bool
    future_times: list[int] = field(default_factory=list)
    future: dict[str, list[float | None]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    last_bar_complete: bool = True

    def last(self, key: str) -> float | None:
        series = self.columns.get(key)
        if series is None or series.empty:
            return None
        value = series.iloc[-1]
        return None if pd.isna(value) else float(value)

    def prev(self, key: str, bars_back: int = 1) -> float | None:
        series = self.columns.get(key)
        if series is None or len(series) <= bars_back:
            return None
        value = series.iloc[-1 - bars_back]
        return None if pd.isna(value) else float(value)


def compute_indicator_set(
    frame: pd.DataFrame,
    params: IndicatorParams,
    timeframe: Timeframe,
    asset_type: AssetType,
    timezone: str = "UTC",
    last_bar_complete: bool = True,
) -> IndicatorSet:
    close = frame["close"]
    cols: dict[str, pd.Series] = {}
    notes: list[str] = []
    volume_ok = has_volume(frame)

    for p in params.sma_periods:
        cols[f"sma_{p}"] = sma(close, p)
    for p in params.ema_periods:
        cols[f"ema_{p}"] = ema(close, p)

    if timeframe.is_intraday and volume_ok:
        cols["vwap"] = vwap(frame, session_keys(frame.index, timezone, asset_type))
    elif timeframe.is_intraday:
        notes.append("VWAP needs volume data, which this instrument does not provide.")
    else:
        notes.append("VWAP is session-anchored and shown on intraday timeframes only.")

    dmi = adx(frame, params.adx_period)
    cols["adx"], cols["plus_di"], cols["minus_di"] = dmi["adx"], dmi["plus_di"], dmi["minus_di"]

    ich = ichimoku(frame, params.ichimoku_tenkan, params.ichimoku_kijun, params.ichimoku_senkou_b, params.ichimoku_kijun)
    for key in ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou"):
        cols[f"ichimoku_{key}"] = ich[key]

    cols["rsi"] = rsi(close, params.rsi_period)
    m = macd(close, params.macd_fast, params.macd_slow, params.macd_signal)
    cols["macd"], cols["macd_signal"], cols["macd_histogram"] = m["macd"], m["signal"], m["histogram"]
    st = stochastic(frame, params.stoch_k, params.stoch_smooth, params.stoch_d)
    cols["stoch_k"], cols["stoch_d"] = st["k"], st["d"]
    cols["roc"] = roc(close, params.roc_period)
    cols["willr"] = williams_r(frame, params.willr_period)

    bb = bollinger(close, params.bb_period, params.bb_std)
    for key in ("middle", "upper", "lower", "percent_b", "bandwidth"):
        cols[f"bb_{key}"] = bb[key]
    cols["atr"] = atr(frame, params.atr_period)
    kc = keltner(frame, params.kc_period, params.kc_atr_period, params.kc_mult)
    cols["kc_middle"], cols["kc_upper"], cols["kc_lower"] = kc["middle"], kc["upper"], kc["lower"]
    cols["hv"] = historical_volatility(close, params.hv_period, periods_per_year(timeframe, asset_type))

    if volume_ok:
        cols["volume"] = frame["volume"]
        cols["volume_sma"] = sma(frame["volume"].fillna(0.0), params.volume_sma_period)
        cols["obv"] = obv(close, frame["volume"])
        cols["mfi"] = mfi(frame, params.mfi_period)
        cols["ad"] = accumulation_distribution(frame)
    else:
        notes.append("Volume is not provided for this instrument; volume indicators are unavailable.")

    result = IndicatorSet(frame, params, timeframe, asset_type, cols, volume_ok, notes=notes, last_bar_complete=last_bar_complete)

    shift = params.ichimoku_kijun - 1
    if len(frame) > 0 and shift > 0:
        result.future_times = future_bar_times(int(frame.index[-1].timestamp()), timeframe, asset_type, shift)
        for key in ("senkou_a_raw", "senkou_b_raw"):
            tail = ich[key].iloc[-shift:] if len(ich) >= shift else ich[key]
            values = [None if pd.isna(v) else float(v) for v in tail]
            values = [None] * (shift - len(values)) + values
            result.future[f"ichimoku_{key.removesuffix('_raw')}"] = values
    return result


# ---------------------------------------------------------------------------- readings
def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0 if b else 0.0


def build_readings(ind: IndicatorSet) -> list[IndicatorReading]:
    p = ind.params
    price = float(ind.frame["close"].iloc[-1])
    readings: list[IndicatorReading] = []

    def insufficient(key: str, name: str, category: str, placement: str, series_keys: list[str], params: dict, needed: int) -> IndicatorReading:
        return IndicatorReading(
            key=key,
            name=name,
            category=category,
            values={k: None for k in series_keys},
            display="n/a",
            state="insufficient_data",
            params=params,
            placement=placement,
            series_keys=series_keys,  # type: ignore[arg-type]
            interpretation=f"Needs at least {needed} bars of history; only {len(ind.frame)} are available.",
        )

    # --- moving averages
    for kind, periods in (("sma", p.sma_periods), ("ema", p.ema_periods)):
        for period in periods:
            key = f"{kind}_{period}"
            name = f"{kind.upper()} {period}"
            value = ind.last(key)
            if value is None:
                readings.append(insufficient(key, name, "trend", "overlay", [key], {"period": period}, period))
                continue
            prior = ind.prev(key, 5)
            slope = "rising" if prior is not None and value > prior else "falling" if prior is not None and value < prior else "flat"
            dist = _pct(price, value)
            state = "bullish" if price > value else "bearish" if price < value else "neutral"
            readings.append(
                IndicatorReading(
                    key=key,
                    name=name,
                    category="trend",
                    values={key: value},
                    display=fmt_price(value),
                    state=state,
                    params={"period": period},
                    placement="overlay",
                    series_keys=[key],
                    interpretation=(
                        f"Price is {abs(dist):.2f}% {'above' if dist >= 0 else 'below'} the {name}, which is {slope} over the last 5 bars. "
                        "Price above a rising average is generally consistent with an uptrend; below a falling one, a downtrend."
                    ),
                )
            )

    # --- VWAP
    if "vwap" in ind.columns:
        value = ind.last("vwap")
        if value is not None:
            dist = _pct(price, value)
            readings.append(
                IndicatorReading(
                    key="vwap",
                    name="VWAP (session)",
                    category="trend",
                    values={"vwap": value},
                    display=fmt_price(value),
                    state="bullish" if price > value else "bearish",
                    params={"anchor": "session"},
                    placement="overlay",
                    series_keys=["vwap"],
                    interpretation=(
                        f"Price is {abs(dist):.2f}% {'above' if dist >= 0 else 'below'} the session VWAP (the volume-weighted average price "
                        "since the session began). Intraday traders often treat VWAP as a fair-value reference."
                    ),
                )
            )

    # --- ADX
    adx_v, pdi, mdi = ind.last("adx"), ind.last("plus_di"), ind.last("minus_di")
    if adx_v is None or pdi is None or mdi is None:
        readings.append(
            insufficient(
                "adx", f"ADX ({p.adx_period})", "trend", "panel", ["adx", "plus_di", "minus_di"], {"period": p.adx_period}, p.adx_period * 2 + 1
            )
        )
    else:
        strength = (
            "no clear trend" if adx_v < 20 else "a developing trend" if adx_v < 25 else "a strong trend" if adx_v < 40 else "a very strong trend"
        )
        direction = (
            "+DI is above -DI (buyers dominate directional movement)" if pdi > mdi else "-DI is above +DI (sellers dominate directional movement)"
        )
        readings.append(
            IndicatorReading(
                key="adx",
                name=f"ADX / DMI ({p.adx_period})",
                category="trend",
                values={"adx": adx_v, "plus_di": pdi, "minus_di": mdi},
                display=f"ADX {adx_v:.1f} · +DI {pdi:.1f} · -DI {mdi:.1f}",
                state=("bullish" if pdi > mdi else "bearish") if adx_v >= 20 else "neutral",
                params={"period": p.adx_period},
                placement="panel",
                series_keys=["adx", "plus_di", "minus_di"],
                interpretation=f"ADX {adx_v:.1f} suggests {strength}; {direction}. ADX measures trend strength, not direction.",
            )
        )

    # --- Ichimoku
    tenkan, kijun = ind.last("ichimoku_tenkan"), ind.last("ichimoku_kijun")
    span_a, span_b = ind.last("ichimoku_senkou_a"), ind.last("ichimoku_senkou_b")
    ich_params = {"tenkan": p.ichimoku_tenkan, "kijun": p.ichimoku_kijun, "senkou_b": p.ichimoku_senkou_b}
    ich_keys = ["ichimoku_tenkan", "ichimoku_kijun", "ichimoku_senkou_a", "ichimoku_senkou_b", "ichimoku_chikou"]
    if None in (tenkan, kijun, span_a, span_b):
        readings.append(insufficient("ichimoku", "Ichimoku Cloud", "trend", "overlay", ich_keys, ich_params, p.ichimoku_senkou_b + p.ichimoku_kijun))
    else:
        top, bottom = max(span_a, span_b), min(span_a, span_b)  # type: ignore[type-var]
        if price > top:
            pos, state = "above the cloud", "bullish"
        elif price < bottom:
            pos, state = "below the cloud", "bearish"
        else:
            pos, state = "inside the cloud (a zone of indecision)", "neutral"
        tk = "Tenkan-sen is above Kijun-sen" if tenkan > kijun else "Tenkan-sen is below Kijun-sen"  # type: ignore[operator]
        readings.append(
            IndicatorReading(
                key="ichimoku",
                name="Ichimoku Cloud",
                category="trend",
                values={"tenkan": tenkan, "kijun": kijun, "senkou_a": span_a, "senkou_b": span_b},
                display=f"Cloud {fmt_price(bottom)}–{fmt_price(top)}",
                state=state,
                params=ich_params,  # type: ignore[arg-type]
                placement="overlay",
                series_keys=ich_keys,
                interpretation=f"Price is {pos}; {tk}. Many traders read price above the cloud as bullish trend context and below as bearish.",
            )
        )

    # --- RSI
    value = ind.last("rsi")
    if value is None:
        readings.append(insufficient("rsi", f"RSI ({p.rsi_period})", "momentum", "panel", ["rsi"], {"period": p.rsi_period}, p.rsi_period + 1))
    else:
        if value >= p.rsi_overbought:
            state, text = (
                "overbought",
                f"At or above {p.rsi_overbought:g}: often called overbought. Strong trends can stay overbought for long periods, so this is not a sell signal by itself.",
            )
        elif value <= p.rsi_oversold:
            state, text = (
                "oversold",
                f"At or below {p.rsi_oversold:g}: often called oversold. Weak trends can stay oversold, so this is not a buy signal by itself.",
            )
        elif value >= 50:
            state, text = "bullish", "Above 50: average gains exceed average losses over the period (positive momentum)."
        else:
            state, text = "bearish", "Below 50: average losses exceed average gains over the period (negative momentum)."
        readings.append(
            IndicatorReading(
                key="rsi",
                name=f"RSI ({p.rsi_period})",
                category="momentum",
                values={"rsi": value},
                display=f"{value:.1f}",
                state=state,
                interpretation=text,
                placement="panel",
                series_keys=["rsi"],  # type: ignore[arg-type]
                params={"period": p.rsi_period, "overbought": p.rsi_overbought, "oversold": p.rsi_oversold},
            )
        )

    # --- MACD
    m, s, h = ind.last("macd"), ind.last("macd_signal"), ind.last("macd_histogram")
    macd_params = {"fast": p.macd_fast, "slow": p.macd_slow, "signal": p.macd_signal}
    if None in (m, s, h):
        readings.append(
            insufficient(
                "macd",
                f"MACD ({p.macd_fast},{p.macd_slow},{p.macd_signal})",
                "momentum",
                "panel",
                ["macd", "macd_signal", "macd_histogram"],
                macd_params,
                p.macd_slow + p.macd_signal,
            )
        )
    else:
        prev_h = ind.prev("macd_histogram")
        rel = "above" if m > s else "below"  # type: ignore[operator]
        trend = "widening" if prev_h is not None and abs(h) > abs(prev_h) else "narrowing"  # type: ignore[arg-type]
        readings.append(
            IndicatorReading(
                key="macd",
                name=f"MACD ({p.macd_fast},{p.macd_slow},{p.macd_signal})",
                category="momentum",
                values={"macd": m, "signal": s, "histogram": h},
                display=f"{m:.4g} / {s:.4g} (hist {h:+.4g})",
                state="bullish" if m > s else "bearish",  # type: ignore[operator]
                params=macd_params,
                placement="panel",
                series_keys=["macd", "macd_signal", "macd_histogram"],
                interpretation=(
                    f"MACD is {rel} its signal line and {'above' if m > 0 else 'below'} zero; the histogram is {trend}. "  # type: ignore[operator]
                    "MACD above the signal line indicates improving short-term momentum relative to the longer term."
                ),
            )
        )

    # --- Stochastic
    k, d = ind.last("stoch_k"), ind.last("stoch_d")
    st_params = {"k": p.stoch_k, "smooth": p.stoch_smooth, "d": p.stoch_d}
    if k is None or d is None:
        readings.append(
            insufficient(
                "stoch",
                f"Stochastic ({p.stoch_k},{p.stoch_smooth},{p.stoch_d})",
                "momentum",
                "panel",
                ["stoch_k", "stoch_d"],
                st_params,
                p.stoch_k + p.stoch_smooth + p.stoch_d,
            )
        )
    else:
        if k >= 80:
            state, zone = "overbought", "in the overbought zone (≥ 80)"
        elif k <= 20:
            state, zone = "oversold", "in the oversold zone (≤ 20)"
        else:
            state, zone = ("bullish" if k > d else "bearish"), "in the neutral range"
        readings.append(
            IndicatorReading(
                key="stoch",
                name=f"Stochastic ({p.stoch_k},{p.stoch_smooth},{p.stoch_d})",
                category="momentum",
                values={"k": k, "d": d},
                display=f"%K {k:.1f} · %D {d:.1f}",
                state=state,  # type: ignore[arg-type]
                params=st_params,
                placement="panel",
                series_keys=["stoch_k", "stoch_d"],
                interpretation=f"%K is {zone} and {'above' if k > d else 'below'} %D. It shows where the close sits within the recent high-low range.",
            )
        )

    # --- ROC
    value = ind.last("roc")
    if value is None:
        readings.append(insufficient("roc", f"ROC ({p.roc_period})", "momentum", "panel", ["roc"], {"period": p.roc_period}, p.roc_period + 1))
    else:
        readings.append(
            IndicatorReading(
                key="roc",
                name=f"ROC ({p.roc_period})",
                category="momentum",
                values={"roc": value},
                display=f"{value:+.2f}%",
                state="bullish" if value > 0 else "bearish" if value < 0 else "neutral",
                params={"period": p.roc_period},
                placement="panel",
                series_keys=["roc"],
                interpretation=f"Price is {abs(value):.2f}% {'higher' if value >= 0 else 'lower'} than {p.roc_period} bars ago.",
            )
        )

    # --- Williams %R
    value = ind.last("willr")
    if value is None:
        readings.append(
            insufficient("willr", f"Williams %R ({p.willr_period})", "momentum", "panel", ["willr"], {"period": p.willr_period}, p.willr_period)
        )
    else:
        state = "overbought" if value >= -20 else "oversold" if value <= -80 else ("bullish" if value > -50 else "bearish")
        readings.append(
            IndicatorReading(
                key="willr",
                name=f"Williams %R ({p.willr_period})",
                category="momentum",
                values={"willr": value},
                display=f"{value:.1f}",
                state=state,
                params={"period": p.willr_period},
                placement="panel",  # type: ignore[arg-type]
                series_keys=["willr"],
                interpretation="Above -20 is commonly read as overbought and below -80 as oversold; it measures the close relative to the period's high.",
            )
        )

    # --- Bollinger
    up, mid, lo, pb, bw = (ind.last(k) for k in ("bb_upper", "bb_middle", "bb_lower", "bb_percent_b", "bb_bandwidth"))
    bb_params = {"period": p.bb_period, "std": p.bb_std}
    if None in (up, mid, lo, pb, bw):
        readings.append(
            insufficient(
                "bbands",
                f"Bollinger Bands ({p.bb_period}, {p.bb_std:g})",
                "volatility",
                "overlay",
                ["bb_upper", "bb_middle", "bb_lower"],
                bb_params,
                p.bb_period,
            )
        )
    else:
        bw_series = ind.columns["bb_bandwidth"].dropna().tail(126)
        pct_rank = float((bw_series <= bw).mean() * 100) if len(bw_series) >= 20 else None
        squeeze = pct_rank is not None and pct_rank <= 10
        if pb > 1:  # type: ignore[operator]
            state, pos = "overbought", "above the upper band"
        elif pb < 0:  # type: ignore[operator]
            state, pos = "oversold", "below the lower band"
        else:
            state, pos = ("bullish" if price > mid else "bearish"), f"inside the bands (%B {pb:.2f})"  # type: ignore[operator]
        readings.append(
            IndicatorReading(
                key="bbands",
                name=f"Bollinger Bands ({p.bb_period}, {p.bb_std:g})",
                category="volatility",
                values={"upper": up, "middle": mid, "lower": lo, "percent_b": pb, "bandwidth": bw},
                display=f"{fmt_price(lo)} – {fmt_price(up)}",
                state=state,
                params=bb_params,  # type: ignore[arg-type]
                placement="overlay",
                series_keys=["bb_upper", "bb_middle", "bb_lower"],
                interpretation=(
                    f"Price is {pos}. Band width is {bw * 100:.2f}% of the middle band"  # type: ignore[operator]
                    + (
                        f" ({pct_rank:.0f}th percentile of the last {len(bw_series)} bars"
                        + (", a squeeze: volatility is compressed" if squeeze else "")
                        + ")."
                        if pct_rank is not None
                        else "."
                    )
                    + " Closes outside the bands show unusually large moves relative to recent volatility."
                ),
            )
        )

    # --- ATR
    value = ind.last("atr")
    if value is None:
        readings.append(insufficient("atr", f"ATR ({p.atr_period})", "volatility", "panel", ["atr"], {"period": p.atr_period}, p.atr_period))
    else:
        atr_pct = value / price * 100 if price else 0.0
        readings.append(
            IndicatorReading(
                key="atr",
                name=f"ATR ({p.atr_period})",
                category="volatility",
                values={"atr": value, "atr_pct": atr_pct},
                display=f"{fmt_price(value)} ({atr_pct:.2f}%)",
                state="neutral",
                params={"period": p.atr_period},
                placement="panel",
                series_keys=["atr"],
                interpretation=f"The average bar range (including gaps) is {fmt_price(value)}, {atr_pct:.2f}% of price. Used to size stops and gauge volatility.",
            )
        )

    # --- Keltner
    ku, km, kl = ind.last("kc_upper"), ind.last("kc_middle"), ind.last("kc_lower")
    kc_params = {"period": p.kc_period, "atr_period": p.kc_atr_period, "multiplier": p.kc_mult}
    if None in (ku, km, kl):
        readings.append(
            insufficient(
                "keltner",
                f"Keltner Channels ({p.kc_period}, {p.kc_mult:g}×ATR{p.kc_atr_period})",
                "volatility",
                "overlay",
                ["kc_upper", "kc_middle", "kc_lower"],
                kc_params,
                max(p.kc_period, p.kc_atr_period),
            )
        )
    else:
        if price > ku:  # type: ignore[operator]
            state, pos = "bullish", "above the upper channel (strong upside momentum)"
        elif price < kl:  # type: ignore[operator]
            state, pos = "bearish", "below the lower channel (strong downside momentum)"
        else:
            state, pos = "neutral", "inside the channel"
        readings.append(
            IndicatorReading(
                key="keltner",
                name=f"Keltner Channels ({p.kc_period}, {p.kc_mult:g}×ATR{p.kc_atr_period})",
                category="volatility",
                values={"upper": ku, "middle": km, "lower": kl},
                display=f"{fmt_price(kl)} – {fmt_price(ku)}",
                state=state,  # type: ignore[arg-type]
                params=kc_params,
                placement="overlay",
                series_keys=["kc_upper", "kc_middle", "kc_lower"],
                interpretation=f"Price is {pos}. The channel is an EMA ± a multiple of ATR.",
            )
        )

    # --- Historical volatility
    value = ind.last("hv")
    if value is None:
        readings.append(
            insufficient("hv", f"Historical volatility ({p.hv_period})", "volatility", "panel", ["hv"], {"period": p.hv_period}, p.hv_period + 1)
        )
    else:
        hv_hist = ind.columns["hv"].dropna()
        rank = float((hv_hist <= value).mean() * 100) if len(hv_hist) >= 50 else None
        state = "neutral" if rank is None else "high" if rank >= 67 else "low" if rank <= 33 else "neutral"
        readings.append(
            IndicatorReading(
                key="hv",
                name=f"Historical volatility ({p.hv_period})",
                category="volatility",
                values={"hv": value},
                display=f"{value:.1f}% annualized",
                state=state,
                params={"period": p.hv_period},
                placement="panel",  # type: ignore[arg-type]
                series_keys=["hv"],
                interpretation=(
                    f"Close-to-close volatility over {p.hv_period} bars, annualized ({periods_per_year(ind.timeframe, ind.asset_type):g} bars/year)."
                    + (f" Current reading is at the {rank:.0f}th percentile of the available history." if rank is not None else "")
                ),
            )
        )

    # --- Volume family
    if not ind.volume_available:
        for key, name in (("volume", "Volume"), ("obv", "On-Balance Volume"), ("mfi", "Money Flow Index"), ("ad", "Accumulation/Distribution")):
            readings.append(
                IndicatorReading(
                    key=key,
                    name=name,
                    category="volume",
                    values={key: None},
                    display="n/a",
                    state="unavailable",
                    params={},
                    placement="panel",
                    series_keys=[],
                    interpretation="The data provider does not supply volume for this instrument.",
                )
            )
        return readings

    vol, vol_avg = ind.last("volume"), ind.last("volume_sma")
    if vol is not None and vol_avg:
        rel = vol / vol_avg
        readings.append(
            IndicatorReading(
                key="volume",
                name=f"Volume vs {p.volume_sma_period}-bar average",
                category="volume",
                values={"volume": vol, "volume_sma": vol_avg, "relative_volume": rel},
                display=f"{rel:.2f}× average",
                state="high" if rel >= 1.5 else "low" if rel <= 0.6 else "neutral",
                params={"period": p.volume_sma_period},
                placement="panel",
                series_keys=["volume", "volume_sma"],
                interpretation=(
                    f"Latest bar volume is {rel:.2f}× its {p.volume_sma_period}-bar average"
                    + (" (the bar is still forming, so this will rise)." if not ind.last_bar_complete else ".")
                    + " Moves on above-average volume are generally considered more significant."
                ),
            )
        )
    else:
        readings.append(
            insufficient("volume", "Volume", "volume", "panel", ["volume", "volume_sma"], {"period": p.volume_sma_period}, p.volume_sma_period)
        )

    obv_v = ind.last("obv")
    obv_series = ind.columns["obv"]
    obv_ma = sma(obv_series, 20).iloc[-1] if len(obv_series) >= 20 else np.nan
    if obv_v is not None and not pd.isna(obv_ma):
        rising = obv_v > obv_ma
        readings.append(
            IndicatorReading(
                key="obv",
                name="On-Balance Volume",
                category="volume",
                values={"obv": obv_v, "obv_sma20": float(obv_ma)},
                display=f"{obv_v:,.0f}",
                state="bullish" if rising else "bearish",
                params={},
                placement="panel",
                series_keys=["obv"],
                interpretation=f"OBV is {'above' if rising else 'below'} its 20-bar average: volume has been heavier on {'up' if rising else 'down'} bars recently.",
            )
        )
    else:
        readings.append(insufficient("obv", "On-Balance Volume", "volume", "panel", ["obv"], {}, 20))

    value = ind.last("mfi")
    if value is None:
        readings.append(
            insufficient("mfi", f"Money Flow Index ({p.mfi_period})", "volume", "panel", ["mfi"], {"period": p.mfi_period}, p.mfi_period + 1)
        )
    else:
        state = "overbought" if value >= 80 else "oversold" if value <= 20 else ("bullish" if value >= 50 else "bearish")
        readings.append(
            IndicatorReading(
                key="mfi",
                name=f"Money Flow Index ({p.mfi_period})",
                category="volume",
                values={"mfi": value},
                display=f"{value:.1f}",
                state=state,
                params={"period": p.mfi_period},
                placement="panel",  # type: ignore[arg-type]
                series_keys=["mfi"],
                interpretation="A volume-weighted RSI. Above 80 is commonly read as overbought and below 20 as oversold.",
            )
        )

    ad_series = ind.columns["ad"]
    if len(ad_series) > 20:
        ad_now, ad_then = float(ad_series.iloc[-1]), float(ad_series.iloc[-21])
        readings.append(
            IndicatorReading(
                key="ad",
                name="Accumulation/Distribution",
                category="volume",
                values={"ad": ad_now},
                display=f"{ad_now:,.0f}",
                state="bullish" if ad_now > ad_then else "bearish",
                params={},
                placement="panel",
                series_keys=["ad"],
                interpretation=f"The A/D line has {'risen' if ad_now > ad_then else 'fallen'} over the last 20 bars, suggesting {'accumulation' if ad_now > ad_then else 'distribution'} (closes near the {'high' if ad_now > ad_then else 'low'} of each bar's range on volume).",
            )
        )
    else:
        readings.append(insufficient("ad", "Accumulation/Distribution", "volume", "panel", ["ad"], {}, 21))
    return readings


def series_payload(ind: IndicatorSet) -> tuple[list[int], dict[str, list[float | None]]]:
    times = [int(ts.timestamp()) for ts in ind.frame.index]
    out: dict[str, list[float | None]] = {}
    for key, series in ind.columns.items():
        arr = series.to_numpy(dtype=float)
        out[key] = [None if not math.isfinite(v) else round(float(v), 10) for v in arr]
    return times, out
