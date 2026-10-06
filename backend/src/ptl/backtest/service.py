"""Runs one backtest end to end and enforces the rigor rules.

- Out-of-sample (OOS) lock: the first backtest on a symbol permanently locks its most recent
  sessions (30% by default) as OOS. In-sample runs are given only bars before the lock date, so
  tuning cannot see OOS data. OOS runs are allowed but each one is logged and counted.
- Every run is logged; distinct parameter combinations tried are counted and reported.
- Bars with error-level quality issues block the run (no fills against impossible prices).
- The strategy is always compared with a buy-and-hold benchmark run with the same costs.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from ptl.backtest import repository as repo
from ptl.backtest.engine import run_backtest
from ptl.backtest.metrics import (
    Estimate,
    FloatArray,
    bootstrap_info,
    daily_returns,
    paired_bootstrap,
    performance,
)
from ptl.backtest.models import CostModel, EngineResult, EquityPoint, PriceBar
from ptl.backtest.report import (
    BacktestReport,
    BootstrapOut,
    CostsOut,
    CurvePoint,
    EstimateOut,
    IntervalOut,
    PerformanceOut,
    PriceBasis,
    RealityCheck,
    StrategyRef,
    TradeOut,
)
from ptl.backtest.repository import OosLock, Period
from ptl.backtest.strategies import STRATEGIES, BuyAndHold
from ptl.data import repository as data_repo
from ptl.data.models import DatasetKind, EquityBar, Severity
from ptl.data.quality import check_equity_bars
from ptl.market_calendar import MarketCalendar
from ptl.provenance import Provenance

DEFAULT_OOS_FRACTION = 0.3
MIN_OOS_FRACTION = 0.1
MAX_OOS_FRACTION = 0.5
MIN_SESSIONS_TO_LOCK = 120
MIN_IN_SAMPLE_SESSIONS = 60
MIN_OOS_SESSIONS = 20
LOW_TRADE_COUNT = 30
ONE_YEAR_SESSIONS = 252
DEFAULT_BENCHMARK = "SPY"
FILL_MODEL = (
    "Signals use closes up to the decision day; orders fill at the next session's open. "
    "Buys pay open x (1 + slippage), sells receive open x (1 - slippage), plus commission. "
    "Fractional shares. Long only, no leverage. Open positions are sold at the final close."
)


class BacktestRefusedError(Exception):
    """The backtest would break a rule or has no usable data. The message says why."""


@dataclass(frozen=True, slots=True)
class RunRequest:
    dataset_id: int
    symbol: str
    strategy_id: str
    period: Period
    params: Mapping[str, int] = field(default_factory=dict)
    costs: CostModel = field(default_factory=CostModel)
    initial_capital: float = 100_000.0
    benchmark_dataset_id: int | None = None
    benchmark_symbol: str | None = None


def to_price_bars(bars: Sequence[EquityBar]) -> tuple[list[PriceBar], PriceBasis]:
    """Use split/dividend-adjusted prices when every bar has adj_close, else raw prices."""
    if bars and all(b.adj_close is not None and b.close > 0 for b in bars):
        adjusted = []
        for b in bars:
            f = (b.adj_close or 0.0) / b.close
            adjusted.append(
                PriceBar(b.session_date, b.open * f, b.high * f, b.low * f, b.close * f)
            )
        return adjusted, "adjusted"
    return [PriceBar(b.session_date, b.open, b.high, b.low, b.close) for b in bars], "raw"


def _dataset(conn: sqlite3.Connection, dataset_id: int) -> data_repo.DatasetRecord:
    record = next((d for d in data_repo.list_datasets(conn) if d.id == dataset_id), None)
    if record is None:
        raise BacktestRefusedError(f"No data: dataset #{dataset_id} does not exist.")
    if record.kind is not DatasetKind.EQUITY_BARS:
        raise BacktestRefusedError(f"Dataset #{dataset_id} is not equity daily bars.")
    return record


def _bars(conn: sqlite3.Connection, dataset_id: int, symbol: str) -> list[EquityBar]:
    bars = repo.load_equity_bars(conn, dataset_id, symbol)
    if not bars:
        raise BacktestRefusedError(f"No data: dataset #{dataset_id} has no bars for {symbol}.")
    return bars


def create_lock(  # noqa: PLR0913 - keyword-only options after `now`
    conn: sqlite3.Connection,
    dataset_id: int,
    symbol: str,
    now: datetime,
    *,
    fraction: float = DEFAULT_OOS_FRACTION,
    bars: Sequence[EquityBar] | None = None,
) -> OosLock:
    """Lock the most recent `fraction` of the symbol's sessions as out-of-sample, permanently."""
    if repo.get_lock(conn, symbol) is not None:
        raise BacktestRefusedError(f"{symbol} already has a permanent out-of-sample lock.")
    if not MIN_OOS_FRACTION <= fraction <= MAX_OOS_FRACTION:
        raise BacktestRefusedError(
            f"Out-of-sample fraction must be between {MIN_OOS_FRACTION:.0%} and "
            f"{MAX_OOS_FRACTION:.0%}."
        )
    bars = bars if bars is not None else _bars(conn, dataset_id, symbol)
    if len(bars) < MIN_SESSIONS_TO_LOCK:
        raise BacktestRefusedError(
            f"{symbol} has {len(bars)} sessions; at least {MIN_SESSIONS_TO_LOCK} are needed to "
            "split in-sample and out-of-sample."
        )
    cut = int(len(bars) * (1 - fraction))
    lock = OosLock(
        symbol=symbol,
        oos_start=bars[cut].session_date,
        oos_fraction=fraction,
        locked_at=now,
        basis=(
            f"Most recent {fraction:.0%} of {len(bars)} sessions in dataset #{dataset_id} "
            f"({bars[0].session_date}..{bars[-1].session_date}) when the lock was created."
        ),
    )
    repo.create_lock(conn, lock)
    return lock


