"""SQL for the learning optimizer and the background daemons.

`agent_learning_log`, `optimizer_active_best` and `daemon_log` are append-only (DB triggers).
`daemon_heartbeats` holds one upserted status row per daemon.
"""

import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from ptl.backtest.repository import canonical_json

Asset = Literal["equity", "options"]
Level = Literal["info", "warn", "error"]
# Event kinds. The UI raises a toast only for the major ones (see MAJOR_KINDS); everything else
# just scrolls through the terminal.
Kind = Literal[
    "info",
    "active_best",
    "budget",
    "idle",
    "risk",
    "kill_switch",
    "data_sync",
    "revalidation",
    "order",
    "error",
]
MAJOR_KINDS: frozenset[str] = frozenset(
    {"active_best", "budget", "idle", "risk", "kill_switch", "data_sync", "revalidation", "order"}
)


@dataclass(frozen=True, slots=True)
class Trial:
    id: int
    created_at: datetime
    search_id: str
    generation: int
    asset: Asset
    strategy: str
    symbol: str
    dataset_id: int
    options_dataset_id: int | None
    params: dict[str, int]
    period: str
    run_id: int | None
    reused: bool
    trades: int | None
    win_rate: float | None
    sharpe: float | None
    max_drawdown: float | None
    total_return: float | None
    excess_annualized: float | None
    fitness: float | None
    verdict: str
    note: str


@dataclass(frozen=True, slots=True)
class ActiveBest:
    id: int
    created_at: datetime
    search_id: str
    asset: Asset
    strategy: str
    symbol: str
    dataset_id: int
    options_dataset_id: int | None
    params: dict[str, int]
    in_sample_fitness: float
    in_sample_log_id: int
    oos_log_id: int | None
    validated: bool
    reason: str
    curve: dict[str, list[dict[str, Any]]]  # period -> downsampled equity points


@dataclass(frozen=True, slots=True)
class LogLine:
    id: int
    created_at: datetime
    daemon: str
    level: Level
    message: str
    kind: str = "info"


@dataclass(frozen=True, slots=True)
class Heartbeat:
    daemon: str
    pid: int
    started_at: datetime
    beat_at: datetime
    status: str
    detail: dict[str, Any]


def _trial(r: sqlite3.Row) -> Trial:
    return Trial(
        id=r["id"],
        created_at=datetime.fromisoformat(r["created_at"]),
        search_id=r["search_id"],
        generation=r["generation"],
        asset=r["asset"],
        strategy=r["strategy"],
        symbol=r["symbol"],
        dataset_id=r["dataset_id"],
        options_dataset_id=r["options_dataset_id"],
        params=json.loads(r["params"]),
        period=r["period"],
        run_id=r["run_id"],
        reused=bool(r["reused"]),
        trades=r["trades"],
        win_rate=r["win_rate"],
        sharpe=r["sharpe"],
        max_drawdown=r["max_drawdown"],
        total_return=r["total_return"],
        excess_annualized=r["excess_annualized"],
        fitness=r["fitness"],
        verdict=r["verdict"],
        note=r["note"],
    )


def _num(summary: Mapping[str, Any], key: str) -> float | None:
    value = summary.get(key)
    return None if value is None else float(value)


