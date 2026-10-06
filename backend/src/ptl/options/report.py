"""API-facing options backtest report."""

from datetime import date

from pydantic import BaseModel

from ptl.backtest.report import (
    CurvePoint,
    EstimateOut,
    PerformanceOut,
    RealityCheck,
    StrategyRef,
)
from ptl.options.engine import OptionsEvent, OptionTrade
from ptl.provenance import Provenance


class OptionTradeOut(BaseModel):
    position_id: int
    opened: date
    closed: date
    description: str
    structure: str
    contracts: int
    entry_premium: float
    max_loss: float
    pnl: float
    exit_kind: str
    sessions_held: int

    @classmethod
    def of(cls, t: OptionTrade) -> "OptionTradeOut":
        return cls(
            position_id=t.position_id,
            opened=t.opened,
            closed=t.closed,
            description=t.description,
            structure=t.structure,
            contracts=t.contracts,
            entry_premium=t.entry_premium,
            max_loss=t.max_loss,
            pnl=t.pnl,
            exit_kind=t.exit_kind,
            sessions_held=t.sessions_held,
        )


class OptionEventOut(BaseModel):
    day: date
    kind: str
    position_id: int | None
    text: str
    cash: float

    @classmethod
    def of(cls, e: OptionsEvent) -> "OptionEventOut":
        return cls(day=e.day, kind=e.kind, position_id=e.position_id, text=e.text, cash=e.cash)


class OptionsCounts(BaseModel):
    rejected_orders: int
    stale_marks: int
    pin_events: int
    early_assignments: int
    margin_shortfalls: int
    peak_collateral: float


class OptionsBacktestReport(BaseModel):
    run_id: int
    symbol: str
    benchmark_symbol: str
    strategy: StrategyRef
    strategy_metrics: PerformanceOut
    benchmark_metrics: PerformanceOut
    excess_annualized_return: EstimateOut
    curve: list[CurvePoint]
    trades: list[OptionTradeOut]
    events: list[OptionEventOut]
    counts: OptionsCounts
    reality_check: RealityCheck
    provenance: Provenance  # the option quotes
    underlying_provenance: Provenance
    benchmark_provenance: Provenance
