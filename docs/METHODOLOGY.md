# Methodology

This document describes every calculation MarketLens performs, so its output can be checked
and reproduced. The code references point to `backend/app/`.

**Observed facts vs algorithmic estimates.** Prices, volumes, and previous day/week/month
highs and lows are *observed* market data. Moving averages and oscillators are
*deterministic transformations* of that data. Support/resistance zones, strength scores,
trendlines, Fibonacci levels, the factor score, conditions and trade scenarios are *algorithmic
estimates*. They describe history and are not guaranteed to hold or to predict anything.

---

## 1. Market data handling (`market/`, `providers/`)

### Timeframes and aggregation (`market/aggregation.py`)

| Timeframe | Coinbase source | Twelve Data |
|---|---|---|
| 1m, 5m, 15m, 1h, 1d | native | native |
| 30m | 2 × 15m | native |
| 4h | 4 × 1h | native (session-aligned by Twelve Data) |
| 1w | 7 × 1d | native |
| 1M | calendar month of 1d | native |

- Buckets are aligned to the UNIX epoch (00:00 UTC). Weeks start Monday 00:00 UTC; months
  start on the 1st.
- OHLCV: `open` = first open, `high` = max, `low` = min, `close` = last close, `volume` = sum.
- If the fetched history starts mid-bucket, that **leading partial bucket is dropped**,
  because its open and range would be wrong.
- **Missing source candles are never invented.** Coinbase publishes no candle for intervals
  without trades, and a bucket is built from the candles that exist.
- The last bar is flagged `last_bar_complete = false` while its period is still open.
- Daily and longer equity bars are labelled with the exchange session date (stored as 00:00
  UTC of that date) and displayed as dates, so they never shift a day by time zone.

### Sessions (`market/sessions.py`)

- Crypto: 24/7.
- US equities (America/New_York): pre-market 04:00–09:30, regular 09:30–16:00, after-hours
  16:00–20:00, Monday–Friday. The provider's `is_market_open` flag overrides the schedule, so
  holidays and early closes show as *closed*. Only regular-session bars and quotes are used.
- Other exchanges: the provider flag only, otherwise *unknown*.
- Spot FX: Sunday 17:00 to Friday 17:00 New York time.

### Freshness (`market/freshness.py`)

The label comes from the **age of the data point's own timestamp**, never from what a plan
advertises:

| Situation | `real_time` | `delayed` | `stale` |
|---|---|---|---|
| Crypto | age ≤ 2 min | ≤ 15 min | older |
| Equity, session open | age ≤ 2 min | ≤ 30 min | older |
| Equity, session closed | n/a; `end_of_day` if age ≤ 4.5 days | n/a | older |

Candle series are judged by whether the last bar is the current period ("Live bar"), and
by how long ago the last bar closed relative to the bar length.

### Caching and failure handling (`cache.py`, `providers/`)

- Read-through cache (Redis, or an in-process LRU) with **single-flight** locking. Concurrent
  requests for the same data cause one upstream call.
- **Stale-on-error:** if a provider is rate-limited or unavailable and an older copy exists
  (up to 6 h), it is returned with `cache.stale = true` and a visible warning.
- **Negative caching:** permanent errors (unknown symbol, rejected key, plan restriction) are
  remembered for 60 s, so they don't burn credits.
- **Retries:** network errors and HTTP 5xx are retried twice with exponential backoff.
  4xx errors are not retried.
- **Circuit breaker:** after 5 consecutive transient failures the provider is skipped for 30 s.
- **Credit rationing:** token buckets enforce Coinbase ≤ 8 req/s and the configured Twelve Data
  credits per minute and per UTC day *before* calling upstream.

## 2. Indicators (`indicators/`)

Notation: `C`, `H`, `L`, `V` = close, high, low, volume; `n` = period. Every function returns
NaN (JSON `null`) for its warm-up period. Insufficient history is never filled in.

**Smoothing conventions** (`indicators/core.py`)

- `SMA(x, n)` = arithmetic mean of the last `n` values.
- `EMA(x, n)`: α = 2/(n+1), **seeded with the SMA of the first n values** (TA-Lib/TradingView
  convention), then `EMAₜ = α·xₜ + (1−α)·EMAₜ₋₁`.
- `RMA(x, n)` (Wilder): α = 1/n, seeded the same way.

