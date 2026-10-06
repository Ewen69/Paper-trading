"""HTTP API for net worth. Data is manual entry or CSV import only, and never leaves this machine.

Amounts cross the API as dollars (two decimals); they are stored as integer cents.
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, Field

from ptl.config import Settings
from ptl.data.live import Clock
from ptl.db import open_db
from ptl.networth import compute, csvio, store
from ptl.networth.store import CATEGORIES, MANUAL_SOURCE, NetWorthError

SOURCE = "Your manual entries and CSV imports (local SQLite)"


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


class AccountOut(BaseModel):
    id: int
    name: str
    kind: Literal["asset", "liability"]
    category: str
    note: str
    latest_amount: float | None
    latest_as_of: date | None
    latest_source: str | None
    age_days: int | None
    stale: bool


class HistoryPointOut(BaseModel):
    as_of: date
    assets: float
    liabilities: float
    net_worth: float
    carried_forward: int
    missing: list[str]


class TotalsOut(BaseModel):
    assets: float
    liabilities: float
    net_worth: float
    as_of: date | None  # newest balance date included
    oldest_included: date | None  # oldest "latest balance" included
    missing: list[str]


class SummaryOut(BaseModel):
    as_of: AwareDatetime
    source: str
    data_type: Literal["manual"]
    stale_after_days: int
    categories: dict[str, list[str]]
    accounts: list[AccountOut]
    totals: TotalsOut
    history: list[HistoryPointOut]
    method: str


class AccountIn(BaseModel):
    name: str
    kind: str
    category: str
    note: str = ""


class BalanceIn(BaseModel):
    account_id: int
    as_of: date
    amount: float = Field(ge=0)


class BalanceOut(BaseModel):
    id: int
    account_id: int
    as_of: date
    amount: float
    source: str
    entered_at: AwareDatetime
    replaced: bool = False


class ImportIn(BaseModel):
    file_name: str
    content: str = Field(max_length=10_000_000)


class ImportOut(BaseModel):
    rows: int
    accounts_created: int
    inserted: int
    updated: int
    source: str


class ProjectionPointOut(BaseModel):
    year: int
    low: float
    high: float


class ProjectionOut(BaseModel):
    as_of: AwareDatetime
    source: str
    start: float
    start_as_of: date
    years: int
    low_rate: float
    high_rate: float
    contribution: float
    points: list[ProjectionPointOut]
    note: str


METHOD = (
    "Net worth = assets minus liabilities, using each account's latest balance on or before the "
    "date. Balances are carried forward until a newer one is entered, never interpolated. "
    "Accounts with no balance yet count as 0 and are listed as missing."
)


def _balance_out(b: store.Balance, replaced: bool = False) -> BalanceOut:
    return BalanceOut(
        id=b.id,
        account_id=b.account_id,
        as_of=b.as_of,
        amount=_dollars(b.amount_cents),
        source=b.source,
        entered_at=b.entered_at,
        replaced=replaced,
    )


def compute_summary(settings: Settings, now: datetime) -> SummaryOut:
    with open_db(settings.database_path) as conn:
        accounts = store.accounts(conn)
        balances = store.balances(conn)
    stale_days = settings.networth_stale_after_days
    latest = compute.latest_balances(accounts, balances, now.date(), stale_days)
    with_balance = [x for x in latest if x.balance is not None]

    def total(kind: str) -> int:
        return sum(
            x.balance.amount_cents for x in with_balance if x.account.kind == kind and x.balance
        )

    dates = [x.balance.as_of for x in with_balance if x.balance]
    return SummaryOut(
        as_of=now,
        source=SOURCE,
        data_type="manual",
        stale_after_days=stale_days,
        categories={k: list(v) for k, v in CATEGORIES.items()},
        accounts=[
            AccountOut(
                id=x.account.id,
                name=x.account.name,
                kind=x.account.kind,
                category=x.account.category,
                note=x.account.note,
                latest_amount=_dollars(x.balance.amount_cents) if x.balance else None,
                latest_as_of=x.balance.as_of if x.balance else None,
                latest_source=x.balance.source if x.balance else None,
                age_days=x.age_days,
                stale=x.stale,
            )
            for x in latest
        ],
        totals=TotalsOut(
            assets=_dollars(total("asset")),
            liabilities=_dollars(total("liability")),
            net_worth=_dollars(total("asset") - total("liability")),
            as_of=max(dates) if dates else None,
            oldest_included=min(dates) if dates else None,
            missing=[x.account.name for x in latest if x.balance is None],
        ),
        history=[
            HistoryPointOut(
                as_of=p.as_of,
                assets=_dollars(p.assets_cents),
                liabilities=_dollars(p.liabilities_cents),
                net_worth=_dollars(p.net_cents),
                carried_forward=p.carried_forward,
                missing=list(p.missing),
            )
            for p in compute.history(accounts, balances)
        ],
        method=METHOD,
    )


def _file_and_projection_routes(router: APIRouter, settings: Settings, clock: Clock) -> None:
    def fail(exc: Exception, status: int = 422) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    @router.post("/import")
    def import_file(body: ImportIn) -> ImportOut:
        with open_db(settings.database_path) as conn:
            try:
                r = csvio.import_csv(conn, body.content, body.file_name, clock())
            except csvio.CsvRejectedError as exc:
                raise fail(exc) from exc
        return ImportOut(
            rows=r.rows,
            accounts_created=r.accounts_created,
            inserted=r.inserted,
            updated=r.updated,
            source=r.source,
        )

    @router.get("/export")
    def export_file() -> Response:
        with open_db(settings.database_path) as conn:
            text = csvio.export_csv(conn)
        stamp = clock().date().isoformat()
        return Response(
            content=text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="net-worth-{stamp}.csv"'},
        )

    @router.get("/projection")
    def projection(
        years: int = Query(default=10),
        low: float = Query(default=0.04),
        high: float = Query(default=0.06),
        contribution: float = Query(default=0.0),
    ) -> ProjectionOut:
        now = clock()
        s = compute_summary(settings, now)
        if s.totals.as_of is None:
            raise HTTPException(status_code=422, detail="No balances yet, so nothing to project.")
        try:
            points = compute.project(
                round(s.totals.net_worth * 100),
                years=years,
                low=low,
                high=high,
                contribution_cents=round(contribution * 100),
            )
        except compute.ProjectionError as exc:
            raise fail(exc) from exc
        return ProjectionOut(
            as_of=now,
            source=f"{SOURCE}; growth rates are your assumptions",
            start=s.totals.net_worth,
            start_as_of=s.totals.as_of,
            years=years,
            low_rate=low,
            high_rate=high,
            contribution=contribution,
            points=[
                ProjectionPointOut(
                    year=p.year, low=_dollars(p.low_cents), high=_dollars(p.high_cents)
                )
                for p in points
            ],
            note=compute.PROJECTION_NOTE,
        )


def build_networth_router(settings: Settings, clock: Clock) -> APIRouter:
    router = APIRouter(prefix="/networth")

    def fail(exc: Exception, status: int = 422) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    @router.get("")
    def summary() -> SummaryOut:
        return compute_summary(settings, clock())

    @router.post("/accounts", status_code=201)
    def create_account(body: AccountIn) -> AccountOut:
        with open_db(settings.database_path) as conn:
            try:
                a = store.create_account(
                    conn,
                    name=body.name,
                    kind=body.kind,
                    category=body.category,
                    note=body.note,
                    now=clock(),
                )
            except NetWorthError as exc:
                raise fail(exc) from exc
        return AccountOut(
            id=a.id,
            name=a.name,
            kind=a.kind,
            category=a.category,
            note=a.note,
            latest_amount=None,
            latest_as_of=None,
            latest_source=None,
            age_days=None,
            stale=True,
        )

    @router.delete("/accounts/{account_id}", status_code=204)
    def delete_account(account_id: int) -> Response:
        with open_db(settings.database_path) as conn:
            if not store.delete_account(conn, account_id):
                raise HTTPException(status_code=404, detail="No such account.")
        return Response(status_code=204)

    @router.get("/accounts/{account_id}/balances")
    def account_balances(account_id: int) -> list[BalanceOut]:
        with open_db(settings.database_path) as conn:
            if store.account_by_id(conn, account_id) is None:
                raise HTTPException(status_code=404, detail="No such account.")
            return [_balance_out(b) for b in reversed(store.balances(conn, account_id))]

    @router.post("/balances", status_code=201)
    def add_balance(body: BalanceIn) -> BalanceOut:
        now = clock()
        with open_db(settings.database_path) as conn:
            if store.account_by_id(conn, body.account_id) is None:
                raise HTTPException(status_code=404, detail="No such account.")
            try:
                store.check_date(body.as_of, now.date())
                cents = store.to_cents(body.amount)
            except NetWorthError as exc:
                raise fail(exc) from exc
            with conn:
                balance, replaced = store.upsert_balance(
                    conn,
                    account_id=body.account_id,
                    as_of=body.as_of,
                    amount_cents=cents,
                    source=MANUAL_SOURCE,
                    now=now,
                )
        return _balance_out(balance, replaced)

    @router.delete("/balances/{balance_id}", status_code=204)
    def delete_balance(balance_id: int) -> Response:
        with open_db(settings.database_path) as conn:
            if not store.delete_balance(conn, balance_id):
                raise HTTPException(status_code=404, detail="No such balance.")
        return Response(status_code=204)

    _file_and_projection_routes(router, settings, clock)
    return router
