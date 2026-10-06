"""Auditor flags and the Graduation Gate, built from real stored records (synthetic test data)."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ptl.backtest import service
from ptl.backtest.repository import Period, RunRecord
from ptl.config import Settings
from ptl.data_api import compute_data_health
from ptl.db import connect, migrate
from ptl.game import GameStateOut, compute_game_state
from ptl.graduation import GATE_NOTE, GateItem, compute_graduation
from ptl.market_calendar import MarketCalendar
from ptl.paper.store import PaperEvidence
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


def codes(game: GameStateOut) -> list[str]:
    return [f.code for f in game.flags]


def test_nothing_to_audit(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar
) -> None:
    game = state(conn, settings, calendar)
    auditor = next(a for a in game.agents if a.id == "auditor")
    assert auditor.status == "idle"
    assert auditor.report[0].text == "Nothing to audit yet."
    assert codes(game) == ["live_off"]
    gate = game.graduation
    assert (gate.met, gate.total) == (0, 4)
    assert [i.status for i in gate.items] == ["not_started"] * 4
    assert gate.note == GATE_NOTE
    assert "cannot, enable live trading" in gate.note


def test_auditor_surfaces_stored_run_warnings(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "two.csv", series={"QQQ": wave, "SPY": trend})
    service.run(conn, request(dataset), calendar, NOW)
    second = service.run(conn, request(dataset, params={"fast": 10, "slow": 40}), calendar, NOW)
    game = state(conn, settings, calendar)

    flags = {f.code: f for f in game.flags}
    assert flags["low_trades"].severity == "warning"
    assert flags["many_trials"].title == "Over-tuning risk"
    assert f"#{second.run_id} QQQ sma_crossover" in flags["many_trials"].summary
    assert flags["many_trials"].refs == [f"run #{second.run_id}"]
    # Both current results lack 30 trades; the flag lists both runs.
    assert flags["low_trades"].summary.startswith("2 current result(s)")
    assert flags["warmup_cash"].severity == "info"
    # Errors first, then warnings, then info.
    ranks = ["error", "warning", "info"]
    assert [f.severity for f in game.flags] == sorted(
        (f.severity for f in game.flags), key=ranks.index
    )
    auditor = next(a for a in game.agents if a.id == "auditor")
    assert auditor.status == "warning"
    assert auditor.station == "audit"
    assert any("Small sample" in line.text for line in auditor.report)


def test_superseded_runs_do_not_pile_up(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    dataset = load(conn, tmp_path, calendar, "q.csv", series={"QQQ": wave})
    for _ in range(3):  # the same combination three times: only the latest counts
        latest = service.run(conn, request(dataset), calendar, NOW)
    low = next(f for f in state(conn, settings, calendar).flags if f.code == "low_trades")
    assert low.refs == [f"run #{latest.run_id}"]


def test_data_gaps_are_flagged(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    path = tmp_path / "gappy.csv"
    path.write_text(
        "symbol,date,open,high,low,close,volume\n"
        "QQQ,2025-01-02,1,2,0.5,1.5,10\n"
        "QQQ,2025-01-10,1,2,0.5,1.5,10\n",
        encoding="utf-8",
    )
    from ptl.data.csv_ingest import import_csv  # noqa: PLC0415
    from ptl.data.models import DatasetKind  # noqa: PLC0415

    import_csv(conn, path, DatasetKind.EQUITY_BARS, "synthetic", calendar, NOW)
    gaps = next(f for f in state(conn, settings, calendar).flags if f.code == "data_gaps")
    assert gaps.summary == "QQQ: 4 missing NYSE sessions (2025-01-03..2025-01-08)."
    assert gaps.station == "data-health"


def _run(run_id: int, symbol: str, period: Period, ci: tuple[float, float] | None) -> RunRecord:
    return RunRecord(
        id=run_id,
        created_at=NOW,
        symbol=symbol,
        period=period,
        strategy="sma_crossover",
        params={"fast": 5, "slow": 20},
        costs={"slippage_bps": 5.0},
        window_start=date(2025, 1, 2),
        window_end=date(2025, 6, 30),
        summary={
            "excess_ci_low": ci[0] if ci else None,
            "excess_ci_high": ci[1] if ci else None,
        },
    )


NO_PAPER = PaperEvidence(0, None, None, 0, 0)


def _edge(runs: list[RunRecord]) -> GateItem:
    return next(i for i in compute_graduation(runs, NO_PAPER, NOW).items if i.id == "oos_edge")


def test_gate_judges_only_the_first_out_of_sample_look() -> None:
    first_fails = _run(1, "SPY", "out-of-sample", (-0.03, 0.02))
    later_passes = _run(2, "SPY", "out-of-sample", (0.01, 0.05))  # a second peek: ignored
    item = _edge([first_fails, later_passes])
    assert item.status == "not_met"
    assert item.progress == "1 first look(s); none with a 95% CI entirely above 0."


def test_gate_needs_the_whole_interval_above_zero() -> None:
    assert _edge([_run(1, "SPY", "out-of-sample", (0.0, 0.04))]).status == "not_met"
    met = _edge([_run(1, "SPY", "out-of-sample", (0.001, 0.04))])
    assert met.status == "met"
    assert met.evidence == (
        "Run #1: sma_crossover {'fast': 5, 'slow': 20} on SPY, 95% CI 0.10% to 4.00%."
    )


def test_gate_ignores_in_sample_runs_and_reports_missing_cis() -> None:
    assert _edge([_run(1, "SPY", "in-sample", (0.02, 0.05))]).status == "not_started"
    item = _edge([_run(1, "SPY", "out-of-sample", None)])
    assert item.status == "not_met"
    assert item.progress.endswith("1 had no stored CI.")


def test_gate_met_on_a_clean_first_look(
    conn: sqlite3.Connection, settings: Settings, calendar: MarketCalendar, tmp_path: Path
) -> None:
    # QQQ trends steadily up while the benchmark SPY barely moves: buy-and-hold QQQ beats SPY.
    def flat(i: int) -> float:
        return 100 + 0.05 * (i % 2)

    def steady(i: int) -> float:
        return 100 * 1.003**i

    dataset = load(conn, tmp_path, calendar, "edge.csv", series={"QQQ": steady, "SPY": flat})
    service.run(conn, request(dataset, strategy_id="buy_and_hold", params={}), calendar, NOW)
    service.run(
        conn,
        request(dataset, "out-of-sample", strategy_id="buy_and_hold", params={}),
        calendar,
        NOW,
    )
    gate = state(conn, settings, calendar).graduation
    item = next(i for i in gate.items if i.id == "oos_edge")
    assert item.status == "met"
    assert item.evidence is not None
    assert "95% CI" in item.evidence
    assert gate.met == 1


def _paper(evidence: PaperEvidence) -> dict[str, GateItem]:
    return {i.id: i for i in compute_graduation([], evidence, NOW).items}


def test_paper_items_count_only_paper_account_fills() -> None:
    empty = _paper(NO_PAPER)
    assert {empty[k].status for k in ("paper_days", "paper_trades", "zero_breaches")} == {
        "not_started"
    }
    first, last = datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 4, 10, tzinfo=UTC)
    items = _paper(PaperEvidence(12, first, last, 0, 30))
    assert (items["paper_days"].status, items["paper_days"].progress) == ("met", "95 of 90 days.")
    assert (items["paper_trades"].status, items["paper_trades"].progress) == (
        "not_met",
        "12 of 200 trades.",
    )
    assert items["zero_breaches"].status == "met"
    tripped = _paper(PaperEvidence(12, first, last, 1, 30))["zero_breaches"]
    assert tripped.status == "not_met"
    assert "1 kill-switch trip" in tripped.progress