| Indicator | Definition | Defaults |
|---|---|---|
| SMA / EMA | as above, on close | SMA 20/50/100/200; EMA 9/12/21/50/200 |
| VWAP | Σ(TP·V)/ΣV with TP = (H+L+C)/3, **reset each session** (UTC day for crypto, exchange-local date for equities). Intraday timeframes only; needs volume | – |
| ADX / DMI | +DM = ΔH if ΔH > −ΔL and > 0; −DM likewise; TR = max(H−L, \|H−Cₜ₋₁\|, \|L−Cₜ₋₁\|); ±DI = 100·RMA(±DM)/RMA(TR); DX = 100·\|+DI−−DI\|/(+DI+−DI); ADX = RMA(DX) | 14 |
| Ichimoku | Tenkan = (HH₉+LL₉)/2; Kijun = (HH₂₆+LL₂₆)/2; Span A = (Tenkan+Kijun)/2 and Span B = (HH₅₂+LL₅₂)/2, both plotted 25 bars ahead (displacement 26 counting the current bar, TradingView convention); Chikou = close plotted 25 bars back (display only) | 9/26/52 |
| RSI | 100 − 100/(1+RMA(gains)/RMA(losses)); 100 when there were no losses, 50 when flat | 14; OB 70 / OS 30 |
| MACD | EMA₁₂ − EMA₂₆; signal = EMA₉(MACD); histogram = MACD − signal | 12/26/9 |
| Stochastic (full) | raw %K = 100·(C−LLₙ)/(HHₙ−LLₙ) (50 if range is 0); %K = SMA(raw, smooth); %D = SMA(%K, d). OB 80 / OS 20 | 14/3/3 |
| ROC | 100·(Cₜ/Cₜ₋ₙ − 1) | 12 |
| Williams %R | −100·(HHₙ−C)/(HHₙ−LLₙ). OB > −20 / OS < −80 | 14 |
| Bollinger Bands | mid = SMA₂₀; ±2·σ with **population** σ (ddof = 0); %B; bandwidth = (upper−lower)/mid | 20, 2 |
| ATR | RMA(TR, n); the first bar's TR is H−L | 14 |
| Keltner | EMA₂₀(C) ± 2·ATR₁₀ | 20, 10, 2 |
| Historical volatility | stdev(ln(Cₜ/Cₜ₋₁), n, ddof=1)·√N·100, with N bars/year = crypto 365·86400/bar-seconds; FX 260·86400/bar-seconds; equities 252 (daily), 252·6.5h/bar-length (intraday), 52 (weekly), 12 (monthly) | 20 |
| Volume average | SMA₂₀(V) and relative volume V/SMA₂₀(V) | 20 |
| OBV | cumulative ±V by sign of ΔC (unchanged close adds 0) | – |
| MFI | money flow = TP·V; MFI = 100 − 100/(1 + Σpositive/Σnegative) over n | 14; OB 80 / OS 20 |
| Accumulation/Distribution | Σ ((C−L)−(H−C))/(H−L) · V | – |

Verification (`tests/test_indicators.py`): hand-computed values; independent loop
implementations (RSI, ADX); and exact or convergent agreement with the `ta` library for
Bollinger, ATR, RSI, MACD, Stochastic, Williams %R, ROC and MFI. `ta` seeds its EMA/RMA with the
first value instead of an SMA, so those comparisons are made after the seed has decayed.

**Readings.** Each indicator's latest value comes with a state (`bullish`, `bearish`,
`neutral`, `overbought`, `oversold`, `high`, `low`, `insufficient_data`, `unavailable`) and a
generic interpretation. No single indicator is presented as a buy or sell signal.

## 3. Support and resistance (`analysis/support_resistance.py`, `swings.py`)

1. **Swings.** Bar *i* is a swing high if `Hᵢ` is greater than the highs of the *w* bars before
   it and ≥ the *w* bars after it (swing lows mirror this). *w* defaults to 5 (4 for 4h, 3
   for 1w, 2 for 1M) and is configurable. The last *w* bars can't be confirmed, because
   that would need future data.
2. **Tolerance.** `tol = max(0.35 · ATR₁₄, 0.15 % · price)`.
3. **Clustering.** Swing prices are sorted. A price joins the current cluster if it is within
   `tol` of the previous price **and** the cluster stays narrower than `2.5 · tol`.
4. **Zone.** `[min − tol/4, max + tol/4]`. Centre = mean of the cluster's swing prices.
5. **Touches** = separate visits of the bar range into the zone (consecutive touching bars
   count once). **Reactions** = swing points inside the cluster.
