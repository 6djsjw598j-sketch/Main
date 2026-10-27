"""Exchange-time helpers. US options trade on New York time."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def to_et(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetime; pass an aware timestamp")
    return moment.astimezone(ET)


def et_date(moment: datetime) -> date:
    return to_et(moment).date()


def dte(expiration: date, today: date) -> int:
    return (expiration - today).days


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7  # Friday is weekday 4
    return first + timedelta(days=offset + 14)


def is_monthly_expiration(day: date) -> bool:
    """Standard monthly expiry: the third Friday, or the Thursday before it when Friday is a holiday."""
    tf = third_friday(day.year, day.month)
    return day == tf or day == tf - timedelta(days=1)


def years_to_expiry(expiration: date, now: datetime) -> float:
    """Time to the 16:00 ET close on expiration day, in years (floored at ~1 hour)."""
    close = datetime.combine(expiration, datetime.min.time(), tzinfo=ET).replace(hour=16)
    seconds = (close - to_et(now)).total_seconds()
    return max(seconds, 3600.0) / (365.0 * 24 * 3600)
