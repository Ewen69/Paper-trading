"""Live telemetry for the Operations Center: `/ws/telemetry` and `/telemetry/state`.

The optimizer and paper runner are separate processes; they write to SQLite (append-only logs
plus a heartbeat row each). This hub tails those tables every second and streams new rows, and
every few seconds recomputes the slower panels (Auditor, Graduation Gate, net worth, portfolio,
paper account, risk guard) from the same stored data the rest of the app uses.

Events: {"type": "hello", ...} on connect, then "trial", "log", "daemons" and "state".
"""

import asyncio
import contextlib
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ptl.agents import learning
from ptl.agents.bus import ActivityBus, Event
from ptl.config import Settings
from ptl.data.live import QuoteSource
from ptl.data_api import compute_data_health
from ptl.db import open_db
from ptl.game import compute_game_state
from ptl.market_calendar import MarketCalendar
from ptl.networth_api import compute_summary
from ptl.paper import store as paper_store
from ptl.portfolio import holdings as pf_holdings
from ptl.portfolio import service as pf_service
from ptl.risk import store as risk_store

Clock = Callable[[], datetime]
FAST_SECONDS = 1.0
SLOW_SECONDS = 15.0
ALIVE_SECONDS = 180.0  # a daemon with no heartbeat for this long is shown as not running
SOURCE = "Local SQLite: learning log, daemon heartbeats, paper log, risk state, your entries"


def trial_event(t: learning.Trial) -> dict[str, Any]:
    return {
        "id": t.id,
        "created_at": t.created_at.isoformat(),
        "search_id": t.search_id,
        "generation": t.generation,
        "asset": t.asset,
        "strategy": t.strategy,
        "symbol": t.symbol,
        "params": t.params,
        "period": t.period,
        "run_id": t.run_id,
        "reused": t.reused,
        "trades": t.trades,
        "win_rate": t.win_rate,
        "sharpe": t.sharpe,
        "max_drawdown": t.max_drawdown,
        "fitness": t.fitness,
        "verdict": t.verdict,
    }


def log_event(line: learning.LogLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "created_at": line.created_at.isoformat(),
        "daemon": line.daemon,
        "level": line.level,
        "message": line.message,
        "kind": line.kind,
    }