6. **Strength (0–100):**

   | Component | Points | Formula |
   |---|---|---|
   | Reactions | 35 | 35 · min(reactions, 5)/5 |
   | Touches | 20 | 20 · min(touches, 8)/8 |
   | Recency | 20 | 20 · exp(−bars since last reaction / (lookback/3)) |
   | Volume | 15 | 15 · clip((mean relative volume at reactions − 0.75)/1.25, 0, 1) |
   | Confluence | 10 | 10 if an observed previous day/week/month high/low or a Fibonacci level lies in the zone |

   Without volume data, the remaining 85 points are rescaled to 100. **Major** if strength ≥
   55 (configurable), otherwise **minor**.
7. **Role.** Zones entirely below the last price are support; above are resistance. A zone
   containing the price is *testing* (support if price ≥ centre).
8. **Status** (last 10 bars). Each close is classified above, inside or below the zone, and
   the most recent close-through is found:
   - upward through a zone that historically acted as resistance → **breakout**;
     as support → **reclaimed** (regained after being lost)
   - downward through historical support → **breakdown**; through historical resistance →
     **lost** (price fell back below a level it had broken above)
9. **Nearest levels and distance.** Nearest support = highest support zone. Distance =
   price − zone top (0 inside). Nearest resistance mirrors this. Distance is also given as %
   of price.
10. Up to 12 strongest zones are returned (configurable), always including the nearest
    support and resistance.

**Prior-period highs/lows** (`analysis/levels.py`) come from daily bars of **completed**
periods. "Previous day" is the last session before today (exchange time zone; UTC for
crypto). Weeks run Monday–Sunday. A month is reported only if the data covers it from its
start.

**Classic pivots**: P = (H+L+C)/3, R1 = 2P−L, S1 = 2P−H, R2 = P+(H−L), S2 = P−(H−L),
R3 = H+2(P−L), S3 = L−2(H−P), using the previous day (intraday charts), the previous week
(daily) or the previous month (weekly/monthly).

**Fibonacci retracement**: from the highest high and lowest low in the lookback window. If
the low came first (upswing), levels = high − r·(high−low); otherwise low + r·(high−low).
Ratios 0, 23.6, 38.2, 50, 61.8, 78.6 and 100 %.

**Trendlines** (`analysis/trendlines.py`): the most recent pair of confirmed swing lows
(support) or highs (resistance), at least 5 bars apart, such that no close between them
violates the line by more than `tol`. The line is projected to the last bar. It is *broken*
when the last close is beyond it by more than `tol`.

## 4. Explainable factor model (`analysis/signals.py`, `summary.py`)

Each factor maps one reading to **v ∈ [−1, +1]**:

| Factor | Group | Default weight | v |
|---|---|---|---|
| Price vs SMA 200 | trend | 1.5 | clip((C − SMA200)/(2·ATR)) |
| SMA 50 vs SMA 200 | trend | 1.0 | sign(SMA50 − SMA200) |
| Price vs SMA 50 | trend | 1.0 | clip((C − SMA50)/(2·ATR)) |
| EMA 9 vs EMA 21 | trend | 1.0 | clip((EMA9 − EMA21)/(0.5·ATR)) |
| ADX/DMI direction | trend | 1.0 | sign(+DI − −DI) · clip((ADX − 15)/20, 0, 1) |
| Ichimoku cloud | trend | 0.75 | +1 above cloud, −1 below, 0 inside |
| RSI | momentum | 1.0 | clip((RSI − 50)/20) |
| MACD vs signal | momentum | 1.0 | +1 above signal & > 0; +0.5 above & < 0; −0.5 below & > 0; −1 below & < 0 |
| Stochastic %K | momentum | 0.5 | clip((%K − 50)/30) |
| Rate of change | momentum | 0.5 | clip(ROC% / (ATR% · √n)) |
| OBV trend | volume | 0.75 | sign(OBV − SMA20(OBV)) |
| Money Flow Index | volume | 0.5 | clip((MFI − 50)/20) |
| Up/down volume | volume | 0.5 | (Σ up-bar V − Σ down-bar V)/Σ V over 20 bars |

**Score** = 100 · Σ wᵢvᵢ / Σ wᵢ over the factors that have data, in [−100, +100]. Each
factor's *contribution* (100 · wᵢvᵢ / Σw) is shown, and the contributions sum to the score.
Bias is **bullish** at ≥ 25, **bearish** at ≤ −25, otherwise **neutral** (thresholds and
weights are configurable per request). **Conflicts** list the factors whose sign opposes the
bias. Factors with |v| < 0.2 count as neutral.