@dataclass(frozen=True, slots=True)
class _Window:
    bars: list[EquityBar]  # everything the engine may see (warm-up + window)
    start: int  # index of the first bar in the window

    @property
    def first(self) -> date:
        return self.bars[self.start].session_date

    @property
    def last(self) -> date:
        return self.bars[-1].session_date


def _window(bars: list[EquityBar], lock: OosLock, period: Period) -> _Window:
    if period == "in-sample":
        visible = [b for b in bars if b.session_date < lock.oos_start]
        if len(visible) < MIN_IN_SAMPLE_SESSIONS:
            raise BacktestRefusedError(
                f"Only {len(visible)} in-sample sessions before the out-of-sample start "
                f"{lock.oos_start}; at least {MIN_IN_SAMPLE_SESSIONS} are needed."
            )
        return _Window(visible, 0)
    start = next((i for i, b in enumerate(bars) if b.session_date >= lock.oos_start), len(bars))
    if len(bars) - start < MIN_OOS_SESSIONS:
        raise BacktestRefusedError(
            f"Only {len(bars) - start} out-of-sample sessions from {lock.oos_start} in this "
            f"dataset; at least {MIN_OOS_SESSIONS} are needed."
        )
    return _Window(bars, start)


def _quality_gate(bars: Sequence[EquityBar], calendar: MarketCalendar, label: str) -> list[str]:
    issues = check_equity_bars(bars, calendar)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        listed = "; ".join(f"{i.check} x{i.count} ({i.first_date}..{i.last_date})" for i in errors)
        raise BacktestRefusedError(
            f"{label} data has error-level quality issues, so no fills are possible against "
            f"it: {listed}. Fix or replace the data and import it again."
        )
    return [
        f"{label} data: {i.check} x{i.count} ({i.first_date}..{i.last_date}). {i.detail}"
        for i in issues
        if i.severity is Severity.WARNING
    ]


def _benchmark_window(bars: list[EquityBar], first: date, last: date, symbol: str) -> _Window:
    visible = [b for b in bars if b.session_date <= last]
    if not visible or visible[0].session_date > first or visible[-1].session_date < last:
        cover = f"{bars[0].session_date}..{bars[-1].session_date}" if bars else "nothing"
        raise BacktestRefusedError(
            f"No data: benchmark {symbol} covers {cover}, but the window is {first}..{last}."
        )
    start = next(i for i, b in enumerate(visible) if b.session_date >= first)
    return _Window(visible, start)


