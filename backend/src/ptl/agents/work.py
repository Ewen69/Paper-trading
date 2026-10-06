"""What agents actually do. Synchronous; the runtime calls these in worker threads.

Sweep rules (they protect the out-of-sample lock):
- Every combination runs IN-SAMPLE only and is logged and counted as a trial like any other run.
- A combination already tried on that symbol is reused, not re-run.
- The finalist is the best in-sample Sharpe. Picking the best of several tries is biased upward,
  so with `promote` it gets ONE out-of-sample test, counted, and never repeated: if that exact
  finalist was already tested out-of-sample, the sweep says so and stops.
"""

import itertools
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ptl.agents import store
from ptl.backtest import repository as bt_repo
from ptl.backtest import service as eq
from ptl.backtest.repository import Period
from ptl.config import Settings
from ptl.data.live import Clock, QuoteSource
from ptl.data_api import compute_data_health
from ptl.market_calendar import MarketCalendar
from ptl.options import service as opt
from ptl.paper.broker import Broker
from ptl.paper.runner import CycleRequest, run_cycle, sync_orders
from ptl.strategy.base import REGISTRY

Emit = Callable[..., None]


class JobError(Exception):
    """The job can't run; the message is shown in the agent's status and the job log."""


@dataclass(frozen=True, slots=True)
class SweepSpec:
    asset: Literal["equity", "options"]
    strategy: str
    symbol: str
    dataset_id: int  # equity bars (the underlying's bars for options)
    options_dataset_id: int | None = None
    grid: Mapping[str, list[int]] | None = None
    promote: bool = False
    params_fixed: Mapping[str, int] = field(default_factory=dict)


