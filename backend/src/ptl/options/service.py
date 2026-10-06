"""Runs one options backtest end to end, with the same rigor rules as the equity backtester.

- The out-of-sample lock is the underlying symbol's lock (shared with stock backtests), and
  in-sample runs never see quotes or bars from the locked period.
- Every run is logged and counted; warnings are stored with stable codes.
- Compared with a buy-and-hold benchmark over the same window, after costs.
- Option quotes must carry exercise_style; the engine won't assume American or European.
- Strikes and settlement use the underlying's RAW prices; the benchmark uses adjusted prices
  (total return) when available.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from ptl.backtest import repository as repo
from ptl.backtest import service as eq
from ptl.backtest.engine import run_backtest
from ptl.backtest.metrics import Estimate, paired_bootstrap, performance
from ptl.backtest.models import CostModel, PriceBar
from ptl.backtest.report import (
    CostLine,
    EstimateOut,
    IntervalOut,
    PerformanceOut,
    RealityCheck,
    StrategyRef,
    WarningOut,
)
from ptl.backtest.repository import Period, RunWarning
from ptl.backtest.strategies import BuyAndHold
from ptl.data import repository as data_repo
from ptl.data.models import DatasetKind
from ptl.market_calendar import MarketCalendar
from ptl.options.chain import count_missing_style, load_chain
from ptl.options.engine import OptionsResult, run_options_backtest
from ptl.options.models import ContractKey, OptionCosts, Quote
from ptl.options.report import OptionEventOut, OptionsBacktestReport, OptionsCounts, OptionTradeOut
from ptl.options.strategies import OPTIONS_STRATEGIES
from ptl.provenance import Provenance

BacktestRefusedError = eq.BacktestRefusedError

FILL_MODEL = (
    "Decisions use today's end-of-day chain and fill at the NEXT session's quotes: buys at "
    "ask + slippage, sells at bid - slippage, never mid. Commissions per contract; x100 "
    "multiplier. Defined risk only (single long options, vertical spreads). Expiration settles "
    "at the underlying close: ITM longs exercise, ITM shorts are assigned (OCC $0.01 rule); "
    "American options deliver shares, European settle in cash. Shares left by a between-strike "
    "finish (pin risk) or early assignment are sold at the next open with stock slippage. "
    "Positions still open on the last session are closed at that day's quotes."
)


@dataclass(frozen=True, slots=True)
class OptionsRunRequest:
    options_dataset_id: int
    underlying_dataset_id: int
    symbol: str
    strategy_id: str
    period: Period
    params: Mapping[str, int] = field(default_factory=dict)
    costs: OptionCosts = field(default_factory=OptionCosts)
    initial_capital: float = 100_000.0
    benchmark_dataset_id: int | None = None
    benchmark_symbol: str | None = None


def option_cost_lines(costs: OptionCosts) -> list[CostLine]:
    early = (
        f"short American legs with time value <= ${costs.early_assignment_extrinsic:.2f}"
        if costs.early_assignment
        else "off (optimistic)"
    )
    return [
        CostLine(
            label="Option slippage",
            value=f"${costs.slippage_per_share:.2f}/share + "
            f"{costs.slippage_spread_fraction:.0%} of the bid-ask spread",
        ),
        CostLine(label="Commission", value=f"${costs.commission_per_contract:.2f} per contract"),
        CostLine(
            label="Assignment/exercise fee",
            value=f"${costs.assignment_fee_per_contract:.2f} per contract",
        ),
        CostLine(label="Share liquidation slippage", value=f"{costs.stock_slippage_bps:g} bps"),
        CostLine(label="Early assignment", value=early),
    ]


def _options_dataset(conn: sqlite3.Connection, dataset_id: int) -> data_repo.DatasetRecord:
    record = next((d for d in data_repo.list_datasets(conn) if d.id == dataset_id), None)
    if record is None:
        raise BacktestRefusedError(f"No data: dataset #{dataset_id} does not exist.")
    if record.kind is not DatasetKind.OPTION_QUOTES:
        raise BacktestRefusedError(f"Dataset #{dataset_id} is not option quotes.")
    return record


def _quote_quality_errors(
    conn: sqlite3.Connection, dataset_id: int, symbol: str, first: date, last: date
) -> int:
    return sum(
        i.count
        for i in data_repo.issues_for(conn, dataset_id)
        if i.symbol == symbol
        and i.severity.value == "error"
        and i.first_date <= last
        and i.last_date >= first
    )


def run(
    conn: sqlite3.Connection, request: OptionsRunRequest, calendar: MarketCalendar, now: datetime
) -> OptionsBacktestReport:
    spec = OPTIONS_STRATEGIES.get(request.strategy_id)
    if spec is None:
        raise BacktestRefusedError(f"Unknown options strategy {request.strategy_id!r}.")
    try:
        strategy, params = spec.create(request.params)
    except ValueError as exc:
        raise BacktestRefusedError(str(exc)) from exc
    if request.initial_capital <= 0:
        raise BacktestRefusedError("Initial capital must be positive.")

    symbol = request.symbol.strip().upper()
    options_ds = _options_dataset(conn, request.options_dataset_id)
    underlying_ds = eq.load_dataset(conn, request.underlying_dataset_id)
    all_bars = eq.load_bars(conn, underlying_ds.id, symbol)
    lock = repo.get_lock(conn, symbol) or eq.create_lock(
        conn, underlying_ds.id, symbol, now, bars=all_bars
    )
    window = eq.resolve_window(all_bars, lock, request.period)
    warnings: list[RunWarning] = eq.quality_gate(window.bars, calendar, symbol)

    missing = count_missing_style(conn, options_ds.id, symbol, window.first, window.last)
    if missing:
        raise BacktestRefusedError(
            f"{missing} {symbol} option quotes in {window.first}..{window.last} have no "
            "exercise_style. Add an exercise_style column (american/european) and re-import; "
            "the engine won't assume one."
        )
    bad_quotes = _quote_quality_errors(conn, options_ds.id, symbol, window.first, window.last)
    if bad_quotes:
        warnings.append(
            RunWarning(
                "quote_quality",
                f"{bad_quotes} error-level {symbol} quote finding(s) overlap this window "
                "(crossed, negative, or after expiry). The engine refuses fills against them.",
            )
        )

    # Strikes are in raw dollars, so the engine sees raw underlying prices.
    raw_bars = [PriceBar(b.session_date, b.open, b.high, b.low, b.close) for b in window.bars]

    def chain_for(day: date) -> Mapping[ContractKey, Quote]:
        return load_chain(conn, options_ds.id, symbol, day)

    result = run_options_backtest(
        raw_bars,
        window.start,
        strategy,
        chain_for,
        costs=request.costs,
        initial_capital=request.initial_capital,
    )

    bench_dataset_id = request.benchmark_dataset_id or underlying_ds.id
    bench_dataset = eq.load_dataset(conn, bench_dataset_id)
    bench_symbol = (request.benchmark_symbol or "").strip().upper()
    if not bench_symbol:
        has_spy = repo.load_equity_bars(conn, bench_dataset_id, eq.DEFAULT_BENCHMARK)
        bench_symbol = eq.DEFAULT_BENCHMARK if has_spy else symbol
    bench_window = eq.benchmark_window(
        eq.load_bars(conn, bench_dataset_id, bench_symbol), window.first, window.last, bench_symbol
    )
    if bench_symbol != symbol or bench_dataset_id != underlying_ds.id:
        warnings += eq.quality_gate(bench_window.bars, calendar, f"Benchmark {bench_symbol}")
    bench_bars, bench_basis = eq.to_price_bars(bench_window.bars)
    bench = run_backtest(
        bench_bars,
        bench_window.start,
        BuyAndHold(),
        CostModel(slippage_bps=request.costs.stock_slippage_bps),
        request.initial_capital,
    )

    s_ret, b_ret = eq.aligned_returns(request.initial_capital, result.equity, bench.equity)
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
        costs=_costs_dict(request.costs),
        dataset_sha256=options_ds.sha256,
        window=(window.first, window.last),
        benchmark_symbol=bench_symbol,
        summary=eq.run_summary(s_perf, excess),
    )
    counts = repo.run_counts(conn, symbol, spec.id)
    warnings += eq.rigor_warnings(
        perf=s_perf,
        excess=excess,
        counts=counts,
        period=request.period,
        symbol=symbol,
        bench_symbol=bench_symbol,
    )
    warnings += _options_warnings(result)
    if bench_basis == "raw":
        warnings.append(
            RunWarning(
                "raw_prices",
                f"Benchmark {bench_symbol} uses raw prices (no adj_close), so its dividends "
                "are excluded.",
            )
        )
    repo.record_warnings(conn, run_id, warnings)

    return OptionsBacktestReport(
        run_id=run_id,
        symbol=symbol,
        benchmark_symbol=bench_symbol,
        strategy=StrategyRef(id=spec.id, name=spec.name, params=params),
        strategy_metrics=PerformanceOut.of(s_perf),
        benchmark_metrics=PerformanceOut.of(b_perf),
        excess_annualized_return=EstimateOut(value=excess.value, ci95=IntervalOut.of(excess.ci)),
        curve=eq.curve_points(result.equity, bench.equity),
        trades=[OptionTradeOut.of(t) for t in result.trades],
        events=[OptionEventOut.of(e) for e in result.events],
        counts=OptionsCounts(
            rejected_orders=result.rejected_orders,
            stale_marks=result.stale_marks,
            pin_events=result.pin_events,
            early_assignments=result.early_assignments,
            margin_shortfalls=result.margin_shortfalls,
            peak_collateral=result.peak_reserve,
        ),
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
            cost_lines=option_cost_lines(request.costs),
            initial_capital=request.initial_capital,
            price_basis="raw",
            benchmark_price_basis=bench_basis,
            fill_model=FILL_MODEL,
            cash_yield="0%: idle cash and collateral earn nothing in this backtest",
            sharpe_risk_free="0%",
            bootstrap=eq.bootstrap_out(len(s_ret)),
            warnings=[WarningOut(code=w.code, text=w.text) for w in warnings],
        ),
        provenance=Provenance(
            source=f"Option quotes: {options_ds.source} ({options_ds.file_name}, "
            f"dataset #{options_ds.id})",
            as_of=now,
            data_type=options_ds.data_type,
            stale=False,
        ),
        underlying_provenance=Provenance(
            source=f"{symbol} raw bars: {underlying_ds.source} ({underlying_ds.file_name}, "
            f"dataset #{underlying_ds.id})",
            as_of=now,
            data_type=underlying_ds.data_type,
            stale=False,
        ),
        benchmark_provenance=Provenance(
            source=f"{bench_symbol} buy-and-hold on {bench_dataset.source} "
            f"({bench_dataset.file_name}, dataset #{bench_dataset.id})",
            as_of=now,
            data_type=bench_dataset.data_type,
            stale=False,
        ),
    )


def _costs_dict(costs: OptionCosts) -> dict[str, float]:
    return {
        "slippage_per_share": costs.slippage_per_share,
        "slippage_spread_fraction": costs.slippage_spread_fraction,
        "commission_per_contract": costs.commission_per_contract,
        "assignment_fee_per_contract": costs.assignment_fee_per_contract,
        "stock_slippage_bps": costs.stock_slippage_bps,
        "early_assignment": float(costs.early_assignment),
        "early_assignment_extrinsic": costs.early_assignment_extrinsic,
    }


def _options_warnings(result: OptionsResult) -> list[RunWarning]:
    rejected, stale, pins = result.rejected_orders, result.stale_marks, result.pin_events
    early, margin = result.early_assignments, result.margin_shortfalls
    out: list[RunWarning] = []
    if rejected:
        out.append(
            RunWarning(
                "rejected_fills",
                f"{rejected} order(s) were not filled (no usable quote, not enough cash for "
                "collateral, or not defined-risk). See the event log.",
            )
        )
    if stale:
        out.append(
            RunWarning(
                "stale_marks",
                f"{stale} leg-day mark(s) had no usable quote and reused the last known value. "
                "The equity curve is less reliable on those days.",
            )
        )
    if pins:
        out.append(
            RunWarning(
                "pin_risk",
                f"{pins} position(s) finished between their strikes. Shares were assigned and "
                "sold at the next open, so losses can exceed the spread's defined maximum.",
            )
        )
    if early:
        out.append(
            RunWarning(
                "early_assignment",
                f"{early} early assignment(s) of short legs with little time value left.",
            )
        )
    if margin:
        out.append(
            RunWarning(
                "assignment_margin",
                f"On {margin} session(s) assignment left cash negative until the shares were "
                "sold; a real broker would have issued a margin call.",
            )
        )
    return out
