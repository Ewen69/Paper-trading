"""SQL for backtest inputs (bars), out-of-sample locks, and the append-only run log."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from ptl.data.models import EquityBar

Period = Literal["in-sample", "out-of-sample"]


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    dataset_id: int
    symbol: str
    source: str
    file_name: str
    first_date: date
    last_date: date
    sessions: int
    has_adj_close: bool


@dataclass(frozen=True, slots=True)
class OosLock:
    symbol: str
    oos_start: date
    oos_fraction: float
    locked_at: datetime
    basis: str


@dataclass(frozen=True, slots=True)
class RunCounts:
    in_sample_combinations_this_strategy: int
    in_sample_combinations_all_strategies: int
    in_sample_runs: int
    oos_evaluations: int


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: int
    created_at: datetime
    symbol: str
    period: Period
    strategy: str
    params: dict[str, int]
    window_start: date
    window_end: date
    summary: dict[str, float | int | None]


def canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def equity_universe(conn: sqlite3.Connection) -> list[UniverseEntry]:
    rows = conn.execute(
        "SELECT b.dataset_id, b.symbol, d.source, d.file_name, MIN(b.session_date) AS first, "
        "MAX(b.session_date) AS last, COUNT(*) AS sessions, "
        "SUM(b.adj_close IS NULL) AS missing_adj "
        "FROM equity_bars b JOIN datasets d ON d.id = b.dataset_id "
        "GROUP BY b.dataset_id, b.symbol ORDER BY b.symbol, b.dataset_id"
    )
    return [
        UniverseEntry(
            dataset_id=r["dataset_id"],
            symbol=r["symbol"],
            source=r["source"],
            file_name=r["file_name"],
            first_date=date.fromisoformat(r["first"]),
            last_date=date.fromisoformat(r["last"]),
            sessions=r["sessions"],
            has_adj_close=r["missing_adj"] == 0,
        )
        for r in rows
    ]


def load_equity_bars(conn: sqlite3.Connection, dataset_id: int, symbol: str) -> list[EquityBar]:
    rows = conn.execute(
        "SELECT * FROM equity_bars WHERE dataset_id = ? AND symbol = ? ORDER BY session_date",
        (dataset_id, symbol),
    )
    return [
        EquityBar(
            symbol=r["symbol"],
            session_date=date.fromisoformat(r["session_date"]),
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            adj_close=r["adj_close"],
        )
        for r in rows
    ]


def get_lock(conn: sqlite3.Connection, symbol: str) -> OosLock | None:
    row = conn.execute("SELECT * FROM oos_locks WHERE symbol = ?", (symbol,)).fetchone()
    if row is None:
        return None
    return OosLock(
        symbol=row["symbol"],
        oos_start=date.fromisoformat(row["oos_start"]),
        oos_fraction=row["oos_fraction"],
        locked_at=datetime.fromisoformat(row["locked_at"]),
        basis=row["basis"],
    )


def create_lock(conn: sqlite3.Connection, lock: OosLock) -> None:
    with conn:
        conn.execute(
            "INSERT INTO oos_locks (symbol, oos_start, oos_fraction, locked_at, basis) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                lock.symbol,
                lock.oos_start.isoformat(),
                lock.oos_fraction,
                lock.locked_at.isoformat(),
                lock.basis,
            ),
        )


def list_locks(conn: sqlite3.Connection) -> list[OosLock]:
    symbols = [r["symbol"] for r in conn.execute("SELECT symbol FROM oos_locks ORDER BY symbol")]
    return [lock for s in symbols if (lock := get_lock(conn, s)) is not None]


def record_run(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    created_at: datetime,
    symbol: str,
    period: Period,
    strategy: str,
    params: Mapping[str, int],
    costs: Mapping[str, float],
    dataset_sha256: str,
    window: tuple[date, date],
    benchmark_symbol: str,
    summary: Mapping[str, float | int | None],
) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO backtest_runs (created_at, symbol, period, strategy, params, costs, "
            "dataset_sha256, window_start, window_end, benchmark_symbol, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                created_at.isoformat(),
                symbol,
                period,
                strategy,
                canonical_json(params),
                canonical_json(costs),
                dataset_sha256,
                window[0].isoformat(),
                window[1].isoformat(),
                benchmark_symbol,
                canonical_json(summary),
            ),
        )
    if cursor.lastrowid is None:  # pragma: no cover
        raise RuntimeError("run insert returned no id")
    return cursor.lastrowid


def run_counts(conn: sqlite3.Connection, symbol: str, strategy: str) -> RunCounts:
    row = conn.execute(
        "SELECT "
        "COUNT(DISTINCT CASE WHEN period = 'in-sample' AND strategy = :strategy THEN params END),"
        "COUNT(DISTINCT CASE WHEN period = 'in-sample' THEN strategy || ' ' || params END),"
        "SUM(period = 'in-sample'), SUM(period = 'out-of-sample') "
        "FROM backtest_runs WHERE symbol = :symbol",
        {"symbol": symbol, "strategy": strategy},
    ).fetchone()
    return RunCounts(row[0], row[1], row[2] or 0, row[3] or 0)


def list_runs(conn: sqlite3.Connection, limit: int = 200) -> list[RunRecord]:
    rows = conn.execute("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))
    return [
        RunRecord(
            id=r["id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            symbol=r["symbol"],
            period=r["period"],
            strategy=r["strategy"],
            params=json.loads(r["params"]),
            window_start=date.fromisoformat(r["window_start"]),
            window_end=date.fromisoformat(r["window_end"]),
            summary=json.loads(r["summary"]),
        )
        for r in rows
    ]
