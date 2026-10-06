from dataclasses import replace
from datetime import UTC, date, datetime

from ptl.data.models import EquityBar, LiveQuote, OptionQuote, QualityIssue, Severity
from ptl.data.quality import (
    REPEATED_QUOTE_RUN,
    check_equity_bars,
    check_live_quote,
    check_option_quotes,
)
from ptl.market_calendar import MarketCalendar


def _bar(day: date, close: float = 100.0, **changes: object) -> EquityBar:
    bar = EquityBar("SPY", day, open=close, high=close + 1, low=close - 1, close=close, volume=10)
    return replace(bar, **changes)  # type: ignore[arg-type]


def _quote(day: date, bid: float = 1.0, ask: float = 1.1, **changes: object) -> OptionQuote:
    q = OptionQuote("SPY", day, date(2025, 1, 17), 585.0, "call", bid=bid, ask=ask)
    return replace(q, **changes)  # type: ignore[arg-type]


def _by_check(issues: list[QualityIssue]) -> dict[str, QualityIssue]:
    return {i.check: i for i in issues}


CLEAN_DAYS = [date(2025, 1, d) for d in (2, 3, 6, 7, 8, 10)]  # Jan 9 2025: market closed


def test_clean_bars_have_no_issues(calendar: MarketCalendar) -> None:
    assert check_equity_bars([_bar(d) for d in CLEAN_DAYS], calendar) == []


def test_missing_sessions_are_counted_and_located(calendar: MarketCalendar) -> None:
    days = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 8), date(2025, 1, 10)]
    issue = _by_check(check_equity_bars([_bar(d) for d in days], calendar))["missing_sessions"]
    assert issue.count == 2  # Jan 6 and Jan 7 (Jan 9 was a closure, not a gap)
    assert (issue.first_date, issue.last_date) == (date(2025, 1, 6), date(2025, 1, 7))
    assert issue.severity is Severity.WARNING


def test_bar_level_checks(calendar: MarketCalendar) -> None:
    bars = [
        _bar(date(2025, 1, 2)),
        _bar(date(2025, 1, 3), high=98.0),  # high below close
        _bar(date(2025, 1, 6), volume=0),
        _bar(date(2025, 1, 7), close=200.0, open=200.0, high=201.0, low=199.0),  # +100%
        _bar(date(2025, 1, 8), close=-1.0),
        _bar(date(2025, 1, 11)),  # Saturday
    ]
    found = _by_check(check_equity_bars(bars, calendar))
    assert found["inconsistent_ohlc"].severity is Severity.ERROR
    assert found["inconsistent_ohlc"].first_date == date(2025, 1, 3)
    assert found["zero_volume"].severity is Severity.WARNING
    assert found["extreme_move"].first_date == date(2025, 1, 7)
    assert found["non_positive_price"].severity is Severity.ERROR
    assert found["non_session_date"].first_date == date(2025, 1, 11)


def test_errors_sort_before_warnings(calendar: MarketCalendar) -> None:
    bars = [_bar(date(2025, 1, 2), volume=0), _bar(date(2025, 1, 3), high=98.0)]
    severities = [i.severity for i in check_equity_bars(bars, calendar)]
    assert severities == sorted(severities, key=[Severity.ERROR, Severity.WARNING].index)


def test_clean_option_quotes(calendar: MarketCalendar) -> None:
    quotes = [_quote(date(2025, 1, 2)), _quote(date(2025, 1, 3), bid=1.2, ask=1.3)]
    assert check_option_quotes(quotes, calendar) == []


def test_option_market_checks(calendar: MarketCalendar) -> None:
    quotes = [
        _quote(date(2025, 1, 2), bid=1.5, ask=1.2),  # crossed
        _quote(date(2025, 1, 2), bid=0.5, ask=0.5, strike=600.0),  # locked
        _quote(date(2025, 1, 2), bid=0.0, ask=0.05, strike=700.0),  # zero bid
        _quote(date(2025, 1, 2), bid=0.0, ask=0.0, strike=800.0),  # no offer
        _quote(date(2025, 1, 21), expiration=date(2025, 1, 17), strike=500.0),  # after expiry
    ]
    found = _by_check(check_option_quotes(quotes, calendar))
    assert found["crossed_market"].severity is Severity.ERROR
    assert found["locked_market"].severity is Severity.WARNING
    assert found["zero_bid"].severity is Severity.INFO
    assert found["no_offer"].severity is Severity.WARNING
    assert found["quote_after_expiration"].severity is Severity.ERROR
    assert "missing_sessions" in found  # Jan 3..Jan 17 have no quotes at all


def test_repeated_quote_run_is_flagged_once(calendar: MarketCalendar) -> None:
    days = calendar.sessions(date(2025, 1, 2), date(2025, 1, 31))[: REPEATED_QUOTE_RUN + 2]
    found = _by_check(check_option_quotes([_quote(d) for d in days], calendar))
    assert found["repeated_quote"].count == 1
    assert found["repeated_quote"].first_date == days[REPEATED_QUOTE_RUN - 1]


def test_short_repeats_are_not_flagged(calendar: MarketCalendar) -> None:
    days = calendar.sessions(date(2025, 1, 2), date(2025, 1, 31))[: REPEATED_QUOTE_RUN - 1]
    assert check_option_quotes([_quote(d) for d in days], calendar) == []


def test_live_quote_checks() -> None:
    ts = datetime(2025, 7, 7, 15, 0, tzinfo=UTC)
    crossed = LiveQuote("SPY", bid=10.0, ask=9.0, bid_size=1, ask_size=1, timestamp=ts)
    assert [i.check for i in check_live_quote(crossed)] == ["crossed_market"]
    one_sided = LiveQuote("SPY", bid=0.0, ask=9.0, bid_size=0, ask_size=1, timestamp=ts)
    assert [i.check for i in check_live_quote(one_sided)] == ["no_bid"]
    good = LiveQuote("SPY", bid=9.0, ask=9.01, bid_size=1, ask_size=1, timestamp=ts)
    assert check_live_quote(good) == []
