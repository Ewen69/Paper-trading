"""Portfolio: holdings, valuation sources, hand-computed metrics, rule tips and the API.

Prices are synthetic test fixtures; nothing here is real market or personal data.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from math import sqrt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ptl.app import create_app
from ptl.config import Settings
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.paper.broker import BrokerPosition, DryRunBroker
from ptl.portfolio import holdings as hs
from ptl.portfolio import metrics, service
from tests.conftest import SettingsFactory
from tests.test_backtest_service import NOW, load, trend, wave
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


def hold(conn: sqlite3.Connection, **kwargs: object) -> hs.Holding:
    return hs.add_holding(conn, hs.HoldingIn(**kwargs), NOW)  # type: ignore[arg-type]


# ---- holdings --------------------------------------------------------------------------


def test_holding_validation_rules(conn: sqlite3.Connection) -> None:
    cases: list[tuple[dict[str, object], str]] = [
        ({"symbol": "SPY", "asset_class": "etf", "quantity": -5}, "Only options can be short"),
        ({"symbol": "SPY", "asset_class": "option", "quantity": 1}, "option_type"),
        (
            {
                "symbol": "SPY",
                "asset_class": "option",
                "quantity": 1.5,
                "option_type": "put",
                "strike": 500,
                "expiration": date(2026, 12, 18),
            },
            "whole contracts",
        ),
        ({"symbol": "SPY", "asset_class": "etf", "quantity": 1, "strike": 5}, "Only options"),
        (
            {"symbol": "X", "asset_class": "fund", "quantity": 1, "manual_price": 10},
            "needs its date",
        ),
        ({"symbol": "X", "asset_class": "stocks", "quantity": 1}, "Asset class must be"),
        ({"symbol": "", "asset_class": "stock", "quantity": 1}, "Symbol"),
    ]
    for kwargs, message in cases:
        with pytest.raises(hs.HoldingError, match=message):
            hold(conn, **kwargs)
    assert hold(conn, symbol="spy", asset_class="etf", quantity=3).symbol == "SPY"


CSV = (
    "symbol,asset_class,quantity,account,option_type,strike,expiration,"
    "manual_price,manual_price_as_of\n"
    """QQQ,etf,10,Brokerage,,,,,
