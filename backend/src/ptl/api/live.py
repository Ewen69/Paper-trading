"""REST routes for agents, the risk engine, and the paper runner."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, Field

from ptl.agents import store as agent_store
from ptl.agents.runtime import AgentRuntime, AgentStateOut, FindingOut, SnapshotOut
from ptl.agents.work import JobError, SweepSpec, combinations
from ptl.config import Settings
from ptl.data.live import Clock
from ptl.db import open_db
from ptl.paper import store as paper_store
from ptl.risk import store as risk_store
from ptl.strategy.base import equity_strategies


class JobOut(BaseModel):
    job_id: int
    agent_id: str
    kind: str


class SweepIn(BaseModel):
    asset: Literal["equity", "options"]
    strategy: str
    symbol: str = Field(min_length=1, max_length=20)
    dataset_id: int
    options_dataset_id: int | None = None
    grid: dict[str, list[int]] | None = None
    promote: bool = True  # one counted out-of-sample test of the finalist


class AutopilotIn(BaseModel):
    enabled: bool


class JobRecordOut(BaseModel):
    id: int
    agent_id: str
    kind: str
    status: str
    created_at: AwareDatetime
    finished_at: AwareDatetime | None
    summary: dict[str, object] | None


class LimitsOut(BaseModel):
    max_loss_per_trade: float
    max_daily_loss: float
    max_open_positions: int
    max_capital_at_risk_pct: float


class DecisionOut(BaseModel):
    id: int
    created_at: AwareDatetime
    source: str
    symbol: str
    order_text: str
    approved: bool
    checks: list[dict[str, object]]
    tripped: bool


class RiskStateOut(BaseModel):
    as_of: AwareDatetime
    source: str
    kill_switch_engaged: bool
    kill_switch_reason: str
    kill_switch_changed_at: AwareDatetime | None
    limits: LimitsOut
    decisions: list[DecisionOut]


class KillSwitchIn(BaseModel):
    engaged: bool
    reason: str = Field(min_length=3, max_length=300)


class CycleIn(BaseModel):
    strategy: str
    params: dict[str, int] = Field(default_factory=dict)
    dataset_id: int
    symbol: str = Field(min_length=1, max_length=20)
    dry_run: bool = True


class RunnerOut(BaseModel):
    id: int
    created_at: AwareDatetime
    strategy: str
    params: dict[str, int]
    dataset_id: int
    symbol: str
    dry_run: bool
    active: bool


class CycleOut(BaseModel):
    id: int
    runner_id: int | None
    created_at: AwareDatetime
    session: date
    mode: str
    strategy: str
    symbol: str
    explanation: str
    target: float | None
    price: float | None
    price_source: str
    current_qty: float | None
    desired_qty: float | None
    outcome: str


class OrderOut(BaseModel):
    id: int
    cycle_id: int
    created_at: AwareDatetime
    mode: str
    symbol: str
    side: str
    qty: float
    reason: str
    risk_decision_id: int
    broker_order_id: str | None
    status: str
    latest_status: str


class PaperLogOut(BaseModel):
    as_of: AwareDatetime
    source: str
    runners: list[RunnerOut]
    cycles: list[CycleOut]
    orders: list[OrderOut]


def build_live_router(settings: Settings, runtime: AgentRuntime, clock: Clock) -> APIRouter:
    router = APIRouter()
    _agent_routes(router, settings, runtime)
    _risk_routes(router, settings, runtime, clock)
    _paper_routes(router, settings, runtime, clock)
    return router


def _submit(
    runtime: AgentRuntime,
    agent_id: str,
    kind: Literal["sweep", "data_check", "paper_cycle"],
    spec: dict[str, object],
) -> JobOut:
    try:
        job_id = runtime.submit(agent_id, kind, spec)
    except JobError as exc:
        raise HTTPException(409, str(exc)) from exc
    return JobOut(job_id=job_id, agent_id=agent_id, kind=kind)


def _agent_routes(router: APIRouter, settings: Settings, runtime: AgentRuntime) -> None:

    @router.get("/agents/state")
    def state() -> SnapshotOut:
        return runtime.snapshot()

    @router.post("/agents/sweeps", status_code=202)
    def sweep(body: SweepIn) -> JobOut:
        spec = SweepSpec(
            asset=body.asset,
            strategy=body.strategy,
            symbol=body.symbol.strip().upper(),
            dataset_id=body.dataset_id,
            options_dataset_id=body.options_dataset_id,
            grid=body.grid,
            promote=body.promote,
        )
        try:
            combinations(spec, settings.agents_max_combinations)
        except JobError as exc:
            raise HTTPException(422, str(exc)) from exc
        agent = "quant-equity" if body.asset == "equity" else "quant-options"
        return _submit(runtime, agent, "sweep", body.model_dump() | {"symbol": spec.symbol})

    @router.post("/agents/data-check", status_code=202)
    def data_check() -> JobOut:
        return _submit(runtime, "scout", "data_check", {})

    @router.post("/agents/{agent_id}/autopilot")
    def autopilot(agent_id: str, body: AutopilotIn) -> AgentStateOut:
        try:
            return runtime.set_autopilot(agent_id, body.enabled)
        except JobError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/agents/findings")
    def findings(limit: int = 100) -> list[FindingOut]:
        with open_db(settings.database_path) as conn:
            return [FindingOut.of(f) for f in agent_store.recent_findings(conn, min(limit, 500))]

    @router.get("/agents/jobs")
    def jobs(limit: int = 50) -> list[JobRecordOut]:
        with open_db(settings.database_path) as conn:
            return [
                JobRecordOut.model_validate(j, from_attributes=True)
                for j in agent_store.recent_jobs(conn, min(limit, 500))
            ]


def _risk_routes(
    router: APIRouter, settings: Settings, runtime: AgentRuntime, clock: Clock
) -> None:

    def risk_state() -> RiskStateOut:
        with open_db(settings.database_path) as conn:
            switch = risk_store.kill_switch(conn)
            decisions = risk_store.recent_decisions(conn)
        limits = risk_store.limits_from(settings)
        return RiskStateOut(
            as_of=clock(),
            source="Risk engine settings (.env) and the risk decision log (local SQLite)",
            kill_switch_engaged=switch.engaged,
            kill_switch_reason=switch.reason,
            kill_switch_changed_at=switch.changed_at,
            limits=LimitsOut(
                max_loss_per_trade=limits.max_loss_per_trade,
                max_daily_loss=limits.max_daily_loss,
                max_open_positions=limits.max_open_positions,
                max_capital_at_risk_pct=limits.max_capital_at_risk_pct,
            ),
            decisions=[DecisionOut.model_validate(d, from_attributes=True) for d in decisions],
        )

    @router.get("/risk/state")
    def get_risk() -> RiskStateOut:
        return risk_state()

    @router.post("/risk/kill-switch")
    def kill(body: KillSwitchIn) -> RiskStateOut:
        with open_db(settings.database_path) as conn:
            switch = risk_store.set_kill_switch(conn, body.engaged, body.reason, clock())
        runtime.publish_kill_switch(switch)
        return risk_state()


def _paper_routes(
    router: APIRouter, settings: Settings, runtime: AgentRuntime, clock: Clock
) -> None:

    def check_strategy(strategy: str) -> None:
        if strategy not in equity_strategies():
            raise HTTPException(
                422,
                f"{strategy!r} isn't an equity strategy; options paper trading isn't built yet.",
            )

    @router.post("/paper/cycles", status_code=202)
    def cycle(body: CycleIn) -> JobOut:
        check_strategy(body.strategy)
        return _submit(runtime, "paper-trader", "paper_cycle", body.model_dump())

    @router.post("/paper/runners")
    def create_runner(body: CycleIn) -> RunnerOut:
        check_strategy(body.strategy)
        with open_db(settings.database_path) as conn:
            runner_id = paper_store.create_runner(
                conn,
                now=clock(),
                strategy=body.strategy,
                params=body.params,
                dataset_id=body.dataset_id,
                symbol=body.symbol.strip().upper(),
                dry_run=body.dry_run,
            )
            runner = next(r for r in paper_store.runners(conn) if r.id == runner_id)
        return _runner(runner)

    @router.post("/paper/runners/{runner_id}/stop")
    def stop_runner(runner_id: int) -> RunnerOut:
        with open_db(settings.database_path) as conn:
            if not paper_store.set_runner_active(conn, runner_id, False):
                raise HTTPException(404, f"No paper runner #{runner_id}.")
            runner = next(r for r in paper_store.runners(conn) if r.id == runner_id)
        return _runner(runner)

    @router.get("/paper/log")
    def paper_log() -> PaperLogOut:
        with open_db(settings.database_path) as conn:
            orders = paper_store.recent_orders(conn)
            return PaperLogOut(
                as_of=clock(),
                source="Paper trading log (local SQLite); broker statuses from Alpaca paper",
                runners=[_runner(r) for r in paper_store.runners(conn)],
                cycles=[
                    CycleOut.model_validate(c, from_attributes=True)
                    for c in paper_store.recent_cycles(conn)
                ],
                orders=[
                    OrderOut.model_validate(
                        _fields(o, OrderOut, latest_status=paper_store.latest_status(conn, o.id))
                    )
                    for o in orders
                ],
            )


def _fields(obj: object, model: type[BaseModel], **extra: object) -> dict[str, object]:
    return {k: getattr(obj, k) for k in model.model_fields if k not in extra} | extra


def _runner(r: paper_store.RunnerRecord) -> RunnerOut:
    return RunnerOut.model_validate(r, from_attributes=True)
