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


@dataclass(frozen=True, slots=True)
class PaperEvidence:
    """What the paper log proves so far. Dry-run orders never count; only paper-account fills."""

    filled_trades: int
    first_fill: datetime | None
    last_fill: datetime | None
    kill_switch_trips: int
    decisions: int


def paper_evidence(conn: sqlite3.Connection) -> PaperEvidence:
    fills = conn.execute(
        """
        SELECT COUNT(*) AS n, MIN(e.at) AS first, MAX(e.at) AS last
        FROM paper_orders o
        JOIN paper_order_events e ON e.order_id = o.id
        WHERE o.mode = 'paper' AND e.status = 'filled'
          AND e.id = (SELECT MAX(id) FROM paper_order_events WHERE order_id = o.id)
        """
    ).fetchone()
    risk = conn.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(tripped), 0) AS trips
        FROM risk_decisions WHERE source = 'paper runner (paper)'
        """
    ).fetchone()
    return PaperEvidence(
        filled_trades=int(fills["n"]),
        first_fill=datetime.fromisoformat(fills["first"]) if fills["first"] else None,
        last_fill=datetime.fromisoformat(fills["last"]) if fills["last"] else None,
        kill_switch_trips=int(risk["trips"]),
        decisions=int(risk["n"]),
    )


@dataclass(frozen=True, slots=True)
class Execution:
    """Quote the order was sized at, its fill, and the slippage between them (+ = worse)."""

    quote_price: float | None
    fill_price: float | None
    slippage_per_share: float | None
    slippage_bps: float | None


def execution(conn: sqlite3.Connection, order: OrderRecord) -> Execution:
    quote_row = conn.execute(
        "SELECT price FROM paper_cycles WHERE id = ?", (order.cycle_id,)
    ).fetchone()
    fill_row = conn.execute(
        "SELECT fill_price FROM paper_order_events WHERE order_id = ? AND fill_price IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (order.id,),
    ).fetchone()
    quote = None if quote_row is None or quote_row["price"] is None else float(quote_row["price"])
    fill = None if fill_row is None else float(fill_row["fill_price"])
    if quote is None or fill is None or quote <= 0:
        return Execution(quote, fill, None, None)
    slip = fill - quote if order.side == "buy" else quote - fill
    return Execution(quote, fill, slip, slip / quote * 10_000)


@dataclass(frozen=True, slots=True)
class SpreadRecord:
    id: int
    created_at: datetime
    runner_id: int | None
    cycle_id: int
    mode: Mode
    underlying: str
    expiration: date
    option_type: str
    short_symbol: str
    long_symbol: str
    short_strike: float
    long_strike: float
    contracts: int
    credit: float  # per share, as quoted at submission
    collateral: float  # total dollars: width x 100 x contracts - credit x 100 x contracts
    open_order_id: int
    close_order_id: int | None
    status: str
    closed_at: datetime | None
    close_debit: float | None
    note: str

    @property
    def label(self) -> str:
        kind = "P" if self.option_type == "put" else "C"
        return (
            f"{self.underlying} {self.expiration} {self.short_strike:g}/{self.long_strike:g}{kind}"
        )


def _spread(r: sqlite3.Row) -> SpreadRecord:
    return SpreadRecord(
        id=r["id"],
        created_at=datetime.fromisoformat(r["created_at"]),
        runner_id=r["runner_id"],
        cycle_id=r["cycle_id"],
        mode=r["mode"],
        underlying=r["underlying"],
        expiration=date.fromisoformat(r["expiration"]),
        option_type=r["option_type"],
        short_symbol=r["short_symbol"],
        long_symbol=r["long_symbol"],
        short_strike=r["short_strike"],
        long_strike=r["long_strike"],
        contracts=r["contracts"],
        credit=r["credit"],
        collateral=r["collateral"],
        open_order_id=r["open_order_id"],
        close_order_id=r["close_order_id"],
        status=r["status"],
        closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
        close_debit=r["close_debit"],
        note=r["note"],
    )


def create_spread(  # noqa: PLR0913
    conn: sqlite3.Connection,
    *,
    now: datetime,
    runner_id: int | None,
    cycle_id: int,
    mode: Mode,
    underlying: str,
    expiration: date,
    option_type: str,
    short_symbol: str,
    long_symbol: str,
    short_strike: float,
    long_strike: float,
    contracts: int,
    credit: float,
    collateral: float,
    open_order_id: int,
    note: str,
) -> int:
    with conn:
        return _id(
            conn.execute(
                """
                INSERT INTO paper_spreads (created_at, runner_id, cycle_id, mode, underlying,
                    expiration, option_type, short_symbol, long_symbol, short_strike,
                    long_strike, contracts, credit, collateral, open_order_id, status, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    now.isoformat(),
                    runner_id,
                    cycle_id,
                    mode,
                    underlying,
                    expiration.isoformat(),
                    option_type,
                    short_symbol,
                    long_symbol,
                    short_strike,
                    long_strike,
                    contracts,
                    credit,
                    collateral,
                    open_order_id,
                    note,
                ),
            )
        )


def spreads(
    conn: sqlite3.Connection,
    *,
    statuses: tuple[str, ...] = ("open", "closing"),
    runner_id: int | None = None,
) -> list[SpreadRecord]:
    marks = ", ".join("?" for _ in statuses)
    sql = f"SELECT * FROM paper_spreads WHERE status IN ({marks})"  # noqa: S608 - placeholders only
    args: list[object] = list(statuses)
    if runner_id is not None:
        sql += " AND runner_id = ?"
        args.append(runner_id)
    return [_spread(r) for r in conn.execute(sql + " ORDER BY id", args)]


def update_spread(  # noqa: PLR0913
    conn: sqlite3.Connection,
    spread_id: int,
    *,
    status: str,
    close_order_id: int | None = None,
    closed_at: datetime | None = None,
    close_debit: float | None = None,
    note: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE paper_spreads SET status = ?, close_order_id = COALESCE(?, close_order_id), "
            "closed_at = COALESCE(?, closed_at), close_debit = COALESCE(?, close_debit), "
            "note = COALESCE(?, note) WHERE id = ?",
            (
                status,
                close_order_id,
                closed_at.isoformat() if closed_at else None,
                close_debit,
                note,
                spread_id,
            ),
        )


def last_fill(conn: sqlite3.Connection, order_id: int) -> float | None:
    row = conn.execute(
        "SELECT fill_price FROM paper_order_events WHERE order_id = ? AND fill_price IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    return None if row is None else float(row["fill_price"])
