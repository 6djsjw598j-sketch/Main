"""Indicator correctness: hand-computed cases, independent reference loops and the `ta` library."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import ta

from app.indicators.core import ema, rma, sma, true_range
from app.indicators.engine import build_readings, compute_indicator_set, periods_per_year, series_payload
from app.indicators.momentum import macd, roc, rsi, stochastic, williams_r
from app.indicators.params import IndicatorParams
from app.indicators.trend import adx, ichimoku, vwap
from app.indicators.volatility import atr, bollinger, historical_volatility, keltner
from app.indicators.volume import accumulation_distribution, mfi, obv
from app.models.market import AssetType, Timeframe
from tests.helpers import make_frame, ohlc_random

# ----------------------------------------------------------------------------- hand-computed


def test_sma_known_values():
    s = pd.Series([1.0, 2, 3, 4, 5, 6])
    out = sma(s, 3)
    assert out.isna().sum() == 2
    assert out.tolist()[2:] == [2.0, 3.0, 4.0, 5.0]


def test_ema_is_sma_seeded():
    # alpha = 2 / (3 + 1) = 0.5; seed = SMA(1,2,3) = 2
    out = ema(pd.Series([1.0, 2, 3, 4, 5, 6]), 3)
    assert out.isna().sum() == 2
    assert out.tolist()[2:] == pytest.approx([2.0, 3.0, 4.0, 5.0])


def test_rma_wilder_smoothing():
    # alpha = 1/2, seed = mean(2, 4) = 3; next = 3 + (6 - 3)/2 = 4.5; next = 4.5 + (8 - 4.5)/2 = 6.25
    out = rma(pd.Series([2.0, 4, 6, 8]), 2)
    assert math.isnan(out.iloc[0])
    assert out.tolist()[1:] == pytest.approx([3.0, 4.5, 6.25])


def test_ema_skips_leading_nan():
    out = ema(pd.Series([np.nan, np.nan, 1.0, 2.0, 3.0, 4.0]), 2)
    assert out.isna().sum() == 3
    # seed = mean(1, 2) = 1.5; alpha = 2/3 -> 1.5 + 2/3*(3-1.5) = 2.5
    assert out.iloc[3] == pytest.approx(1.5)
    assert out.iloc[4] == pytest.approx(2.5)


def test_rsi_extremes():
    up = pd.Series(np.arange(1.0, 31.0))
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    flat = pd.Series([5.0] * 30)
    assert rsi(flat, 14).iloc[-1] == pytest.approx(50.0)
    down = pd.Series(np.arange(30.0, 0.0, -1))
    assert rsi(down, 14).iloc[-1] == pytest.approx(0.0)


def _reference_rsi(closes: list[float], n: int) -> list[float | None]:
    """Independent loop implementation of Wilder's RSI."""
    out: list[float | None] = [None] * len(closes)
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + avg_g / avg_l) if avg_l else 100.0
    for i in range(n + 1, len(closes)):
        avg_g = (avg_g * (n - 1) + gains[i - 1]) / n
        avg_l = (avg_l * (n - 1) + losses[i - 1]) / n
        out[i] = 100 - 100 / (1 + avg_g / avg_l) if avg_l else 100.0
    return out


def test_rsi_matches_reference_loop():
    closes = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
        46.00,
        46.03,
        46.41,
        46.22,
        45.64,
        46.21,
        46.25,
        45.71,
        46.45,
        45.78,
        45.35,
        44.03,
        44.18,
        44.22,
        44.57,
        43.42,
        42.66,
        43.13,
    ]
    ours = rsi(pd.Series(closes), 14).tolist()
    ref = _reference_rsi(closes, 14)
    for a, b in zip(ours[14:], ref[14:], strict=True):
        assert a == pytest.approx(b, abs=1e-9)
    assert all(math.isnan(v) for v in ours[:14])
    # First value by hand: avg gain = 3.34/14, avg loss = 1.40/14 -> RS = 2.3857 -> RSI = 70.464
    assert ours[14] == pytest.approx(100 - 100 / (1 + 3.34 / 1.40), abs=1e-6)


def test_macd_simple_case():
    s = pd.Series(np.linspace(1, 100, 100))
    out = macd(s, 12, 26, 9)
    assert out["macd"].iloc[:25].isna().all()
    assert out["signal"].iloc[:33].isna().all()
    assert not math.isnan(out["signal"].iloc[33])
    # Steady uptrend -> fast EMA above slow EMA.
    assert out["macd"].iloc[-1] > 0


def test_true_range_first_bar_uses_high_low():
    df = make_frame([10, 12, 11], spread=1.0)
    tr = true_range(df)
    assert tr.iloc[0] == pytest.approx(df["high"].iloc[0] - df["low"].iloc[0])
    # Bar 1: high 13, low 9, prev close 10 -> max(4, 3, 1) = 4
    assert tr.iloc[1] == pytest.approx(4.0)


def test_obv_hand_computed():
    close = pd.Series([10.0, 11, 11, 10, 12])
    volume = pd.Series([100.0, 200, 300, 400, 500])
    # 0, +200, 0 (unchanged), -400, +500
    assert obv(close, volume).tolist() == [0.0, 200.0, 200.0, -200.0, 300.0]


