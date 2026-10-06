"""Agent subsystem: in-sample sweeps, one counted OOS test, runtime events, WebSocket, API."""

import asyncio
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ptl.agents import store
from ptl.agents.runtime import AgentRuntime
from ptl.agents.work import JobError, SweepSpec, combinations, execute_sweep
from ptl.app import create_app
from ptl.backtest import repository as bt_repo
from ptl.config import Settings
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.paper.broker import DryRunBroker
from tests.conftest import SettingsFactory
from tests.test_backtest_service import NOW, load, trend, wave
from tests.test_data_api import FakeSource

GRID = {"fast": [5, 10], "slow": [20, 40]}


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings()


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def dataset(conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path) -> int:
    return load(conn, tmp_path, calendar, "two.csv", series={"QQQ": wave, "SPY": trend})


def spec(dataset: int, promote: bool = True) -> SweepSpec:
    return SweepSpec("equity", "sma_crossover", "QQQ", dataset, grid=GRID, promote=promote)


def sweep(
    conn: sqlite3.Connection, s: SweepSpec, calendar: MarketCalendar, events: list[dict[str, Any]]
) -> dict[str, Any]:
    job = store.create_job(conn, "quant-equity", "sweep", {}, NOW)
    return execute_sweep(
        conn,
        job,
        "quant-equity",
        s,
        calendar=calendar,
        clock=lambda: NOW,
        emit=lambda **e: events.append(e),
        max_combinations=50,
    )


def test_sweep_runs_in_sample_and_tests_one_finalist_out_of_sample(
    conn: sqlite3.Connection, calendar: MarketCalendar, dataset: int
) -> None:
    events: list[dict[str, Any]] = []
    result = sweep(conn, spec(dataset), calendar, events)
    runs = bt_repo.all_runs(conn)
    assert [r.period for r in runs].count("in-sample") == 4
    assert [r.period for r in runs].count("out-of-sample") == 1
    assert result["tested"] == 4
    oos = next(r for r in runs if r.period == "out-of-sample")
    assert oos.params == result["finalist"]
    findings = store.recent_findings(conn)
    assert [f.period for f in findings] == ["out-of-sample"] + ["in-sample"] * 4
    assert "Single out-of-sample test of the finalist" in findings[0].note
    assert "Best-of-many is biased upward" in findings[0].note
    messages = [e["message"] for e in events if "message" in e]
    assert messages[0] == "In-sample test 1/4: sma_crossover (fast=5, slow=20) on QQQ"
    assert any(e.get("params") == {"fast": 10, "slow": 40} for e in events)


def test_repeat_sweep_reuses_runs_and_never_retests_the_holdout(
    conn: sqlite3.Connection, calendar: MarketCalendar, dataset: int
) -> None:
    sweep(conn, spec(dataset), calendar, [])
    before = len(bt_repo.all_runs(conn))
    events: list[dict[str, Any]] = []
    result = sweep(conn, spec(dataset), calendar, events)
    assert len(bt_repo.all_runs(conn)) == before  # nothing re-run, nothing re-counted
    assert result["reused"] == 4
    assert "not re-tested (the holdout is looked at once)" in events[-1]["message"]
    counts = bt_repo.run_counts(conn, "QQQ", "sma_crossover")
    assert counts.oos_evaluations == 1


def test_unpromoted_sweep_stays_in_sample(
    conn: sqlite3.Connection, calendar: MarketCalendar, dataset: int
) -> None:
    sweep(conn, spec(dataset, promote=False), calendar, [])
    assert {r.period for r in bt_repo.all_runs(conn)} == {"in-sample"}


def test_grid_validation() -> None:
    assert (
        len(
            combinations(
                SweepSpec(
                    "equity", "sma_crossover", "X", 1, grid={"fast": [50], "slow": [20, 100]}
                ),
                50,
            )
        )
        == 1
    )
    with pytest.raises(JobError, match="Unknown parameter"):
        combinations(SweepSpec("equity", "sma_crossover", "X", 1, grid={"bogus": [1]}), 50)
    with pytest.raises(JobError, match="exceed the limit"):
        combinations(
            SweepSpec(
                "equity",
                "sma_crossover",
                "X",
                1,
                grid={"fast": list(range(2, 30)), "slow": list(range(30, 40))},
            ),
            50,
        )
    with pytest.raises(JobError, match="No equity strategy"):
        combinations(SweepSpec("equity", "put_credit_spread", "X", 1), 50)


def runtime(settings: Settings) -> AgentRuntime:
    return AgentRuntime(
        settings,
        calendar=MarketCalendar(),
        clock=lambda: NOW,
        source=FakeSource(configured=False),
        broker_factory=lambda _dry: DryRunBroker(None, 100_000),
    )


