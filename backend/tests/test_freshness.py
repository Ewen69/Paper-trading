from datetime import UTC, date, datetime, timedelta

from ptl.data.freshness import dataset_staleness, live_quote_staleness
from ptl.market_calendar import MarketCalendar
from ptl.provenance import DataType
from tests.conftest import MARKET_OPEN_NOW, WEEKEND_NOW

RT = timedelta(seconds=120)
DELAYED = timedelta(minutes=30)


def _live(ts: datetime, now: datetime, dt: DataType, cal: MarketCalendar) -> bool:
    return live_quote_staleness(ts, dt, now, cal, RT, DELAYED).stale


def test_open_market_uses_max_age_per_data_type(calendar: MarketCalendar) -> None:
    now = MARKET_OPEN_NOW
    assert not _live(now - timedelta(seconds=60), now, DataType.REAL_TIME, calendar)
    assert _live(now - timedelta(seconds=180), now, DataType.REAL_TIME, calendar)
    assert not _live(now - timedelta(minutes=20), now, DataType.DELAYED, calendar)
    assert _live(now - timedelta(minutes=40), now, DataType.DELAYED, calendar)


def test_closed_market_compares_to_last_close(calendar: MarketCalendar) -> None:
    last_close = datetime(2025, 7, 3, 17, 0, tzinfo=UTC)
    assert not _live(last_close - timedelta(seconds=30), WEEKEND_NOW, DataType.REAL_TIME, calendar)
    result = live_quote_staleness(
        last_close - timedelta(hours=3), DataType.REAL_TIME, WEEKEND_NOW, calendar, RT, DELAYED
    )
    assert result.stale
    assert result.reason is not None
    assert "3.0 h older than the last close" in result.reason


def test_dataset_staleness_counts_sessions(calendar: MarketCalendar) -> None:
    # Last completed session before WEEKEND_NOW is 2025-07-03.
    assert not dataset_staleness(date(2025, 7, 3), WEEKEND_NOW, calendar, 5).stale
    assert not dataset_staleness(date(2025, 6, 26), WEEKEND_NOW, calendar, 5).stale  # 5 behind
    stale = dataset_staleness(date(2025, 6, 25), WEEKEND_NOW, calendar, 5)  # 6 behind
    assert stale.stale
    assert stale.reason is not None
    assert "6 sessions" in stale.reason