def test_accumulation_distribution_hand_computed():
    df = pd.DataFrame({"open": [1.0, 1.0], "high": [10.0, 10.0], "low": [0.0, 0.0], "close": [10.0, 5.0], "volume": [100.0, 50.0]})
    # bar0: MFM = ((10-0)-(10-10))/10 = 1 -> +100; bar1: MFM = 0 -> 0
    assert accumulation_distribution(df).tolist() == [100.0, 100.0]


def test_vwap_resets_each_session():
    idx = pd.to_datetime(["2024-01-01 22:00", "2024-01-01 23:00", "2024-01-02 00:00", "2024-01-02 01:00"], utc=True)
    df = pd.DataFrame(
        {"open": [1.0] * 4, "high": [12.0, 14, 30, 33], "low": [8.0, 10, 30, 27], "close": [10.0, 12, 30, 30], "volume": [1.0, 3.0, 2.0, 2.0]},
        index=idx,
    )
    keys = pd.Series(idx.strftime("%Y-%m-%d"), index=idx)
    out = vwap(df, keys)
    # Session 1: tp 10, 12 -> (10*1 + 12*3)/4 = 11.5 ; session 2: tp 30, 30 -> 30
    assert out.tolist() == pytest.approx([10.0, 11.5, 30.0, 30.0])


def test_ichimoku_lines_and_displacement():
    df = ohlc_random(120)
    out = ichimoku(df, 9, 26, 52, 26)
    i = 80
    expected_tenkan = (df["high"].iloc[i - 8 : i + 1].max() + df["low"].iloc[i - 8 : i + 1].min()) / 2
    assert out["tenkan"].iloc[i] == pytest.approx(expected_tenkan)
    # Plotted span at bar i was computed 25 bars earlier (no look-ahead).
    assert out["senkou_a"].iloc[i] == pytest.approx(out["senkou_a_raw"].iloc[i - 25])
    assert out["senkou_b"].iloc[i] == pytest.approx(out["senkou_b_raw"].iloc[i - 25])


def test_historical_volatility_manual():
    close = pd.Series([100.0, 101, 99, 102, 103, 101, 104])
    hv = historical_volatility(close, 5, 252)
    rets = np.log(close / close.shift(1)).iloc[-5:]
    assert hv.iloc[-1] == pytest.approx(rets.std(ddof=1) * math.sqrt(252) * 100)


def _reference_adx(df: pd.DataFrame, n: int) -> tuple[float, float, float]:
    h, lo, c = df["high"].tolist(), df["low"].tolist(), df["close"].tolist()
    trs, pdms, mdms = [], [], []
    for i in range(1, len(c)):
        up, down = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdms.append(up if up > down and up > 0 else 0.0)
        mdms.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    atr_, p, m = sum(trs[:n]) / n, sum(pdms[:n]) / n, sum(mdms[:n]) / n
    dxs = []
    for k in range(n, len(trs) + 1):
        if k > n:
            atr_ = (atr_ * (n - 1) + trs[k - 1]) / n
            p = (p * (n - 1) + pdms[k - 1]) / n
            m = (m * (n - 1) + mdms[k - 1]) / n
        pdi, mdi = 100 * p / atr_, 100 * m / atr_
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi))
    adx_ = sum(dxs[:n]) / n
    for dx in dxs[n:]:
        adx_ = (adx_ * (n - 1) + dx) / n
    return adx_, pdi, mdi


def test_adx_matches_reference_loop():
    df = ohlc_random(150, seed=11)
    out = adx(df, 14)
    ref_adx, ref_pdi, ref_mdi = _reference_adx(df, 14)
    assert out["adx"].iloc[-1] == pytest.approx(ref_adx, rel=1e-9)
    assert out["plus_di"].iloc[-1] == pytest.approx(ref_pdi, rel=1e-9)
    assert out["minus_di"].iloc[-1] == pytest.approx(ref_mdi, rel=1e-9)
    assert out["adx"].iloc[: 2 * 14 - 1].isna().all()


# ----------------------------------------------------------------------------- `ta` cross-checks


@pytest.fixture(scope="module")
def big() -> pd.DataFrame:
    return ohlc_random(600, seed=5)


def test_bollinger_matches_ta(big):
    ours = bollinger(big["close"], 20, 2.0)
    ref = ta.volatility.BollingerBands(big["close"], window=20, window_dev=2)
    np.testing.assert_allclose(ours["upper"].iloc[19:], ref.bollinger_hband().iloc[19:], rtol=1e-10)
    np.testing.assert_allclose(ours["lower"].iloc[19:], ref.bollinger_lband().iloc[19:], rtol=1e-10)


def test_atr_matches_ta(big):
    ours = atr(big, 14)
    ref = ta.volatility.AverageTrueRange(big["high"], big["low"], big["close"], window=14).average_true_range()
    np.testing.assert_allclose(ours.iloc[14:], ref.iloc[14:], rtol=1e-9)


