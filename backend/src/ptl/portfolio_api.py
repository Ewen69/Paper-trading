"""HTTP API for holdings and portfolio analysis.

Holdings stay local; paper positions are read-only.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, Field

from ptl.config import Settings
from ptl.data.live import Clock
from ptl.db import open_db
from ptl.market_calendar import MarketCalendar
from ptl.paper.broker import Broker
from ptl.portfolio import holdings as hstore
from ptl.portfolio import service
from ptl.portfolio.service import Book

MIN_WINDOW = 20
MAX_WINDOW = 2520
SOURCE = "Your holdings (local SQLite), imported prices and quotes, and the Alpaca paper account"
METHOD = (
    "Risk replays today's weights over past daily prices (constant weights, rebalanced daily). "
    "It describes how the current mix would have moved, not how your account did, and past "
    "behavior doesn't predict future results. Options are excluded from the replay; cash earns "
    "0. Concentration uses non-cash long positions grouped by symbol."
)


class GreeksOut(BaseModel):
    delta: float
    theta: float
    vega: float


class PositionOut(BaseModel):
    key: str
    book: Literal["manual", "paper"]
    holding_id: int | None
    symbol: str
    label: str
    asset_class: str
    account: str
    quantity: float
    price: float | None
    price_source: str | None
    price_as_of: date | None
    stale: bool
    stale_reason: str | None
    value: float | None
    weight: float | None
    greeks: GreeksOut | None
    notes: list[str]


class AllocationOut(BaseModel):
    asset_class: str
    value: float
    weight: float


class ConcentrationOut(BaseModel):
    hhi: float
    effective_n: float
    top: list[tuple[str, float]]
    top5_weight: float


class RiskOut(BaseModel):
    start: date
    end: date
    returns: int
    volatility: float
    max_drawdown: float
    benchmark_volatility: float | None
    beta: float | None
    correlation: float | None


class TipOut(BaseModel):
    id: str
    title: str
    status: Literal["fired", "clear", "not_evaluated"]
    observed: str
    threshold: str
    detail: str


class PaperStatusOut(BaseModel):
    status: Literal["included", "not_requested", "not_configured", "error"]
    detail: str


class AnalysisOut(BaseModel):
    as_of: AwareDatetime
    source: str
    book: Book
    benchmark: str
    window: int
    paper: PaperStatusOut
    positions: list[PositionOut]
    total_value: float
    long_value: float
    allocation: list[AllocationOut]
    concentration: ConcentrationOut | None
    risk: RiskOut | None
    risk_note: str
    risk_included: list[str]
    risk_excluded: list[str]
    coverage: float | None
    price_basis: str
    greeks_total: GreeksOut | None
    greeks_missing: list[str]
    tips: list[TipOut]
    method: str


class HoldingBody(BaseModel):
    symbol: str
    asset_class: str
    quantity: float
    account: str = ""
    option_type: str | None = None
    strike: float | None = None
    expiration: date | None = None
    manual_price: float | None = None
    manual_price_as_of: date | None = None
    note: str = ""


class HoldingOut(HoldingBody):
    id: int
    created_at: AwareDatetime


class ImportBody(BaseModel):
    content: str = Field(max_length=5_000_000)
    replace: bool = False


class ImportOut(BaseModel):
    imported: int
    removed: int


def _holding_out(h: hstore.Holding) -> HoldingOut:
    return HoldingOut(
        id=h.id,
        created_at=h.created_at,
        symbol=h.symbol,
        asset_class=h.asset_class,
        quantity=h.quantity,
        account=h.account,
        option_type=h.option_type,
        strike=h.strike,
        expiration=h.expiration,
        manual_price=h.manual_price,
        manual_price_as_of=h.manual_price_as_of,
        note=h.note,
    )


def _greeks(g: service.Greeks | None) -> GreeksOut | None:
    return None if g is None else GreeksOut(delta=g.delta, theta=g.theta, vega=g.vega)


def _analysis_out(
    a: service.Analysis,
    *,
    now: datetime,
    benchmark: str,
    window: int,
    paper: PaperStatusOut,
) -> AnalysisOut:
    total = a.total_value
    return AnalysisOut(
        as_of=now,
        source=SOURCE,
        book=a.book,
        benchmark=benchmark,
        window=window,
        paper=paper,
        positions=[
            PositionOut(
                key=p.key,
                book=p.book,
                holding_id=p.holding_id,
                symbol=p.symbol,
                label=p.label,
                asset_class=p.asset_class,
                account=p.account,
                quantity=p.quantity,
                price=p.price,
                price_source=p.price_source,
                price_as_of=p.price_as_of,
                stale=p.stale,
                stale_reason=p.stale_reason,
                value=p.value,
                weight=(p.value / total) if p.value is not None and total else None,
                greeks=_greeks(p.greeks),
                notes=list(p.notes),
            )
            for p in a.positions
        ],
        total_value=total,
        long_value=a.long_value,
        allocation=[AllocationOut(asset_class=k, value=v, weight=w) for k, v, w in a.allocation],
        concentration=(
            None
            if a.concentration is None
            else ConcentrationOut(
                hhi=a.concentration.hhi,
                effective_n=a.concentration.effective_n,
                top=list(a.concentration.top),
                top5_weight=a.concentration.top5_weight,
            )
        ),
        risk=(
            None
            if a.risk is None
            else RiskOut(
                start=a.risk.start,
                end=a.risk.end,
                returns=a.risk.returns,
                volatility=a.risk.volatility,
                max_drawdown=a.risk.max_drawdown,
                benchmark_volatility=a.risk.benchmark_volatility,
                beta=a.risk.beta,
                correlation=a.risk.correlation,
            )
        ),
        risk_note=a.risk_note,
        risk_included=a.risk_included,
        risk_excluded=a.risk_excluded,
        coverage=a.coverage,
        price_basis=a.price_basis,
        greeks_total=_greeks(a.greeks_total),
        greeks_missing=a.greeks_missing,
        tips=[
            TipOut(
                id=t.id,
                title=t.title,
                status=t.status,
                observed=t.observed,
                threshold=t.threshold,
                detail=t.detail,
            )
            for t in a.tips
        ],
        method=METHOD,
    )


def build_portfolio_router(
    settings: Settings,
    calendar: MarketCalendar,
    clock: Clock,
    brokers: Callable[[bool], Broker],
) -> APIRouter:
    router = APIRouter(prefix="/portfolio")

    def paper_positions(book: Book, now: datetime) -> tuple[list[service.Position], PaperStatusOut]:
        if book == "manual":
            return [], PaperStatusOut(status="not_requested", detail="Manual holdings only.")
        if not settings.broker_credentials_configured:
            return [], PaperStatusOut(
                status="not_configured",
                detail="No data: Alpaca paper API keys are not set in .env.",
            )
        try:
            positions = brokers(True).positions()  # dry-run broker: read-only account access
        except Exception as exc:
            return [], PaperStatusOut(
                status="error", detail=f"No data: paper account unreachable ({type(exc).__name__})."
            )
        return service.paper_positions(positions, now), PaperStatusOut(
            status="included",
            detail=f"{len(positions)} position(s) from the Alpaca paper account (simulated money).",
        )

    @router.get("")
    def analysis(book: Book = "manual", benchmark: str = "", window: int = 0) -> AnalysisOut:
        now = clock()
        bench = (benchmark or settings.portfolio_benchmark).strip().upper()
        win = window or settings.portfolio_window_sessions
        if not MIN_WINDOW <= win <= MAX_WINDOW:
            raise HTTPException(
                status_code=422,
                detail=f"Window must be {MIN_WINDOW} to {MAX_WINDOW} sessions.",
            )
        ctx = service.PricingContext(
            now=now,
            calendar=calendar,
            stale_after_sessions=settings.dataset_stale_after_sessions,
            manual_stale_after_days=settings.networth_stale_after_days,
        )
        paper, paper_status = paper_positions(book, now)
        with open_db(settings.database_path) as conn:
            manual = (
                [service.value_holding(conn, h, ctx) for h in hstore.holdings(conn)]
                if book != "paper"
                else []
            )
            result = service.analyze(
                conn, [*manual, *paper], book=book, benchmark=bench, window=win
            )
        return _analysis_out(result, now=now, benchmark=bench, window=win, paper=paper_status)

    @router.get("/holdings")
    def list_holdings() -> list[HoldingOut]:
        with open_db(settings.database_path) as conn:
            return [_holding_out(h) for h in hstore.holdings(conn)]

    @router.post("/holdings", status_code=201)
    def add_holding(body: HoldingBody) -> HoldingOut:
        with open_db(settings.database_path) as conn:
            try:
                h = hstore.add_holding(conn, hstore.HoldingIn(**body.model_dump()), clock())
            except hstore.HoldingError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _holding_out(h)

    @router.delete("/holdings/{holding_id}", status_code=204)
    def delete_holding(holding_id: int) -> Response:
        with open_db(settings.database_path) as conn:
            if not hstore.delete_holding(conn, holding_id):
                raise HTTPException(status_code=404, detail="No such holding.")
        return Response(status_code=204)

    @router.post("/import")
    def import_holdings(body: ImportBody) -> ImportOut:
        with open_db(settings.database_path) as conn:
            try:
                imported, removed = hstore.import_csv(
                    conn, body.content, replace=body.replace, now=clock()
                )
            except hstore.CsvRejectedError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ImportOut(imported=imported, removed=removed)

    @router.get("/export")
    def export_holdings() -> Response:
        with open_db(settings.database_path) as conn:
            text = hstore.export_csv(conn)
        stamp = clock().date().isoformat()
        return Response(
            content=text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="holdings-{stamp}.csv"'},
        )

    return router
