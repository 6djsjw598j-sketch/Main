"""Classifies how current a data point is, based on its actual timestamp.

The label is derived from the measured age of the data rather than from what a plan
advertises, so a delayed feed can never be shown as real-time.

| Asset                      | real_time     | delayed        | otherwise  |
|----------------------------|---------------|----------------|------------|
| Crypto (24/7)              | age <= 2 min  | age <= 15 min  | stale      |
| Equities, session open     | age <= 2 min  | age <= 30 min  | stale      |
| Equities, session closed   | end_of_day if age <= 4.5 days, else stale        |

``grace`` (the bar length for candle data) is added to every threshold because a bar's
timestamp is its open time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.market import Freshness, MarketSession

REALTIME_MAX = 120
CRYPTO_DELAYED_MAX = 15 * 60
EQUITY_DELAYED_MAX = 30 * 60
CLOSED_MAX = int(4.5 * 86400)


def _fmt_age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def classify_freshness(
    as_of: datetime,
    *,
    is_crypto: bool,
    session: MarketSession,
    now: datetime | None = None,
    grace: float = 0.0,
) -> Freshness:
    now = now or datetime.now(UTC)
    age = max(0.0, (now - as_of).total_seconds())
    age_txt = _fmt_age(age)
    if is_crypto or session.status == "always_open":
        if age <= REALTIME_MAX + grace:
            return Freshness(
                as_of=as_of,
                age_seconds=age,
                status="real_time",
                label="Real-time",
                is_stale=False,
                detail=f"Latest exchange data, updated {age_txt} ago.",
            )
        if age <= CRYPTO_DELAYED_MAX + grace:
            return Freshness(
                as_of=as_of,
                age_seconds=age,
                status="delayed",
                label=f"Delayed {age_txt}",
                is_stale=False,
                detail=f"Latest data point is {age_txt} old.",
            )
        return Freshness(
            as_of=as_of,
            age_seconds=age,
            status="stale",
            label="Stale",
            is_stale=True,
            detail=f"No update for {age_txt}; the market trades 24/7 so this indicates a data problem.",
        )
    if not session.is_open:
        if age <= CLOSED_MAX + grace:
            return Freshness(
                as_of=as_of,
                age_seconds=age,
                status="end_of_day",
                label="Last close",
                is_stale=False,
                detail=f"{session.label}. Showing the most recent session data ({age_txt} old).",
            )
        return Freshness(
            as_of=as_of,
            age_seconds=age,
            status="stale",
            label="Stale",
            is_stale=True,
            detail=f"Latest data point is {age_txt} old, longer than a normal market closure.",
        )
    if age <= REALTIME_MAX + grace:
        return Freshness(
            as_of=as_of,
            age_seconds=age,
            status="real_time",
            label="Real-time",
            is_stale=False,
            detail=f"Updated {age_txt} ago during the regular session.",
        )
    if age <= EQUITY_DELAYED_MAX + grace:
        return Freshness(
            as_of=as_of,
            age_seconds=age,
            status="delayed",
            label=f"Delayed {age_txt}",
            is_stale=False,
            detail=f"Market is open but the latest data point is {age_txt} old.",
        )
    return Freshness(
        as_of=as_of, age_seconds=age, status="stale", label="Stale", is_stale=True, detail=f"Market is open but no update for {age_txt}."
    )


def classify_candle_freshness(
    last_bar_open: datetime,
    bar_end: datetime,
    *,
    is_crypto: bool,
    session: MarketSession,
    timeframe_label: str,
    now: datetime | None = None,
) -> Freshness:
    """Freshness of a candle series, judged by whether its last bar is the current period."""
    now = now or datetime.now(UTC)
    interval = max(60.0, (bar_end - last_bar_open).total_seconds())
    if bar_end > now:
        if is_crypto or session.is_open:
            return Freshness(
                as_of=last_bar_open,
                age_seconds=0.0,
                status="real_time",
                label="Live bar",
                is_stale=False,
                detail=f"The latest {timeframe_label} bar is the current, still-forming period.",
            )
        return Freshness(
            as_of=last_bar_open,
            age_seconds=0.0,
            status="end_of_day",
            label="Last close",
            is_stale=False,
            detail=f"{session.label}. The latest {timeframe_label} bar holds the most recent session data.",
        )
    lag = (now - bar_end).total_seconds()
    lag_txt = _fmt_age(lag)
    if is_crypto or session.status == "always_open":
        if lag <= interval + REALTIME_MAX:
            return Freshness(
                as_of=last_bar_open,
                age_seconds=lag,
                status="real_time",
                label="Real-time",
                is_stale=False,
                detail=f"The latest {timeframe_label} bar closed {lag_txt} ago (no trades since, or the next bar has not been published yet).",
            )
        if lag <= interval + CRYPTO_DELAYED_MAX:
            return Freshness(
                as_of=last_bar_open,
                age_seconds=lag,
                status="delayed",
                label=f"Delayed {lag_txt}",
                is_stale=False,
                detail=f"The latest {timeframe_label} bar closed {lag_txt} ago.",
            )
        return Freshness(
            as_of=last_bar_open,
            age_seconds=lag,
            status="stale",
            label="Stale",
            is_stale=True,
            detail=f"No new {timeframe_label} bar for {lag_txt}; the market trades 24/7 so this indicates a data problem or no trading.",
        )
    if not session.is_open:
        if lag <= CLOSED_MAX:
            return Freshness(
                as_of=last_bar_open,
                age_seconds=lag,
                status="end_of_day",
                label="Last close",
                is_stale=False,
                detail=f"{session.label}. The latest {timeframe_label} bar closed {lag_txt} ago.",
            )
        return Freshness(
            as_of=last_bar_open,
            age_seconds=lag,
            status="stale",
            label="Stale",
            is_stale=True,
            detail=f"The latest {timeframe_label} bar closed {lag_txt} ago, longer than a normal market closure.",
        )
    if lag <= interval + REALTIME_MAX:
        return Freshness(
            as_of=last_bar_open,
            age_seconds=lag,
            status="real_time",
            label="Real-time",
            is_stale=False,
            detail=f"The latest {timeframe_label} bar closed {lag_txt} ago.",
        )
    if lag <= interval + EQUITY_DELAYED_MAX:
        return Freshness(
            as_of=last_bar_open,
            age_seconds=lag,
            status="delayed",
            label=f"Delayed {lag_txt}",
            is_stale=False,
            detail=f"Market is open but the latest {timeframe_label} bar closed {lag_txt} ago.",
        )
    return Freshness(
        as_of=last_bar_open,
        age_seconds=lag,
        status="stale",
        label="Stale",
        is_stale=True,
        detail=f"Market is open but no new {timeframe_label} bar for {lag_txt}.",
    )
