"""Phase 7: daily bar sync, Active Best re-validation, options paper spreads, multi-leg orders.

Every price here is a made-up test value; fakes stand in for Alpaca (no network).
"""

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alpaca.trading.enums import OrderClass, PositionIntent
from alpaca.trading.requests import LimitOrderRequest

from ptl.agents import learning
from ptl.config import Settings
from ptl.data import daily_sync
from ptl.data.csv_ingest import import_csv
from ptl.data.daily_sync import DailyBar
from ptl.data.models import DatasetKind
from ptl.data.option_chain import ChainQuote, occ_symbol, parse_occ
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.paper import store
from ptl.paper.broker import AlpacaPaperBroker, SpreadLeg, SpreadTicket
from ptl.paper.options_cycle import OptionsCycleRequest, reconcile_spreads, run_options_cycle
from ptl.paper.runner import CycleRefusedError
from ptl.runner.daemon import RunnerState, maybe_sync
from ptl.safety import ALPACA_PAPER_BASE_URL
from tests.conftest import SettingsFactory
from tests.test_paper_runner import SESSIONS, FakeBroker, FakeClient

CAL = MarketCalendar()
OPEN_NOW = datetime(2025, 1, 2, 15, 0, tzinfo=UTC)  # Thu 10:00 New York, market open
EXPIRY = date(2025, 2, 21)


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings()


@pytest.fixture
def conn(settings: Settings, tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    migrate(connection)
    lines = ["symbol,date,open,high,low,close,volume,adj_close"]
    lines += [f"SPY,{d},100,101,99,100,1000,100" for d in SESSIONS]  # flat 100, through 2024-12-31
    (tmp_path / "spy.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    import_csv(
        connection, tmp_path / "spy.csv", DatasetKind.EQUITY_BARS, "synthetic", CAL, OPEN_NOW
    )
    yield connection
    connection.close()


# ---- daily bar sync ---------------------------------------------------------------------


class FakeBars:
    """Test-only market data: close = 100 + day index; adjusted = close x factor."""

    label = "Fake market data"

    def __init__(self, factor: float = 1.0) -> None:
        self.factor = factor
        self.calls: list[tuple[str, date, date, bool]] = []

    def daily_bars(self, symbol: str, start: date, end: date, *, adjusted: bool) -> list[DailyBar]:
        self.calls.append((symbol, start, end, adjusted))
        out = []
        for i, d in enumerate(CAL.sessions(date(2024, 1, 2), end)):
            if d < start:
                continue
            close = 100.0 + i
            px = close * self.factor if adjusted else close
            out.append(DailyBar(d, px, px + 1, px - 1, px, 1000))
        return out


def sync_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"sync_backfill_start": date(2024, 12, 2), "sync_delay_minutes": 20}
    )


def test_target_session_waits_for_the_close_plus_a_delay() -> None:
    # Thu 2025-01-02 21:10 UTC = 16:10 New York: today's bar isn't settled yet (20 min delay).
    assert daily_sync.target_session(datetime(2025, 1, 2, 21, 10, tzinfo=UTC), CAL, 20) == date(
        2024, 12, 31
    )
    assert daily_sync.target_session(datetime(2025, 1, 2, 21, 25, tzinfo=UTC), CAL, 20) == date(
        2025, 1, 2
    )


