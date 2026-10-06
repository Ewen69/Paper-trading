"""HTTP API for the backtester (Strategy Lab)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, Field

from ptl.backtest import repository as repo
from ptl.backtest import service
from ptl.backtest.models import MAX_SLIPPAGE_BPS, CostModel
from ptl.backtest.report import BacktestReport
from ptl.backtest.strategies import STRATEGIES
from ptl.config import Settings
from ptl.data.live import Clock
from ptl.db import open_db
from ptl.market_calendar import MarketCalendar


class ParamOut(BaseModel):
    name: str
    label: str
    default: int
    minimum: int
    maximum: int
    description: str


class StrategyOut(BaseModel):
    id: str
    name: str
    description: str
    params: list[ParamOut]


class LockOut(BaseModel):
    symbol: str
    oos_start: date
    oos_fraction: float
    locked_at: AwareDatetime
    basis: str

    @classmethod
    def of(cls, lock: repo.OosLock) -> "LockOut":
        return cls(
            symbol=lock.symbol,
            oos_start=lock.oos_start,
            oos_fraction=lock.oos_fraction,
            locked_at=lock.locked_at,
            basis=lock.basis,
        )


class UniverseEntryOut(BaseModel):
    dataset_id: int
    symbol: str
    source: str
    file_name: str
    first_date: date
    last_date: date
    sessions: int
    has_adj_close: bool
    lock: LockOut | None
    in_sample_runs: int
    oos_evaluations: int


class UniverseOut(BaseModel):
    as_of: AwareDatetime
    source: str
    entries: list[UniverseEntryOut]


class LockIn(BaseModel):
    dataset_id: int
    symbol: str = Field(min_length=1, max_length=20)
    oos_fraction: float = service.DEFAULT_OOS_FRACTION


class RunIn(BaseModel):
    dataset_id: int
    symbol: str = Field(min_length=1, max_length=20)
    strategy: str
    period: Literal["in-sample", "out-of-sample"]
    params: dict[str, int] = Field(default_factory=dict)
    slippage_bps: float = Field(default=5.0, ge=0, le=MAX_SLIPPAGE_BPS)
    commission_per_order: float = Field(default=0.0, ge=0)
    commission_bps: float = Field(default=0.0, ge=0)
    initial_capital: float = Field(default=100_000.0, gt=0, le=1e9)
    benchmark_dataset_id: int | None = None
    benchmark_symbol: str | None = Field(default=None, max_length=20)


class RunSummaryOut(BaseModel):
    id: int
    created_at: AwareDatetime
    symbol: str
    period: Literal["in-sample", "out-of-sample"]
    strategy: str
    params: dict[str, int]
    window_start: date
    window_end: date
    summary: dict[str, float | int | None]


def build_backtest_router(settings: Settings, calendar: MarketCalendar, clock: Clock) -> APIRouter:
    router = APIRouter(prefix="/backtest")

    @router.get("/strategies")
    def strategies() -> list[StrategyOut]:
        return [
            StrategyOut(
                id=s.id,
                name=s.name,
                description=s.description,
                params=[
                    ParamOut(
                        name=p.name,
                        label=p.label,
                        default=p.default,
                        minimum=p.minimum,
                        maximum=p.maximum,
                        description=p.description,
                    )
                    for p in s.params
                ],
            )
            for s in STRATEGIES.values()
        ]

    @router.get("/universe")
    def universe() -> UniverseOut:
        with open_db(settings.database_path) as conn:
            entries = []
            for e in repo.equity_universe(conn):
                lock = repo.get_lock(conn, e.symbol)
                counts = repo.run_counts(conn, e.symbol, "")
                entries.append(
                    UniverseEntryOut(
                        dataset_id=e.dataset_id,
                        symbol=e.symbol,
                        source=e.source,
                        file_name=e.file_name,
                        first_date=e.first_date,
                        last_date=e.last_date,
                        sessions=e.sessions,
                        has_adj_close=e.has_adj_close,
                        lock=LockOut.of(lock) if lock else None,
                        in_sample_runs=counts.in_sample_runs,
                        oos_evaluations=counts.oos_evaluations,
                    )
                )
        return UniverseOut(
            as_of=clock(), source="Imported equity datasets (local SQLite)", entries=entries
        )

    @router.post("/locks")
    def create_lock(body: LockIn) -> LockOut:
        with open_db(settings.database_path) as conn:
            try:
                lock = service.create_lock(
                    conn,
                    body.dataset_id,
                    body.symbol.strip().upper(),
                    clock(),
                    fraction=body.oos_fraction,
                )
            except service.BacktestRefusedError as exc:
                raise HTTPException(409, str(exc)) from exc
        return LockOut.of(lock)

    @router.post("/runs")
    def run(body: RunIn) -> BacktestReport:
        try:
            costs = CostModel(body.slippage_bps, body.commission_per_order, body.commission_bps)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        request = service.RunRequest(
            dataset_id=body.dataset_id,
            symbol=body.symbol,
            strategy_id=body.strategy,
            period=body.period,
            params=body.params,
            costs=costs,
            initial_capital=body.initial_capital,
            benchmark_dataset_id=body.benchmark_dataset_id,
            benchmark_symbol=body.benchmark_symbol,
        )
        with open_db(settings.database_path) as conn:
            try:
                return service.run(conn, request, calendar, clock())
            except service.BacktestRefusedError as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.get("/runs")
    def runs() -> list[RunSummaryOut]:
        with open_db(settings.database_path) as conn:
            return [
                RunSummaryOut(
                    id=r.id,
                    created_at=r.created_at,
                    symbol=r.symbol,
                    period=r.period,
                    strategy=r.strategy,
                    params=r.params,
                    window_start=r.window_start,
                    window_end=r.window_end,
                    summary=r.summary,
                )
                for r in repo.list_runs(conn)
            ]

    return router
