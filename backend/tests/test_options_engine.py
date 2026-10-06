"""End-to-end engine scenarios with hand-computed cash. Synthetic test fixtures only.

Common setup: 2x SPY Jan-17-2025 580/575 put credit spread, decided on Jan 2, filled Jan 3 at
Jan 3 quotes: credit 196.00, commissions 2.60, so cash goes 10,000.00 -> 10,193.40.
Sessions: Jan 2, 3, 6, 7, 8, 10 (Jan 9 closed), 13-17, 21 (Jan 20 MLK holiday).
"""

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime

import pytest

from ptl.backtest.models import PriceBar
from ptl.data.csv_ingest import import_csv
from ptl.data.models import DatasetKind
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.options.chain import load_chain
from ptl.options.engine import (
    CloseIntent,
    Intent,
    OpenIntent,
    OptionsResult,
    OptionsView,
    run_options_backtest,
)
from ptl.options.models import Contract, ContractKey, Leg, OptionCosts, Quote
from tests.conftest import FIXTURES

EXP = date(2025, 1, 17)
COSTS = OptionCosts(slippage_per_share=0.02, commission_per_contract=0.65)
SPREAD = (
    Leg(Contract("SPY", "", EXP, 580.0, "put", "american"), -1),
    Leg(Contract("SPY", "", EXP, 575.0, "put", "american"), 1),
)
SESSIONS = MarketCalendar().sessions(date(2025, 1, 2), date(2025, 1, 21))


def bars(
    closes: Mapping[date, float] | None = None,
    opens: Mapping[date, float] | None = None,
    base: float = 585.0,
) -> list[PriceBar]:
    out = []
    for day in SESSIONS:
        close = (closes or {}).get(day, base)
        open_ = (opens or {}).get(day, close)
        out.append(PriceBar(day, open_, max(open_, close) + 1, min(open_, close) - 1, close))
    return out


class Scripted:
    """Opens the spread at the first decision; optionally closes it at a given decision day."""

    warmup = 0

    def __init__(
        self, legs: tuple[Leg, ...] = SPREAD, contracts: int = 2, close_on: date | None = None
    ) -> None:
        self.legs = legs
        self.contracts = contracts
        self.close_on = close_on
        self.seen: list[tuple[date, int, set[date]]] = []

    def decide(self, view: OptionsView) -> Sequence[Intent]:
        self.seen.append((view.day, len(view.history), {q.day for q in view.chain.values()}))
        if len(self.seen) == 1:
            return [OpenIntent(self.legs, self.contracts, "Scripted open.")]
        if self.close_on == view.day and view.positions:
            return [CloseIntent(view.positions[0].id, "Scripted close.")]
        return []


@pytest.fixture(scope="module")
def chain_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[sqlite3.Connection, int]]:
    conn = connect(tmp_path_factory.mktemp("eng") / "eng.sqlite3")
    migrate(conn)
    result = import_csv(
        conn,
        FIXTURES / "options" / "synthetic_spy_put_spread.csv",
        DatasetKind.OPTION_QUOTES,
        "synthetic",
        MarketCalendar(),
        datetime(2026, 10, 5, tzinfo=UTC),
    )
    yield conn, result.dataset_id
    conn.close()


def run(
    chain_db: tuple[sqlite3.Connection, int],
    price_bars: list[PriceBar],
    strategy: Scripted | None = None,
    capital: float = 10_000.0,
    underlying: str = "SPY",
) -> OptionsResult:
    conn, dataset_id = chain_db

    def chain_for(day: date) -> Mapping[ContractKey, Quote]:
        return load_chain(conn, dataset_id, underlying, day)

    return run_options_backtest(
        price_bars, 0, strategy or Scripted(), chain_for, costs=COSTS, initial_capital=capital
    )


def kinds(result: OptionsResult) -> list[str]:
    return [e.kind for e in result.events]


def test_marks_use_bid_for_longs_and_ask_for_shorts(
    chain_db: tuple[sqlite3.Connection, int],
) -> None:
    result = run(chain_db, bars({EXP: 590.0}))
    # Jan 3: cash 10,193.40; long 575P at bid 2.00 (+400), short 580P at ask 3.20 (-640).
    assert result.equity[1].equity == pytest.approx(10_193.40 - 240.00)
    assert result.equity[0].equity == 10_000.0  # Jan 2: decided, not yet filled


def test_out_of_the_money_expiry_keeps_the_credit(chain_db: tuple[sqlite3.Connection, int]) -> None:
    result = run(chain_db, bars({EXP: 590.0}))
    [trade] = result.trades
    assert trade.pnl == pytest.approx(193.40)  # credit 196.00 - commissions 2.60
    assert trade.exit_kind == "expired worthless"
    assert trade.closed == EXP
    assert result.equity[-1].equity == pytest.approx(10_193.40)
    assert kinds(result).count("expired_worthless") == 2
    assert result.peak_reserve == pytest.approx(1000.0)


def test_in_the_money_expiry_nets_to_max_loss(chain_db: tuple[sqlite3.Connection, int]) -> None:
    result = run(chain_db, bars({EXP: 570.0}))
    [trade] = result.trades
    # Settlement nets -1,000 with zero shares: 193.40 - 1,000 = -806.60 = -(804 + 2.60)
    assert trade.pnl == pytest.approx(-806.60)
    assert trade.exit_kind == "settled in the money at expiration"
    assert result.equity[-1].equity == pytest.approx(9_193.40)
    assert result.margin_shortfalls == 0
    assert "liquidate" not in kinds(result)


