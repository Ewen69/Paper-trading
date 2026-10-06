from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ptl.app import create_app
from ptl.data.csv_ingest import import_csv
from ptl.data.models import DatasetKind
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from tests.conftest import SettingsFactory
from tests.test_backtest_service import NOW, trend, wave, write_bars_csv
from tests.test_data_api import FakeSource


@pytest.fixture
def client(make_settings: SettingsFactory, tmp_path: Path, calendar: MarketCalendar) -> TestClient:
    settings = make_settings()
    conn = connect(settings.database_path)
    migrate(conn)
    path = write_bars_csv(tmp_path / "bars.csv", calendar, {"QQQ": wave, "SPY": trend})
    import_csv(conn, path, DatasetKind.EQUITY_BARS, "synthetic", calendar, NOW)
    conn.close()
    app = create_app(settings, quote_source=FakeSource(), calendar=calendar, clock=lambda: NOW)
    return TestClient(app)


def test_strategies_endpoint(client: TestClient) -> None:
    body = client.get("/backtest/strategies").json()
    ids = [s["id"] for s in body]
    assert ids == ["buy_and_hold", "sma_crossover"]
    fast = body[1]["params"][0]
    assert (fast["name"], fast["default"], fast["minimum"]) == ("fast", 50, 2)


def test_universe_shows_symbols_and_locks(client: TestClient) -> None:
    before = client.get("/backtest/universe").json()
    assert [(e["symbol"], e["sessions"], e["lock"]) for e in before["entries"]] == [
        ("QQQ", 300, None),
        ("SPY", 300, None),
    ]
    first_run = {
        "dataset_id": 1,
        "symbol": "QQQ",
        "strategy": "buy_and_hold",
        "period": "in-sample",
    }
    client.post("/backtest/runs", json=first_run)
    after = client.get("/backtest/universe").json()
    qqq = after["entries"][0]
    assert qqq["lock"]["oos_fraction"] == 0.3
    assert qqq["in_sample_runs"] == 1


def test_run_returns_report_with_reality_check(client: TestClient) -> None:
    response = client.post(
        "/backtest/runs",
        json={
            "dataset_id": 1,
            "symbol": "qqq",
            "strategy": "sma_crossover",
            "params": {"fast": 5, "slow": 20},
            "period": "in-sample",
            "slippage_bps": 2,
            "commission_per_order": 1,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "QQQ"
    assert body["strategy"]["params"] == {"fast": 5, "slow": 20}
    rc = body["reality_check"]
    assert rc["costs"] == {"slippage_bps": 2.0, "commission_per_order": 1.0, "commission_bps": 0.0}
    assert rc["parameter_combinations_tried"] == 1
    assert rc["sessions"] == 210
    assert body["provenance"]["data_type"] == "end-of-day"
    assert body["strategy_metrics"]["sharpe"]["ci95"] is not None
    runs = client.get("/backtest/runs").json()
    assert runs[0]["summary"]["trades"] == body["strategy_metrics"]["trade_count"]


def test_refusals_are_409_and_bad_input_422(client: TestClient) -> None:
    refused = client.post(
        "/backtest/runs",
        json={"dataset_id": 99, "symbol": "QQQ", "strategy": "buy_and_hold", "period": "in-sample"},
    )
    assert refused.status_code == 409
    assert "does not exist" in refused.json()["detail"]
    invalid = client.post(
        "/backtest/runs",
        json={
            "dataset_id": 1,
            "symbol": "QQQ",
            "strategy": "buy_and_hold",
            "period": "in-sample",
            "slippage_bps": -3,
        },
    )
    assert invalid.status_code == 422


def test_explicit_lock(client: TestClient) -> None:
    created = client.post(
        "/backtest/locks", json={"dataset_id": 1, "symbol": "SPY", "oos_fraction": 0.25}
    )
    assert created.status_code == 200
    assert created.json()["oos_fraction"] == 0.25
    again = client.post("/backtest/locks", json={"dataset_id": 1, "symbol": "SPY"})
    assert again.status_code == 409