def log_trial(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    now: datetime,
    search_id: str,
    generation: int,
    asset: Asset,
    strategy: str,
    symbol: str,
    dataset_id: int,
    options_dataset_id: int | None,
    params: Mapping[str, int],
    period: str,
    run_id: int | None,
    reused: bool,
    summary: Mapping[str, Any],
    fitness: float | None,
    verdict: str,
    note: str,
) -> Trial:
    trades = summary.get("trades")
    with conn:
        cur = conn.execute(
            """
            INSERT INTO agent_learning_log (created_at, search_id, generation, asset, strategy,
                symbol, dataset_id, options_dataset_id, params, period, run_id, reused, trades,
                win_rate, sharpe, max_drawdown, total_return, excess_annualized, fitness,
                verdict, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now.isoformat(),
                search_id,
                generation,
                asset,
                strategy,
                symbol,
                dataset_id,
                options_dataset_id,
                canonical_json(params),
                period,
                run_id,
                int(reused),
                None if trades is None else int(trades),
                _num(summary, "win_rate"),
                _num(summary, "sharpe"),
                _num(summary, "max_drawdown"),
                _num(summary, "total_return"),
                _num(summary, "excess_annualized_return"),
                fitness,
                verdict,
                note,
            ),
        )
    row = conn.execute("SELECT * FROM agent_learning_log WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _trial(row)


def trials_after(conn: sqlite3.Connection, after_id: int, limit: int = 200) -> list[Trial]:
    rows = conn.execute(
        "SELECT * FROM agent_learning_log WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)
    )
    return [_trial(r) for r in rows]


def recent_trials(conn: sqlite3.Connection, limit: int = 50) -> list[Trial]:
    rows = conn.execute("SELECT * FROM agent_learning_log ORDER BY id DESC LIMIT ?", (limit,))
    return [_trial(r) for r in rows]


def trial_counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(period = 'in-sample') AS ins, "
        "SUM(period = 'out-of-sample') AS oos, SUM(reused) AS reused, "
        "COUNT(DISTINCT search_id) AS searches FROM agent_learning_log"
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "in_sample": int(row["ins"] or 0),
        "out_of_sample": int(row["oos"] or 0),
        "reused": int(row["reused"] or 0),
        "searches": int(row["searches"] or 0),
    }


def _best(r: sqlite3.Row) -> ActiveBest:
    return ActiveBest(
        id=r["id"],
        created_at=datetime.fromisoformat(r["created_at"]),
        search_id=r["search_id"],
        asset=r["asset"],
        strategy=r["strategy"],
        symbol=r["symbol"],
        dataset_id=r["dataset_id"],
        options_dataset_id=r["options_dataset_id"],
        params=json.loads(r["params"]),
        in_sample_fitness=r["in_sample_fitness"],
        in_sample_log_id=r["in_sample_log_id"],
        oos_log_id=r["oos_log_id"],
        validated=bool(r["validated"]),
        reason=r["reason"],
        curve=json.loads(r["curve"]),
    )


def record_active_best(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    now: datetime,
    search_id: str,
    trial: Trial,
    oos: Trial | None,
    validated: bool,
    reason: str,
    curve: dict[str, list[dict[str, Any]]],
    dataset_id: int | None = None,
) -> ActiveBest:
    if trial.fitness is None:
        raise ValueError("Active Best needs an in-sample fitness.")
    with conn:
        cur = conn.execute(
            """
            INSERT INTO optimizer_active_best (created_at, search_id, asset, strategy, symbol,
                dataset_id, options_dataset_id, params, in_sample_fitness, in_sample_log_id,
                oos_log_id, validated, reason, curve)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now.isoformat(),
                search_id,
                trial.asset,
                trial.strategy,
                trial.symbol,
                dataset_id if dataset_id is not None else trial.dataset_id,
                trial.options_dataset_id,
                canonical_json(trial.params),
                trial.fitness,
                trial.id,
                oos.id if oos else None,
                int(validated),
                reason,
                json.dumps(curve, separators=(",", ":")),
            ),
        )
    row = conn.execute(
        "SELECT * FROM optimizer_active_best WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return _best(row)


def active_best(conn: sqlite3.Connection, asset: Asset) -> ActiveBest | None:
    row = conn.execute(
        "SELECT * FROM optimizer_active_best WHERE asset = ? ORDER BY id DESC LIMIT 1", (asset,)
    ).fetchone()
    return _best(row) if row else None


def active_best_history(conn: sqlite3.Connection, limit: int = 20) -> list[ActiveBest]:
    rows = conn.execute("SELECT * FROM optimizer_active_best ORDER BY id DESC LIMIT ?", (limit,))
    return [_best(r) for r in rows]


def trial_by_id(conn: sqlite3.Connection, trial_id: int) -> Trial | None:
    row = conn.execute("SELECT * FROM agent_learning_log WHERE id = ?", (trial_id,)).fetchone()
    return _trial(row) if row else None


# ---- daemon log and heartbeats ------------------------------------------------------------


def log(  # noqa: PLR0913
    conn: sqlite3.Connection,
    daemon: str,
    message: str,
    now: datetime,
    level: Level = "info",
    *,
    kind: Kind = "info",
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO daemon_log (created_at, daemon, level, message, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            (now.isoformat(), daemon, level, message, kind),
        )


def log_after(conn: sqlite3.Connection, after_id: int, limit: int = 200) -> list[LogLine]:
    rows = conn.execute(
        "SELECT * FROM daemon_log WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)
    )
    return [
        LogLine(
            r["id"],
            datetime.fromisoformat(r["created_at"]),
            r["daemon"],
            r["level"],
            r["message"],
            r["kind"],
        )
        for r in rows
    ]


def recent_log(conn: sqlite3.Connection, limit: int = 100) -> list[LogLine]:
    rows = list(conn.execute("SELECT * FROM daemon_log ORDER BY id DESC LIMIT ?", (limit,)))
    return [
        LogLine(
            r["id"],
            datetime.fromisoformat(r["created_at"]),
            r["daemon"],
            r["level"],
            r["message"],
            r["kind"],
        )
        for r in reversed(rows)
    ]


def beat(  # noqa: PLR0913
    conn: sqlite3.Connection,
    daemon: str,
    *,
    started_at: datetime,
    now: datetime,
    status: str,
    detail: Mapping[str, Any],
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO daemon_heartbeats (daemon, pid, started_at, beat_at, status, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (daemon) DO UPDATE SET pid = excluded.pid,
                started_at = excluded.started_at, beat_at = excluded.beat_at,
                status = excluded.status, detail = excluded.detail
            """,
            (
                daemon,
                os.getpid(),
                started_at.isoformat(),
                now.isoformat(),
                status,
                json.dumps(detail, separators=(",", ":"), default=str),
            ),
        )


def heartbeats(conn: sqlite3.Connection) -> list[Heartbeat]:
    rows = conn.execute("SELECT * FROM daemon_heartbeats ORDER BY daemon")
    return [
        Heartbeat(
            daemon=r["daemon"],
            pid=r["pid"],
            started_at=datetime.fromisoformat(r["started_at"]),
            beat_at=datetime.fromisoformat(r["beat_at"]),
            status=r["status"],
            detail=json.loads(r["detail"]),
        )
        for r in rows
    ]


def scored_in_sample(conn: sqlite3.Connection, strategy: str, symbol: str) -> list[Trial]:
    """Every scored in-sample trial ever logged for a target, one per parameter set, best first."""
    rows = conn.execute(
        "SELECT * FROM agent_learning_log WHERE strategy = ? AND symbol = ? "
        "AND period = 'in-sample' AND fitness IS NOT NULL ORDER BY id",
        (strategy, symbol),
    )
    latest: dict[str, Trial] = {}
    for r in rows:
        t = _trial(r)
        latest[canonical_json(t.params)] = t
    return sorted(latest.values(), key=lambda t: t.fitness or 0.0, reverse=True)


def oos_trial(
    conn: sqlite3.Connection, strategy: str, symbol: str, params: Mapping[str, int]
) -> Trial | None:
    """The logged out-of-sample trial for exactly these params, if any."""
    row = conn.execute(
        "SELECT * FROM agent_learning_log WHERE strategy = ? AND symbol = ? AND params = ? "
        "AND period = 'out-of-sample' ORDER BY id LIMIT 1",
        (strategy, symbol, canonical_json(params)),
    ).fetchone()
    return _trial(row) if row else None
