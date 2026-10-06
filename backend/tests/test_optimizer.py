"""Learning optimizer, paper runner daemon, telemetry and the launcher. Prices are synthetic."""

import math
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ptl import orchestrator
from ptl.agents import learning
from ptl.agents.optimizer import Target, next_target, run_daemon, run_search, targets
from ptl.app import create_app
from ptl.backtest import repository as bt_repo
from ptl.config import Settings
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.runner.daemon import RunnerState, tick
from ptl.telemetry import TelemetryHub, build_state
from tests.conftest import SettingsFactory
from tests.test_backtest_service import NOW, load, trend, wave
from tests.test_data_api import FakeSource
from tests.test_paper_runner import NOW as RUNNER_NOW
from tests.test_paper_runner import SESSIONS, FakeBroker, Quotes

TARGET = Target("equity", "sma_crossover", "QQQ", 1)


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(
        optimizer_population=6,
        optimizer_generations=2,
        optimizer_max_trials_per_target=14,
        optimizer_min_trades=2,
    )


@pytest.fixture
def conn(
    settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    migrate(connection)
    load(connection, tmp_path, calendar, "bars.csv", series={"QQQ": wave, "SPY": trend})
    yield connection
    connection.close()


def search(
    conn: sqlite3.Connection, settings: Settings, seed: int = 7
) -> learning.ActiveBest | None:
    return run_search(
        conn,
        TARGET,
        settings=settings,
        calendar=MarketCalendar(),
        clock=lambda: NOW,
        seed=seed,
        beat=lambda _detail: None,
    )


def test_targets_cover_strategies_with_a_search_space(conn: sqlite3.Connection) -> None:
    found = {(t.strategy, t.symbol) for t in targets(conn)}
    assert found == {
        ("sma_crossover", "QQQ"),
        ("sma_crossover", "SPY"),
        ("trend_filter", "QQQ"),
        ("trend_filter", "SPY"),
    }  # buy_and_hold has nothing to tune


def test_search_is_in_sample_first_and_counts_every_trial(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    best = search(conn, settings)
    trials = learning.recent_trials(conn, 500)
    ins = [t for t in trials if t.period == "in-sample"]
    oos = [t for t in trials if t.period == "out-of-sample"]
    # Budget: 14 distinct in-sample trials, each a logged backtest run.
    assert len({str(t.params) for t in ins}) == len(ins) <= 14
    assert bt_repo.run_counts(
        conn, "QQQ", "sma_crossover"
    ).in_sample_combinations_this_strategy == len(ins)
    assert all(t.run_id is not None and not t.reused for t in ins)
    # Top 5% (at least one) of the scored candidates get one counted holdout look each.
    scored = sorted((t for t in ins if t.fitness is not None), key=lambda t: -(t.fitness or 0))
    assert len(oos) == max(1, math.ceil(0.05 * len(scored)))
    assert all(t.fitness is None for t in oos)  # holdout results never become fitness
    assert [t.params for t in oos] == [t.params for t in scored[: len(oos)]]
    # The Active Best is the in-sample winner; its own holdout result only labels it.
    assert best is not None
    assert best.params == scored[0].params
    assert best.in_sample_fitness == scored[0].fitness
    assert best.oos_log_id == oos[0].id
    assert set(best.curve) == {"in-sample", "out-of-sample"}
    assert best.curve["in-sample"][0].keys() == {"day", "strategy", "benchmark"}
    lines = [x.message for x in learning.recent_log(conn)]
    assert any(line.startswith("Gen 1: best in-sample Sharpe") for line in lines)
    assert any(line.startswith("New Active Best (equity)") for line in lines)


def test_budget_holds_and_holdout_is_never_reused(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    search(conn, settings)
    looks = bt_repo.run_counts(conn, "QQQ", "sma_crossover").oos_evaluations
    search(conn, settings, seed=8)  # budget spent: nothing new to try
    assert (
        bt_repo.run_counts(conn, "QQQ", "sma_crossover").in_sample_combinations_this_strategy <= 14
    )
    after = bt_repo.run_counts(conn, "QQQ", "sma_crossover").oos_evaluations
    assert after == looks  # the same finalist is never re-tested out-of-sample
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM agent_learning_log")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE optimizer_active_best SET validated = 1")


def test_daemon_runs_a_search_then_idles_when_budgets_are_spent(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    tight = settings.model_copy(update={"optimizer_max_trials_per_target": 10})
    assert run_daemon(tight, clock=lambda: NOW, once=True, seed=1) == 1
    beats = {h.daemon: h for h in learning.heartbeats(conn)}
    assert beats["optimizer"].status == "stopped"
    # Spend every budget, then the daemon says it is idle instead of searching forever.
    for _ in range(12):  # 4 targets x a 10-trial budget: a few searches each at most
        if next_target(conn, tight) is None:
            break
        run_daemon(tight, clock=lambda: NOW, once=True, seed=2)
    assert run_daemon(tight, clock=lambda: NOW, once=True) == 0
    assert any(
        "Idle: every target has used its trial budget" in x.message
        for x in learning.recent_log(conn)
    )


def test_daemon_stops_on_request(conn: sqlite3.Connection, settings: Settings) -> None:
    stop = threading.Event()
    stop.set()
    assert run_daemon(settings, clock=lambda: NOW, stop=stop) == 0


# ---- paper runner daemon ---------------------------------------------------------------


@pytest.fixture
def runner_conn(
    settings: Settings, tmp_path: Path, calendar: MarketCalendar
) -> Iterator[sqlite3.Connection]:
    from ptl.data.csv_ingest import import_csv  # noqa: PLC0415
    from ptl.data.models import DatasetKind  # noqa: PLC0415

    connection = connect(settings.database_path)
    migrate(connection)
    lines = ["symbol,date,open,high,low,close,volume,adj_close"]
    lines += [f"SPY,{d},100,101,99,100,1000,100" for d in SESSIONS]
    (tmp_path / "spy.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    import_csv(
        connection, tmp_path / "spy.csv", DatasetKind.EQUITY_BARS, "synthetic", calendar, RUNNER_NOW
    )
    yield connection
    connection.close()


def make_best(conn: sqlite3.Connection, *, validated: bool) -> learning.ActiveBest:
    trial = learning.log_trial(
        conn,
        now=RUNNER_NOW,
        search_id="s-test",
        generation=1,
        asset="equity",
        strategy="buy_and_hold",
        symbol="SPY",
        dataset_id=1,
        options_dataset_id=None,
        params={},
        period="in-sample",
        run_id=None,
        reused=False,
        summary={"sharpe": 1.0, "trades": 1},
        fitness=1.0,
        verdict="evaluated",
        note="test",
    )
    return learning.record_active_best(
        conn,
        now=RUNNER_NOW,
        search_id="s-test",
        trial=trial,
        oos=None,
        validated=validated,
        reason="test",
        curve={},
    )


def run_tick(
    conn: sqlite3.Connection, settings: Settings, state: RunnerState, broker: FakeBroker
) -> dict[str, Any]:
    return tick(
        conn,
        state,
        settings=settings,
        broker=broker,
        quotes=Quotes(100.0),
        calendar=MarketCalendar(),
        now=RUNNER_NOW,
    )


def test_runner_waits_for_a_validated_active_best(
    runner_conn: sqlite3.Connection, settings: Settings
) -> None:
    state = RunnerState()
    broker = FakeBroker()
    assert run_tick(runner_conn, settings, state, broker)["active_best"] is None
    make_best(runner_conn, validated=False)
    run_tick(runner_conn, settings, state, broker)
    assert broker.submitted == []
    messages = [x.message for x in learning.recent_log(runner_conn)]
    assert "Waiting: the optimizer has no Active Best yet." in messages
    assert any("failed its out-of-sample check" in m for m in messages)


def test_runner_trades_a_validated_best_once_per_session(
    runner_conn: sqlite3.Connection, settings: Settings
) -> None:
    make_best(runner_conn, validated=True)
    state = RunnerState()
    broker = FakeBroker()
    detail = run_tick(runner_conn, settings, state, broker)
    # $10,000 equity at 100.00: max loss per trade $1,000 is the tightest cap -> 10 shares.
    assert [(o.side, o.qty) for o in broker.submitted] == [("buy", 10.0)]
    assert detail["active_best"]["validated"] is True
    risk = {r["name"]: r for r in detail["account"]["risk"]}
    assert risk["Open positions"]["limit"] == 5.0
    run_tick(runner_conn, settings, state, broker)  # same session: nothing new
    assert len(broker.submitted) == 1
    assert any("Rationale: Buy and hold" in x.message for x in learning.recent_log(runner_conn))


# ---- telemetry -------------------------------------------------------------------------


def test_telemetry_state_and_stream(conn: sqlite3.Connection, settings: Settings) -> None:
    state = build_state(settings, FakeSource(configured=False), MarketCalendar(), NOW)
    assert {d["daemon"]: d["status"] for d in state["daemons"]} == {
        "optimizer": "not running",
        "paper-runner": "not running",
    }
    assert state["optimizer"]["active_best"]["equity"] is None
    assert "flags" in state["auditor"]
    assert state["net_worth"]["totals"]["as_of"] is None
    hub = TelemetryHub(
        settings, source=FakeSource(configured=False), calendar=MarketCalendar(), clock=lambda: NOW
    )
    hub._init_cursors()
    search(conn, settings)
    kinds = [e["type"] for e in hub.poll()]
    assert "trial" in kinds and "log" in kinds  # noqa: PT018
    assert hub.poll() == []  # nothing new since

    app = create_app(
        settings,
        quote_source=FakeSource(configured=False),
        calendar=MarketCalendar(),
        clock=lambda: NOW,
    )
    with TestClient(app) as client, client.websocket_connect("/ws/telemetry") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["state"]["optimizer"]["active_best"]["equity"]["symbol"] == "QQQ"
        assert len(hello["trials"]) > 0
        assert client.get("/telemetry/state").json()["optimizer"]["counts"]["out_of_sample"] >= 1


def test_launcher_commands_default_to_dry_run() -> None:
    names = {
        name: cmd
        for name, cmd, _ in orchestrator.commands(
            paper=False, optimizer=True, runner=True, web=False
        )
    }
    assert set(names) == {"api", "optimizer", "runner"}
    assert names["runner"][-1] == "--dry-run"
    assert (
        orchestrator.commands(paper=True, optimizer=False, runner=True, web=False)[1][1][-1]
        == "--paper"
    )