def daemons(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in ("optimizer", "paper-runner"):
        hb = next((h for h in learning.heartbeats(conn) if h.daemon == name), None)
        if hb is None:
            out.append(
                {
                    "daemon": name,
                    "status": "not running",
                    "alive": False,
                    "beat_at": None,
                    "detail": {},
                }
            )
            continue
        age = (now - hb.beat_at).total_seconds()
        alive = hb.status not in ("stopped",) and age < ALIVE_SECONDS
        out.append(
            {
                "daemon": name,
                "status": hb.status
                if alive
                else ("stopped" if hb.status == "stopped" else "no heartbeat"),
                "alive": alive,
                "pid": hb.pid,
                "started_at": hb.started_at.isoformat(),
                "beat_at": hb.beat_at.isoformat(),
                "age_seconds": round(age, 1),
                "detail": hb.detail,
            }
        )
    return out


def _best(b: learning.ActiveBest | None, conn: sqlite3.Connection) -> dict[str, Any] | None:
    if b is None:
        return None
    ins = learning.trial_by_id(conn, b.in_sample_log_id)
    oos = learning.trial_by_id(conn, b.oos_log_id) if b.oos_log_id else None
    return {
        "id": b.id,
        "created_at": b.created_at.isoformat(),
        "asset": b.asset,
        "strategy": b.strategy,
        "symbol": b.symbol,
        "dataset_id": b.dataset_id,
        "params": b.params,
        "in_sample_fitness": b.in_sample_fitness,
        "validated": b.validated,
        "reason": b.reason,
        "in_sample": None if ins is None else trial_event(ins),
        "out_of_sample": None if oos is None else trial_event(oos),
        "curve": b.curve,
    }


def _section(build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return build()
    except Exception as exc:
        return {"error": f"No data: {type(exc).__name__}: {exc}"}


def build_state(
    settings: Settings, source: QuoteSource, calendar: MarketCalendar, now: datetime
) -> dict[str, Any]:
    with open_db(settings.database_path) as conn:

        def optimizer() -> dict[str, Any]:
            return {
                "counts": learning.trial_counts(conn),
                "active_best": {
                    "equity": _best(learning.active_best(conn, "equity"), conn),
                    "options": _best(learning.active_best(conn, "options"), conn),
                },
                "history": [
                    {
                        "id": b.id,
                        "created_at": b.created_at.isoformat(),
                        "strategy": b.strategy,
                        "symbol": b.symbol,
                        "params": b.params,
                        "in_sample_fitness": b.in_sample_fitness,
                        "validated": b.validated,
                    }
                    for b in learning.active_best_history(conn, 10)
                ],
            }

        def paper() -> dict[str, Any]:
            evidence = paper_store.paper_evidence(conn)
            orders = []
            for o in paper_store.recent_orders(conn, 10):
                ex = paper_store.execution(conn, o)
                orders.append(
                    {
                        "id": o.id,
                        "created_at": o.created_at.isoformat(),
                        "mode": o.mode,
                        "symbol": o.symbol,
                        "side": o.side,
                        "qty": o.qty,
                        "status": paper_store.latest_status(conn, o.id),
                        "reason": o.reason,
                        **asdict(ex),
                    }
                )
            return {
                "orders": orders,
                "filled_paper_trades": evidence.filled_trades,
                "kill_switch_trips": evidence.kill_switch_trips,
            }

        def risk() -> dict[str, Any]:
            switch = risk_store.kill_switch(conn)
            return {
                "kill_switch": {"engaged": switch.engaged, "reason": switch.reason},
                "limits": asdict(risk_store.limits_from(settings)),
            }

        def auditor() -> dict[str, Any]:
            health = compute_data_health(settings, source, calendar, now)
            game = compute_game_state(
                conn, health, now, stale_after_days=settings.networth_stale_after_days
            )
            return {
                "data_health": health.overall,
                "flags": [f.model_dump(mode="json") for f in game.flags],
                "graduation": game.graduation.model_dump(mode="json"),
            }

        def portfolio() -> dict[str, Any]:
            ctx = pf_service.PricingContext(
                now=now,
                calendar=calendar,
                stale_after_sessions=settings.dataset_stale_after_sessions,
                manual_stale_after_days=settings.networth_stale_after_days,
            )
            positions = [pf_service.value_holding(conn, h, ctx) for h in pf_holdings.holdings(conn)]
            a = pf_service.analyze(
                conn,
                positions,
                book="manual",
                benchmark=settings.portfolio_benchmark,
                window=settings.portfolio_window_sessions,
            )
            return {
                "total_value": a.total_value,
                "allocation": [
                    {"asset_class": k, "value": v, "weight": w} for k, v, w in a.allocation
                ],
                "positions": len(a.positions),
                "unpriced": sum(1 for p in a.positions if p.value is None),
                "rules_fired": sum(1 for t in a.tips if t.status == "fired"),
            }

        state: dict[str, Any] = {
            "as_of": now.isoformat(),
            "source": SOURCE,
            "daemons": daemons(conn, now),
            "optimizer": _section(optimizer),
            "paper": _section(paper),
            "risk": _section(risk),
            "auditor": _section(auditor),
            "portfolio": _section(portfolio),
        }
    nw = _section(lambda: compute_summary(settings, now).model_dump(mode="json"))
    state["net_worth"] = (
        nw
        if "error" in nw
        else {
            "totals": nw["totals"],
            "history": nw["history"],
            "source": nw["source"],
            "as_of": nw["as_of"],
        }
    )
    return state


class TelemetryHub:
    def __init__(
        self, settings: Settings, *, source: QuoteSource, calendar: MarketCalendar, clock: Clock
    ) -> None:
        self.settings = settings
        self.source = source
        self.calendar = calendar
        self.clock = clock
        self.bus = ActivityBus()
        self._task: asyncio.Task[None] | None = None
        self._last_trial = 0
        self._last_log = 0
        self._daemon_key: tuple[Any, ...] = ()
        self.state: dict[str, Any] | None = None

    async def start(self) -> None:
        self.bus.bind(asyncio.get_running_loop())
        await asyncio.to_thread(self._init_cursors)
        self._task = asyncio.create_task(self._loop(), name="telemetry")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _init_cursors(self) -> None:
        with open_db(self.settings.database_path) as conn:
            self._last_trial = int(
                conn.execute("SELECT COALESCE(MAX(id), 0) FROM agent_learning_log").fetchone()[0]
            )
            self._last_log = int(
                conn.execute("SELECT COALESCE(MAX(id), 0) FROM daemon_log").fetchone()[0]
            )

    def poll(self) -> list[Event]:
        """New trials and log lines since the last poll, plus daemon status if it changed."""
        events: list[Event] = []
        with open_db(self.settings.database_path) as conn:
            for t in learning.trials_after(conn, self._last_trial):
                events.append({"type": "trial", "trial": trial_event(t)})
                self._last_trial = t.id
            for line in learning.log_after(conn, self._last_log):
                events.append({"type": "log", "line": log_event(line)})
                self._last_log = line.id
            status = daemons(conn, self.clock())
        key = tuple((d["daemon"], d["status"], d["beat_at"]) for d in status)
        if key != self._daemon_key:
            self._daemon_key = key
            events.append({"type": "daemons", "daemons": status})
        return events

    def refresh(self) -> dict[str, Any]:
        self.state = build_state(self.settings, self.source, self.calendar, self.clock())
        return self.state

    def hello(self) -> Event:
        with open_db(self.settings.database_path) as conn:
            log_lines = [log_event(x) for x in learning.recent_log(conn, 150)]
            trials = [trial_event(t) for t in reversed(learning.recent_trials(conn, 60))]
        return {
            "type": "hello",
            "state": self.state or self.refresh(),
            "log": log_lines,
            "trials": trials,
        }

    async def _loop(self) -> None:
        since_slow = SLOW_SECONDS  # refresh immediately
        while True:
            try:
                for event in await asyncio.to_thread(self.poll):
                    self.bus.publish(event)
                if since_slow >= SLOW_SECONDS:
                    since_slow = 0.0
                    state = await asyncio.to_thread(self.refresh)
                    self.bus.publish({"type": "state", "state": state})
            except Exception as exc:
                self.bus.publish({"type": "error", "message": f"telemetry poll failed: {exc!r}"})
            await asyncio.sleep(FAST_SECONDS)
            since_slow += FAST_SECONDS


def build_telemetry_router(hub: TelemetryHub) -> APIRouter:
    router = APIRouter()

    @router.get("/telemetry/state")
    def telemetry_state() -> dict[str, Any]:
        return hub.refresh()

    @router.websocket("/ws/telemetry")
    async def telemetry(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = hub.bus.subscribe()
        try:
            await websocket.send_json(await asyncio.to_thread(hub.hello))
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            hub.bus.unsubscribe(queue)

    return router