def test_sync_backfills_then_appends_and_refreshes_after_a_corporate_action(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    s = sync_settings(settings)
    bars = FakeBars()
    first = daily_sync.sync_symbol(conn, "QQQ", source=bars, settings=s, calendar=CAL, now=OPEN_NOW)
    assert (first.created, first.note, first.through) == (True, "backfilled", date(2024, 12, 31))
    assert first.added == len(CAL.sessions(date(2024, 12, 2), date(2024, 12, 31)))
    assert daily_sync.sync_dataset_id(conn, "QQQ") == first.dataset_id
    again = daily_sync.sync_symbol(conn, "QQQ", source=bars, settings=s, calendar=CAL, now=OPEN_NOW)
    assert (again.added, again.note) == (0, "already current")

    # Next evening, after a dividend: adjusted closes shift, so the history is refreshed.
    later = datetime(2025, 1, 3, 22, 0, tzinfo=UTC)
    shifted = FakeBars(factor=0.99)
    nxt = daily_sync.sync_symbol(conn, "QQQ", source=shifted, settings=s, calendar=CAL, now=later)
    assert (nxt.added, nxt.through, nxt.adjusted_refresh) == (2, date(2025, 1, 3), True)
    assert first.dataset_id is not None
    rows = conn.execute(
        "SELECT close, adj_close FROM equity_bars WHERE dataset_id = ? ORDER BY session_date",
        (first.dataset_id,),
    ).fetchall()
    assert all(r["adj_close"] == pytest.approx(r["close"] * 0.99) for r in rows)
    ds = conn.execute("SELECT * FROM datasets WHERE id = ?", (first.dataset_id,)).fetchone()
    assert (ds["coverage_end"], ds["data_type"]) == ("2025-01-03", "end-of-day")
    assert ds["source"] == "Fake market data; daily sync"
    assert daily_sync.freshest_dataset(conn, "SPY") == 1  # only the imported CSV has SPY
    assert daily_sync.freshest_dataset(conn, "QQQ") == first.dataset_id


def make_best(
    conn: sqlite3.Connection,
    *,
    asset: str = "equity",
    strategy: str = "buy_and_hold",
    params: dict[str, int] | None = None,
    validated: bool = True,
) -> learning.ActiveBest:
    trial = learning.log_trial(
        conn,
        now=OPEN_NOW,
        search_id="s-test",
        generation=1,
        asset=asset,  # type: ignore[arg-type]
        strategy=strategy,
        symbol="SPY",
        dataset_id=1,
        options_dataset_id=None,
        params=params or {},
        period="in-sample",
        run_id=None,
        reused=False,
        summary={"sharpe": 1.0, "trades": 12},
        fitness=1.0,
        verdict="evaluated",
        note="test",
    )
    return learning.record_active_best(
        conn,
        now=OPEN_NOW,
        search_id="s-test",
        trial=trial,
        oos=None,
        validated=validated,
        reason="test",
        curve={},
    )


def test_sync_rechecks_the_active_best_instead_of_resetting_budgets(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    s = sync_settings(settings).model_copy(update={"sync_backfill_start": date(2024, 1, 2)})
    best = make_best(conn)
    state = RunnerState()
    now = datetime(2025, 1, 3, 22, 0, tzinfo=UTC)
    results = maybe_sync(conn, state, settings=s, bars=FakeBars(), calendar=CAL, clock=lambda: now)
    assert {r.symbol for r in results} == {"SPY"}
    assert state.synced_through == date(2025, 1, 3)
    newest = learning.active_best(conn, "equity")
    assert newest is not None
    assert newest.id != best.id
    assert newest.dataset_id == daily_sync.sync_dataset_id(conn, "SPY")
    assert newest.in_sample_log_id == best.in_sample_log_id  # same in-sample evidence
    assert newest.reason.startswith("Re-validated after new bars")
    kinds = {line.kind for line in learning.recent_log(conn)}
    assert {"data_sync", "revalidation"} <= kinds
    # Already current: no fetch until the next session's bar is due.
    calls = FakeBars()
    assert maybe_sync(conn, state, settings=s, bars=calls, calendar=CAL, clock=lambda: now) == []
    assert calls.calls == []


# ---- options paper spreads ---------------------------------------------------------------


def q(strike: float, bid: float, ask: float) -> ChainQuote:
    symbol = occ_symbol("SPY", EXPIRY, "put", strike)
    return ChainQuote(symbol, parse_occ(symbol, "SPY"), bid, ask, OPEN_NOW)


class FakeChain:
    label = "Fake options data (delayed)"

    def __init__(
        self, chain: Sequence[ChainQuote], live: dict[str, ChainQuote] | None = None
    ) -> None:
        self.chain = list(chain)
        self.live = live or {c.symbol: c for c in chain}

    def put_chain(self, underlying: str, exp_from: date, exp_to: date) -> list[ChainQuote]:
        return [c for c in self.chain if exp_from <= c.contract.expiration <= exp_to]

    def quotes(self, underlying: str, symbols: Sequence[str]) -> dict[str, ChainQuote]:
        return {s: self.live[s] for s in symbols if s in self.live}


CHAIN = [q(100, 3.0, 3.2), q(95, 1.20, 1.30), q(90, 0.40, 0.50), q(85, 0.10, 0.15)]
PARAMS = {"dte": 30, "otm_percent": 5, "width": 5, "contracts": 2, "take_profit": 50, "max_open": 1}


def options_cycle(  # noqa: PLR0913 - test helper with defaults
    conn: sqlite3.Connection,
    settings: Settings,
    broker: FakeBroker,
    chain: FakeChain | None,
    now: datetime = OPEN_NOW,
    *,
    allow_open: bool = True,
) -> list[str]:
    results = run_options_cycle(
        conn,
        OptionsCycleRequest("put_credit_spread", PARAMS, 1, "SPY"),
        settings=settings,
        broker=broker,
        chain=chain,
        calendar=CAL,
        now=now,
        allow_open=allow_open,
    )
    return [r.outcome for r in results]


def test_opens_a_put_credit_spread_sized_by_collateral(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    broker = FakeBroker()
    [outcome] = options_cycle(conn, settings, broker, FakeChain(CHAIN))
    # Spot 100, 5% OTM -> short 95P; long = 95 - 5 = 90P. Credit = bid 1.20 - ask 0.50 = 0.70.
    # Collateral per spread = 5 x 100 - 0.70 x 100 = 430 (+ 1.30 commissions = 431.30).
    # Caps: per-trade 1,000 (tightest), position 2,500, headroom 5,000 -> floor(1000/431.3) = 2.
    assert broker.spreads == [
        SpreadTicket(
            legs=(SpreadLeg("SPY250221P00095000", "sell"), SpreadLeg("SPY250221P00090000", "buy")),
            contracts=2,
            net_limit=pytest.approx(-0.70),  # type: ignore[arg-type]
            opening=True,
        )
    ]
    assert outcome.startswith(
        "Submitted to Alpaca paper: sell 2 x SPY 2025-02-21 95/90P for a credit of 0.70"
    )
    assert "(collateral $860.00)" in outcome
    [spread] = store.spreads(conn)
    assert (spread.contracts, spread.status) == (2, "open")
    assert (spread.credit, spread.collateral) == pytest.approx((0.7, 860.0))
    [cycle] = store.recent_cycles(conn, 1)
    assert (
        "sell SPY250221P00095000 at bid 1.20 - buy SPY250221P00090000 at ask 0.50"
        in cycle.price_source
    )
    assert "sized by max loss per trade ($1,000.00) = 2" in cycle.explanation
    decision = risk_decisions(conn)[0]
    assert decision["approved"] == 1
    assert "worst case $862.60 vs limit $1,000.00" in decision["checks"]


def risk_decisions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM risk_decisions ORDER BY id DESC").fetchall()


def test_take_profit_closes_with_one_debit_order_and_reconciles(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    broker = FakeBroker()
    options_cycle(conn, settings, broker, FakeChain(CHAIN))
    cheap = {
        "SPY250221P00095000": q(95, 0.25, 0.30),
        "SPY250221P00090000": q(90, 0.05, 0.08),
    }
    later = OPEN_NOW + timedelta(hours=2)
    [outcome] = options_cycle(
        conn, settings, broker, FakeChain(CHAIN, cheap), later, allow_open=False
    )
    # Captured = credit 140 - cost to close (0.30 - 0.05) x 200 = 90 >= 50% of 140.
    assert outcome.startswith(
        "Close sent to Alpaca paper: close 2 x SPY 2025-02-21 95/90P for a debit of 0.25"
    )
    close = broker.spreads[-1]
    assert close.opening is False
    assert close.net_limit == pytest.approx(0.25)
    assert close.legs == (
        SpreadLeg("SPY250221P00095000", "buy"),
        SpreadLeg("SPY250221P00090000", "sell"),
    )
    [spread] = store.spreads(conn, statuses=("closing",))
    assert spread.close_order_id is not None
    store.record_event(
        conn,
        order_id=spread.close_order_id,
        now=later,
        status="filled",
        filled_qty=2,
        fill_price=0.26,
        detail="test fill",
    )
    assert reconcile_spreads(conn, later) == ["SPY 2025-02-21 95/90P: closed."]
    [closed] = store.spreads(conn, statuses=("closed",))
    assert closed.close_debit == pytest.approx(0.26)


def test_spreads_close_before_expiry_and_dry_run_sends_nothing(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    from ptl.paper.broker import DryRunBroker  # noqa: PLC0415

    dry = DryRunBroker(None, 10_000)
    options_cycle(conn, settings, dry, FakeChain(CHAIN))  # type: ignore[arg-type]
    [spread] = store.spreads(conn)
    assert spread.mode == "dry_run"
    eve = datetime(2025, 2, 20, 15, 0, tzinfo=UTC)  # one day before expiry, market open
    [outcome] = options_cycle(conn, settings, dry, FakeChain(CHAIN), eve, allow_open=False)  # type: ignore[arg-type]
    assert outcome.startswith("Would close (dry run)")
    [closed] = store.spreads(conn, statuses=("closed",))
    assert "assumed closed at the quoted debit" in closed.note


def test_no_order_when_one_spread_exceeds_the_limits(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    tight = settings.model_copy(update={"risk_max_loss_per_trade": 300.0})
    broker = FakeBroker()
    [outcome] = options_cycle(conn, tight, broker, FakeChain(CHAIN))
    assert outcome == (
        "No order: one SPY 2025-02-21 95/90P needs $431.30, more than the max loss per trade "
        "allows."
    )
    assert broker.spreads == []


def test_options_refusals(conn: sqlite3.Connection, settings: Settings) -> None:
    with pytest.raises(CycleRefusedError, match="needs live option quotes"):
        options_cycle(conn, settings, FakeBroker(), None)
    closed = datetime(2025, 1, 4, 15, 0, tzinfo=UTC)  # Saturday
    with pytest.raises(CycleRefusedError, match="only placed while the market is open"):
        options_cycle(conn, settings, FakeBroker(), FakeChain(CHAIN), closed)


def test_occ_round_trip() -> None:
    assert occ_symbol("SPY", EXPIRY, "put", 95) == "SPY250221P00095000"
    c = parse_occ("SPY250221P00095500", "SPY")
    assert (c.expiration, c.strike, c.option_type) == (EXPIRY, 95.5, "put")


def test_alpaca_mleg_request_and_guards(settings: Settings) -> None:
    client = FakeClient(ALPACA_PAPER_BASE_URL)
    broker = AlpacaPaperBroker(settings, client)
    broker.submit_spread(
        SpreadTicket(
            legs=(SpreadLeg("SPY250221P00095000", "sell"), SpreadLeg("SPY250221P00090000", "buy")),
            contracts=2,
            net_limit=-0.704,
            opening=True,
        )
    )
    [request] = client.orders
    assert isinstance(request, LimitOrderRequest)
    assert (request.order_class, request.qty, request.limit_price) == (OrderClass.MLEG, 2, -0.70)
    assert request.legs is not None
    assert [(leg.symbol, leg.position_intent) for leg in request.legs] == [
        ("SPY250221P00095000", PositionIntent.SELL_TO_OPEN),
        ("SPY250221P00090000", PositionIntent.BUY_TO_OPEN),
    ]
    with pytest.raises(ValueError, match="no naked shorts"):
        broker.submit_spread(
            SpreadTicket(
                legs=(SpreadLeg("A", "sell"), SpreadLeg("B", "sell")),
                contracts=1,
                net_limit=-1,
                opening=True,
            )
        )
