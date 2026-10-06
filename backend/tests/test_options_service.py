"""Options strategy decisions and the full options backtest service, on synthetic fixtures.

Underlying SPY bars are synthetic: 585.00 every session from 2024-06-03 to 2025-01-21 except
the Jan 17 2025 expiration close, so the first run locks Nov 2024 onward as out-of-sample and
the fixture's January option chain falls inside it.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ptl.app import create_app
from ptl.backtest import repository as repo
from ptl.data.csv_ingest import import_csv
from ptl.data.models import DatasetKind
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.options import service
from ptl.options.models import OptionCosts
from tests.conftest import FIXTURES, SettingsFactory
from tests.test_data_api import FakeSource

NOW = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
EXP = date(2025, 1, 17)
COSTS = OptionCosts(slippage_per_share=0.02, commission_per_contract=0.65)
# otm 0% -> highest put strike <= 585 is 580; width 5 -> long 575; dte 10 -> Jan 17 from Jan 2.
PARAMS = {"dte": 10, "otm_percent": 0, "width": 5, "contracts": 2, "take_profit": 0, "max_open": 1}


def write_spy_bars(path: Path, calendar: MarketCalendar, expiry_close: float) -> Path:
    lines = ["symbol,date,open,high,low,close,volume,adj_close"]
    for day in calendar.sessions(date(2024, 6, 3), date(2025, 1, 21)):
        close = expiry_close if day == EXP else 585.0
        lines.append(f"SPY,{day},{close},{close + 1},{close - 1},{close},1000,{close}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def load_all(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path, expiry_close: float = 590.0
) -> tuple[int, int]:
    options = import_csv(
        conn,
        FIXTURES / "options" / "synthetic_spy_put_spread.csv",
        DatasetKind.OPTION_QUOTES,
        "synthetic",
        calendar,
        NOW,
    )
    bars = import_csv(
        conn,
        write_spy_bars(tmp_path / "spy.csv", calendar, expiry_close),
        DatasetKind.EQUITY_BARS,
        "synthetic",
        calendar,
        NOW,
    )
    return options.dataset_id, bars.dataset_id


def request(
    options_id: int, bars_id: int, period: repo.Period = "out-of-sample", **params: int
) -> service.OptionsRunRequest:
    return service.OptionsRunRequest(
        options_dataset_id=options_id,
        underlying_dataset_id=bars_id,
        symbol="SPY",
        strategy_id="put_credit_spread",
        period=period,
        params={**PARAMS, **params},
        costs=COSTS,
        initial_capital=10_000.0,
    )


def test_out_of_sample_run_trades_the_chain(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    options_id, bars_id = load_all(conn, calendar, tmp_path)
    report = service.run(conn, request(options_id, bars_id), calendar, NOW)
    [trade] = report.trades
    assert trade.description == "SPY 2025-01-17 580/575 put credit spread x2"
    assert trade.pnl == pytest.approx(193.40)  # expired worthless at 590: credit - commissions
    assert trade.exit_kind == "expired worthless"
    opened = next(e for e in report.events if e.kind == "open")
    assert "Short 580P is the highest put strike <= close 585.00 x (1 - 0%) = 585.00" in opened.text
    assert report.counts.peak_collateral == pytest.approx(1000.0)
    rc = report.reality_check
    assert rc.price_basis == "raw"
    assert rc.cost_lines[1].value == "$0.65 per contract"
    assert rc.oos_evaluations == 1
    assert report.benchmark_symbol == "SPY"
    assert report.provenance.source.startswith("Option quotes: synthetic")
    codes = [w.code for w in rc.warnings]
    assert "low_trades" in codes
    assert [w.code for w in repo.warnings_by_run(conn)[report.run_id]] == codes


def test_in_sample_run_cannot_see_the_locked_january_chain(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    options_id, bars_id = load_all(conn, calendar, tmp_path)
    report = service.run(conn, request(options_id, bars_id, "in-sample"), calendar, NOW)
    lock = repo.get_lock(conn, "SPY")
    assert lock is not None
    assert report.reality_check.window_end < lock.oos_start <= date(2025, 1, 2)
    assert report.trades == []  # the only chain is in the locked period


def test_take_profit_closes_before_expiry(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    # Cost to close on Jan 16 (580P ask 0.90 + 0.02, 575P bid 0.20 - 0.02) = 0.74 x 200 = 148;
    # captured 196 - 148 = 48 >= 20% of 196 = 39.20 (Jan 15: 28 < 39.20). Fills Jan 17 quotes:
    # (0.50 + 0.02) - (0.05 - 0.02) = 0.49 x 200 = 98 + 2.60 commissions.
    options_id, bars_id = load_all(conn, calendar, tmp_path)
    report = service.run(conn, request(options_id, bars_id, take_profit=20), calendar, NOW)
    [trade] = report.trades
    assert trade.exit_kind == "closed"
    assert trade.closed == EXP
    assert trade.pnl == pytest.approx(193.40 - 98.00 - 2.60)
    close = next(e for e in report.events if e.kind == "close")
    assert "captured 48.00 = credit 196.00 - cost to close 148.00" in close.text


def test_pin_risk_surfaces_as_stored_warnings(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    options_id, bars_id = load_all(conn, calendar, tmp_path, expiry_close=577.0)
    report = service.run(conn, request(options_id, bars_id), calendar, NOW)
    codes = {w.code for w in report.reality_check.warnings}
    assert {"pin_risk", "assignment_margin"} <= codes
    assert report.counts.pin_events == 1
    # Jan 21 bars open at 585 (synthetic): 200 x 585 x (1 - 5 bps) = 116,941.50 back.
    assert report.trades[0].pnl == pytest.approx(193.40 - 116_000 + 200 * 585 * 0.9995)


def test_missing_exercise_style_is_refused(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    no_style = tmp_path / "no_style.csv"
    no_style.write_text(
        "underlying,quote_date,expiration,strike,option_type,bid,ask\n"
        "SPY,2025-01-03,2025-01-17,580,P,3.10,3.20\n",
        encoding="utf-8",
    )
    options = import_csv(conn, no_style, DatasetKind.OPTION_QUOTES, "s", calendar, NOW)
    bars_csv = write_spy_bars(tmp_path / "b.csv", calendar, 590)
    bars = import_csv(conn, bars_csv, DatasetKind.EQUITY_BARS, "s", calendar, NOW)
    with pytest.raises(service.BacktestRefusedError, match="no exercise_style"):
        service.run(conn, request(options.dataset_id, bars.dataset_id), calendar, NOW)


def test_options_api(
    make_settings: SettingsFactory, calendar: MarketCalendar, tmp_path: Path
) -> None:
    settings = make_settings()
    conn = connect(settings.database_path)
    migrate(conn)
    options_id, bars_id = load_all(conn, calendar, tmp_path)
    conn.close()
    client = TestClient(
        create_app(settings, quote_source=FakeSource(), calendar=calendar, clock=lambda: NOW)
    )
    universe = client.get("/backtest/options/universe").json()
    [entry] = [e for e in universe["entries"] if e["underlying"] == "SPY"]
    assert entry["underlying_datasets"][0]["dataset_id"] == bars_id
    assert entry["rows_missing_style"] == 0
    body = {
        "options_dataset_id": options_id,
        "underlying_dataset_id": bars_id,
        "symbol": "SPY",
        "strategy": "put_credit_spread",
        "period": "out-of-sample",
        "params": PARAMS,
        "slippage_per_share": 0.02,
        "initial_capital": 10_000,
    }
    response = client.post("/backtest/options/runs", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["trades"][0]["pnl"] == pytest.approx(193.40)
    bad = client.post("/backtest/options/runs", json={**body, "commission_per_contract": -1})
    assert bad.status_code == 422
    refused = client.post("/backtest/options/runs", json={**body, "options_dataset_id": bars_id})
    assert refused.status_code == 409
