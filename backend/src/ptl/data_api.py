"""HTTP API for the data layer: Data Health and live quotes. Every payload carries provenance."""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel

from ptl.config import Settings
from ptl.data import repository
from ptl.data.freshness import dataset_staleness, live_quote_staleness
from ptl.data.live import (
    Clock,
    FeedInfo,
    InvalidSymbolError,
    NoQuoteError,
    NotConfiguredError,
    QuoteSource,
    QuoteSourceError,
)
from ptl.data.models import DatasetKind, LiveQuote, QualityIssue, Severity
from ptl.data.quality import check_live_quote
from ptl.db import open_db
from ptl.market_calendar import MarketCalendar
from ptl.provenance import DataType, Provenance

Status = Literal["ok", "warning", "error"]
_RANK: dict[str, int] = {"ok": 0, "warning": 1, "error": 2}


class IssueOut(BaseModel):
    check: str
    severity: Severity
    symbol: str
    count: int
    first_date: date
    last_date: date
    detail: str

    @classmethod
    def of(cls, issue: QualityIssue) -> "IssueOut":
        return cls(
            check=issue.check,
            severity=issue.severity,
            symbol=issue.symbol,
            count=issue.count,
            first_date=issue.first_date,
            last_date=issue.last_date,
            detail=issue.detail,
        )


class DatasetOut(BaseModel):
    id: int
    kind: DatasetKind
    file_name: str
    row_count: int
    symbol_count: int
    coverage_start: date
    coverage_end: date
    status: Status
    issues: list[IssueOut]
    provenance: Provenance


class FeedOut(BaseModel):
    name: str
    data_type: DataType
    note: str

    @classmethod
    def of(cls, feed: FeedInfo) -> "FeedOut":
        return cls(name=feed.name, data_type=feed.data_type, note=feed.note)


class LiveSourceOut(BaseModel):
    name: str
    status: Status
    configured: bool
    reachable: bool | None
    account_status: str | None
    error: str | None
    checked_at: AwareDatetime
    stock_feed: FeedOut
    option_feed: FeedOut


class DataHealthOut(BaseModel):
    overall: Literal["ok", "warning", "error", "no data"]
    as_of: AwareDatetime
    source: str
    market_open: bool
    last_completed_session: date
    live_source: LiveSourceOut
    datasets: list[DatasetOut]


class QuoteOut(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread: float
    bid_size: float
    ask_size: float
    provenance: Provenance
    issues: list[IssueOut]


def _issue_status(issues: list[QualityIssue], stale: bool) -> Status:
    if any(i.severity is Severity.ERROR for i in issues):
        return "error"
    if stale or any(i.severity is Severity.WARNING for i in issues):
        return "warning"
    return "ok"


def compute_data_health(
    settings: Settings, source: QuoteSource, calendar: MarketCalendar, now: datetime
) -> DataHealthOut:
    """The Data Health report. Shared by the API route and the game's Data Scout."""
    live = source.status()
    live_status: Status = (
        "ok" if live.reachable else ("warning" if not live.configured else "error")
    )
    datasets: list[DatasetOut] = []
    with open_db(settings.database_path) as conn:
        for record in repository.list_datasets(conn):
            issues = repository.issues_for(conn, record.id)
            staleness = dataset_staleness(
                record.coverage_end, now, calendar, settings.dataset_stale_after_sessions
            )
            datasets.append(
                DatasetOut(
                    id=record.id,
                    kind=record.kind,
                    file_name=record.file_name,
                    row_count=record.row_count,
                    symbol_count=record.symbol_count,
                    coverage_start=record.coverage_start,
                    coverage_end=record.coverage_end,
                    status=_issue_status(issues, staleness.stale),
                    issues=[IssueOut.of(i) for i in issues],
                    provenance=Provenance(
                        source=record.source,
                        as_of=record.imported_at,
                        data_type=record.data_type,
                        stale=staleness.stale,
                        stale_reason=staleness.reason,
                    ),
                )
            )

    statuses = [live_status, *(d.status for d in datasets)]
    overall: Literal["ok", "warning", "error", "no data"] = max(statuses, key=_RANK.__getitem__)
    if not datasets and not live.reachable:
        overall = "no data"
    return DataHealthOut(
        overall=overall,
        as_of=now,
        source="Paper Trading Lab data layer (local SQLite + live source check)",
        market_open=calendar.is_open(now),
        last_completed_session=calendar.last_completed_session(now),
        live_source=LiveSourceOut(
            name=live.name,
            status=live_status,
            configured=live.configured,
            reachable=live.reachable,
            account_status=live.account_status,
            error=live.error,
            checked_at=live.checked_at,
            stock_feed=FeedOut.of(live.stock_feed),
            option_feed=FeedOut.of(live.option_feed),
        ),
        datasets=datasets,
    )


def build_data_router(
    settings: Settings, source: QuoteSource, calendar: MarketCalendar, clock: Clock
) -> APIRouter:
    router = APIRouter()

    @router.get("/data/health")
    def data_health() -> DataHealthOut:
        return compute_data_health(settings, source, calendar, clock())

    def _respond(quote: LiveQuote, feed: FeedInfo) -> QuoteOut:
        staleness = live_quote_staleness(
            quote.timestamp,
            feed.data_type,
            clock(),
            calendar,
            timedelta(seconds=settings.real_time_max_age_seconds),
            timedelta(seconds=settings.delayed_max_age_seconds),
        )
        return QuoteOut(
            symbol=quote.symbol,
            bid=quote.bid,
            ask=quote.ask,
            spread=round(quote.ask - quote.bid, 6),
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
            provenance=Provenance(
                source=feed.name,
                as_of=quote.timestamp,
                data_type=feed.data_type,
                stale=staleness.stale,
                stale_reason=staleness.reason,
            ),
            issues=[IssueOut.of(i) for i in check_live_quote(quote)],
        )

    def _fetch(kind: Literal["stock", "option"], symbol: str) -> QuoteOut:
        try:
            if kind == "stock":
                return _respond(source.latest_stock_quote(symbol), source.stock_feed)
            return _respond(source.latest_option_quote(symbol), source.option_feed)
        except InvalidSymbolError as exc:
            raise HTTPException(422, str(exc)) from exc
        except NotConfiguredError as exc:
            raise HTTPException(503, f"No data: {exc}") from exc
        except NoQuoteError as exc:
            raise HTTPException(404, f"No data: {exc}") from exc
        except QuoteSourceError as exc:
            raise HTTPException(502, f"No data: {exc}") from exc

    @router.get("/quotes/stock/{symbol}")
    def stock_quote(symbol: str) -> QuoteOut:
        return _fetch("stock", symbol)

    @router.get("/quotes/option/{symbol}")
    def option_quote(symbol: str) -> QuoteOut:
        return _fetch("option", symbol)

    return router
