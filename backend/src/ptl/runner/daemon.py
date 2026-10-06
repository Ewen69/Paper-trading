"""The paper execution daemon: a continuous loop around the Phase 3 paper cycle.

Each tick it:
1. reads the account read-only and publishes a heartbeat (balance, positions, risk usage);
2. takes the optimizer's equity Active Best, and only if it is VALIDATED (its single
   out-of-sample test passed) runs one paper cycle per completed session for it;
3. polls open paper orders for fills.

Every cycle goes through the hard-coded risk engine and is logged (intent, quote at submission,
risk decision, broker response, fills). Dry run is the default and never sends anything. Paper
mode talks only to the Alpaca PAPER endpoint, re-checked before every order (ptl.safety); there
is no live-trading code path.
"""

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from ptl.agents import learning
from ptl.agents.learning import ActiveBest
from ptl.config import Settings
from ptl.data.live import QuoteSource
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.paper import store
from ptl.paper.broker import Broker
from ptl.paper.runner import CycleRefusedError, CycleRequest, run_cycle, sync_orders
from ptl.risk import store as risk_store
from ptl.safety import assert_paper_endpoint

DAEMON = "paper-runner"
Clock = Callable[[], datetime]


@dataclass(slots=True)
class RunnerState:
    attempted: set[tuple[int, date]] = field(default_factory=set)
    runner_for_best: dict[int, int] = field(default_factory=dict)
    last_note: str = ""
    last_outcome: str = ""


def _runner_id(conn: sqlite3.Connection, best: ActiveBest, dry_run: bool, now: datetime) -> int:
    for r in store.runners(conn, active_only=True):
        if (r.strategy, r.params, r.dataset_id, r.symbol, r.dry_run) == (
            best.strategy,
            best.params,
            best.dataset_id,
            best.symbol,
            dry_run,
        ):
            return r.id
    return store.create_runner(
        conn,
        now=now,
        strategy=best.strategy,
        params=best.params,
        dataset_id=best.dataset_id,
        symbol=best.symbol,
        dry_run=dry_run,
    )