SPY,option,-1,Brokerage,put,540,2026-12-18,,
Target fund,fund,2,401k,,,,50.5,2026-09-30
Cash,cash,1000,Bank,,,,,
"""
)


def test_csv_import_replace_and_round_trip(conn: sqlite3.Connection) -> None:
    hold(conn, symbol="OLD", asset_class="stock", quantity=1)
    assert hs.import_csv(conn, CSV, replace=True, now=NOW) == (4, 1)
    exported = hs.export_csv(conn)
    assert hs.import_csv(conn, exported, replace=True, now=NOW) == (4, 4)
    assert hs.export_csv(conn) == exported
    bad = CSV.replace("QQQ,etf,10", "QQQ,etf,-10").replace("2026-12-18", "Dec 18")
    with pytest.raises(hs.CsvRejectedError) as caught:
        hs.import_csv(conn, bad, replace=True, now=NOW)
    assert [e.split(":")[0] for e in caught.value.errors] == ["Line 2", "Line 3"]
    assert len(hs.holdings(conn)) == 4  # nothing changed


# ---- metrics (hand-computed) -----------------------------------------------------------


def test_concentration_by_hand() -> None:
    c = metrics.concentration({"A": 60.0, "B": 30.0, "C": 10.0, "short": -5.0})
    assert c is not None
    assert c.hhi == pytest.approx(0.36 + 0.09 + 0.01)
    assert c.effective_n == pytest.approx(1 / 0.46)
    assert c.top[0][0] == "A"
    assert c.top[0][1] == pytest.approx(0.6)
    assert metrics.concentration({"x": -1.0}) is None


def _days(n: int) -> list[date]:
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def test_replay_risk_by_hand() -> None:
    # A alternates +2% / -2%; half the portfolio is cash, so it alternates +1% / -1%.
    days = _days(23)
    price = [100.0]
    for i in range(22):
        price.append(price[-1] * (1.02 if i % 2 == 0 else 0.98))
    series = dict(zip(days, price, strict=True))
    r = metrics.replay_risk({"A": 0.5, "__cash__": 0.5}, {"A": series}, series, window=252)
    assert r.returns == 22
    # 22 returns of +/-1%, mean 0: sample stdev = 0.01 x sqrt(22 / 21)
    assert r.volatility == pytest.approx(0.01 * sqrt(22 / 21) * sqrt(252))
    # Peak 1.01 after day 1; trough at the end, (1.01 x 0.99)^11.
    assert r.max_drawdown == pytest.approx((1.01 * 0.99) ** 11 / 1.01 - 1)
    assert r.beta == pytest.approx(0.5)
    assert r.correlation == pytest.approx(1.0)
    with pytest.raises(metrics.RiskUnavailableError, match="need at least 20"):
        metrics.replay_risk({"A": 1.0}, {"A": dict(list(series.items())[:10])}, None, 252)


# ---- valuation and analysis --------------------------------------------------------------


def ctx(calendar: MarketCalendar) -> service.PricingContext:
    return service.PricingContext(
        now=NOW, calendar=calendar, stale_after_sessions=5, manual_stale_after_days=45
    )


def add_option_quote(conn: sqlite3.Connection, *, greeks: bool) -> None:
    with conn:
        cur = conn.execute(
            "INSERT INTO datasets (kind, source, data_type, file_name, sha256, imported_at, "
            "row_count, symbol_count, coverage_start, coverage_end) VALUES "
            "('option_quotes', 'test', 'end-of-day', 'q.csv', ?, ?, 1, 1, ?, ?)",
            (f"sha-{greeks}", NOW.isoformat(), "2026-10-02", "2026-10-02"),
        )
        conn.execute(
            "INSERT INTO option_quotes (dataset_id, underlying, quote_date, expiration, strike, "
            "option_type, bid, ask, delta, theta, vega) VALUES (?, 'SPY', '2026-10-02', "
            "'2026-12-18', 540, 'put', 4.10, 4.30, ?, ?, ?)",
            (
                cur.lastrowid,
                -0.25 if greeks else None,
                -0.05 if greeks else None,
                0.6 if greeks else None,
            ),
        )


def test_valuation_sources_in_order(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    load(conn, tmp_path, calendar, "bars.csv", series={"QQQ": wave})
    qqq = service.value_holding(
        conn, hold(conn, symbol="QQQ", asset_class="etf", quantity=10), ctx(calendar)
    )
    assert qqq.price_source is not None
    assert qqq.price_source.startswith("Imported end-of-day close")
    assert qqq.stale  # synthetic bars end in 2024
    fund = service.value_holding(
        conn,
        hold(
            conn,
            symbol="Target fund",
            asset_class="fund",
            quantity=2,
            manual_price=50.5,
            manual_price_as_of=date(2026, 9, 30),
        ),
        ctx(calendar),
    )
    assert (fund.value, fund.price_source, fund.stale) == (101.0, "Manual price you entered", False)
    nothing = service.value_holding(
        conn, hold(conn, symbol="ZZZ", asset_class="stock", quantity=1), ctx(calendar)
    )
    assert nothing.value is None
    assert "not valued" in nothing.notes[0]
    cash = service.value_holding(
        conn, hold(conn, symbol="Cash", asset_class="cash", quantity=250), ctx(calendar)
    )
    assert cash.value == 250

    add_option_quote(conn, greeks=True)
    short_put = hold(
        conn,
        symbol="SPY",
        asset_class="option",
        quantity=-2,
        option_type="put",
        strike=540,
        expiration=date(2026, 12, 18),
    )
    put = service.value_holding(conn, short_put, ctx(calendar))
    # A short closes at the ask: 4.30 x 100 x -2 = -860
    assert put.value == pytest.approx(-860.0)
    assert put.greeks is not None
    assert (put.greeks.delta, put.greeks.theta, put.greeks.vega) == pytest.approx((50, 10, -120))


def test_analysis_and_tips(
    conn: sqlite3.Connection, calendar: MarketCalendar, tmp_path: Path
) -> None:
    load(conn, tmp_path, calendar, "bars.csv", series={"QQQ": wave, "SPY": trend})
    hold(conn, symbol="QQQ", asset_class="etf", quantity=10)
    hold(conn, symbol="Cash", asset_class="cash", quantity=1000)
    hold(conn, symbol="ZZZ", asset_class="stock", quantity=1)
    add_option_quote(conn, greeks=False)
    hold(
        conn,
        symbol="SPY",
        asset_class="option",
        quantity=-1,
        option_type="put",
        strike=540,
        expiration=date(2026, 12, 18),
    )
    positions = [service.value_holding(conn, h, ctx(calendar)) for h in hs.holdings(conn)]
    a = service.analyze(conn, positions, book="manual", benchmark="SPY", window=60)
    assert a.risk is not None
    assert a.risk.returns == 60
    assert a.risk_included == ["QQQ"]
    assert a.coverage == pytest.approx(1.0)  # QQQ + cash = all long value
    assert a.greeks_missing == ["SPY 2026-12-18 540 put"]
    tips = {t.id: t for t in a.tips}
    assert tips["largest_position"].status == "fired"  # QQQ is 100% of non-cash long value
    assert tips["largest_position"].observed == "QQQ is 100.0% of non-cash long value"
    assert tips["unpriced"].observed == "1 not valued: ZZZ"
    assert tips["short_options"].status == "fired"
    assert tips["stale_prices"].status == "fired"
    assert tips["expired_options"].status == "clear"
    assert all(t.threshold for t in a.tips)


# ---- API --------------------------------------------------------------------------------


class Reader:
    def positions(self) -> dict[str, BrokerPosition]:
        return {"AAPL": BrokerPosition("AAPL", 10, 2_300.0)}


def test_api(make_settings: SettingsFactory) -> None:
    keyed = make_settings(alpaca_api_key_id="k", alpaca_api_secret_key="s")
    app = create_app(
        keyed,
        quote_source=FakeSource(configured=False),
        calendar=MarketCalendar(),
        clock=lambda: datetime(2026, 10, 6, 15, tzinfo=UTC),
        brokers=lambda _dry: DryRunBroker(Reader(), 100_000),  # type: ignore[arg-type]
    )
    with TestClient(app) as c:
        bad = c.post(
            "/portfolio/holdings", json={"symbol": "X", "asset_class": "etf", "quantity": 0}
        )
        assert bad.status_code == 422
        made = c.post(
            "/portfolio/holdings",
            json={
                "symbol": "vti",
                "asset_class": "etf",
                "quantity": 5,
                "manual_price": 300,
                "manual_price_as_of": "2026-10-01",
            },
        )
        assert made.status_code == 201
        manual = c.get("/portfolio").json()
        assert (manual["book"], manual["paper"]["status"], manual["total_value"]) == (
            "manual",
            "not_requested",
            1500.0,
        )
        assert manual["risk"] is None
        assert "No priced stock" in manual["risk_note"] or "history" in manual["risk_note"]
        combined = c.get("/portfolio", params={"book": "combined"}).json()
        assert combined["paper"]["status"] == "included"
        assert [p["book"] for p in combined["positions"]] == ["manual", "paper"]
        assert combined["total_value"] == 3800.0
        assert c.get("/portfolio", params={"window": 5}).status_code == 422
        export = c.get("/portfolio/export")
        assert export.text.splitlines()[1] == "VTI,etf,5,,,,,300,2026-10-01"
        rejected = c.post("/portfolio/import", json={"content": "nope", "replace": True})
        assert rejected.status_code == 422
        hid = made.json()["id"]
        assert c.delete(f"/portfolio/holdings/{hid}").status_code == 204
        analyst = next(a for a in c.get("/game/state").json()["agents"] if a["id"] == "analyst")
        assert analyst["station"] == "portfolio"


def test_paper_book_without_keys_says_no_data(settings: Settings) -> None:
    app = create_app(
        settings,
        quote_source=FakeSource(configured=False),
        calendar=MarketCalendar(),
        clock=lambda: NOW,
    )
    with TestClient(app) as c:
        paper = c.get("/portfolio", params={"book": "paper"}).json()
        assert paper["paper"] == {
            "status": "not_configured",
            "detail": "No data: Alpaca paper API keys are not set in .env.",
        }
        assert paper["positions"] == []
