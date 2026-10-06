"""Typed wrapper around the NYSE (XNYS) calendar from `exchange_calendars`.

All gap and staleness checks go through here so "is this a trading day?" has one answer.
"""

from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import pandas as pd

CALENDAR_START = date(1990, 1, 2)
NEW_YORK = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _xnys() -> Any:  # noqa: ANN401 - exchange_calendars is untyped; contained to this module
    return xc.get_calendar("XNYS", start=CALENDAR_START.isoformat())


def _to_utc(ts: Any) -> datetime:  # noqa: ANN401
    value = pd.Timestamp(ts).to_pydatetime()
    if not isinstance(value, datetime):  # pragma: no cover - defensive
        raise TypeError(f"expected datetime, got {type(value)!r}")
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


class MarketCalendar:
    """NYSE sessions. Dates outside the calendar's range are reported, never guessed."""

    @property
    def first_session(self) -> date:
        return _to_utc(_xnys().first_session).date()

    @property
    def last_session(self) -> date:
        return _to_utc(_xnys().last_session).date()

    def covers(self, day: date) -> bool:
        return self.first_session <= day <= self.last_session

    def is_session(self, day: date) -> bool:
        return self.covers(day) and bool(_xnys().is_session(day.isoformat()))

    def sessions(self, start: date, end: date) -> list[date]:
        """Sessions in [start, end], clipped to the calendar's range."""
        start, end = max(start, self.first_session), min(end, self.last_session)
        if start > end:
            return []
        index = _xnys().sessions_in_range(start.isoformat(), end.isoformat())
        return [_to_utc(ts).date() for ts in index]

    def is_open(self, at: datetime) -> bool:
        return bool(_xnys().is_open_on_minute(pd.Timestamp(at.astimezone(UTC))))

    def last_close(self, at: datetime) -> datetime:
        """Most recent session close strictly before `at`."""
        return _to_utc(_xnys().previous_close(pd.Timestamp(at.astimezone(UTC)).floor("min")))

    def last_completed_session(self, at: datetime) -> date:
        """Session date (New York) of the most recent close strictly before `at`."""
        return self.last_close(at).astimezone(NEW_YORK).date()