def account_view(conn: sqlite3.Connection, broker: Broker, settings: Settings) -> dict[str, Any]:
    """Balance, positions and how much of each risk limit is used. Read-only."""
    try:
        account = broker.account()
        positions = broker.positions()
    except Exception as exc:
        return {"error": f"No data: account unreachable ({type(exc).__name__})."}
    limits = risk_store.limits_from(settings)
    switch = risk_store.kill_switch(conn)
    at_risk = sum(abs(p.market_value) for p in positions.values())
    largest = max((abs(p.market_value) for p in positions.values()), default=0.0)
    day_pnl = account.equity - account.last_equity
    equity = account.equity or 1.0

    def use(value: float, cap: float) -> float:
        return value / cap if cap > 0 else 0.0

    open_count = sum(1 for p in positions.values() if p.qty)
    return {
        "source": account.source,
        "equity": account.equity,
        "cash": account.cash,
        "day_pnl": day_pnl,
        "positions": [
            {"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value}
            for p in sorted(positions.values(), key=lambda p: p.symbol)
        ],
        "kill_switch": {"engaged": switch.engaged, "reason": switch.reason},
        "risk": [
            {
                "name": "Daily loss",
                "used": max(0.0, -day_pnl),
                "limit": limits.max_daily_loss,
                "utilization": use(max(0.0, -day_pnl), limits.max_daily_loss),
            },
            {
                "name": "Open positions",
                "used": float(open_count),
                "limit": float(limits.max_open_positions),
                "utilization": use(open_count, limits.max_open_positions),
            },
            {
                "name": "Capital at risk",
                "used": at_risk,
                "limit": limits.max_capital_at_risk_pct * equity,
                "utilization": use(at_risk, limits.max_capital_at_risk_pct * equity),
            },
            {
                "name": "Largest position",
                "used": largest,
                "limit": limits.max_position_pct * equity,
                "utilization": use(largest, limits.max_position_pct * equity),
            },
        ],
    }


def tick(  # noqa: PLR0913
    conn: sqlite3.Connection,
    state: RunnerState,
    *,
    settings: Settings,
    broker: Broker,
    quotes: QuoteSource | None,
    calendar: MarketCalendar,
    now: datetime,
) -> dict[str, Any]:
    """One loop iteration. Returns the heartbeat detail."""
    dry_run = broker.mode == "dry_run"

    def note(message: str, level: learning.Level = "info") -> None:
        if message != state.last_note:  # don't repeat the same line every minute
            learning.log(conn, DAEMON, message, now, level)
            state.last_note = message

    session = calendar.last_completed_session(now)
    best = learning.active_best(conn, "equity")
    detail: dict[str, Any] = {
        "mode": broker.mode,
        "session": session.isoformat(),
        "active_best": None
        if best is None
        else {
            "id": best.id,
            "strategy": best.strategy,
            "symbol": best.symbol,
            "params": best.params,
            "validated": best.validated,
        },
    }
    if best is None:
        note("Waiting: the optimizer has no equity Active Best yet.")
    elif not best.validated:
        note(
            f"Waiting: the Active Best ({best.strategy} on {best.symbol}) failed its "
            "out-of-sample check, so it isn't paper traded."
        )
    else:
        runner_id = state.runner_for_best.get(best.id) or _runner_id(conn, best, dry_run, now)
        state.runner_for_best[best.id] = runner_id
        done = store.last_session_for_runner(conn, runner_id) == session
        if not done and (runner_id, session) not in state.attempted:
            state.attempted.add((runner_id, session))
            try:
                result = run_cycle(
                    conn,
                    CycleRequest(
                        best.strategy, best.params, best.dataset_id, best.symbol, runner_id
                    ),
                    settings=settings,
                    broker=broker,
                    quotes=quotes,
                    calendar=calendar,
                    now=now,
                )
                state.last_outcome = result.outcome
                note(f"Session {session}: {result.outcome} Rationale: {result.explanation}")
            except CycleRefusedError as exc:
                state.last_outcome = f"Refused: {exc}"
                note(f"Session {session}: cycle refused. {exc}", "warn")
        elif done:
            note(f"Session {session} already handled for {best.strategy} on {best.symbol}.")
    if not dry_run:
        try:
            changed = sync_orders(conn, broker, now)
            if changed:
                learning.log(conn, DAEMON, f"{changed} paper order status change(s) logged.", now)
        except Exception as exc:
            learning.log(conn, DAEMON, f"Order sync failed: {exc!r}", now, "error")
    detail["last_outcome"] = state.last_outcome
    detail["account"] = account_view(conn, broker, settings)
    return detail


def run_daemon(  # noqa: PLR0913
    settings: Settings,
    *,
    broker: Broker,
    quotes: QuoteSource | None,
    calendar: MarketCalendar | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    once: bool = False,
    stop: threading.Event | None = None,
) -> int:
    assert_paper_endpoint(settings.alpaca_base_url)
    calendar = calendar or MarketCalendar()
    stop = stop or threading.Event()
    conn = connect(settings.database_path)
    migrate(conn)
    started = clock()
    state = RunnerState()
    ticks = 0
    mode = "DRY RUN (nothing is sent)" if broker.mode == "dry_run" else "Alpaca PAPER account"
    learning.log(conn, DAEMON, f"Paper runner started: {mode}.", started)
    try:
        while not stop.is_set():
            now = clock()
            try:
                detail = tick(
                    conn,
                    state,
                    settings=settings,
                    broker=broker,
                    quotes=quotes,
                    calendar=calendar,
                    now=now,
                )
                status = "running"
            except Exception as exc:
                detail = {"error": repr(exc)}
                status = "error"
                learning.log(conn, DAEMON, f"Tick failed: {exc!r}", now, "error")
            learning.beat(conn, DAEMON, started_at=started, now=now, status=status, detail=detail)
            ticks += 1
            if once:
                break
            stop.wait(settings.runner_poll_seconds)
    finally:
        learning.beat(conn, DAEMON, started_at=started, now=clock(), status="stopped", detail={})
        learning.log(conn, DAEMON, "Paper runner stopped.", clock())
        conn.close()
    return ticks
