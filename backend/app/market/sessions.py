"""Trading-session status.

US equities follow the NYSE/Nasdaq schedule (pre-market 04:00-09:30, regular
09:30-16:00, after-hours 16:00-20:00 America/New_York, Monday-Friday). Exchange holidays
and early closes are detected through the provider's ``is_market_open`` flag when it is
available. Other exchanges rely on the provider flag only. Crypto trades 24/7. Spot FX
trades from Sunday 17:00 to Friday 17:00 New York time.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.models.market import AssetType, MarketSession

NEW_YORK = ZoneInfo("America/New_York")
US_TIMEZONES = {"America/New_York"}

PRE_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTER_CLOSE = time(20, 0)


def us_schedule_phase(now: datetime) -> str:
    local = now.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        return "closed"
    t = local.time()
    if PRE_OPEN <= t < REGULAR_OPEN:
        return "pre_market"
    if REGULAR_OPEN <= t < REGULAR_CLOSE:
        return "open"
    if REGULAR_CLOSE <= t < AFTER_CLOSE:
        return "after_hours"
    return "closed"


def forex_open(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK)
    wd, t = local.weekday(), local.time()
    if wd == 5:
        return False
    if wd == 6:
        return t >= time(17, 0)
    if wd == 4:
        return t < time(17, 0)
    return True


def market_session(
    asset_type: AssetType,
    timezone: str,
    now: datetime | None = None,
    provider_open_flag: bool | None = None,
) -> MarketSession:
    now = now or datetime.now(UTC)
    if asset_type is AssetType.CRYPTO:
        return MarketSession(status="always_open", label="24/7 market", timezone="UTC", is_open=True)
    if asset_type is AssetType.FOREX:
        is_open = provider_open_flag if provider_open_flag is not None else forex_open(now)
        return MarketSession(
            status="open" if is_open else "closed",
            label="FX market open" if is_open else "FX market closed (weekend)",
            timezone="America/New_York",
            is_open=is_open,
        )
    if timezone in US_TIMEZONES:
        phase = us_schedule_phase(now)
        if provider_open_flag is True:
            return MarketSession(status="open", label="Market open", timezone=timezone, is_open=True)
        if phase == "open":
            if provider_open_flag is False:
                return MarketSession(
                    status="closed",
                    label="Market closed",
                    timezone=timezone,
                    is_open=False,
                    note="Closed during regular hours (exchange holiday or early close).",
                )
            return MarketSession(
                status="open",
                label="Market open",
                timezone=timezone,
                is_open=True,
                note="Based on the regular schedule; holidays are not verified.",
            )
        if phase == "pre_market":
            return MarketSession(
                status="pre_market",
                label="Pre-market",
                timezone=timezone,
                is_open=False,
                note="Regular session opens 09:30 ET. Extended-hours prices are not included in the data shown.",
            )
        if phase == "after_hours":
            return MarketSession(
                status="after_hours",
                label="After hours",
                timezone=timezone,
                is_open=False,
                note="Regular session closed at 16:00 ET. Extended-hours prices are not included in the data shown.",
            )
        return MarketSession(status="closed", label="Market closed", timezone=timezone, is_open=False)
    if provider_open_flag is not None:
        return MarketSession(
            status="open" if provider_open_flag else "closed",
            label="Market open" if provider_open_flag else "Market closed",
            timezone=timezone,
            is_open=provider_open_flag,
        )
    return MarketSession(status="unknown", label="Session unknown", timezone=timezone, is_open=False)


def equity_market_likely_closed(timezone: str, now: datetime | None = None) -> bool:
    """Used to lengthen cache TTLs (and save API credits) when no new bars can arrive."""
    if timezone not in US_TIMEZONES:
        return False
    return us_schedule_phase(now or datetime.now(UTC)) == "closed"