def test_autopilot_plans_only_untried_in_sample_work(
    conn: sqlite3.Connection, settings: Settings, dataset: int
) -> None:
    rt = runtime(settings.model_copy(update={"agents_autopilot": True}))
    job_ids = rt.plan_autopilot_work()
    jobs = {j.id: j for j in store.recent_jobs(conn)}
    planned = [jobs[i] for i in job_ids]
    assert [(j.agent_id, j.kind) for j in planned] == [
        ("scout", "data_check"),
        ("quant-equity", "sweep"),
    ]
    sweep_spec = planned[1].spec
    assert sweep_spec["promote"] is False  # autopilot never touches the holdout
    assert sweep_spec["strategy"] == "sma_crossover"


def test_runtime_streams_status_and_findings(settings: Settings, dataset: int) -> None:
    async def scenario() -> list[dict[str, Any]]:
        rt = runtime(settings)
        await rt.start()
        queue = rt.bus.subscribe()
        rt.submit(
            "quant-equity",
            "sweep",
            {
                "asset": "equity",
                "strategy": "sma_crossover",
                "symbol": "QQQ",
                "dataset_id": dataset,
                "grid": {"fast": [5], "slow": [20]},
                "promote": False,
            },
        )
        seen: list[dict[str, Any]] = []
        while not (
            seen
            and seen[-1].get("type") == "agent"
            and seen[-1]["agent"]["status"] == "idle"
            and any(e["type"] == "finding" for e in seen)
        ):
            seen.append(await asyncio.wait_for(queue.get(), timeout=30))
        await rt.stop()
        return seen

    events = asyncio.run(scenario())
    statuses = [e["agent"]["status"] for e in events if e["type"] == "agent"]
    assert statuses[0] == "queued"
    assert "running" in statuses
    finding = next(e["finding"] for e in events if e["type"] == "finding")
    assert (finding["period"], finding["params"]) == ("in-sample", {"fast": 5, "slow": 20})


def test_runtime_reports_job_errors(settings: Settings, conn: sqlite3.Connection) -> None:
    async def scenario() -> dict[str, Any]:
        rt = runtime(settings)
        await rt.start()
        rt.submit(
            "quant-equity",
            "sweep",
            {
                "asset": "equity",
                "strategy": "sma_crossover",
                "symbol": "NOPE",
                "dataset_id": 99,
                "grid": {"fast": [5], "slow": [20]},
            },
        )
        queue = rt.bus.subscribe()
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=30)
            if event["type"] == "agent" and event["agent"]["status"] == "error":
                await rt.stop()
                return event

    event = asyncio.run(scenario())
    assert "does not exist" in event["agent"]["message"]


def wait_for_job(client: TestClient, job_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        jobs: list[dict[str, Any]] = client.get("/agents/jobs").json()
        job = next(j for j in jobs if j["id"] == job_id)
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_websocket_and_rest_api(settings: Settings, dataset: int) -> None:
    app = create_app(
        settings,
        quote_source=FakeSource(configured=False),
        calendar=MarketCalendar(),
        clock=lambda: NOW,
        brokers=lambda _dry: DryRunBroker(None, 100_000),
    )
    with TestClient(app) as client, client.websocket_connect("/ws/activity") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert [s["name"] for s in snapshot["sectors"]] == [
            "Data Ingestion Sector",
            "Strategy Research Sector",
            "Options Risk Sector",
            "Execution Hub",
        ]
        assert snapshot["kill_switch"]["engaged"] is False

        job = client.post("/agents/data-check").json()
        assert job["agent_id"] == "scout"
        while True:
            event = ws.receive_json()
            if scout_finished(event):
                break
        assert wait_for_job(client, job["job_id"])["status"] == "done"

        bad = client.post(
            "/agents/sweeps",
            json={
                "asset": "equity",
                "strategy": "sma_crossover",
                "symbol": "QQQ",
                "dataset_id": 1,
                "grid": {"bogus": [1]},
            },
        )
        assert bad.status_code == 422

        killed = client.post("/risk/kill-switch", json={"engaged": True, "reason": "test stop"})
        assert killed.json()["kill_switch_engaged"] is True
        while (event := ws.receive_json())["type"] != "kill_switch":
            pass
        assert event == {"type": "kill_switch", "engaged": True, "reason": "test stop"}

        options = client.post(
            "/paper/cycles",
            json={"strategy": "put_credit_spread", "dataset_id": 1, "symbol": "SPY"},
        )
        assert options.status_code == 422
        runner = client.post(
            "/paper/runners", json={"strategy": "buy_and_hold", "dataset_id": 1, "symbol": "spy"}
        ).json()
        assert (runner["symbol"], runner["dry_run"], runner["active"]) == ("SPY", True, True)
        assert client.post(f"/paper/runners/{runner['id']}/stop").json()["active"] is False
        assert client.get("/paper/log").json()["runners"][0]["id"] == runner["id"]
        assert client.get("/risk/state").json()["limits"]["max_open_positions"] == 5


def scout_finished(event: dict[str, Any]) -> bool:
    agent = event.get("agent", {})
    return bool(
        event["type"] == "agent"
        and agent.get("id") == "scout"
        and agent.get("status") == "idle"
        and agent.get("job_id") is None
    )
