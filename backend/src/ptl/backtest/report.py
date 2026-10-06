"""API-facing backtest report. Every number here is computed from imported data and the engine."""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from ptl.backtest.metrics import Estimate, Interval, Performance
from ptl.backtest.models import Trade
from ptl.provenance import Provenance

PriceBasis = Literal["adjusted", "raw"]


class IntervalOut(BaseModel):
    low: float
    high: float

    @classmethod
    def of(cls, interval: Interval | None) -> "IntervalOut | None":
        return None if interval is None else cls(low=interval.low, high=interval.high)


class EstimateOut(BaseModel):
    value: float | None
    ci95: IntervalOut | None

    @classmethod
    def of(cls, estimate: Estimate) -> "EstimateOut":
        return cls(value=estimate.value, ci95=IntervalOut.of(estimate.ci))


class PerformanceOut(BaseModel):
    sessions: int
    final_equity: float
    total_return: float
    annualized_return: EstimateOut
    annual_volatility: float | None
    sharpe: EstimateOut
    max_drawdown: float
    max_drawdown_date: date | None
    exposure: float
    trade_count: int
    win_rate: EstimateOut
    expectancy: EstimateOut
    avg_win: float | None
    avg_loss: float | None
    total_costs: float

    @classmethod
    def of(cls, p: Performance) -> "PerformanceOut":
        return cls(
            sessions=p.sessions,
            final_equity=p.final_equity,
            total_return=p.total_return,
            annualized_return=EstimateOut.of(p.annualized_return),
            annual_volatility=p.annual_volatility,
            sharpe=EstimateOut.of(p.sharpe),
            max_drawdown=p.max_drawdown,
            max_drawdown_date=p.max_drawdown_date,
            exposure=p.exposure,
            trade_count=p.trade_count,
            win_rate=EstimateOut.of(p.win_rate),
            expectancy=EstimateOut.of(p.expectancy),
            avg_win=p.avg_win,
            avg_loss=p.avg_loss,
            total_costs=p.total_costs,
        )


class TradeOut(BaseModel):
    entry_date: date
    exit_date: date
    cash_out: float
    cash_in: float
    pnl: float
    return_pct: float
    sessions_held: int
    exit_reason: str

    @classmethod
    def of(cls, t: Trade) -> "TradeOut":
        return cls(
            entry_date=t.entry_date,
            exit_date=t.exit_date,
            cash_out=t.cash_out,
            cash_in=t.cash_in,
            pnl=t.pnl,
            return_pct=t.return_pct,
            sessions_held=t.sessions_held,
            exit_reason=t.exit_reason,
        )


class CurvePoint(BaseModel):
    day: date
    strategy: float | None
    benchmark: float | None


class CostsOut(BaseModel):
    slippage_bps: float
    commission_per_order: float
    commission_bps: float


class BootstrapOut(BaseModel):
    method: str
    resamples: int
    block_length: int
    seed: int
    confidence: float


class RealityCheck(BaseModel):
    """What a reader needs before trusting any number above it."""

    period: Literal["in-sample", "out-of-sample"]
    window_start: date
    window_end: date
    sessions: int
    trade_count: int
    parameter_combinations_tried: int  # in-sample, this strategy, this symbol
    parameter_combinations_tried_all_strategies: int
    in_sample_runs: int
    oos_evaluations: int
    oos_start: date
    oos_locked_at: AwareDatetime
    oos_lock_basis: str
    costs: CostsOut
    initial_capital: float
    price_basis: PriceBasis
    benchmark_price_basis: PriceBasis
    fill_model: str
    cash_yield: str
    sharpe_risk_free: str
    bootstrap: BootstrapOut
    warnings: list[str]


class StrategyRef(BaseModel):
    id: str
    name: str
    params: dict[str, int]


class BacktestReport(BaseModel):
    run_id: int
    symbol: str
    benchmark_symbol: str
    strategy: StrategyRef
    strategy_metrics: PerformanceOut
    benchmark_metrics: PerformanceOut
    excess_annualized_return: EstimateOut
    curve: list[CurvePoint]
    trades: list[TradeOut]
    reality_check: RealityCheck
    provenance: Provenance
    benchmark_provenance: Provenance
