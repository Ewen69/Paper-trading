"""Paper runner: signal -> size -> risk -> broker -> log. Fakes here are test-only."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from uuid import UUID

import pytest
from alpaca.trading.enums import AccountStatus, OrderSide, OrderStatus
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import MarketOrderRequest, OrderRequest

from ptl.config import Settings
from ptl.data.csv_ingest import import_csv
from ptl.data.live import NoQuoteError, QuoteSource
from ptl.data.models import DatasetKind, LiveQuote
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.paper import store
from ptl.paper.broker import (
    AlpacaPaperBroker,
    Broker,
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    DryRunBroker,
    Mode,
    OrderTicket,
)
from ptl.paper.runner import (
    CycleRefusedError,
    CycleRequest,
    CycleResult,
    run_cycle,
    sync_orders,
)
from ptl.risk import store as risk_store
from ptl.safety import ALPACA_PAPER_BASE_URL, LiveTradingRefusedError
from tests.conftest import SettingsFactory
from tests.test_data_api import FakeSource

SESSIONS = MarketCalendar().sessions(date(2024, 1, 2), date(2024, 12, 31))
LAST = SESSIONS[-1]  # 2024-12-31
NOW = datetime(LAST.year, LAST.month, LAST.day, 23, 0, tzinfo=UTC)  # after the close


class FakeBroker:
    """Test-only broker: fixed account/positions; records what it was asked to submit."""

    def __init__(
        self,
        mode: Mode = "paper",
        equity: float = 10_000,
        last_equity: float = 10_000,
        positions: dict[str, BrokerPosition] | None = None,
    ) -> None:
        self._mode = mode
        self._account = BrokerAccount(equity, last_equity, equity, "test broker")
        self._positions = positions or {}
        self.submitted: list[OrderTicket] = []
        self.status = "accepted"

    @property
    def mode(self) -> Mode:
        return self._mode

    def account(self) -> BrokerAccount:
        return self._account

    def positions(self) -> dict[str, BrokerPosition]:
        return self._positions

    def submit(self, ticket: OrderTicket) -> BrokerOrder:
        self.submitted.append(ticket)
        return BrokerOrder(f"b-{len(self.submitted)}", "accepted", None, None)

    def order_status(self, order_id: str) -> BrokerOrder:
        filled = self.status == "filled"
        return BrokerOrder(
            order_id, self.status, 16.0 if filled else None, 600.1 if filled else None
        )


class Quotes(FakeSource):
    def __init__(self, ask: float | None) -> None:
        super().__init__()
        self.ask = ask

    def latest_stock_quote(self, symbol: str) -> LiveQuote:
        if self.ask is None:
            raise NoQuoteError("no quote")
        return LiveQuote(symbol, self.ask - 0.02, self.ask, 1, 1, NOW - timedelta(hours=3))


def generous(settings: Settings, per_trade: float = 20_000.0) -> Settings:
    return settings.model_copy(
        update={"risk_max_loss_per_trade": per_trade, "risk_max_capital_at_risk_pct": 1.0}
    )


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(risk_max_loss_per_trade=5_000.0)


@pytest.fixture
def conn(
    settings: Settings, tmp_path: Path, calendar: MarketCalendar
) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    migrate(connection)
    lines = ["symbol,date,open,high,low,close,volume,adj_close"]
    lines += [f"SPY,{d},100,101,99,100,1000,100" for d in SESSIONS]
    (tmp_path / "spy.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    import_csv(
        connection, tmp_path / "spy.csv", DatasetKind.EQUITY_BARS, "synthetic", calendar, NOW
    )
    yield connection
    connection.close()


def cycle(  # noqa: PLR0913, PLR0917 - test helper with defaults
    conn: sqlite3.Connection,
    settings: Settings,
    broker: Broker,
    quotes: QuoteSource | None = None,
    strategy: str = "buy_and_hold",
    now: datetime = NOW,
) -> CycleResult:
    return run_cycle(
        conn,
        CycleRequest(strategy, {}, 1, "SPY"),
        settings=settings,
        broker=broker,
        quotes=quotes,
        calendar=MarketCalendar(),
        now=now,
    )


def test_buy_is_sized_risk_checked_submitted_and_logged(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    broker = FakeBroker()
    # Target 100% of $10,000 at live ask 600.00 -> floor(16.67) = 16 shares; worst case 9,600.
    result = cycle(conn, settings, broker, Quotes(ask=600.0))
    assert result.decision is not None
    assert not result.decision.approved  # 9,600 > per-trade limit 5,000
    assert "worst case $9,600.00 vs limit $5,000.00" in result.outcome
    assert broker.submitted == []
    # Raise the limits so the same order passes: 9,600 <= 20,000 and <= 100% of equity.
    approved = cycle(conn, generous(settings), broker, Quotes(ask=600.0))
    assert approved.decision is not None
    assert approved.decision.approved
    assert broker.submitted == [OrderTicket("SPY", "buy", 16.0)]
    [order] = store.recent_orders(conn, 1)
    assert (order.side, order.qty, order.status) == ("buy", 16.0, "accepted")
    assert order.broker_order_id == "b-1"
    assert "Buy and hold: always 100% invested." in order.reason
    assert "Target 100% of equity $10,000.00 (test broker) at 600.00 = 16 shares" in order.reason
    [latest, rejected] = store.recent_cycles(conn, 2)
    assert latest.price_source.startswith("live ask 600.00")
    assert "blocked by the risk engine" in rejected.outcome
    assert len(risk_store.recent_decisions(conn)) == 2


def test_dry_run_never_sends_and_says_so(conn: sqlite3.Connection, settings: Settings) -> None:
    broker = DryRunBroker(reader=None, starting_capital=100_000)
    result = cycle(
        conn, generous(settings, 200_000.0), broker, quotes=None
    )  # no keys: falls back to last close
    assert result.mode == "dry_run"
    assert result.outcome.startswith("Would submit (dry run): buy 1000 SPY")
    [c] = store.recent_cycles(conn, 1)
    assert c.price_source == f"last close {LAST} (no live quote; dry run)"
    [order] = store.recent_orders(conn, 1)
    assert (order.mode, order.status) == ("dry_run", "not_sent")


def test_paper_mode_requires_a_live_quote(conn: sqlite3.Connection, settings: Settings) -> None:
    with pytest.raises(CycleRefusedError, match="No live quote"):
        cycle(conn, settings, FakeBroker(), Quotes(ask=None))


def test_stale_history_is_refused(conn: sqlite3.Connection, settings: Settings) -> None:
    later = NOW + timedelta(days=7)
    with pytest.raises(CycleRefusedError, match="Import fresh bars first"):
        cycle(conn, settings, FakeBroker(), Quotes(600.0), now=later)


def test_options_and_unknown_strategies_are_refused(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    with pytest.raises(CycleRefusedError, match="Options paper trading isn't built yet"):
        cycle(conn, settings, FakeBroker(), strategy="put_credit_spread")
    with pytest.raises(CycleRefusedError, match="Unknown strategy"):
        cycle(conn, settings, FakeBroker(), strategy="nope")


def test_no_order_when_already_at_target(conn: sqlite3.Connection, settings: Settings) -> None:
    broker = FakeBroker(positions={"SPY": BrokerPosition("SPY", 16, 9_600)})
    result = cycle(conn, settings, broker, Quotes(600.0))
    assert result.outcome == "No order: already holding the target 16 shares."
    assert broker.submitted == []


def test_kill_switch_blocks_buys_but_allows_sells(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    risk_store.set_kill_switch(conn, True, "manual test", NOW)
    blocked = cycle(conn, settings, FakeBroker(), Quotes(100.0))
    assert "kill switch is engaged" in blocked.outcome
    # sma_crossover wants cash on flat prices (fast == slow), so it sells the 5 shares held.
    seller = FakeBroker(positions={"SPY": BrokerPosition("SPY", 5, 500)})
    sold = cycle(conn, settings, seller, Quotes(100.0), strategy="sma_crossover")
    assert seller.submitted == [OrderTicket("SPY", "sell", 5.0)]
    assert sold.decision is not None
    assert sold.decision.approved


def test_daily_loss_breach_trips_the_kill_switch(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    broker = FakeBroker(equity=7_000, last_equity=10_000)  # -3,000 today vs limit 2,000
    result = cycle(conn, settings, broker, Quotes(100.0))
    assert result.decision is not None
    assert result.decision.trip_kill_switch
    switch = risk_store.kill_switch(conn)
    assert switch.engaged
    assert "breached the daily loss limit $2,000.00" in switch.reason


def test_sync_records_fills_and_logs_are_append_only(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    broker = FakeBroker()
    cycle(conn, generous(settings), broker, Quotes(600.0))
    assert store.paper_evidence(conn).filled_trades == 0  # submitted is not filled
    broker.status = "filled"
    assert sync_orders(conn, broker, NOW) == 1
    assert sync_orders(conn, broker, NOW) == 0  # final: not polled again
    [order] = store.recent_orders(conn, 1)
    assert store.latest_status(conn, order.id) == "filled"
    evidence = store.paper_evidence(conn)
    assert (evidence.filled_trades, evidence.decisions, evidence.kill_switch_trips) == (1, 1, 0)
    for table in ("paper_cycles", "paper_orders", "paper_order_events", "risk_decisions"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names


# ---- Alpaca adapter (fake client, no network) ----


class FakeClient:
    def __init__(self, base_url: object) -> None:
        self._base_url = base_url
        self.orders: list[OrderRequest] = []

    def get_account(self) -> TradeAccount:
        return TradeAccount(
            id=UUID(int=1),
            account_number="PA1",
            status=AccountStatus.ACTIVE,
            equity="10500.5",
            last_equity="10000",
            cash="500.5",
        )

    def get_all_positions(self) -> list[Position]:
        position = {
            "asset_id": str(UUID(int=2)),
            "symbol": "SPY",
            "exchange": "ARCA",
            "asset_class": "us_equity",
            "avg_entry_price": "600",
            "qty": "16",
            "side": "long",
            "market_value": "9700",
            "cost_basis": "9600",
        }
        return [Position.model_validate(position)]

    def submit_order(self, order_data: OrderRequest) -> Order:
        self.orders.append(order_data)
        return _order("accepted")

    def get_order_by_id(self, order_id: object, filter: object = None) -> Order:
        return _order("filled")


def _order(status: str) -> Order:
    filled = status == "filled"
    return Order.model_validate(
        {
            "id": str(UUID(int=3)),
            "client_order_id": "c",
            "created_at": NOW,
            "updated_at": NOW,
            "submitted_at": NOW,
            "symbol": "SPY",
            "asset_class": "us_equity",
            "qty": "16",
            "filled_qty": "16" if filled else "0",
            "filled_avg_price": "600.10" if filled else None,
            "order_class": "simple",
            "order_type": "market",
            "type": "market",
            "side": "buy",
            "time_in_force": "day",
            "status": status,
            "extended_hours": False,
        }
    )


def test_alpaca_paper_broker_maps_and_guards(settings: Settings) -> None:
    client = FakeClient(ALPACA_PAPER_BASE_URL)
    broker = AlpacaPaperBroker(settings, client)
    assert broker.account() == BrokerAccount(10_500.5, 10_000, 500.5, "Alpaca paper account")
    assert broker.positions()["SPY"] == BrokerPosition("SPY", 16, 9_700)
    sent = broker.submit(OrderTicket("SPY", "buy", 16))
    assert sent.status == "accepted"
    [request] = client.orders
    assert isinstance(request, MarketOrderRequest)
    assert (request.symbol, request.qty, request.side) == ("SPY", 16, OrderSide.BUY)
    assert broker.order_status(sent.id).status == OrderStatus.FILLED.value


class _LiveUrl(Enum):
    LIVE = "https://api.alpaca" + ".markets"  # split so the static guard test stays meaningful


def test_alpaca_broker_refuses_a_live_client(settings: Settings) -> None:
    with pytest.raises(LiveTradingRefusedError):
        AlpacaPaperBroker(settings, FakeClient(_LiveUrl.LIVE))
    client = FakeClient(ALPACA_PAPER_BASE_URL)
    broker = AlpacaPaperBroker(settings, client)
    client._base_url = _LiveUrl.LIVE  # swapped after construction: still caught before submit
    with pytest.raises(LiveTradingRefusedError):
        broker.submit(OrderTicket("SPY", "buy", 1))
    assert client.orders == []
