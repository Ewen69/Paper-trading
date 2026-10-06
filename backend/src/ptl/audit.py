"""The Auditor: lifts warnings that are otherwise buried in single reports into standing flags.

Sources, all stored or live-checked facts:
- dataset quality findings and staleness (Data Health),
- the live-source check,
- warnings stored with each backtest run (run_warnings), taking only the latest run of each
  (symbol, strategy, parameters, period) so superseded results don't pile up.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from ptl.backtest.repository import RunRecord, RunWarning
from ptl.data_api import DataHealthOut, IssueOut

FlagSeverity = Literal["error", "warning", "info"]
_RANK: dict[str, int] = {"error": 0, "warning": 1, "info": 2}
_MAX_LISTED = 3

# Stored warning code -> (severity, short title).
RUN_WARNING_FLAGS: dict[str, tuple[FlagSeverity, str]] = {
    "low_trades": ("warning", "Small sample"),
    "short_sample": ("warning", "Short history"),
    "no_ci": ("warning", "No confidence interval"),
    "many_trials": ("warning", "Over-tuning risk"),
    "oos_repeat": ("warning", "Holdout reused"),
    "raw_prices": ("warning", "Unadjusted prices"),
    "basis_mismatch": ("warning", "Unfair comparison"),
    "data_quality": ("warning", "Data issues inside a backtest window"),
    "ci_includes_zero": ("info", "No clear edge"),
    "warmup_cash": ("info", "Warm-up spent in cash"),
}


class Flag(BaseModel):
    code: str
    severity: FlagSeverity
    title: str
    summary: str
    source: str
    as_of: AwareDatetime
    station: str | None
    refs: list[str]


def _listed(items: Sequence[str]) -> str:
    shown = "; ".join(items[:_MAX_LISTED])
    return shown + (f"; and {len(items) - _MAX_LISTED} more" if len(items) > _MAX_LISTED else "")


def _finding(i: IssueOut) -> str:
    return f"{i.symbol} {i.check} x{i.count} ({i.first_date}..{i.last_date})"


def _gap(i: IssueOut) -> str:
    return f"{i.symbol}: {i.count} missing NYSE sessions ({i.first_date}..{i.last_date})"


def _data_flags(health: DataHealthOut) -> list[Flag]:
    flags: list[Flag] = []
    for d in health.datasets:
        ref = f"dataset #{d.id}"
        source = f"Dataset #{d.id} quality checks ({d.provenance.source})"
        when = d.provenance.as_of
        errors = [i for i in d.issues if i.severity == "error"]
        gaps = [i for i in d.issues if i.check == "missing_sessions"]
        other = [i for i in d.issues if i.severity == "warning" and i.check != "missing_sessions"]
        if errors:
            flags.append(
                Flag(
                    code="dataset_errors",
                    severity="error",
                    title=f"Impossible rows in {d.file_name}",
                    summary=_listed([_finding(i) for i in errors])
                    + ". Backtests on these symbols are blocked until the data is replaced.",
                    source=source,
                    as_of=when,
                    station="data-health",
                    refs=[ref],
                )
            )
        if gaps:
            flags.append(
                Flag(
                    code="data_gaps",
                    severity="warning",
                    title=f"Data gaps in {d.file_name}",
                    summary=_listed([_gap(i) for i in gaps]) + ".",
                    source=source,
                    as_of=when,
                    station="data-health",
                    refs=[ref],
                )
            )
        if other:
            flags.append(
                Flag(
                    code="data_warnings",
                    severity="warning",
                    title=f"Suspicious rows in {d.file_name}",
                    summary=_listed([f"{i.symbol} {i.check} x{i.count}" for i in other]) + ".",
                    source=source,
                    as_of=when,
                    station="data-health",
                    refs=[ref],
                )
            )
        if d.provenance.stale and d.provenance.stale_reason:
            flags.append(
                Flag(
                    code="stale_data",
                    severity="info",
                    title=f"{d.file_name} is not current",
                    summary=d.provenance.stale_reason,
                    source=source,
                    as_of=health.as_of,
                    station="data-health",
                    refs=[ref],
                )
            )
    live = health.live_source
    if live.configured and live.reachable is False:
        flags.append(
            Flag(
                code="live_unreachable",
                severity="error",
                title="Live feed unreachable",
                summary=live.error or "The Alpaca paper account check failed.",
                source="Alpaca paper account check",
                as_of=live.checked_at,
                station="data-health",
                refs=[],
            )
        )
    elif not live.configured:
        flags.append(
            Flag(
                code="live_off",
                severity="info",
                title="Live quotes off",
                summary="Paper API keys are not set in .env, so live quotes show no data.",
                source="Alpaca paper account check",
                as_of=live.checked_at,
                station="data-health",
                refs=[],
            )
        )
    return flags


def _current_runs(runs: Sequence[RunRecord]) -> list[RunRecord]:
    """Latest run per (symbol, strategy, params, period): the results a user is looking at."""
    latest: dict[tuple[str, str, str, str], RunRecord] = {}
    for r in runs:
        latest[(r.symbol, r.strategy, str(sorted(r.params.items())), r.period)] = r
    return sorted(latest.values(), key=lambda r: r.id, reverse=True)


def _run_flags(runs: Sequence[RunRecord], warnings: Mapping[int, list[RunWarning]]) -> list[Flag]:
    by_code: dict[str, list[tuple[RunRecord, RunWarning]]] = defaultdict(list)
    for run in _current_runs(runs):
        seen: set[str] = set()
        for w in warnings.get(run.id, []):
            if w.code in RUN_WARNING_FLAGS and w.code not in seen:
                seen.add(w.code)
                by_code[w.code].append((run, w))
    flags = []
    for code, hits in by_code.items():
        severity, title = RUN_WARNING_FLAGS[code]
        latest_run, latest_warning = hits[0]
        labels = [f"#{r.id} {r.symbol} {r.strategy} ({r.period})" for r, _ in hits]
        flags.append(
            Flag(
                code=code,
                severity=severity,
                title=title,
                summary=(
                    f"{len(hits)} current result(s): {_listed(labels)}. "
                    f"Latest: {latest_warning.text}"
                ),
                source=f"Backtest run log, warnings stored with run(s) {_listed(labels[:3])}",
                as_of=latest_run.created_at,
                station="strategy-lab",
                refs=[f"run #{r.id}" for r, _ in hits],
            )
        )
    return flags


def compute_flags(
    health: DataHealthOut, runs: Sequence[RunRecord], warnings: Mapping[int, list[RunWarning]]
) -> list[Flag]:
    flags = _data_flags(health) + _run_flags(runs, warnings)
    by_time = sorted(flags, key=lambda f: f.as_of, reverse=True)
    return sorted(by_time, key=lambda f: _RANK[f.severity])
