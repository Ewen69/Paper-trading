"""SQL for paper runners, cycles, orders and order events (the log tables are append-only)."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from ptl.paper.broker import FINAL_STATUSES, Mode, Side


@dataclass(frozen=True, slots=True)
class RunnerRecord:
    id: int
    created_at: datetime
    strategy: str
    params: dict[str, int]
    dataset_id: int
    symbol: str
    dry_run: bool
    active: bool


@dataclass(frozen=True, slots=True)
class CycleRecord:
    id: int
    runner_id: int | None
    created_at: datetime
    session: date
    mode: Mode
    strategy: str
    symbol: str
    explanation: str
    target: float | None
    price: float | None
    price_source: str
    current_qty: float | None
    desired_qty: float | None
    outcome: str


@dataclass(frozen=True, slots=True)
class OrderRecord:
    id: int
    cycle_id: int
    created_at: datetime
    mode: Mode
    symbol: str
    side: Side
    qty: float
    reason: str
    risk_decision_id: int
    broker_order_id: str | None
    status: str


def _id(cursor: sqlite3.Cursor) -> int:
    if cursor.lastrowid is None:  # pragma: no cover
        raise RuntimeError("insert returned no id")
    return cursor.lastrowid


def create_runner(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    now: datetime,
    strategy: str,
    params: Mapping[str, int],
    dataset_id: int,
    symbol: str,
    dry_run: bool,
) -> int:
    with conn:
        return _id(
            conn.execute(
                "INSERT INTO paper_runners (created_at, strategy, params, dataset_id, symbol, "
                "dry_run, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (
                    now.isoformat(),
                    strategy,
                    json.dumps(dict(params)),
                    dataset_id,
                    symbol,
                    int(dry_run),
                ),
            )
        )


def set_runner_active(conn: sqlite3.Connection, runner_id: int, active: bool) -> bool:
    with conn:
        cur = conn.execute(
            "UPDATE paper_runners SET active = ? WHERE id = ?", (int(active), runner_id)
        )
    return cur.rowcount > 0


def runners(conn: sqlite3.Connection, active_only: bool = False) -> list[RunnerRecord]:
    sql = (
        "SELECT * FROM paper_runners WHERE active = 1 ORDER BY id"
        if active_only
        else "SELECT * FROM paper_runners ORDER BY id"
    )
    return [
        RunnerRecord(
            id=r["id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            strategy=r["strategy"],
            params=json.loads(r["params"]),
            dataset_id=r["dataset_id"],
            symbol=r["symbol"],
            dry_run=bool(r["dry_run"]),
            active=bool(r["active"]),
        )
        for r in conn.execute(sql)
    ]


def record_cycle(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    runner_id: int | None,
    now: datetime,
    session: date,
    mode: Mode,
    strategy: str,
    params: Mapping[str, int],
    symbol: str,
    explanation: str,
    target: float | None,
    price: float | None,
    price_source: str,
    current_qty: float | None,
    desired_qty: float | None,
    outcome: str,
) -> int:
    with conn:
        return _id(
            conn.execute(
                "INSERT INTO paper_cycles (runner_id, created_at, session, mode, strategy, params, "
                "symbol, explanation, target, price, price_source, current_qty, desired_qty, "
                "outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    runner_id,
                    now.isoformat(),
                    session.isoformat(),
                    mode,
                    strategy,
                    json.dumps(dict(params)),
                    symbol,
                    explanation,
                    target,
                    price,
                    price_source,
                    current_qty,
                    desired_qty,
                    outcome,
                ),
            )
        )


def record_order(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    cycle_id: int,
    now: datetime,
    mode: Mode,
    symbol: str,
    side: Side,
    qty: float,
    reason: str,
    risk_decision_id: int,
    broker_order_id: str | None,
    status: str,
) -> int:
    with conn:
        return _id(
            conn.execute(
                "INSERT INTO paper_orders (cycle_id, created_at, mode, symbol, side, qty, reason, "
                "risk_decision_id, broker_order_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cycle_id,
                    now.isoformat(),
                    mode,
                    symbol,
                    side,
                    qty,
                    reason,
                    risk_decision_id,
                    broker_order_id,
                    status,
                ),
            )
        )


def record_event(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    order_id: int,
    now: datetime,
    status: str,
    filled_qty: float | None,
    fill_price: float | None,
    detail: str,
) -> int:
    with conn:
        return _id(
            conn.execute(
                "INSERT INTO paper_order_events (order_id, at, status, filled_qty, fill_price, "
                "detail) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, now.isoformat(), status, filled_qty, fill_price, detail),
            )
        )


def latest_status(conn: sqlite3.Connection, order_id: int) -> str:
    row = conn.execute(
        "SELECT status FROM paper_order_events WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    return str(row["status"]) if row else ""


def open_paper_orders(conn: sqlite3.Connection) -> list[OrderRecord]:
    return [
        o
        for o in recent_orders(conn, 500)
        if o.mode == "paper" and latest_status(conn, o.id) not in FINAL_STATUSES
    ]


def recent_cycles(conn: sqlite3.Connection, limit: int = 50) -> list[CycleRecord]:
    rows = conn.execute("SELECT * FROM paper_cycles ORDER BY id DESC LIMIT ?", (limit,))
    return [
        CycleRecord(
            id=r["id"],
            runner_id=r["runner_id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            session=date.fromisoformat(r["session"]),
            mode=r["mode"],
            strategy=r["strategy"],
            symbol=r["symbol"],
            explanation=r["explanation"],
            target=r["target"],
            price=r["price"],
            price_source=r["price_source"],
            current_qty=r["current_qty"],
            desired_qty=r["desired_qty"],
            outcome=r["outcome"],
        )
        for r in rows
    ]


def recent_orders(conn: sqlite3.Connection, limit: int = 50) -> list[OrderRecord]:
    rows = conn.execute("SELECT * FROM paper_orders ORDER BY id DESC LIMIT ?", (limit,))
    return [
        OrderRecord(
            id=r["id"],
            cycle_id=r["cycle_id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            mode=r["mode"],
            symbol=r["symbol"],
            side=r["side"],
            qty=r["qty"],
            reason=r["reason"],
            risk_decision_id=r["risk_decision_id"],
            broker_order_id=r["broker_order_id"],
            status=r["status"],
        )
        for r in rows
    ]


def last_session_for_runner(conn: sqlite3.Connection, runner_id: int) -> date | None:
    row = conn.execute(
        "SELECT MAX(session) AS s FROM paper_cycles WHERE runner_id = ?", (runner_id,)
    ).fetchone()
    return date.fromisoformat(row["s"]) if row and row["s"] else None
