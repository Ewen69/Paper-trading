from datetime import UTC, date, datetime

from ptl.market_calendar import MarketCalendar
from tests.conftest import MARKET_OPEN_NOW, WEEKEND_NOW


def test_known_holidays_and_special_closures(calendar: MarketCalendar) -> None:
    assert not calendar.is_session(date(2025, 1, 1))  # New Year's Day
    assert not calendar.is_session(date(2025, 1, 9))  # National Day of Mourning (Carter)
    assert not calendar.is_session(date(2025, 7, 4))  # Independence Day
    assert calendar.is_session(date(2025, 1, 8))


def test_sessions_skip_closures(calendar: MarketCalendar) -> None:
    assert calendar.sessions(date(2025, 1, 8), date(2025, 1, 13)) == [
        date(2025, 1, 8),
        date(2025, 1, 10),
        date(2025, 1, 13),
    ]


def test_sessions_are_clipped_to_calendar_range(calendar: MarketCalendar) -> None:
    assert calendar.sessions(date(1980, 1, 1), date(1980, 12, 31)) == []
    assert not calendar.covers(date(1980, 6, 2))


def test_open_and_last_close(calendar: MarketCalendar) -> None:
    assert calendar.is_open(MARKET_OPEN_NOW)
    assert not calendar.is_open(WEEKEND_NOW)
    # 2025-07-03 was an early close at 13:00 New York = 17:00 UTC.
    assert calendar.last_close(WEEKEND_NOW) == datetime(2025, 7, 3, 17, 0, tzinfo=UTC)
    assert calendar.last_completed_session(WEEKEND_NOW) == date(2025, 7, 3)
    assert calendar.last_completed_session(MARKET_OPEN_NOW) == date(2025, 7, 3)
