"""The live agent runtime: one asyncio worker per agent, jobs on queues, activity on a bus.

Blocking work (backtests, broker calls) runs in threads via asyncio.to_thread; status updates
are marshalled back onto the event loop and published to WebSocket subscribers.

Autopilot (on by default, `AGENTS_AUTOPILOT`): research agents keep sweeping every
(dataset, symbol, strategy) whose default grid still has untried in-sample combinations, so
they never re-run or re-count work. Autopilot never promotes to out-of-sample; that's an
explicit request. The scout re-checks data health periodically, and active paper runners get
one cycle per completed session.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

from ptl.agents import store
from ptl.agents.bus import ActivityBus
from ptl.agents.roster import AGENT_BY_ID, AGENTS, SECTORS, JobKind
from ptl.agents.work import (
    JobError,
    SweepSpec,
    combinations,
    execute_data_check,
    execute_paper_cycle,
    execute_sweep,
)
from ptl.backtest import repository as bt_repo
from ptl.config import Settings
from ptl.data.live import Clock, QuoteSource
from ptl.db import open_db
from ptl.market_calendar import MarketCalendar
from ptl.options.chain import option_universe
from ptl.paper import store as paper_store
from ptl.paper.broker import Broker
from ptl.paper.runner import CycleRefusedError, CycleRequest
from ptl.risk import store as risk_store
from ptl.strategy.base import equity_strategies, option_strategies

logger = logging.getLogger(__name__)

AgentStatus = Literal["idle", "queued", "running", "error", "watching"]
BrokerFactory = Callable[[bool], Broker]
DATA_CHECK_EVERY_SECONDS = 600


class AgentStateOut(BaseModel):
    id: str
    name: str
    sector: str
    role: str
    status: AgentStatus
    message: str
    job_id: int | None
    params: dict[str, int] | None
    progress_done: int | None
    progress_total: int | None
    queued: int
    autopilot: bool
    updated_at: AwareDatetime


class SectorOut(BaseModel):
    id: str
    name: str
    purpose: str
    slots: int
    agents: list[str]


class FindingOut(BaseModel):
    id: int
    job_id: int
    agent_id: str
    created_at: AwareDatetime
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

    @classmethod
    def of(cls, f: store.FindingRecord) -> "FindingOut":
        return cls(**{k: getattr(f, k) for k in cls.model_fields})


class KillSwitchOut(BaseModel):
    engaged: bool
    reason: str
    changed_at: AwareDatetime | None


class SnapshotOut(BaseModel):
    as_of: AwareDatetime
    source: str
    sectors: list[SectorOut]
    agents: list[AgentStateOut]
    findings: list[FindingOut]
    kill_switch: KillSwitchOut


@dataclass
class _State:
    status: AgentStatus
    message: str
    updated_at: datetime
    job_id: int | None = None
    params: dict[str, int] | None = None
    progress_done: int | None = None
    progress_total: int | None = None
    autopilot: bool = True


class AgentRuntime:
    def __init__(  # noqa: PLR0913
        self,
        settings: Settings,
        *,
        calendar: MarketCalendar,
        clock: Clock,
        source: QuoteSource,
        broker_factory: BrokerFactory,
        bus: ActivityBus | None = None,
    ) -> None:
        self.settings = settings
        self.calendar = calendar
        self.clock = clock
        self.source = source
        self.broker_factory = broker_factory
        self.bus = bus or ActivityBus()
        now = clock()
        self._states = {
            a.id: _State(
                status="watching" if not a.kinds else "idle",
                message="Watching risk decisions" if not a.kinds else "Waiting for work",
                updated_at=now,
                autopilot=settings.agents_autopilot,
            )
            for a in AGENTS
        }
        self._queues: dict[str, asyncio.Queue[int]] = {
            a.id: asyncio.Queue() for a in AGENTS if a.kinds
        }
        self._tasks: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_data_check: datetime | None = None

    # ---- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.bus.bind(self._loop)
        with open_db(self.settings.database_path) as conn:
            stale = store.fail_stale_jobs(conn, self.clock())
        if stale:
            logger.info("Marked %d interrupted agent job(s) as failed", stale)
        for agent_id in self._queues:
            self._tasks.append(
                asyncio.create_task(self._worker(agent_id), name=f"agent:{agent_id}")
            )
        self._tasks.append(asyncio.create_task(self._autopilot(), name="agents:autopilot"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    # ---- jobs -----------------------------------------------------------------------------

    def submit(self, agent_id: str, kind: JobKind, spec: dict[str, Any]) -> int:
        agent = AGENT_BY_ID.get(agent_id)
        if agent is None or kind not in agent.kinds:
            raise JobError(f"Agent {agent_id!r} doesn't run {kind!r} jobs.")
        with open_db(self.settings.database_path) as conn:
            job_id = store.create_job(conn, agent_id, kind, spec, self.clock())
        self._queues[agent_id].put_nowait(job_id)
        if self._states[agent_id].status == "idle":
            self._update(agent_id, status="queued", message=f"Job #{job_id} queued")
        else:
            self._publish_agent(agent_id)
        return job_id

    def busy(self, agent_id: str) -> bool:
        return (
            self._states[agent_id].status in ("running", "queued")
            or not self._queues[agent_id].empty()
        )

    async def _worker(self, agent_id: str) -> None:
        queue = self._queues[agent_id]
        while True:
            job_id = await queue.get()
            await self.run_job(agent_id, job_id)

    async def run_job(self, agent_id: str, job_id: int) -> None:
        with open_db(self.settings.database_path) as conn:
            store.mark_running(conn, job_id, self.clock())
        self._update(agent_id, status="running", message=f"Job #{job_id} started", job_id=job_id)
        try:
            summary = await asyncio.to_thread(self._execute, agent_id, job_id)
            ok, message = True, None
        except (JobError, CycleRefusedError, ValueError) as exc:
            summary, ok, message = {"error": str(exc)}, False, str(exc)
        except Exception as exc:  # a worker must survive anything a job throws
            logger.exception("Agent %s job %d crashed", agent_id, job_id)
            summary, ok, message = {"error": repr(exc)}, False, f"Job crashed: {exc!r}"
        with open_db(self.settings.database_path) as conn:
            store.finish_job(conn, job_id, ok, summary, self.clock())
        if ok:
            self._update(
                agent_id,
                status="idle",
                job_id=None,
                progress_done=None,
                progress_total=None,
                params=None,
            )
        else:
            self._update(agent_id, status="error", message=message or "failed", job_id=None)

    def _execute(self, agent_id: str, job_id: int) -> dict[str, Any]:
        def emit(**update: Any) -> None:  # noqa: ANN401
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._apply, agent_id, update)

        with open_db(self.settings.database_path) as conn:
            record = store.job(conn, job_id)
            if record is None:
                raise JobError(f"job #{job_id} vanished")
            spec = record.spec
            if record.kind == "sweep":
                return execute_sweep(
                    conn,
                    job_id,
                    agent_id,
                    SweepSpec(**spec),
                    calendar=self.calendar,
                    clock=self.clock,
                    emit=emit,
                    max_combinations=self.settings.agents_max_combinations,
                )
            if record.kind == "data_check":
                return execute_data_check(
                    self.settings, self.source, self.calendar, self.clock, emit
                )
            if record.kind == "paper_cycle":
                broker = self.broker_factory(bool(spec.get("dry_run", True)))
                request = CycleRequest(
                    strategy_id=str(spec["strategy"]),
                    params=dict(spec.get("params", {})),
                    dataset_id=int(spec["dataset_id"]),
                    symbol=str(spec["symbol"]),
                    runner_id=spec.get("runner_id"),
                )
                result = execute_paper_cycle(
                    conn,
                    request,
                    settings=self.settings,
                    broker=broker,
                    quotes=self.source,
                    calendar=self.calendar,
                    clock=self.clock,
                    emit=emit,
                )
                if result.get("decision"):
                    risk_update = {
                        "status": "watching",
                        "message": f"Order #{result.get('order_id') or '-'}: {result['decision']}",
                    }
                    if self._loop is not None:
                        self._loop.call_soon_threadsafe(self._apply, "risk-officer", risk_update)
                return result
            raise JobError(f"unknown job kind {record.kind!r}")

    # ---- state & events ---------------------------------------------------------------------

    def _apply(self, agent_id: str, update: dict[str, Any]) -> None:
        finding = update.pop("finding", None)
        progress = update.pop("progress", None)
        if progress is not None:
            update["progress_done"], update["progress_total"] = progress
        if update:
            self._update(agent_id, **update)
        if isinstance(finding, store.FindingRecord):
            self.bus.publish(
                {"type": "finding", "finding": FindingOut.of(finding).model_dump(mode="json")}
            )

    def _update(self, agent_id: str, **fields: Any) -> None:  # noqa: ANN401
        state = self._states[agent_id]
        for key, value in fields.items():
            setattr(state, key, value)
        state.updated_at = self.clock()
        self._publish_agent(agent_id)

    def _publish_agent(self, agent_id: str) -> None:
        self.bus.publish(
            {"type": "agent", "agent": self.agent_state(agent_id).model_dump(mode="json")}
        )

    def agent_state(self, agent_id: str) -> AgentStateOut:
        a, s = AGENT_BY_ID[agent_id], self._states[agent_id]
        return AgentStateOut(
            id=a.id,
            name=a.name,
            sector=a.sector,
            role=a.role,
            status=s.status,
            message=s.message,
            job_id=s.job_id,
            params=s.params,
            progress_done=s.progress_done,
            progress_total=s.progress_total,
            queued=self._queues[a.id].qsize() if a.id in self._queues else 0,
            autopilot=s.autopilot,
            updated_at=s.updated_at,
        )

    def set_autopilot(self, agent_id: str, enabled: bool) -> AgentStateOut:
        if agent_id not in AGENT_BY_ID:
            raise JobError(f"Unknown agent {agent_id!r}.")
        self._update(agent_id, autopilot=enabled)
        return self.agent_state(agent_id)

    def publish_kill_switch(self, switch: risk_store.KillSwitch) -> None:
        message = f"Kill switch {'ENGAGED' if switch.engaged else 'off'}: {switch.reason}"
        self._update("risk-officer", status="watching", message=message)
        self.bus.publish(
            {"type": "kill_switch", "engaged": switch.engaged, "reason": switch.reason}
        )

    def snapshot(self) -> SnapshotOut:
        with open_db(self.settings.database_path) as conn:
            findings = store.recent_findings(conn, 30)
            switch = risk_store.kill_switch(conn)
        return SnapshotOut(
            as_of=self.clock(),
            source="Live agent runtime (in-memory status) + agent findings log (local SQLite)",
            sectors=[
                SectorOut(
                    id=s.id,
                    name=s.name,
                    purpose=s.purpose,
                    slots=s.slots,
                    agents=[a.id for a in AGENTS if a.sector == s.id],
                )
                for s in SECTORS
            ],
            agents=[self.agent_state(a.id) for a in AGENTS],
            findings=[FindingOut.of(f) for f in findings],
            kill_switch=KillSwitchOut(
                engaged=switch.engaged, reason=switch.reason, changed_at=switch.changed_at
            ),
        )

    # ---- autopilot --------------------------------------------------------------------------

    async def _autopilot(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.plan_autopilot_work)
            except Exception:  # keep the loop alive; the error is logged
                logger.exception("Autopilot planning failed")
            await asyncio.sleep(self.settings.agents_poll_seconds)

    def plan_autopilot_work(self) -> list[int]:
        """Queue the next useful job for each idle agent with autopilot on. Returns job ids."""
        queued: list[int] = []

        def submit(agent_id: str, kind: JobKind, spec: dict[str, Any]) -> None:
            if self._loop is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self._submit_async(agent_id, kind, spec), self._loop
                )
                queued.append(future.result(timeout=10))
            else:
                queued.append(self.submit(agent_id, kind, spec))

        now = self.clock()
        if self._states["scout"].autopilot and not self.busy("scout"):
            due = self._last_data_check is None or (
                (now - self._last_data_check).total_seconds() >= DATA_CHECK_EVERY_SECONDS
            )
            if due:
                self._last_data_check = now
                submit("scout", "data_check", {})
        with open_db(self.settings.database_path) as conn:
            for agent_id, spec in self._next_sweeps(conn).items():
                submit(agent_id, "sweep", spec)
            if self._states["paper-trader"].autopilot and not self.busy("paper-trader"):
                session = self.calendar.last_completed_session(now)
                for runner in paper_store.runners(conn, active_only=True):
                    last = paper_store.last_session_for_runner(conn, runner.id)
                    if last is None or last < session:
                        submit(
                            "paper-trader",
                            "paper_cycle",
                            {
                                "runner_id": runner.id,
                                "strategy": runner.strategy,
                                "params": runner.params,
                                "dataset_id": runner.dataset_id,
                                "symbol": runner.symbol,
                                "dry_run": runner.dry_run,
                            },
                        )
                        break
        return queued

    async def _submit_async(self, agent_id: str, kind: JobKind, spec: dict[str, Any]) -> int:
        return self.submit(agent_id, kind, spec)

    def _untried(self, conn: Any, symbol: str, strategy: str, spec: SweepSpec) -> bool:  # noqa: ANN401
        combos = combinations(spec, self.settings.agents_max_combinations)
        return any(bt_repo.find_run(conn, symbol, strategy, c, "in-sample") is None for c in combos)

    def _next_sweeps(self, conn: Any) -> dict[str, dict[str, Any]]:  # noqa: ANN401
        plan: dict[str, dict[str, Any]] = {}
        if self._states["quant-equity"].autopilot and not self.busy("quant-equity"):
            spec = self._next_equity_sweep(conn)
            if spec is not None:
                plan["quant-equity"] = _spec_dict(spec)
        if self._states["quant-options"].autopilot and not self.busy("quant-options"):
            spec = self._next_options_sweep(conn)
            if spec is not None:
                plan["quant-options"] = _spec_dict(spec)
        return plan

    def _next_equity_sweep(self, conn: Any) -> SweepSpec | None:  # noqa: ANN401
        for entry in bt_repo.equity_universe(conn):
            if entry.sessions < 120:  # noqa: PLR2004 - the minimum to lock out-of-sample
                continue
            for sid in equity_strategies():
                if sid == "buy_and_hold":
                    continue
                spec = SweepSpec("equity", sid, entry.symbol, entry.dataset_id)
                if self._untried(conn, entry.symbol, sid, spec):
                    return spec
        return None

    def _next_options_sweep(self, conn: Any) -> SweepSpec | None:  # noqa: ANN401
        equity = bt_repo.equity_universe(conn)
        for option in option_universe(conn):
            bars = next((u for u in equity if u.symbol == option.underlying), None)
            if bars is None or option.rows_missing_style:
                continue
            for sid in option_strategies():
                spec = SweepSpec(
                    "options", sid, option.underlying, bars.dataset_id, option.dataset_id
                )
                if self._untried(conn, option.underlying, sid, spec):
                    return spec
        return None


def _spec_dict(spec: SweepSpec) -> dict[str, Any]:
    return {
        "asset": spec.asset,
        "strategy": spec.strategy,
        "symbol": spec.symbol,
        "dataset_id": spec.dataset_id,
        "options_dataset_id": spec.options_dataset_id,
        "grid": dict(spec.grid) if spec.grid else None,
        "promote": spec.promote,
        "params_fixed": dict(spec.params_fixed),
    }
