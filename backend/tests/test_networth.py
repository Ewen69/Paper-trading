"""Net worth: storage, carry-forward history, staleness, CSV, projections and the API.

All amounts are made-up test values; nothing here is real financial data.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ptl.app import create_app
from ptl.cli import main as cli_main
from ptl.config import Settings
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.networth import compute, csvio, store
from tests.conftest import SettingsFactory
from tests.test_data_api import FakeSource

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings()


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    migrate(connection)
    yield connection
    connection.close()


def add(conn: sqlite3.Connection, account: store.Account, day: str, dollars: float) -> None:
    with conn:
        store.upsert_balance(
            conn,
            account_id=account.id,
            as_of=date.fromisoformat(day),
            amount_cents=store.to_cents(dollars),
            source=store.MANUAL_SOURCE,
            now=NOW,
        )


def account(conn: sqlite3.Connection, name: str, kind: str, category: str) -> store.Account:
    return store.create_account(conn, name=name, kind=kind, category=category, note="", now=NOW)


# ---- storage and validation ----


def test_accounts_validate_kind_category_and_unique_names(conn: sqlite3.Connection) -> None:
    account(conn, "Checking", "asset", "cash")
    with pytest.raises(store.NetWorthError, match="already exists"):
        account(conn, "checking", "asset", "cash")  # names are case-insensitive
    with pytest.raises(store.NetWorthError, match="isn't valid for a liability"):
        account(conn, "Card", "liability", "cash")
    with pytest.raises(store.NetWorthError, match="'asset' or 'liability'"):
        account(conn, "X", "income", "cash")
    with pytest.raises(store.NetWorthError, match="positive numbers"):
        store.to_cents(-1)
    with pytest.raises(store.NetWorthError, match="in the future"):
        store.check_date(date(2026, 10, 7), NOW.date())


def test_same_date_entry_replaces_and_cents_are_exact(conn: sqlite3.Connection) -> None:
    cash = account(conn, "Checking", "asset", "cash")
    add(conn, cash, "2026-09-30", 0.1)
    add(conn, cash, "2026-09-30", 0.2)
    [only] = store.balances(conn, cash.id)
    assert only.amount_cents == 20
    assert store.delete_account(conn, cash.id)
    assert store.balances(conn) == []  # balances go with the account


# ---- history and staleness (hand-computed) ----


def test_history_carries_forward_and_lists_missing_accounts(conn: sqlite3.Connection) -> None:
    cash = account(conn, "Checking", "asset", "cash")
    house = account(conn, "House", "asset", "real_estate")
    loan = account(conn, "Mortgage", "liability", "mortgage")
    add(conn, cash, "2026-01-31", 5_000)
    add(conn, loan, "2026-01-31", 200_000)
    add(conn, house, "2026-02-28", 300_000)
    add(conn, cash, "2026-03-31", 6_500.25)
    points = compute.history(store.accounts(conn), store.balances(conn))
    assert [(str(p.as_of), p.net_cents, p.carried_forward, p.missing) for p in points] == [
        ("2026-01-31", 500_000 - 20_000_000, 0, ("House",)),
        # house 300,000 + cash 5,000 (carried) - loan 200,000 (carried) = 105,000
        ("2026-02-28", 10_500_000, 2, ()),
        # house 300,000 (carried) + cash 6,500.25 - loan 200,000 (carried) = 106,500.25
        ("2026-03-31", 10_650_025, 2, ()),
    ]


def test_latest_balances_flag_stale_and_never_entered(conn: sqlite3.Connection) -> None:
    cash = account(conn, "Checking", "asset", "cash")
    old = account(conn, "Car", "asset", "vehicle")
    account(conn, "Card", "liability", "credit_card")
    add(conn, cash, "2026-09-30", 1)
    add(conn, old, "2026-08-21", 1)  # 46 days before 2026-10-06
    latest = compute.latest_balances(store.accounts(conn), store.balances(conn), NOW.date(), 45)
    assert {x.account.name: (x.age_days, x.stale) for x in latest} == {
        "Checking": (6, False),
        "Car": (46, True),
        "Card": (None, True),
    }


# ---- projections ----


def test_projection_compounds_the_hand_computed_way() -> None:
    points = compute.project(100_000_00, years=2, low=0.0, high=0.05, contribution_cents=10_000_00)
    # low (0%):  100,000 + 2 x 10,000 = 120,000
    # high (5%): 100,000 x 1.05^2 + 10,000 x (1.05^2 - 1) / 0.05 = 110,250 + 20,500 = 130,750
    assert [(p.year, p.low_cents, p.high_cents) for p in points] == [
        (0, 100_000_00, 100_000_00),
        (1, 110_000_00, 115_000_00),
        (2, 120_000_00, 130_750_00),
    ]


def test_projection_refuses_meaningless_inputs() -> None:
    with pytest.raises(compute.ProjectionError, match="positive net worth"):
        compute.project(-1, years=10, low=0.04, high=0.06, contribution_cents=0)
    with pytest.raises(compute.ProjectionError, match="low ≤ high"):
        compute.project(100, years=10, low=0.06, high=0.04, contribution_cents=0)
    with pytest.raises(compute.ProjectionError, match="between 1 and 50"):
        compute.project(100, years=0, low=0.04, high=0.06, contribution_cents=0)


# ---- CSV ----

GOOD = """account,kind,category,as_of,amount
Checking,asset,cash,2026-08-31,4200.15
Mortgage,liability,mortgage,2026-08-31,212000
Checking,asset,cash,2026-09-30,3900
"""


def test_csv_import_creates_accounts_and_reimport_replaces(conn: sqlite3.Connection) -> None:
    r = csvio.import_csv(conn, GOOD, "bank.csv", NOW)
    assert (r.rows, r.accounts_created, r.inserted, r.updated) == (3, 2, 3, 0)
    assert r.source == "CSV import: bank.csv"
    again = csvio.import_csv(conn, GOOD, "bank.csv", NOW)
    assert (again.accounts_created, again.inserted, again.updated) == (0, 0, 3)
    assert csvio.export_csv(conn) == (
        "account,kind,category,as_of,amount\n"
        "Checking,asset,cash,2026-08-31,4200.15\n"
        "Checking,asset,cash,2026-09-30,3900.00\n"
        "Mortgage,liability,mortgage,2026-08-31,212000.00\n"
    )


def test_csv_import_is_all_or_nothing(conn: sqlite3.Connection) -> None:
    account(conn, "Checking", "asset", "cash")
    bad = """account,kind,category,as_of,amount
