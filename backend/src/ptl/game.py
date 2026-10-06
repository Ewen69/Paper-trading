"""Game layer: agents, mission log, XP and badges, all derived from real app records.

Rules:
- Every agent line and every event comes from a stored record or a live check, with its source
  and timestamp. Nothing is invented for flavor.
- XP rewards research process only (importing data, sealing out-of-sample data, testing
  out-of-sample once). Returns, wins, and profit never earn XP, and there are no streaks or
  leaderboards.
- Agents for unbuilt phases are shown locked, with the phase that unlocks them.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel

from ptl.audit import Flag, compute_flags
from ptl.backtest import repository as bt_repo
from ptl.config import Settings
from ptl.data import repository as data_repo
from ptl.data.live import Clock, QuoteSource
from ptl.data_api import DataHealthOut, compute_data_health
from ptl.db import open_db
from ptl.graduation import GraduationOut, compute_graduation
from ptl.market_calendar import MarketCalendar

AgentStatus = Literal["ok", "warning", "error", "idle", "locked"]

LEVEL_XP = 100
AGENT_LEVEL_XP = 50
TOO_MANY_COMBINATIONS = 5
RESTRAINT_MAX_COMBINATIONS = 3
COST_STRESS_BPS = 10.0
RUN_LOG_SOURCE = "Backtest run log (local SQLite)"
AUDIT_SOURCE = "Auditor (data findings and stored backtest warnings)"
AUDITOR_LINES = 3


@dataclass(frozen=True, slots=True)
class XpRule:
    id: str
    description: str
    xp: int


XP_RULES: tuple[XpRule, ...] = (
    XpRule("import", "Import a historical dataset", 20),
    XpRule("oos_lock", "Seal a symbol's out-of-sample period", 30),
    XpRule("first_in_sample", "First in-sample backtest on a symbol", 15),
    XpRule(
        "first_oos",
        "First out-of-sample test of a symbol, after doing in-sample work on it first",
        50,
    ),
)
_XP = {rule.id: rule.xp for rule in XP_RULES}
XP_POLICY = (
    "XP is earned for research process only. Returns, wins and profit never earn XP. "
    "Repeat looks at out-of-sample data earn nothing."
)


class ReportLine(BaseModel):
    text: str
    source: str
    as_of: AwareDatetime


class AgentOut(BaseModel):
    id: str
    name: str
    role: str
    station: str | None
    status: AgentStatus
    unlocks_in: str | None
    xp: int
    level: int
    report: list[ReportLine]


class EventOut(BaseModel):
    at: AwareDatetime
    agent: str
    kind: str
    text: str
    xp: int
    xp_note: str | None
    source: str


class BadgeOut(BaseModel):
    id: str
    name: str
    description: str
    earned: bool
    earned_at: AwareDatetime | None
    detail: str | None


class XpRuleOut(BaseModel):
    id: str
    description: str
    xp: int


class PlayerOut(BaseModel):
    xp: int
    level: int
    level_start_xp: int
    next_level_xp: int


class GameStateOut(BaseModel):
    as_of: AwareDatetime
    source: str
    market_open: bool
    last_completed_session: str
    player: PlayerOut
    agents: list[AgentOut]
    events: list[EventOut]
    badges: list[BadgeOut]
    xp_rules: list[XpRuleOut]
    xp_policy: str
    flags: list[Flag]
    graduation: GraduationOut


@dataclass(frozen=True, slots=True)
class _Locked:
    id: str
    name: str
    role: str
    unlocks_in: str


LOCKED_AGENTS: tuple[_Locked, ...] = (
    _Locked("risk", "Risk Officer", "Enforces loss limits and the kill switch", "Phase 3"),
    _Locked("trader", "Paper Trader", "Places orders on the Alpaca paper account", "Phase 3"),
    _Locked("accountant", "Accountant", "Tracks net worth from your manual entries", "Phase 4"),
    _Locked("analyst", "Analyst", "Portfolio allocation, risk and Greeks", "Phase 5"),
    _Locked(
        "lead",
        "Lead Reviewer",
        "AI agents report here; checks every claim against computed numbers",
        "AI agents phase",
    ),
)


def _level(xp: int, size: int) -> int:
    return xp // size + 1


# ---- Events and XP --------------------------------------------------------------------------


def _events(
    datasets: Sequence[data_repo.DatasetRecord],
    locks: Sequence[bt_repo.OosLock],
    runs: Sequence[bt_repo.RunRecord],
) -> list[EventOut]:
    events: list[EventOut] = []
    for d in datasets:
        kind = "daily bars" if d.kind.value == "equity_bars" else "option quotes"
        events.append(
            EventOut(
                at=d.imported_at,
                agent="scout",
                kind="import",
                text=f"Imported {d.file_name}: {d.row_count:,} rows of {kind} from {d.source}.",
                xp=_XP["import"],
                xp_note=None,
                source=f"Dataset #{d.id} (local SQLite)",
            )
        )
    for lock in locks:
        events.append(
            EventOut(
                at=lock.locked_at,
                agent="quant",
                kind="oos_lock",
                text=f"Sealed {lock.symbol} out-of-sample data from {lock.oos_start}.",
                xp=_XP["oos_lock"],
                xp_note=None,
                source="Out-of-sample locks (local SQLite)",
            )
        )
    in_sample_symbols: set[str] = set()
    oos_symbols: set[str] = set()
    for run in runs:
        params = ", ".join(f"{k}={v}" for k, v in sorted(run.params.items())) or "no params"
        label = f"#{run.id} {run.strategy} ({params}) on {run.symbol}"
        if run.period == "in-sample":
            first = run.symbol not in in_sample_symbols
            in_sample_symbols.add(run.symbol)
            xp = _XP["first_in_sample"] if first else 0
            note = None if first else "XP only for the first in-sample run per symbol"
            text = f"In-sample backtest {label}."
        else:
            first = run.symbol not in oos_symbols
            oos_symbols.add(run.symbol)
            prepared = run.symbol in in_sample_symbols
            xp = _XP["first_oos"] if first and prepared else 0
            note = (
                None
                if xp
                else "repeat look at the holdout: no XP"
                if not first
                else "no in-sample work first: no XP"
            )
            text = f"Out-of-sample test {label}."
        events.append(
            EventOut(
                at=run.created_at,
                agent="quant",
                kind=f"run_{run.period}",
                text=text,
                xp=xp,
                xp_note=note,
                source=f"{RUN_LOG_SOURCE}, run #{run.id}",
            )
        )
    # Newest first. Ties (same timestamp) keep record order: imports, then locks, then runs.
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1].at, pair[0]), reverse=True)
    return [event for _, event in ordered]


# ---- Badges ---------------------------------------------------------------------------------


def _badges(
    datasets: Sequence[data_repo.DatasetRecord],
    clean_dataset_ids: set[int],
    locks: Sequence[bt_repo.OosLock],
    runs: Sequence[bt_repo.RunRecord],
) -> list[BadgeOut]:
    def badge(
        id_: str, name: str, description: str, at: datetime | None, detail: str | None = None
    ) -> BadgeOut:
        return BadgeOut(
            id=id_,
            name=name,
            description=description,
            earned=at is not None,
            earned_at=at,
            detail=detail if at is not None else None,
        )

    first_import = min(datasets, key=lambda d: d.imported_at, default=None)
    clean = min(
        (d for d in datasets if d.id in clean_dataset_ids),
        key=lambda d: d.imported_at,
        default=None,
    )
    first_lock = min(locks, key=lambda lock: lock.locked_at, default=None)
    stress = next((r for r in runs if r.costs.get("slippage_bps", 0) >= COST_STRESS_BPS), None)

    # Per symbol: in-sample combinations tried before its first out-of-sample look.
    combos_before: dict[str, set[str]] = defaultdict(set)
    oos_runs: dict[str, list[bt_repo.RunRecord]] = defaultdict(list)
    for r in runs:
        if r.period == "in-sample" and not oos_runs[r.symbol]:
            combos_before[r.symbol].add(f"{r.strategy} {sorted(r.params.items())}")
        elif r.period == "out-of-sample":
            oos_runs[r.symbol].append(r)
    restraint = next(
        (
            (sym, looks[0])
            for sym, looks in oos_runs.items()
            if looks and 0 < len(combos_before[sym]) <= RESTRAINT_MAX_COMBINATIONS
        ),
        None,
    )
    one_shot = next(
        (
            (sym, looks[0])
            for sym, looks in oos_runs.items()
            if len(looks) == 1 and combos_before[sym]
        ),
        None,
    )

    return [
        badge(
            "first_contact",
            "First Contact",
            "Import your first historical dataset.",
            first_import.imported_at if first_import else None,
            first_import.file_name if first_import else None,
        ),
        badge(
            "clean_room",
            "Clean Room",
            "Import a dataset with zero quality findings.",
            clean.imported_at if clean else None,
            clean.file_name if clean else None,
        ),
        badge(
            "sealed_vault",
            "Sealed Vault",
            "Lock a symbol's out-of-sample period before tuning.",
            first_lock.locked_at if first_lock else None,
            first_lock.symbol if first_lock else None,
        ),
        badge(
            "restraint",
            "Restraint",
            f"Try {RESTRAINT_MAX_COMBINATIONS} or fewer parameter combinations before testing a "
            "symbol out-of-sample.",
            restraint[1].created_at if restraint else None,
            restraint[0] if restraint else None,
        ),
        badge(
            "one_shot",
            "One Shot",
            "Look at a symbol's out-of-sample period exactly once. Lost if you look again.",
            one_shot[1].created_at if one_shot else None,
            one_shot[0] if one_shot else None,
        ),
        badge(
            "cost_realist",
            "Cost Realist",
            f"Stress-test costs: run a backtest with {COST_STRESS_BPS:g}+ bps slippage.",
            stress.created_at if stress else None,
            f"run #{stress.id}" if stress else None,
        ),
    ]


# ---- Agents ---------------------------------------------------------------------------------


def _scout(health: DataHealthOut, xp: int) -> AgentOut:
    live = health.live_source
    datasets = health.datasets
    rows = sum(d.row_count for d in datasets)
    stale = sum(1 for d in datasets if d.provenance.stale)
    errors = sum(1 for d in datasets if d.status == "error")
    sqlite_source = "Imported datasets (local SQLite)"
    lines = [
        ReportLine(
            text=(
                f"{len(datasets)} dataset(s), {rows:,} rows. {stale} not current, "
                f"{errors} with error-level findings."
                if datasets
                else "No historical datasets yet. Import a CSV to start (docs/DATA.md)."
            ),
            source=sqlite_source,
            as_of=health.as_of,
        )
    ]
    if not live.configured:
        live_text = "Live feed offline: paper API keys are not set in .env."
    elif live.reachable:
        live_text = (
            f"Live feed online. Paper account {live.account_status}. Stocks: "
            f"{live.stock_feed.name} ({live.stock_feed.data_type}); options: "
            f"{live.option_feed.name} ({live.option_feed.data_type})."
        )
    else:
        live_text = f"Live feed error: {live.error}"
    lines.append(
        ReportLine(text=live_text, source="Alpaca paper account check", as_of=live.checked_at)
    )
    lines.append(
        ReportLine(
            text=(
                f"NYSE is {'open' if health.market_open else 'closed'}. Last completed session: "
                f"{health.last_completed_session}."
            ),
            source="NYSE calendar (exchange_calendars)",
            as_of=health.as_of,
        )
    )
    status: AgentStatus = "idle" if health.overall == "no data" else health.overall
    return AgentOut(
        id="scout",
        name="Data Scout",
        role="Imports data, checks its quality and watches the live feed",
        station="data-health",
        status=status,
        unlocks_in=None,
        xp=xp,
        level=_level(xp, AGENT_LEVEL_XP),
        report=lines,
    )


def _quant(runs: Sequence[bt_repo.RunRecord], now: datetime, xp: int) -> AgentOut:
    if not runs:
        return AgentOut(
            id="quant",
            name="Quant",
            role="Runs honest backtests and guards the out-of-sample vault",
            station="strategy-lab",
            status="idle",
            unlocks_in=None,
            xp=xp,
            level=_level(xp, AGENT_LEVEL_XP),
            report=[
                ReportLine(
                    text="No backtests yet. Import daily bars, then open the Strategy Lab.",
                    source=RUN_LOG_SOURCE,
                    as_of=now,
                )
            ],
        )
    in_sample = [r for r in runs if r.period == "in-sample"]
    by_symbol: dict[str, list[bt_repo.RunRecord]] = defaultdict(list)
    for r in runs:
        by_symbol[r.symbol].append(r)
    lines = [
        ReportLine(
            text=(
                f"{len(runs)} backtest(s) logged ({len(in_sample)} in-sample, "
                f"{len(runs) - len(in_sample)} out-of-sample) on {len(by_symbol)} symbol(s)."
            ),
            source=RUN_LOG_SOURCE,
            as_of=runs[-1].created_at,
        )
    ]
    caution = False
    busiest = sorted(by_symbol.items(), key=lambda kv: len(kv[1]), reverse=True)[:2]
    for symbol, symbol_runs in busiest:
        combos = {
            f"{r.strategy} {sorted(r.params.items())}"
            for r in symbol_runs
            if r.period == "in-sample"
        }
        looks = sum(1 for r in symbol_runs if r.period == "out-of-sample")
        text = (
            f"{symbol}: {len(combos)} parameter combination(s) tried in-sample; "
            f"out-of-sample looked at {looks} time(s)."
        )
        if len(combos) > TOO_MANY_COMBINATIONS or looks > 1:
            caution = True
            text += " Caution: results on this symbol are biased upward."
        lines.append(ReportLine(text=text, source=RUN_LOG_SOURCE, as_of=symbol_runs[-1].created_at))
    return AgentOut(
        id="quant",
        name="Quant",
        role="Runs honest backtests and guards the out-of-sample vault",
        station="strategy-lab",
        status="warning" if caution else "ok",
        unlocks_in=None,
        xp=xp,
        level=_level(xp, AGENT_LEVEL_XP),
        report=lines,
    )


def _auditor(flags: Sequence[Flag], has_material: bool, now: datetime) -> AgentOut:
    if not has_material:
        status: AgentStatus = "idle"
        lines = [ReportLine(text="Nothing to audit yet.", source=AUDIT_SOURCE, as_of=now)]
    else:
        severities = {f.severity for f in flags}
        status = (
            "error" if "error" in severities else "warning" if "warning" in severities else "ok"
        )
        lines = [
            ReportLine(text=f"{f.title}: {f.summary}", source=f.source, as_of=f.as_of)
            for f in flags[:AUDITOR_LINES]
        ]
        if len(flags) > AUDITOR_LINES:
            lines.append(
                ReportLine(
                    text=f"{len(flags) - AUDITOR_LINES} more flag(s) in the Auditor's station.",
                    source=AUDIT_SOURCE,
                    as_of=now,
                )
            )
        if not flags:
            lines = [
                ReportLine(
                    text="No open flags on the current data and latest results.",
                    source=AUDIT_SOURCE,
                    as_of=now,
                )
            ]
    return AgentOut(
        id="auditor",
        name="Auditor",
        role="Flags weak evidence: small samples, data gaps, over-tuning, reused holdouts",
        station="audit",
        status=status,
        unlocks_in=None,
        xp=0,
        level=1,
        report=lines,
    )


def compute_game_state(
    conn: sqlite3.Connection, health: DataHealthOut, now: datetime
) -> GameStateOut:
    datasets = data_repo.list_datasets(conn)
    clean_ids = {d.id for d in datasets if not data_repo.issues_for(conn, d.id)}
    locks = bt_repo.list_locks(conn)
    runs = bt_repo.all_runs(conn)
    flags = compute_flags(health, runs, bt_repo.warnings_by_run(conn))

    events = _events(datasets, locks, runs)
    xp_by_agent: dict[str, int] = defaultdict(int)
    for event in events:
        xp_by_agent[event.agent] += event.xp
    total = sum(xp_by_agent.values())
    level = _level(total, LEVEL_XP)

    agents = [
        _scout(health, xp_by_agent["scout"]),
        _quant(runs, now, xp_by_agent["quant"]),
        _auditor(flags, bool(datasets or runs), now),
        *(
            AgentOut(
                id=a.id,
                name=a.name,
                role=a.role,
                station=None,
                status="locked",
                unlocks_in=a.unlocks_in,
                xp=0,
                level=0,
                report=[],
            )
            for a in LOCKED_AGENTS
        ),
    ]
    return GameStateOut(
        as_of=now,
        source="Computed from local records (datasets, out-of-sample locks, backtest run log) "
        "and the live source check",
        market_open=health.market_open,
        last_completed_session=health.last_completed_session.isoformat(),
        player=PlayerOut(
            xp=total,
            level=level,
            level_start_xp=(level - 1) * LEVEL_XP,
            next_level_xp=level * LEVEL_XP,
        ),
        agents=agents,
        events=events,
        badges=_badges(datasets, clean_ids, locks, runs),
        xp_rules=[XpRuleOut(id=r.id, description=r.description, xp=r.xp) for r in XP_RULES],
        xp_policy=XP_POLICY,
        flags=flags,
        graduation=compute_graduation(runs, now),
    )


def build_game_router(
    settings: Settings, source: QuoteSource, calendar: MarketCalendar, clock: Clock
) -> APIRouter:
    router = APIRouter()

    @router.get("/game/state")
    def game_state() -> GameStateOut:
        now = clock()
        health = compute_data_health(settings, source, calendar, now)
        with open_db(settings.database_path) as conn:
            return compute_game_state(conn, health, now)

    return router