def _fmt(params: Mapping[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(params.items())) or "defaults"


def combinations(spec: SweepSpec, max_combinations: int) -> list[dict[str, int]]:
    cls = REGISTRY.get(spec.strategy)
    if cls is None or cls.asset != spec.asset:
        raise JobError(f"No {spec.asset} strategy {spec.strategy!r}.")
    grid = dict(spec.grid) if spec.grid else cls.grid()
    unknown = set(grid) - {p.name for p in cls.params}
    if unknown:
        raise JobError(f"Unknown parameter(s) in grid: {sorted(unknown)}")
    keys = sorted(grid)
    combos = [
        {**spec.params_fixed, **dict(zip(keys, values, strict=True))}
        for values in itertools.product(*(grid[k] for k in keys))
    ]
    if len(combos) > max_combinations:
        raise JobError(f"{len(combos)} combinations exceed the limit of {max_combinations}.")
    valid = []
    for combo in combos:
        try:
            _, resolved = cls.create(combo)
        except ValueError:
            continue
        valid.append(resolved)
    return valid


def _run(  # noqa: PLR0913
    conn: sqlite3.Connection,
    spec: SweepSpec,
    params: dict[str, int],
    period: Period,
    *,
    calendar: MarketCalendar,
    clock: Clock,
) -> tuple[int, dict[str, Any]]:
    try:
        if spec.asset == "equity":
            report = eq.run(
                conn,
                eq.RunRequest(spec.dataset_id, spec.symbol, spec.strategy, period, params),
                calendar,
                clock(),
            )
            run_id = report.run_id
        else:
            if spec.options_dataset_id is None:
                raise JobError("Options sweeps need an options dataset.")
            options_report = opt.run(
                conn,
                opt.OptionsRunRequest(
                    options_dataset_id=spec.options_dataset_id,
                    underlying_dataset_id=spec.dataset_id,
                    symbol=spec.symbol,
                    strategy_id=spec.strategy,
                    period=period,
                    params=params,
                ),
                calendar,
                clock(),
            )
            run_id = options_report.run_id
    except eq.BacktestRefusedError as exc:
        raise JobError(str(exc)) from exc
    record = bt_repo.find_run(conn, spec.symbol, spec.strategy, params, period)
    return run_id, (record.summary if record else {})


def execute_sweep(  # noqa: PLR0913
    conn: sqlite3.Connection,
    job_id: int,
    agent_id: str,
    spec: SweepSpec,
    *,
    calendar: MarketCalendar,
    clock: Clock,
    emit: Emit,
    max_combinations: int,
) -> dict[str, Any]:
    combos = combinations(spec, max_combinations)
    if not combos:
        raise JobError("No valid parameter combinations in the grid.")
    total = len(combos)
    tested: list[tuple[dict[str, int], dict[str, Any], int]] = []
    reused = 0
    for i, params in enumerate(combos, start=1):
        label = f"{spec.strategy} ({_fmt(params)}) on {spec.symbol}"
        emit(
            status="running",
            message=f"In-sample test {i}/{total}: {label}",
            params=params,
            progress=(i - 1, total),
        )
        existing = bt_repo.find_run(conn, spec.symbol, spec.strategy, params, "in-sample")
        if existing is not None:
            reused += 1
            run_id, summary = existing.id, existing.summary
            note = f"Already tried in run #{run_id}; reused (not re-run, not re-counted)."
        else:
            run_id, summary = _run(conn, spec, params, "in-sample", calendar=calendar, clock=clock)
            note = f"In-sample run #{run_id}."
        finding = store.record_finding(
            conn,
            job_id=job_id,
            agent_id=agent_id,
            now=clock(),
            symbol=spec.symbol,
            strategy=spec.strategy,
            params=params,
            period="in-sample",
            run_id=run_id,
            summary=summary,
            note=note,
        )
        emit(finding=finding, progress=(i, total))
        tested.append((params, summary, run_id))

    ranked = [t for t in tested if (t[1].get("trades") or 0) > 0 and t[1].get("sharpe") is not None]
    ranked.sort(key=lambda t: float(t[1]["sharpe"]), reverse=True)
    result: dict[str, Any] = {"tested": total, "reused": reused, "finalist": None}
    if not ranked:
        emit(status="idle", message=f"Sweep done: {total} combinations, none produced trades.")
        return result
    best, best_summary, best_run = ranked[0]
    result["finalist"] = best
    result["finalist_in_sample_run"] = best_run
    selection = (
        f"Finalist {_fmt(best)}: highest in-sample Sharpe "
        f"({float(best_summary['sharpe']):.2f}) of {len(ranked)} combination(s) with trades. "
        "Best-of-many is biased upward."
    )
    if not spec.promote:
        emit(status="idle", message=f"Sweep done. {selection} Not promoted to out-of-sample.")
        return result

    prior = bt_repo.find_run(conn, spec.symbol, spec.strategy, best, "out-of-sample")
    if prior is not None:
        result["oos_run"] = prior.id
        emit(
            status="idle",
            message=f"Sweep done. {selection} Already tested out-of-sample in run #{prior.id}; "
            "not re-tested (the holdout is looked at once).",
        )
        return result
    emit(status="running", message=f"{selection} Running its single out-of-sample test.")
    oos_id, oos_summary = _run(conn, spec, best, "out-of-sample", calendar=calendar, clock=clock)
    finding = store.record_finding(
        conn,
        job_id=job_id,
        agent_id=agent_id,
        now=clock(),
        symbol=spec.symbol,
        strategy=spec.strategy,
        params=best,
        period="out-of-sample",
        run_id=oos_id,
        summary=oos_summary,
        note=f"Single out-of-sample test of the finalist. {selection}",
    )
    emit(finding=finding)
    result["oos_run"] = oos_id
    emit(status="idle", message=f"Sweep done. Finalist tested out-of-sample once (run #{oos_id}).")
    return result


def execute_data_check(
    settings: Settings, source: QuoteSource, calendar: MarketCalendar, clock: Clock, emit: Emit
) -> dict[str, Any]:
    emit(status="running", message="Checking datasets and the live feed")
    health = compute_data_health(settings, source, calendar, clock())
    issues = sum(len(d.issues) for d in health.datasets)
    live = health.live_source
    feed = "online" if live.reachable else ("keys not set" if not live.configured else "error")
    message = (
        f"{len(health.datasets)} dataset(s), {issues} quality finding(s), overall "
        f"{health.overall}; live feed {feed}."
    )
    emit(status="idle", message=message)
    return {"datasets": len(health.datasets), "findings": issues, "overall": health.overall}


def execute_paper_cycle(  # noqa: PLR0913
    conn: sqlite3.Connection,
    request: CycleRequest,
    *,
    settings: Settings,
    broker: Broker,
    quotes: QuoteSource | None,
    calendar: MarketCalendar,
    clock: Clock,
    emit: Emit,
) -> dict[str, Any]:
    mode = "dry run" if broker.mode == "dry_run" else "Alpaca paper"
    emit(
        status="running",
        message=f"Paper cycle ({mode}): {request.strategy_id} ({_fmt(request.params)}) "
        f"on {request.symbol}",
        params=request.params,
    )
    result = run_cycle(
        conn,
        request,
        settings=settings,
        broker=broker,
        quotes=quotes,
        calendar=calendar,
        now=clock(),
    )
    synced = sync_orders(conn, broker, clock()) if broker.mode == "paper" else 0
    emit(status="idle", message=result.outcome)
    return {
        "cycle_id": result.cycle_id,
        "order_id": result.order_id,
        "approved": None if result.decision is None else result.decision.approved,
        "decision": None if result.decision is None else result.decision.summary,
        "synced": synced,
    }
