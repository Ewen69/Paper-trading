"""Staleness rules. Stale data is still shown, but always with the flag and the reason."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ptl.market_calendar import MarketCalendar
from ptl.provenance import DataType


@dataclass(frozen=True, slots=True)
class Staleness:
    stale: bool
    reason: str | None = None


FRESH = Staleness(stale=False)


def _age(seconds: float) -> str:
    if seconds < 120:  # noqa: PLR2004
        return f"{seconds:.0f}s"
    if seconds < 7200:  # noqa: PLR2004
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def live_quote_staleness(  # noqa: PLR0913, PLR0917
    timestamp: datetime,
    data_type: DataType,
    now: datetime,
    calendar: MarketCalendar,
    real_time_max_age: timedelta,
    delayed_max_age: timedelta,
) -> Staleness:
    """Market open: stale if older than the max age for its data type.
    Market closed: stale if it wasn't updated within the max age of the last close.
    """
    max_age = real_time_max_age if data_type is DataType.REAL_TIME else delayed_max_age
    if calendar.is_open(now):
        age = (now - timestamp).total_seconds()
        if age > max_age.total_seconds():
            return Staleness(
                True, f"Quote is {_age(age)} old (limit {_age(max_age.total_seconds())})."
            )
        return FRESH
    last_close = calendar.last_close(now)
    if timestamp < last_close - max_age:
        behind = (last_close - timestamp).total_seconds()
        return Staleness(
            True,
            f"Market closed. The last quote is {_age(behind)} older than the last close "
            f"({last_close.isoformat()}).",
        )
    return FRESH


def dataset_staleness(
    coverage_end: date, now: datetime, calendar: MarketCalendar, stale_after_sessions: int
) -> Staleness:
    """A dataset is stale when its last date trails the last completed session."""
    last_session = calendar.last_completed_session(now)
    if coverage_end >= last_session:
        return FRESH
    behind = len([s for s in calendar.sessions(coverage_end, last_session) if s > coverage_end])
    if behind > stale_after_sessions:
        return Staleness(
            True,
            f"Data ends {coverage_end}, which is {behind} sessions before the last completed "
            f"session ({last_session}).",
        )
    return FRESH