def test_rsi_converges_to_ta(big):
    # `ta` seeds its smoothing differently; after a long warm-up both agree.
    ours = rsi(big["close"], 14)
    ref = ta.momentum.RSIIndicator(big["close"], window=14).rsi()
    np.testing.assert_allclose(ours.iloc[400:], ref.iloc[400:], rtol=1e-8)


def test_macd_converges_to_ta(big):
    ours = macd(big["close"], 12, 26, 9)
    ref = ta.trend.MACD(big["close"], window_slow=26, window_fast=12, window_sign=9)
    np.testing.assert_allclose(ours["macd"].iloc[400:], ref.macd().iloc[400:], rtol=1e-7)
    np.testing.assert_allclose(ours["signal"].iloc[400:], ref.macd_signal().iloc[400:], rtol=1e-6)


def test_stochastic_matches_ta(big):
    ours = stochastic(big, 14, 1, 3)
    ref = ta.momentum.StochasticOscillator(big["high"], big["low"], big["close"], window=14, smooth_window=3)
    np.testing.assert_allclose(ours["k"].iloc[13:], ref.stoch().iloc[13:], rtol=1e-10)
    np.testing.assert_allclose(ours["d"].iloc[15:], ref.stoch_signal().iloc[15:], rtol=1e-10)


def test_williams_and_roc_match_ta(big):
    np.testing.assert_allclose(
        williams_r(big, 14).iloc[13:],
        ta.momentum.WilliamsRIndicator(big["high"], big["low"], big["close"], lbp=14).williams_r().iloc[13:],
        rtol=1e-10,
    )
    np.testing.assert_allclose(roc(big["close"], 12).iloc[12:], ta.momentum.ROCIndicator(big["close"], window=12).roc().iloc[12:], rtol=1e-10)


def test_mfi_matches_ta(big):
    ours = mfi(big, 14)
    ref = ta.volume.MFIIndicator(big["high"], big["low"], big["close"], big["volume"], window=14).money_flow_index()
    np.testing.assert_allclose(ours.iloc[20:], ref.iloc[20:], rtol=1e-8)


def test_keltner_bands_use_ema_and_atr(big):
    out = keltner(big, 20, 10, 2.0)
    expected_upper = ema(big["close"], 20) + 2.0 * atr(big, 10)
    np.testing.assert_allclose(out["upper"].iloc[30:], expected_upper.iloc[30:])


# ----------------------------------------------------------------------------- engine behaviour


def test_engine_handles_short_history_without_errors():
    df = make_frame(np.linspace(100, 110, 15))
    ind = compute_indicator_set(df, IndicatorParams(), Timeframe.D1, AssetType.STOCK, "America/New_York")
    readings = {r.key: r for r in build_readings(ind)}
    assert readings["sma_200"].state == "insufficient_data"
    assert readings["sma_200"].values["sma_200"] is None
    assert readings["macd"].state == "insufficient_data"
    assert "Needs at least" in readings["ichimoku"].interpretation
    times, series = series_payload(ind)
    assert len(times) == 15
    assert all(v is None for v in series["sma_200"])  # never invented
    json_values = [v for values in series.values() for v in values if v is not None]
    assert all(math.isfinite(v) for v in json_values)


def test_engine_marks_volume_indicators_unavailable():
    df = make_frame(np.linspace(100, 120, 120), volume=None)
    ind = compute_indicator_set(df, IndicatorParams(), Timeframe.D1, AssetType.INDEX, "America/New_York")
    readings = {r.key: r for r in build_readings(ind)}
    for key in ("volume", "obv", "mfi", "ad"):
        assert readings[key].state == "unavailable"
    assert "vwap" not in ind.columns


def test_engine_overbought_and_custom_params():
    df = make_frame(np.linspace(100, 200, 120))
    params = IndicatorParams(rsi_period=7, sma_periods="10,30", ema_periods=[5])
    ind = compute_indicator_set(df, params, Timeframe.H1, AssetType.CRYPTO, "UTC")
    readings = {r.key: r for r in build_readings(ind)}
    assert readings["rsi"].state == "overbought"
    assert readings["rsi"].name == "RSI (7)"
    assert {"sma_10", "sma_30", "ema_5", "vwap"} <= set(ind.columns)
    assert len(ind.future_times) == 25  # Ichimoku projection
    assert ind.future_times[0] > int(df.index[-1].timestamp())


def test_params_validation():
    with pytest.raises(ValueError):
        IndicatorParams(macd_fast=30, macd_slow=26)
    with pytest.raises(ValueError):
        IndicatorParams(sma_periods="20,abc")
    with pytest.raises(ValueError):
        IndicatorParams(sma_periods="1")
    assert IndicatorParams(sma_periods=["50,20"]).sma_periods == [20, 50]


def test_periods_per_year():
    assert periods_per_year(Timeframe.D1, AssetType.STOCK) == 252
    assert periods_per_year(Timeframe.D1, AssetType.CRYPTO) == 365
    assert periods_per_year(Timeframe.H1, AssetType.STOCK) == pytest.approx(252 * 6.5)
    assert periods_per_year(Timeframe.MN1, AssetType.CRYPTO) == 12