The score is an **uncalibrated description** of current technical conditions. It is not a
probability that price will rise, and no accuracy figure is claimed.

**Causality.** Every factor at bar *t* uses only data up to bar *t*. The Ichimoku spans
used are those plotted at *t* (computed 25 bars earlier), and the Chikou line is never used. A
test (`test_factors_are_bounded_and_causal`) recomputes the factors on truncated history and
checks they are identical.

**Trend horizons.** Three votes each. Two or more agreeing votes set the direction:

- short: C vs EMA21, EMA9 vs EMA21, EMA21 vs 5 bars ago
- medium: C vs SMA50, SMA50 vs 10 bars ago, sign of MACD
- long: C vs SMA200, SMA50 vs SMA200, SMA200 vs 20 bars ago

**Momentum**: RSI vs 50, MACD histogram sign, %K vs %D, ROC sign. ≥ 3 positive → positive,
≥ 3 negative → negative, otherwise mixed.

**Volatility regime**: percentile of today's ATR% (ATR/close) within the last 252 bars (at
least 50 needed). < 33 → low, > 67 → high. **Squeeze** when Bollinger bandwidth is in its
lowest 10 % of the last 126 bars.

**Volume confirmation**: the 20-bar change in price and in OBV share a sign → *confirming*,
opposite signs → *diverging*. Shows *unavailable* when the provider has no volume.

**Conditions**:

- Breakout/breakdown watch: the nearest zone and its distance in ATR, or *active* if a zone
  was crossed in the last 10 bars.
- Bullish/bearish **RSI divergence**: the last two swing lows/highs (window 3, last 90 bars)
  make a lower low with a higher RSI (or a higher high with a lower RSI), the latter by more
  than 2 points and within the last 15 bars.
- RSI overbought/oversold.
- MACD crossing against the prevailing trend.

**Confirmation and invalidation levels**:

- bullish bias: confirmation = top of nearest resistance, invalidation = bottom of nearest support
- bearish bias: the mirror
- neutral bias: bullish trigger above resistance, bearish trigger below support

**Events** (last 60 bars): EMA 9/21 crosses, golden/death crosses, MACD signal and zero
crosses, RSI entering/leaving overbought/oversold, closes outside Bollinger Bands, close vs
SMA 200, closes beyond the prior 50-bar range, volume spikes (> 2.5 × average), and S/R zone
breaks.

## 5. Trade scenarios (`analysis/setups.py`)

Labelled *technical scenarios, not financial advice or predictions*. They need ≥ 100 bars,
a valid ATR and at least one detected zone.

- **Bullish pullback**: entry zone = nearest support zone; invalidation = zone low − 0.25·ATR.
- **Bullish breakout**: used when price is within 0.75·ATR of the nearest resistance (or just
  broke it); entry = [zone high, zone high + 0.25·ATR]; invalidation = zone low − 0.25·ATR.
- **Targets** = the lower edges of the next one or two resistance zones beyond the entry. If
  there are none, no target is shown (none is invented).
- **Reward/risk** = (target − entry mid)/(entry mid − invalidation). Values below 1.5 are flagged.
- Bearish scenarios mirror these rules. Each scenario states whether it aligns with the factor
  bias, and lists confirming and invalidating conditions.
- Not modelled: fees, gaps, position sizing, and the probability of reaching any level.

## 6. Backtest (`analysis/backtest.py`)

- Signal: the factor score. **Enter** when score ≥ entry threshold (default 25); **exit**
  when score ≤ exit threshold (default 0). Optionally short when score ≤ −entry.
- **Timing**: signals are evaluated at the close of bar *t* and filled at the **open of bar
  t+1**. A still-forming last bar is excluded. A position open at the end is marked at the last
  close and flagged.
- **Costs**: commission and slippage in basis points, **per side**, applied to each fill
  (buys fill higher, sells lower).
- **Warm-up**: evaluation starts once every enabled non-volume factor has data, so the model
  doesn't change composition mid-test. At least 100 evaluated bars are required.
- **Metrics**: trades, win rate, mean/median/best/worst trade, profit factor, compounded total
  return, buy-and-hold over the same window, max drawdown (marked at closes), exposure,
  average bars held.
- **Caveats** (shown in the UI): in-sample, small samples are unreliable, no gaps/borrow/taxes,
  and thresholds are not calibrated probabilities.