def test_pin_risk_assigns_shares_and_liquidates_next_open(
    chain_db: tuple[sqlite3.Connection, int],
) -> None:
    jan21 = date(2025, 1, 21)
    result = run(chain_db, bars({EXP: 577.0}, opens={jan21: 574.0}))
    # Jan 17 close 577: short 580P assigned, buy 200 @ 580 = -116,000; long 575P worthless.
    jan17 = next(p for p in result.equity if p.day == EXP)
    assert jan17.equity == pytest.approx(10_193.40 - 116_000 + 200 * 577)  # 9,593.40
    # Jan 21 (after the MLK holiday) open 574, sold at 574 x (1 - 5 bps) = 573.713.
    [trade] = result.trades
    assert trade.pnl == pytest.approx(193.40 - 116_000 + 200 * 573.713)  # -1,064.00
    assert trade.pnl < -806.60  # pin risk lost more than the spread's defined max loss
    assert trade.closed == jan21
    assert result.pin_events == 1
    assert result.margin_shortfalls == 1  # cash went negative until the shares were sold
    assert result.equity[-1].equity == pytest.approx(8_936.00)
    pin = next(e for e in result.events if e.kind == "pin")
    assert pin.text.startswith("Pin risk: 577 finished between the strikes")


def test_early_assignment_closes_the_spread(chain_db: tuple[sqlite3.Connection, int]) -> None:
    conn, dataset_id = chain_db
    jan7 = date(2025, 1, 7)
    short_580, long_575 = (leg.contract for leg in SPREAD)
    deep = {
        short_580.key: Quote(short_580, jan7, 19.90, 20.03),
        long_575.key: Quote(long_575, jan7, 14.95, 15.10),
    }

    def chain_for(day: date) -> Mapping[ContractKey, Quote]:
        return deep if day == jan7 else load_chain(conn, dataset_id, "SPY", day)

    result = run_options_backtest(
        bars({jan7: 560.0}), 0, Scripted(), chain_for, costs=COSTS, initial_capital=10_000.0
    )
    [trade] = result.trades
    assert trade.exit_kind == "early assignment"
    assert trade.closed == jan7
    assert trade.pnl == pytest.approx(-806.60)  # assigned 580P, exercised 575P: net -1,000
    assert result.early_assignments == 1
    note = next(e for e in result.events if e.kind == "early_assignment")
    assert "time value = ask 20.03 - intrinsic 20.00 = 0.03 <= 0.05" in note.text


def test_collateral_check_rejects_the_open(chain_db: tuple[sqlite3.Connection, int]) -> None:
    result = run(chain_db, bars({EXP: 590.0}), capital=500.0)
    assert result.trades == []
    assert result.rejected_orders == 1
    reject = next(e for e in result.events if e.kind == "reject")
    assert "needs 806.60 (collateral 804.00 + debit 0.00 + commissions 2.60)" in reject.text
    assert "only 500.00 is available" in reject.text
    assert result.equity[-1].equity == 500.0


def test_close_by_signal_fills_next_session(chain_db: tuple[sqlite3.Connection, int]) -> None:
    # Decide to close on Jan 8; fill at Jan 10 quotes: buy 580P 2.10 + 0.02, sell 575P
    # 1.05 - 0.02 -> (2.12 - 1.03) x 200 = 218.00 + 2.60 commissions.
    result = run(chain_db, bars({EXP: 590.0}), Scripted(close_on=date(2025, 1, 8)))
    [trade] = result.trades
    assert trade.closed == date(2025, 1, 10)
    assert trade.pnl == pytest.approx(193.40 - 218.00 - 2.60)
    assert trade.exit_kind == "closed"


def test_european_cash_settlement_and_stale_marks(chain_db: tuple[sqlite3.Connection, int]) -> None:
    spx = (
        Leg(Contract("SPX", "", EXP, 5800.0, "put", "european"), -1),
        Leg(Contract("SPX", "", EXP, 5750.0, "put", "european"), 1),
    )
    result = run(chain_db, bars({EXP: 5777.0}, base=5850.0), Scripted(spx, 1), underlying="SPX")
    [trade] = result.trades
    # Open: sell 5800P 31.00 - 0.02, buy 5750P 22.00 + 0.02 -> 8.96 x 100 = 896.00 - 1.30.
    # Expiry at 5777: short 5800P cash-settles -23 x 100 = -2,300; long 5750P worthless.
    assert trade.pnl == pytest.approx(896.00 - 1.30 - 2300.00)
    assert "liquidate" not in kinds(result)  # cash settlement: no shares
    assert result.stale_marks > 0  # the fixture only quotes SPX on Jan 3


def test_strategy_sees_only_today(chain_db: tuple[sqlite3.Connection, int]) -> None:
    strategy = Scripted()
    run(chain_db, bars({EXP: 590.0}), strategy)
    for i, (day, history_len, chain_days) in enumerate(strategy.seen):
        assert history_len == i + 1
        assert chain_days <= {day}  # never tomorrow's quotes
    assert strategy.seen[-1][0] == EXP  # no decision on the final session


def test_naked_short_is_rejected_by_the_engine(chain_db: tuple[sqlite3.Connection, int]) -> None:
    result = run(chain_db, bars({EXP: 590.0}), Scripted((SPREAD[0],), 1))
    assert result.trades == []
    assert result.rejected_orders == 1
    assert "naked" in next(e for e in result.events if e.kind == "reject").text
