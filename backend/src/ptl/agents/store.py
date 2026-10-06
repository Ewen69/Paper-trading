"""SQL for agent jobs (status changes allowed) and agent findings (append-only)."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: int
    agent_id: str
    kind: str
    spec: dict[str, Any]
    status: str
    created_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FindingRecord:
    id: int
    job_id: int
    agent_id: str
    created_at: datetime
    symbol: str
    strategy: str
    params: dict[str, int]
    period: str
    run_id: int | None
    trades: int | None
    win_rate: float | None
    sharpe: float | None
    total_return: float | None
    excess_annualized: float | None
    note: str


def create_job(
    conn: sqlite3.Connection, agent_id: str, kind: str, spec: Mapping[str, Any], now: datetime
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO agent_jobs (agent_id, kind, spec, status, created_at) "
            "VALUES (?, ?, ?, 'queued', ?)",
            (agent_id, kind, json.dumps(dict(spec), sort_keys=True), now.isoformat()),
        )
    if cur.lastrowid is None:  # pragma: no cover
        raise RuntimeError("insert returned no id")
    return cur.lastrowid


def mark_running(conn: sqlite3.Connection, job_id: int, now: datetime) -> None:
    with conn:
        conn.execute(
            "UPDATE agent_jobs SET status = 'running', started_at = ? WHERE id = ?",
            (now.isoformat(), job_id),
        )


def finish_job(
    conn: sqlite3.Connection, job_id: int, ok: bool, summary: Mapping[str, Any], now: datetime
) -> None:
    with conn:
        conn.execute(
            "UPDATE agent_jobs SET status = ?, finished_at = ?, summary = ? WHERE id = ?",
            ("done" if ok else "failed", now.isoformat(), json.dumps(dict(summary)), job_id),
        )


def _job(r: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=r["id"],
        agent_id=r["agent_id"],
        kind=r["kind"],
        spec=json.loads(r["spec"]),
        status=r["status"],
        created_at=datetime.fromisoformat(r["created_at"]),
        finished_at=datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None,
        summary=json.loads(r["summary"]) if r["summary"] else None,
    )


def job(conn: sqlite3.Connection, job_id: int) -> JobRecord | None:
    row = conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
    return None if row is None else _job(row)


def recent_jobs(conn: sqlite3.Connection, limit: int = 50) -> list[JobRecord]:
    return [
        _job(r) for r in conn.execute("SELECT * FROM agent_jobs ORDER BY id DESC LIMIT ?", (limit,))
    ]


def last_job(conn: sqlite3.Connection, agent_id: str, kind: str) -> JobRecord | None:
    row = conn.execute(
        "SELECT * FROM agent_jobs WHERE agent_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
        (agent_id, kind),
    ).fetchone()
    return None if row is None else _job(row)


def fail_stale_jobs(conn: sqlite3.Connection, now: datetime) -> int:
    """Jobs left queued or running by a previous process can't resume; mark them failed."""
    with conn:
        cur = conn.execute(
            "UPDATE agent_jobs SET status = 'failed', finished_at = ?, "
            'summary = \'{"error": "interrupted by a restart"}\' '
            "WHERE status IN ('queued', 'running')",
            (now.isoformat(),),
        )
    return cur.rowcount


def record_finding(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    job_id: int,
    agent_id: str,
    now: datetime,
    symbol: str,
    strategy: str,
    params: Mapping[str, int],
    period: str,
    run_id: int | None,
    summary: Mapping[str, Any],
    note: str,
) -> FindingRecord:
    def num(key: str) -> float | None:
        value = summary.get(key)
        return None if value is None else float(value)

    trades = summary.get("trades")
    with conn:
        cur = conn.execute(
            "INSERT INTO agent_findings (job_id, agent_id, created_at, symbol, strategy, params, "
            "period, run_id, trades, win_rate, sharpe, total_return, excess_annualized, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                agent_id,
                now.isoformat(),
                symbol,
                strategy,
                json.dumps(dict(params), sort_keys=True),
                period,
                run_id,
                None if trades is None else int(trades),
                num("win_rate"),
                num("sharpe"),
                num("total_return"),
                num("excess_annualized_return"),
                note,
            ),
        )
    if cur.lastrowid is None:  # pragma: no cover
        raise RuntimeError("insert returned no id")
    row = conn.execute("SELECT * FROM agent_findings WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _finding(row)


def _finding(r: sqlite3.Row) -> FindingRecord:
    return FindingRecord(
        id=r["id"],
        job_id=r["job_id"],
        agent_id=r["agent_id"],
        created_at=datetime.fromisoformat(r["created_at"]),
        symbol=r["symbol"],
        strategy=r["strategy"],
        params=json.loads(r["params"]),
        period=r["period"],
        run_id=r["run_id"],
        trades=r["trades"],
        win_rate=r["win_rate"],
        sharpe=r["sharpe"],
        total_return=r["total_return"],
        excess_annualized=r["excess_annualized"],
        note=r["note"],
    )


def recent_findings(conn: sqlite3.Connection, limit: int = 50) -> list[FindingRecord]:
    rows = conn.execute("SELECT * FROM agent_findings ORDER BY id DESC LIMIT ?", (limit,))
    return [_finding(r) for r in rows]
