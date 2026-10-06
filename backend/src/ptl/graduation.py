"""Graduation Gate: a checklist of evidence a strategy would need before real money.

It only tracks. Nothing in this app reads the gate to change behavior, and no code path can
enable live trading, whatever the gate shows.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from ptl.backtest.repository import RunRecord

GateStatus = Literal["met", "not_met", "not_started"]

PAPER_DAYS_TARGET = 90
PAPER_TRADES_TARGET = 200
PAPER_SOURCE = "Paper trading log (built in Phase 3; no paper trading has happened yet)"
GATE_NOTE = (
    "Tracking only. Meeting every item does not, and cannot, enable live trading in this app."
)


class GateItem(BaseModel):
    id: str
    title: str
    requirement: str
    status: GateStatus
    progress: str
    evidence: str | None
    source: str
    as_of: AwareDatetime


class GraduationOut(BaseModel):
    items: list[GateItem]
    met: int
    total: int
    note: str
    as_of: AwareDatetime
    source: str


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _oos_edge(runs: Sequence[RunRecord], now: datetime) -> GateItem:
    first_looks: dict[str, RunRecord] = {}
    for r in runs:  # oldest first, so the first OOS run per symbol wins
        if r.period == "out-of-sample":
            first_looks.setdefault(r.symbol, r)

    def item(status: GateStatus, progress: str, evidence: str | None, as_of: datetime) -> GateItem:
        return GateItem(
            id="oos_edge",
            title="Beats buy-and-hold out-of-sample",
            requirement=(
                "On a symbol's first out-of-sample test, the 95% CI for annualized excess "
                "return vs buy-and-hold, after costs, is entirely above 0."
            ),
            status=status,
            progress=progress,
            evidence=evidence,
            source="Backtest run log (first out-of-sample run per symbol)",
            as_of=as_of,
        )

    if not first_looks:
        return item("not_started", "No out-of-sample tests yet.", None, now)
    passing = []
    without_ci = 0
    for run in first_looks.values():
        low, high = run.summary.get("excess_ci_low"), run.summary.get("excess_ci_high")
        if low is None or high is None:
            without_ci += 1
        elif low > 0:
            passing.append((run, float(low), float(high)))
    latest = max(r.created_at for r in first_looks.values())
    if passing:
        run, low, high = passing[0]
        return item(
            "met",
            f"{len(passing)} of {len(first_looks)} first look(s) clear the bar.",
            f"Run #{run.id}: {run.strategy} {run.params} on {run.symbol}, "
            f"95% CI {_pct(low)} to {_pct(high)}.",
            run.created_at,
        )
    no_ci_note = f" {without_ci} had no stored CI." if without_ci else ""
    return item(
        "not_met",
        f"{len(first_looks)} first look(s); none with a 95% CI entirely above 0.{no_ci_note}",
        None,
        latest,
    )


def compute_graduation(runs: Sequence[RunRecord], now: datetime) -> GraduationOut:
    items = [
        GateItem(
            id="paper_days",
            title="3 months of paper trading",
            requirement=(
                f"At least {PAPER_DAYS_TARGET} days between the first and latest paper trade."
            ),
            status="not_started",
            progress=f"0 of {PAPER_DAYS_TARGET} days. Paper trading arrives in Phase 3.",
            evidence=None,
            source=PAPER_SOURCE,
            as_of=now,
        ),
        GateItem(
            id="paper_trades",
            title=f"{PAPER_TRADES_TARGET}+ paper trades",
            requirement=f"At least {PAPER_TRADES_TARGET} filled paper trades, all logged.",
            status="not_started",
            progress=f"0 of {PAPER_TRADES_TARGET} trades. Paper trading arrives in Phase 3.",
            evidence=None,
            source=PAPER_SOURCE,
            as_of=now,
        ),
        _oos_edge(runs, now),
        GateItem(
            id="zero_breaches",
            title="Zero risk-limit breaches",
            requirement="No risk-limit breaches or kill-switch trips during paper trading.",
            status="not_started",
            progress="Nothing to check until paper trading and risk limits exist (Phase 3).",
            evidence=None,
            source=PAPER_SOURCE,
            as_of=now,
        ),
    ]
    return GraduationOut(
        items=items,
        met=sum(1 for i in items if i.status == "met"),
        total=len(items),
        note=GATE_NOTE,
        as_of=now,
        source="Backtest run log and paper trading log (local SQLite)",
    )
