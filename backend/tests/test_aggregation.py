from __future__ import annotations

from datetime import UTC, datetime

from app.market.aggregation import aggregate, bucket_end, bucket_start, last_bar_complete_fixed
from app.models.market import Candle, Timeframe


def ts(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def c(t: str, o: float, h: float, low: float, cl: float, v: float | None = 1.0) -> Candle:
    return Candle(time=ts(t), open=o, high=h, low=low, close=cl, volume=v)


def test_15m_to_30m_ohlcv():
    src = [
        c("2024-03-04 10:00", 10, 12, 9, 11, 5),
        c("2024-03-04 10:15", 11, 15, 10, 14, 7),
        c("2024-03-04 10:30", 14, 14, 8, 9, 3),
        c("2024-03-04 10:45", 9, 10, 7, 8, 2),
    ]
    out = aggregate(src, Timeframe.M30)
    assert [x.time for x in out] == [ts("2024-03-04 10:00"), ts("2024-03-04 10:30")]
    assert (out[0].open, out[0].high, out[0].low, out[0].close, out[0].volume) == (10, 15, 9, 14, 12)
    assert (out[1].open, out[1].high, out[1].low, out[1].close, out[1].volume) == (14, 14, 7, 8, 5)


def test_leading_partial_bucket_is_dropped():
    # 4h buckets start at 00:00, 04:00 ... UTC; data starting at 02:00 cannot form a valid 00:00 bucket.
    src = [c(f"2024-03-04 {h:02d}:00", 1, 2, 0.5, 1.5) for h in range(2, 9)]
    out = aggregate(src, Timeframe.H4)
    assert out[0].time == ts("2024-03-04 04:00")
    assert len(out) == 2  # 04-08 (complete) and 08- (forming)


def test_missing_source_candles_are_not_invented():
    src = [c("2024-03-04 00:00", 10, 11, 9, 10), c("2024-03-04 02:00", 10, 13, 10, 12)]  # 01:00 and 03:00 missing
    out = aggregate(src, Timeframe.H4)
    assert len(out) == 1
    assert (out[0].high, out[0].close) == (13, 12)


def test_weekly_buckets_start_monday():
    src = [c(f"2024-03-{d:02d} 00:00", d, d + 1, d - 1, d) for d in range(4, 18)]  # Mon 4th .. Sun 17th
    out = aggregate(src, Timeframe.W1)
    assert [datetime.fromtimestamp(x.time, UTC).date().isoformat() for x in out] == ["2024-03-04", "2024-03-11"]
    assert out[0].open == 4 and out[0].close == 10 and out[0].high == 11 and out[0].low == 3


def test_monthly_buckets_and_end():
    src = [c("2024-01-01 00:00", 1, 2, 0.5, 1.5), c("2024-01-31 00:00", 1.5, 3, 1, 2), c("2024-02-01 00:00", 2, 2.5, 1.8, 2.2)]
    out = aggregate(src, Timeframe.MN1)
    assert len(out) == 2
    assert out[0].high == 3 and out[0].close == 2
    assert bucket_end(ts("2024-12-01 00:00"), Timeframe.MN1) == ts("2025-01-01 00:00")
    assert bucket_start(ts("2024-02-29 13:00"), Timeframe.MN1) == ts("2024-02-01 00:00")


def test_volume_none_when_unavailable():
    src = [c("2024-03-04 10:00", 1, 2, 0, 1, None), c("2024-03-04 10:15", 1, 2, 0, 1, None)]
    assert aggregate(src, Timeframe.M30)[0].volume is None


def test_last_bar_complete():
    candles = [c("2024-03-04 10:00", 1, 2, 0, 1)]
    assert last_bar_complete_fixed(candles, Timeframe.H1, ts("2024-03-04 11:00")) is True
    assert last_bar_complete_fixed(candles, Timeframe.H1, ts("2024-03-04 10:59")) is False
