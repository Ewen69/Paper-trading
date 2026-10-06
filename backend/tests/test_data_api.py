"""Data Health and quote endpoints, wired with an in-test fake quote source."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ptl.app import create_app
from ptl.config import Settings
from ptl.data.csv_ingest import import_csv
from ptl.data.live import (
    FeedInfo,
    NotConfiguredError,
    SourceStatus,
    normalize_occ_symbol,
    normalize_stock_symbol,
)
from ptl.data.models import DatasetKind, LiveQuote
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.provenance import DataType
from tests.conftest import FIXTURES, MARKET_OPEN_NOW, SettingsFactory

STOCK_FEED = FeedInfo("Fake IEX", DataType.REAL_TIME, "test feed")
OPTION_FEED = FeedInfo("Fake indicative", DataType.DELAYED, "test feed")


class FakeSource:
    name = "Fake"
    stock_feed = STOCK_FEED
    option_feed = OPTION_FEED

    def __init__(self, *, configured: bool = True, quote_age: timedelta = timedelta(0)) -> None:
        self.configured = configured
        self.quote_age = quote_age

    def _quote(self, symbol: str) -> LiveQuote:
        if not self.configured:
            raise NotConfiguredError("keys not configured")
        return LiveQuote(symbol, 600.0, 600.02, 1, 2, MARKET_OPEN_NOW - self.quote_age)

    def latest_stock_quote(self, symbol: str) -> LiveQuote:
        return self._quote(normalize_stock_symbol(symbol))

    def latest_option_quote(self, occ_symbol: str) -> LiveQuote:
        return self._quote(normalize_occ_symbol(occ_symbol))

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            configured=self.configured,
            reachable=True if self.configured else None,
            account_status="ACTIVE" if self.configured else None,
            error=None if self.configured else "not configured",
            checked_at=MARKET_OPEN_NOW,
            stock_feed=STOCK_FEED,
            option_feed=OPTION_FEED,
        )


def _client(settings: Settings, source: FakeSource, now: datetime = MARKET_OPEN_NOW) -> TestClient:
    app = create_app(settings, quote_source=source, calendar=MarketCalendar(), clock=lambda: now)
    return TestClient(app)


def test_empty_and_unconfigured_reports_no_data(make_settings: SettingsFactory) -> None:
    body = _client(make_settings(), FakeSource(configured=False)).get("/data/health").json()
    assert body["overall"] == "no data"
    assert body["datasets"] == []
    assert body["live_source"]["status"] == "warning"
    assert body["live_source"]["configured"] is False
    assert body["market_open"] is True
    assert body["last_completed_session"] == "2025-07-03"


def test_health_lists_datasets_with_provenance_and_staleness(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings()
    conn = connect(settings.database_path)
    migrate(conn)
    import_csv(
        conn,
        FIXTURES / "synthetic_equity_bars.csv",
        DatasetKind.EQUITY_BARS,
        "Test vendor",
        MarketCalendar(),
        MARKET_OPEN_NOW,
    )
    conn.close()

    body = _client(settings, FakeSource()).get("/data/health").json()
    [dataset] = body["datasets"]
    assert dataset["row_count"] == 6
    assert dataset["provenance"]["source"] == "Test vendor"
    assert dataset["provenance"]["data_type"] == "end-of-day"
    assert dataset["provenance"]["stale"] is True  # data ends Jan 2025, "now" is July 2025
    assert "sessions before the last completed session" in dataset["provenance"]["stale_reason"]
    assert dataset["status"] == "warning"
    assert body["overall"] == "warning"


def test_stock_quote_has_provenance(make_settings: SettingsFactory) -> None:
    body = _client(make_settings(), FakeSource()).get("/quotes/stock/spy").json()
    assert (body["symbol"], body["bid"], body["ask"]) == ("SPY", 600.0, 600.02)
    assert body["spread"] == pytest.approx(0.02)
    assert body["provenance"]["source"] == "Fake IEX"
    assert body["provenance"]["data_type"] == "real-time"
    assert body["provenance"]["stale"] is False
    assert body["issues"] == []
    assert "mid" not in body


def test_old_quote_is_flagged_stale(make_settings: SettingsFactory) -> None:
    source = FakeSource(quote_age=timedelta(minutes=10))
    body = _client(make_settings(), source).get("/quotes/stock/SPY").json()
    assert body["provenance"]["stale"] is True


def test_option_quote_uses_option_feed(make_settings: SettingsFactory) -> None:
    body = _client(make_settings(), FakeSource()).get("/quotes/option/SPY251219C00600000").json()
    assert body["provenance"]["data_type"] == "delayed"


@pytest.mark.parametrize(
    ("path", "configured", "status"),
    [
        ("/quotes/stock/SPY", False, 503),
        ("/quotes/stock/not a symbol", True, 422),
        ("/quotes/option/SPY", True, 422),
    ],
)
def test_quote_errors_say_no_data(
    make_settings: SettingsFactory, path: str, configured: bool, status: int
) -> None:
    response = _client(make_settings(), FakeSource(configured=configured)).get(path)
    assert response.status_code == status
    assert "detail" in response.json()