def _aligned_returns(
    initial: float, a: Sequence[EquityPoint], b: Sequence[EquityPoint]
) -> tuple[FloatArray, FloatArray]:
    by_day_b = {p.day: p for p in b}
    common = [(p, by_day_b[p.day]) for p in a if p.day in by_day_b]
    return (
        daily_returns(initial, [x for x, _ in common]),
        daily_returns(initial, [y for _, y in common]),
    )


def _curve(strategy: EngineResult, benchmark: EngineResult) -> list[CurvePoint]:
    s = {p.day: p.equity for p in strategy.equity}
    b = {p.day: p.equity for p in benchmark.equity}
    return [CurvePoint(day=d, strategy=s.get(d), benchmark=b.get(d)) for d in sorted(s | b)]


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def run(  # noqa: PLR0912, PLR0915 - a linear recipe; splitting it would hide the order of the rules
    conn: sqlite3.Connection, request: RunRequest, calendar: MarketCalendar, now: datetime
) -> BacktestReport:
    spec = STRATEGIES.get(request.strategy_id)
    if spec is None:
        raise BacktestRefusedError(f"Unknown strategy {request.strategy_id!r}.")
    try:
        strategy, params = spec.create(request.params)
    except ValueError as exc:
        raise BacktestRefusedError(str(exc)) from exc
    if request.initial_capital <= 0:
        raise BacktestRefusedError("Initial capital must be positive.")

    symbol = request.symbol.strip().upper()
    dataset = _dataset(conn, request.dataset_id)
    all_bars = _bars(conn, dataset.id, symbol)
    lock = repo.get_lock(conn, symbol) or create_lock(conn, dataset.id, symbol, now, bars=all_bars)
    window = _window(all_bars, lock, request.period)
    warnings = _quality_gate(window.bars, calendar, symbol)

    bench_dataset_id = request.benchmark_dataset_id or dataset.id
    bench_dataset = _dataset(conn, bench_dataset_id)
    bench_symbol = (request.benchmark_symbol or "").strip().upper()
    if not bench_symbol:
        has_spy = repo.load_equity_bars(conn, bench_dataset_id, DEFAULT_BENCHMARK)
        bench_symbol = DEFAULT_BENCHMARK if has_spy else symbol
    bench_window = _benchmark_window(
        _bars(conn, bench_dataset_id, bench_symbol), window.first, window.last, bench_symbol
    )
    if bench_symbol != symbol or bench_dataset_id != dataset.id:
        warnings += _quality_gate(bench_window.bars, calendar, f"Benchmark {bench_symbol}")

    price_bars, basis = to_price_bars(window.bars)
    bench_bars, bench_basis = to_price_bars(bench_window.bars)
    result = run_backtest(
        price_bars, window.start, strategy, request.costs, request.initial_capital
    )
    bench = run_backtest(
        bench_bars, bench_window.start, BuyAndHold(), request.costs, request.initial_capital
    )

    s_ret, b_ret = _aligned_returns(request.initial_capital, result.equity, bench.equity)
    boot = paired_bootstrap(s_ret, b_ret)
    s_perf = performance(result, boot.strategy_annualized, boot.strategy_sharpe)
    b_perf = performance(bench, boot.benchmark_annualized, boot.benchmark_sharpe)
    s_ann, b_ann = s_perf.annualized_return.value, b_perf.annualized_return.value
    excess = Estimate(
        None if s_ann is None or b_ann is None else s_ann - b_ann, boot.excess_annualized
    )

    run_id = repo.record_run(
        conn,
        created_at=now,
        symbol=symbol,
        period=request.period,
        strategy=spec.id,
        params=params,
        costs=asdict(request.costs),
        dataset_sha256=dataset.sha256,
        window=(window.first, window.last),
        benchmark_symbol=bench_symbol,
        summary={
            "total_return": s_perf.total_return,
            "annualized_return": s_ann,
            "sharpe": s_perf.sharpe.value,
            "max_drawdown": s_perf.max_drawdown,
            "trades": s_perf.trade_count,
            "excess_annualized_return": excess.value,
        },
    )
    counts = repo.run_counts(conn, symbol, spec.id)

    # Warnings: each one is traceable to a number in this report.
    if window.start < strategy.warmup:
        warnings.append(
            f"The strategy needs {strategy.warmup} sessions of history before its first "
            f"signal. It held cash for about the first {strategy.warmup - window.start} sessions "
            "of this window, while the benchmark was invested."
        )
    if s_perf.trade_count < LOW_TRADE_COUNT:
        warnings.append(
            f"Only {s_perf.trade_count} trade(s). Win rate and expectancy are unreliable below "
            f"{LOW_TRADE_COUNT} trades."
        )
    if s_perf.sessions < ONE_YEAR_SESSIONS:
        warnings.append(f"Only {s_perf.sessions} sessions (under one year of data).")
    if excess.ci is None:
        warnings.append("Too few sessions for a confidence interval on excess return.")
    elif excess.ci.contains(0.0):
        warnings.append(
            f"The 95% CI for annualized excess return vs {bench_symbol} is "
            f"{_pct(excess.ci.low)} to {_pct(excess.ci.high)}. It includes 0, so this backtest "
            "does not distinguish the strategy from buy-and-hold."
        )
    if request.period == "in-sample" and counts.in_sample_combinations_this_strategy > 1:
        warnings.append(
            f"{counts.in_sample_combinations_this_strategy} parameter combinations of this "
            f"strategy have been tried on {symbol} in-sample. The best of several tries is "
            "biased upward; judge it on out-of-sample data."
        )
    if request.period == "out-of-sample" and counts.oos_evaluations > 1:
        warnings.append(
            f"The out-of-sample period for {symbol} has now been evaluated "
            f"{counts.oos_evaluations} times. Each look makes it less of a clean holdout."
        )
    if basis == "raw":
        warnings.append(
            f"{symbol} uses raw prices (no adj_close column), so dividends are excluded and "
            "splits look like crashes."
        )
    if basis != bench_basis:
        warnings.append(
            f"Strategy uses {basis} prices but the benchmark uses {bench_basis} prices, which "
            "biases the comparison."
        )

    info = bootstrap_info(len(s_ret))
    return BacktestReport(
        run_id=run_id,
        symbol=symbol,
        benchmark_symbol=bench_symbol,
        strategy=StrategyRef(id=spec.id, name=spec.name, params=params),
        strategy_metrics=PerformanceOut.of(s_perf),
        benchmark_metrics=PerformanceOut.of(b_perf),
        excess_annualized_return=EstimateOut(value=excess.value, ci95=IntervalOut.of(excess.ci)),
        curve=_curve(result, bench),
        trades=[TradeOut.of(t) for t in result.trades],
        reality_check=RealityCheck(
            period=request.period,
            window_start=window.first,
            window_end=window.last,
            sessions=s_perf.sessions,
            trade_count=s_perf.trade_count,
            parameter_combinations_tried=counts.in_sample_combinations_this_strategy,
            parameter_combinations_tried_all_strategies=counts.in_sample_combinations_all_strategies,
            in_sample_runs=counts.in_sample_runs,
            oos_evaluations=counts.oos_evaluations,
            oos_start=lock.oos_start,
            oos_locked_at=lock.locked_at,
            oos_lock_basis=lock.basis,
            costs=CostsOut(**asdict(request.costs)),
            initial_capital=request.initial_capital,
            price_basis=basis,
            benchmark_price_basis=bench_basis,
            fill_model=FILL_MODEL,
            cash_yield="0%: idle cash earns nothing in this backtest",
            sharpe_risk_free="0%",
            bootstrap=BootstrapOut(
                method=info.method,
                resamples=info.resamples,
                block_length=info.block_length,
                seed=info.seed,
                confidence=info.confidence,
            ),
            warnings=warnings,
        ),
        provenance=Provenance(
            source=f"Backtest on {dataset.source} ({dataset.file_name}, dataset #{dataset.id})",
            as_of=now,
            data_type=dataset.data_type,
            stale=False,
        ),
        benchmark_provenance=Provenance(
            source=(
                f"{bench_symbol} buy-and-hold on {bench_dataset.source} "
                f"({bench_dataset.file_name}, dataset #{bench_dataset.id})"
            ),
            as_of=now,
            data_type=bench_dataset.data_type,
            stale=False,
        ),
    )
