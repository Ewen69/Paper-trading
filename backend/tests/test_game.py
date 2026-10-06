"""Game layer: XP and badges follow process rules and are derived only from real records."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ptl.app import create_app
from ptl.backtest import service
from ptl.backtest.models import CostModel
from ptl.config import Settings
from ptl.data_api import compute_data_health
from ptl.db import connect, migrate
from ptl.game import GameStateOut, compute_game_state
from ptl.market_calendar import MarketCalendar
from tests.conftest import SettingsFactory
from tests.test_backtest_service import NOW, load, request, trend, wave
from tests.test_data_api import FakeSource


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings()


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    migrate(connection)
    yield connection
    connection.close()


def state(conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar) -> GameStateOut:
    health = compute_data_health(settings, FakeSource(configured=False), calendar, NOW)
    return compute_game_state(conn, health, NOW)


def agent(game: GameStateOut, agent_id: str) -> dict[str, object]:
    return next(a for a in game.agents if a.id == agent_id).model_dump()


def earned(game: GameStateOut) -> set[str]:
    return {b.id for b in game.badges if b.earned}


def test_fresh_start(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar
) -> None:
    game = state(conn, settings, calendar)
    assert (game.player.xp, game.player.level, game.player.next_level_xp) == (0, 1, 100)
    assert game.events == []
    assert earned(game) == set()
    scout, quant = agent(game, "scout"), agent(game, "quant")
    assert scout["status"] == "idle"
    assert "No historical datasets yet" in str(scout["report"])
    assert "keys are not set" in str(scout["report"])
    assert quant["status"] == "idle"
    locked = [a for a in game.agents if a.status == "locked"]
    assert [a.id for a in locked] == ["risk", "trader", "accountant", "analyst", "lead"]
    assert all(a.unlocks_in and a.report == [] and a.station is None for a in locked)
    assert "never earn XP" in game.xp_policy


def test_xp_follows_process_rules(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "two.csv", series={"QQQ": wave, "SPY": trend})
    service.run(conn, request(dataset), calendar, NOW)  # lock +30, first in-sample +15
    service.run(conn, request(dataset, params={"fast": 10, "slow": 40}), calendar, NOW)  # 0
    service.run(conn, request(dataset, "out-of-sample"), calendar, NOW)  # first OOS +50

    game = state(conn, settings, calendar)
    assert game.player.xp == 20 + 30 + 15 + 0 + 50
    assert game.player.level == 2
    by_kind = [(e.kind, e.xp) for e in reversed(game.events)]
    assert by_kind == [
        ("import", 20),
        ("oos_lock", 30),
        ("run_in-sample", 15),
        ("run_in-sample", 0),
        ("run_out-of-sample", 50),
    ]
    second_run = next(e for e in game.events if e.kind == "run_in-sample" and e.xp == 0)
    assert second_run.xp_note == "XP only for the first in-sample run per symbol"
    assert earned(game) == {"first_contact", "clean_room", "sealed_vault", "restraint", "one_shot"}
    assert agent(game, "quant")["xp"] == 95
    assert agent(game, "scout")["xp"] == 20

    service.run(conn, request(dataset, "out-of-sample"), calendar, NOW)  # repeat look
    again = state(conn, settings, calendar)
    assert again.player.xp == game.player.xp  # no XP for peeking again
    assert again.events[0].xp_note == "repeat look at the holdout: no XP"
    assert "one_shot" not in earned(again)  # badge lost on the second look
    assert "restraint" in earned(again)


def test_out_of_sample_without_in_sample_work_earns_nothing(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "q.csv", series={"QQQ": wave})
    service.run(conn, request(dataset, "out-of-sample"), calendar, NOW)
    game = state(conn, settings, calendar)
    oos = next(e for e in game.events if e.kind == "run_out-of-sample")
    assert oos.xp == 0
    assert oos.xp_note == "no in-sample work first: no XP"
    assert "one_shot" not in earned(game)


def test_cost_stress_badge_and_quant_caution(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "q.csv", series={"QQQ": wave})
    stressed = service.RunRequest(
        dataset_id=dataset,
        symbol="QQQ",
        strategy_id="buy_and_hold",
        period="in-sample",
        costs=CostModel(slippage_bps=10),
    )
    service.run(conn, stressed, calendar, NOW)
    for fast in range(2, 8):  # 6 more combinations: past the caution threshold
        service.run(conn, request(dataset, params={"fast": fast, "slow": 30}), calendar, NOW)
    game = state(conn, settings, calendar)
    assert "cost_realist" in earned(game)
    quant = agent(game, "quant")
    assert quant["status"] == "warning"
    assert "7 parameter combination(s)" in str(quant["report"])
    assert "biased upward" in str(quant["report"])


def test_xp_does_not_depend_on_results(
    make_settings: SettingsFactory, calendar: MarketCalendar, tmp_path: Path
) -> None:
    totals = []
    for name, series in (("up", trend), ("wavy", wave)):
        settings = make_settings(database_path=tmp_path / f"{name}.sqlite3")
        conn = connect(settings.database_path)
        migrate(conn)
        dataset = load(conn, tmp_path, calendar, f"{name}.csv", series={"QQQ": series})
        service.run(conn, request(dataset), calendar, NOW)
        service.run(conn, request(dataset, "out-of-sample"), calendar, NOW)
        totals.append(state(conn, settings, calendar).player.xp)
        conn.close()
    assert totals[0] == totals[1]


def test_game_state_endpoint(settings: Settings, calendar: MarketCalendar) -> None:
    app = create_app(settings, quote_source=FakeSource(), calendar=calendar, clock=lambda: NOW)
    body = TestClient(app).get("/game/state").json()
    assert body["player"]["xp"] == 0
    assert body["agents"][0]["name"] == "Data Scout"
    assert body["agents"][0]["report"][1]["source"] == "Alpaca paper account check"
    assert [r["xp"] for r in body["xp_rules"]] == [20, 30, 15, 50]
