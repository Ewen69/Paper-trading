"""Kill-switch state and the append-only risk decision log."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime

from ptl.config import Settings
from ptl.risk.engine import ProposedOrder, RiskDecision, RiskLimits


@dataclass(frozen=True, slots=True)
class KillSwitch:
    engaged: bool
    reason: str
    changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    id: int
    created_at: datetime
    source: str
    symbol: str
    order_text: str
    approved: bool
    checks: list[dict[str, object]]
    tripped: bool


def limits_from(settings: Settings) -> RiskLimits:
    return RiskLimits(
        max_loss_per_trade=settings.risk_max_loss_per_trade,
        max_daily_loss=settings.risk_max_daily_loss,
        max_open_positions=settings.risk_max_open_positions,
        max_capital_at_risk_pct=settings.risk_max_capital_at_risk_pct,
        max_position_pct=settings.risk_max_position_pct,
    )


def kill_switch(conn: sqlite3.Connection) -> KillSwitch:
    row = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
    if row is None:
        return KillSwitch(False, "never engaged", None)
    return KillSwitch(
        bool(row["engaged"]), row["reason"], datetime.fromisoformat(row["changed_at"])
    )


def set_kill_switch(
    conn: sqlite3.Connection, engaged: bool, reason: str, now: datetime
) -> KillSwitch:
    with conn:
        conn.execute(
            "INSERT INTO risk_state (id, engaged, reason, changed_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET engaged = excluded.engaged, reason = excluded.reason, "
            "changed_at = excluded.changed_at",
            (int(engaged), reason, now.isoformat()),
        )
    return KillSwitch(engaged, reason, now)


def record_decision(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    source: str,
    order: ProposedOrder,
    decision: RiskDecision,
) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO risk_decisions (created_at, source, symbol, order_text, approved, "
            "checks, tripped) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                source,
                order.symbol,
                order.description,
                int(decision.approved),
                json.dumps([asdict(c) for c in decision.checks]),
                int(decision.trip_kill_switch),
            ),
        )
    if cursor.lastrowid is None:  # pragma: no cover
        raise RuntimeError("insert returned no id")
    return cursor.lastrowid


def recent_decisions(conn: sqlite3.Connection, limit: int = 50) -> list[DecisionRecord]:
    rows = conn.execute("SELECT * FROM risk_decisions ORDER BY id DESC LIMIT ?", (limit,))
    return [
        DecisionRecord(
            id=r["id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            source=r["source"],
            symbol=r["symbol"],
            order_text=r["order_text"],
            approved=bool(r["approved"]),
            checks=json.loads(r["checks"]),
            tripped=bool(r["tripped"]),
        )
        for r in rows
    ]