Savings,asset,cash,2026-09-30,100
Checking,liability,credit_card,2026-09-30,5
Savings,asset,cash,2026-09-30,101
Card,liability,credit_card,2026-13-01,5
Card,liability,credit_card,2026-10-07,5
Car,asset,vehicle,2026-09-30,-3
"""
    with pytest.raises(csvio.CsvRejectedError) as caught:
        csvio.import_csv(conn, bad, "bad.csv", NOW)
    assert caught.value.errors == [
        "Line 4: Savings already has a balance for 2026-09-30 in this file.",
        "Line 5: date must be YYYY-MM-DD and amount a plain number (got '2026-13-01', '5').",
        "Line 6: Date 2026-10-07 is in the future. Balances record what was true.",
        "Line 7: Enter amounts as positive numbers; the account's kind sets the sign.",
        "Line 3: Checking is a asset (cash), not a liability (credit_card).",
    ]
    assert [a.name for a in store.accounts(conn)] == ["Checking"]  # Savings was not created
    assert store.balances(conn) == []
    with pytest.raises(csvio.CsvRejectedError, match="header must be exactly"):
        csvio.import_csv(conn, "name,amount\nx,1\n", "x.csv", NOW)


def test_cli_import_and_export(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(settings.database_path))
    source = tmp_path / "in.csv"
    source.write_text(GOOD, encoding="utf-8")
    target = tmp_path / "out.csv"
    assert cli_main(["networth-import", str(source)]) == 0
    assert cli_main(["networth-export", str(target)]) == 0
    assert target.read_text(encoding="utf-8").startswith("account,kind,category,as_of,amount\n")


# ---- API ----


def client(settings: Settings) -> TestClient:
    app = create_app(
        settings,
        quote_source=FakeSource(configured=False),
        calendar=MarketCalendar(),
        clock=lambda: NOW,
    )
    return TestClient(app)


def test_api_end_to_end(settings: Settings) -> None:
    with client(settings) as c:
        empty = c.get("/networth").json()
        assert empty["totals"]["as_of"] is None
        assert empty["data_type"] == "manual"
        assert c.get("/networth/projection").status_code == 422

        cash = c.post(
            "/networth/accounts", json={"name": "Checking", "kind": "asset", "category": "cash"}
        )
        assert cash.status_code == 201
        card = c.post(
            "/networth/accounts",
            json={"name": "Card", "kind": "liability", "category": "credit_card"},
        ).json()
        bad = c.post(
            "/networth/accounts", json={"name": "X", "kind": "asset", "category": "mortgage"}
        )
        assert bad.status_code == 422

        cash_id = cash.json()["id"]
        first = c.post(
            "/networth/balances",
            json={"account_id": cash_id, "as_of": "2026-09-30", "amount": 10_000},
        )
        assert first.json()["replaced"] is False
        again = c.post(
            "/networth/balances",
            json={"account_id": cash_id, "as_of": "2026-09-30", "amount": 12_000},
        )
        assert again.json()["replaced"] is True
        future = c.post(
            "/networth/balances", json={"account_id": cash_id, "as_of": "2026-10-07", "amount": 1}
        )
        assert future.status_code == 422
        c.post(
            "/networth/balances",
            json={"account_id": card["id"], "as_of": "2026-10-01", "amount": 2_000.5},
        )

        s = c.get("/networth").json()
        assert s["totals"] == {
            "assets": 12_000.0,
            "liabilities": 2_000.5,
            "net_worth": 9_999.5,
            "as_of": "2026-10-01",
            "oldest_included": "2026-09-30",
            "missing": [],
        }
        assert [p["net_worth"] for p in s["history"]] == [12_000.0, 9_999.5]
        assert {a["name"]: a["age_days"] for a in s["accounts"]} == {"Checking": 6, "Card": 5}

        p = c.get("/networth/projection", params={"years": 1, "low": 0.04, "high": 0.06}).json()
        assert (p["start"], p["points"][1]) == (
            9_999.5,
            {"year": 1, "low": 10_399.48, "high": 10_599.47},
        )
        assert "not a forecast" in p["note"]

        export = c.get("/networth/export")
        assert export.headers["content-type"].startswith("text/csv")
        assert 'filename="net-worth-2026-10-06.csv"' in export.headers["content-disposition"]

        imported = c.post("/networth/import", json={"file_name": "x.csv", "content": "bad"})
        assert imported.status_code == 422
        assert "nothing was saved" in imported.json()["detail"]

        [balance] = c.get(f"/networth/accounts/{card['id']}/balances").json()
        assert c.delete(f"/networth/balances/{balance['id']}").status_code == 204
        assert c.delete(f"/networth/accounts/{card['id']}").status_code == 204
        assert c.delete(f"/networth/accounts/{card['id']}").status_code == 404


def test_accountant_reports_counts_never_amounts(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    with client(settings) as c:
        idle = next(a for a in c.get("/game/state").json()["agents"] if a["id"] == "accountant")
        assert (idle["status"], idle["station"]) == ("idle", "net-worth")
        cash = account(conn, "Checking", "asset", "cash")
        add(conn, cash, "2026-09-30", 123_456)
        account(conn, "Card", "liability", "credit_card")
        acct = next(a for a in c.get("/game/state").json()["agents"] if a["id"] == "accountant")
        assert acct["status"] == "warning"
        text = " ".join(line["text"] for line in acct["report"])
        assert "2 account(s), 1 balance(s). Latest entry 2026-09-30." in text
        assert "Card" in text
        assert "123" not in text  # amounts stay on the Net Worth station
