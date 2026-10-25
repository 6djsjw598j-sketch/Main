"""Timeframe aggregation (e.g. 1h -> 4h, 1d -> 1w) and bar-completeness helpers.

Rules:

* Fixed-length buckets (30m, 4h) are aligned to the UNIX epoch, i.e. to UTC midnight.
  Weekly buckets start Monday 00:00 UTC; monthly buckets start on the 1st 00:00 UTC.
* open = first open, high = max high, low = min low, close = last close, volume = sum.
* A leading bucket whose first source candle does not start at the bucket boundary is
  dropped: the fetch window began mid-bucket, so its open/high/low would be wrong.
* Missing source candles (e.g. intervals without trades) are never invented; the bucket
  is built from the candles that exist.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.models.market import Candle, Timeframe


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def bucket_start(ts: int, target: Timeframe) -> int:
    seconds = target.seconds
    if target is Timeframe.W1:
        d = datetime.fromtimestamp(ts, UTC).date()
        return _epoch(d - timedelta(days=d.weekday()))
    if target is Timeframe.MN1:
        d = datetime.fromtimestamp(ts, UTC).date()
        return _epoch(d.replace(day=1))
    assert seconds is not None
    return ts - ts % seconds


def bucket_end(start: int, target: Timeframe) -> int:
    if target is Timeframe.MN1:
        d = datetime.fromtimestamp(start, UTC).date()
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return _epoch(nxt)
    assert target.seconds is not None
    return start + target.seconds


def aggregate(candles: list[Candle], target: Timeframe) -> list[Candle]:
    """Aggregate ascending, de-duplicated candles into ``target`` buckets."""
    groups: list[tuple[int, list[Candle]]] = []
    for candle in candles:
        key = bucket_start(candle.time, target)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(candle)
        else:
            groups.append((key, [candle]))
    if groups and groups[0][1][0].time != groups[0][0]:
        groups = groups[1:]  # fetch window started mid-bucket
    out: list[Candle] = []
    for key, members in groups:
        volumes = [m.volume for m in members if m.volume is not None]
        out.append(
            Candle(
                time=key,
                open=members[0].open,
                high=max(m.high for m in members),
                low=min(m.low for m in members),
                close=members[-1].close,
                volume=sum(volumes) if volumes else None,
            )
        )
    return out


def last_bar_complete_fixed(candles: list[Candle], timeframe: Timeframe, now: float) -> bool:
    if not candles:
        return True
    return bucket_end(candles[-1].time, timeframe) <= now
