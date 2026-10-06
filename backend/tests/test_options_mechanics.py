"""Hand-computed tests for option fills, collateral, expiration and assignment.

Quotes come from a synthetic CSV fixture loaded through the real importer. Every expected
number is worked out in the comments: per share x 100 (multiplier) x contracts.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ptl.data.csv_ingest import import_csv
from ptl.data.models import DatasetKind
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.options.chain import load_chain
from ptl.options.models import Contract, ContractKey, ExerciseStyle, Leg, OptionCosts, Quote
from ptl.options.pricing import NakedShortError, UnfillableError, price_close, price_open
from ptl.options.settlement import early_assignment, settle_at_expiration
from tests.conftest import FIXTURES

EXP = date(2025, 1, 17)
COSTS = OptionCosts(slippage_per_share=0.02, commission_per_contract=0.65)


def put(strike: float, underlying: str = "SPY") -> Contract:
    style: ExerciseStyle = "european" if underlying == "SPX" else "american"
    return Contract(underlying, "", EXP, strike, "put", style)


def call(strike: float) -> Contract:
    return Contract("SPY", "", EXP, strike, "call", "american")


SHORT_580 = Leg(put(580), -1)
LONG_575 = Leg(put(575), 1)
PUT_CREDIT_SPREAD = (SHORT_580, LONG_575)


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[ContractKey, Quote]]:
    db = tmp_path_factory.mktemp("opt") / "opt.sqlite3"
    conn: sqlite3.Connection = connect(db)
    migrate(conn)
    result = import_csv(
        conn,
        FIXTURES / "options" / "synthetic_spy_put_spread.csv",
        DatasetKind.OPTION_QUOTES,
        "synthetic",
        MarketCalendar(),
        datetime(2026, 10, 5, tzinfo=UTC),
    )
    day = date(2025, 1, 3)
    merged = load_chain(conn, result.dataset_id, "SPY", day)
    merged.update(load_chain(conn, result.dataset_id, "SPX", day))
    yield merged
    conn.close()


# ---- (a) entry pricing, slippage, commissions, collateral ------------------------------------


def test_credit_spread_entry_pricing(chain: dict[ContractKey, Quote]) -> None:
    # 2 contracts. Sell 580P at bid 3.10 - 0.02 = 3.08; buy 575P at ask 2.08 + 0.02 = 2.10.
    fill = price_open(PUT_CREDIT_SPREAD, 2, chain, COSTS)
    short, long_ = fill.legs
    assert (short.side, long_.side) == ("sell", "buy")
    assert short.price == pytest.approx(3.08)
    assert long_.price == pytest.approx(2.10)
    assert fill.structure == "credit_vertical"
    # Net credit (3.08 - 2.10) x 100 x 2 = 196.00
    assert fill.net_premium == pytest.approx(196.00)
    # Commission 0.65 x 2 contracts x 2 legs = 2.60
    assert fill.commissions == pytest.approx(2.60)
    # Collateral = width 5 x 100 x 2 - credit 196 = 804.00; the broker holds the full 1,000.
    assert fill.collateral_required == pytest.approx(804.00)
    assert fill.reserve == pytest.approx(1000.00)
    assert fill.max_loss == pytest.approx(804.00)
    # Cash this consumes from the available balance: 804.00 + 2.60
    assert fill.cash_needed == pytest.approx(806.60)
    # Slippage paid: 0.02 x 100 x 2 on each leg = 4.00 each
    assert [leg.slippage_cost for leg in fill.legs] == [pytest.approx(4.0)] * 2


def test_spread_fraction_slippage(chain: dict[ContractKey, Quote]) -> None:
    costs = replace(COSTS, slippage_spread_fraction=0.5)
    # 580P: spread 0.10 -> slippage 0.02 + 0.05 = 0.07 -> 3.10 - 0.07 = 3.03
    # 575P: spread 0.08 -> slippage 0.02 + 0.04 = 0.06 -> 2.08 + 0.06 = 2.14
    fill = price_open(PUT_CREDIT_SPREAD, 1, chain, costs)
    assert [leg.price for leg in fill.legs] == [pytest.approx(3.03), pytest.approx(2.14)]
    assert fill.net_premium == pytest.approx(89.00)


def test_never_fills_at_mid(chain: dict[ContractKey, Quote]) -> None:
    free = OptionCosts(slippage_per_share=0, commission_per_contract=0)
    fill = price_open(PUT_CREDIT_SPREAD, 1, chain, free)
    assert [leg.price for leg in fill.legs] == [3.10, 2.08]  # bid for the sale, ask for the buy
    assert fill.net_premium == pytest.approx(102.0)  # not the mid-to-mid 1.11 x 100


def test_debit_spread_and_long_option(chain: dict[ContractKey, Quote]) -> None:
    # Bear put debit spread: buy 580P at 3.22, sell 575P at 1.98 -> debit 1.24 x 100 x 2
    debit = price_open((Leg(put(580), 1), Leg(put(575), -1)), 2, chain, COSTS)
    assert debit.structure == "debit_vertical"
    assert debit.net_premium == pytest.approx(-248.00)
    assert (debit.collateral_required, debit.reserve) == (0.0, 0.0)
    assert debit.max_loss == pytest.approx(248.00)
    assert debit.cash_needed == pytest.approx(248.00 + 2.60)
    # Single long put: 3.22 x 100 = 322.00 + 0.65 commission
    single = price_open((Leg(put(580), 1),), 1, chain, COSTS)
    assert single.structure == "long_option"
    assert single.net_premium == pytest.approx(-322.00)
    assert single.cash_needed == pytest.approx(322.65)


def test_closing_fills_use_bid_for_longs_and_ask_for_shorts(
    chain: dict[ContractKey, Quote],
) -> None:
    # Close the credit spread: buy back 580P at 3.20 + 0.02; sell 575P at 2.00 - 0.02.
    short_close, long_close = price_close([(put(580), -2), (put(575), 2)], chain, COSTS)
    assert (short_close.side, long_close.side) == ("buy", "sell")
    assert short_close.price == pytest.approx(3.22)
    assert long_close.price == pytest.approx(1.98)
    assert short_close.premium + long_close.premium == pytest.approx(-248.00)


# ---- rejected orders -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("legs", "message"),
    [
        ((Leg(put(570), -1), Leg(put(565), 1)), "570P quote is unusable"),  # crossed 1.50/1.40
        ((Leg(put(565), -1), Leg(put(560), 1)), "leaves no credit"),  # bid 0.01 < slippage
        ((Leg(call(600), 1),), "no ask"),
        ((Leg(put(590), 1),), "No quote"),
    ],
)
def test_unfillable_quotes(
    chain: dict[ContractKey, Quote], legs: tuple[Leg, ...], message: str
) -> None:
    with pytest.raises(UnfillableError, match=message):
        price_open(legs, 1, chain, COSTS)


@pytest.mark.parametrize(
    "legs",
    [
        (Leg(put(580), -1),),  # naked short
        (Leg(put(580), -1), Leg(call(575), 1)),  # put vs call
        (Leg(put(580), -1), Leg(replace(put(575), expiration=date(2025, 2, 21)), 1)),
        (Leg(put(580), 1), Leg(put(575), 1)),  # two longs is not a vertical
        (SHORT_580, LONG_575, Leg(put(570), 1)),
    ],
)
def test_only_defined_risk_structures(
    chain: dict[ContractKey, Quote], legs: tuple[Leg, ...]
) -> None:
    with pytest.raises(NakedShortError):
        price_open(legs, 1, chain, COSTS)


# ---- (b, c, d) expiration -------------------------------------------------------------------

POSITION = [(put(580), -2), (put(575), 2)]


def test_both_out_of_the_money_expire_worthless() -> None:
    s = settle_at_expiration(POSITION, 590.0, COSTS)
    assert [leg.outcome for leg in s.legs] == ["expired_worthless", "expired_worthless"]
    assert (s.cash, s.shares, s.notes) == (0.0, 0, ())


def test_both_in_the_money_net_to_cash_with_no_shares() -> None:
    # Short 580P assigned: buy 200 @ 580 = -116,000. Long 575P exercised: sell 200 @ 575 =
    # +115,000. Settled together: -1,000 cash (width 5 x 100 x 2), zero shares.
    s = settle_at_expiration(POSITION, 570.0, COSTS)
    assert [leg.outcome for leg in s.legs] == ["assigned", "exercised"]
    assert [leg.shares for leg in s.legs] == [200, -200]
    assert s.cash == pytest.approx(-1000.0)
    assert s.shares == 0
    assert s.notes == ()


def test_pin_risk_between_strikes_leaves_shares() -> None:
    # 577 is between the strikes: short 580P assigned (buy 200 @ 580), long 575P worthless.
    s = settle_at_expiration(POSITION, 577.0, COSTS)
    assert [leg.outcome for leg in s.legs] == ["assigned", "expired_worthless"]
    assert s.cash == pytest.approx(-116_000.0)
    assert s.shares == 200
    assert len(s.notes) == 1
    assert s.notes[0].startswith("Pin risk: 577 finished between the strikes")


def test_auto_exercise_threshold_is_one_cent() -> None:
    assert settle_at_expiration(POSITION, 579.995, COSTS).legs[0].outcome == "expired_worthless"
    assert settle_at_expiration(POSITION, 579.99, COSTS).legs[0].outcome == "assigned"


def test_assignment_fees() -> None:
    costs = replace(COSTS, assignment_fee_per_contract=5.0)
    s = settle_at_expiration(POSITION, 570.0, costs)
    assert s.fees == pytest.approx(20.0)  # 5 x 2 contracts x 2 legs
    assert s.cash == pytest.approx(-1020.0)


def test_european_options_settle_in_cash() -> None:
    # Short 5800P, long 5750P, 1 contract, SPX settles at 5777: short pays 23 x 100.
    s = settle_at_expiration([(put(5800, "SPX"), -1), (put(5750, "SPX"), 1)], 5777.0, COSTS)
    assert [leg.outcome for leg in s.legs] == ["cash_settled", "expired_worthless"]
    assert s.cash == pytest.approx(-2300.0)
    assert s.shares == 0


def test_short_call_assignment_delivers_shares() -> None:
    # Bear call spread: short 600C, long 605C, 1 contract, finishes at 602 (pin).
    s = settle_at_expiration([(call(600), -1), (call(605), 1)], 602.0, COSTS)
    assert [leg.outcome for leg in s.legs] == ["assigned", "expired_worthless"]
    assert s.cash == pytest.approx(60_000.0)  # sold 100 @ 600: now short 100 shares
    assert s.shares == -100


# ---- early assignment -------------------------------------------------------------------------


def quotes(day: date, *rows: tuple[Contract, float, float]) -> dict[ContractKey, Quote]:
    return {c.key: Quote(c, day, bid, ask) for c, bid, ask in rows}


DEEP = quotes(date(2025, 1, 7), (put(580), 19.90, 20.03), (put(575), 14.95, 15.10))


def test_early_assignment_when_time_value_is_gone() -> None:
    # Spot 560: 580P intrinsic 20.00, ask 20.03 -> time value 0.03 <= 0.05: assigned.
    # The ITM 575P is exercised with it, so shares net to zero and cash is -1,000.
    s = early_assignment(POSITION, DEEP, 560.0, COSTS)
    assert s is not None
    assert [(leg.contract.strike, leg.outcome) for leg in s.legs] == [
        (580, "assigned"),
        (575, "exercised"),
    ]
    assert s.cash == pytest.approx(-1000.0)
    assert s.shares == 0
    assert "time value = ask 20.03 - intrinsic 20.00 = 0.03 <= 0.05" in s.notes[0]


def test_no_early_assignment_with_time_value_left() -> None:
    rich = quotes(date(2025, 1, 7), (put(580), 19.90, 20.06), (put(575), 14.95, 15.10))
    assert early_assignment(POSITION, rich, 560.0, COSTS) is None  # time value 0.06
    assert early_assignment(POSITION, DEEP, 590.0, COSTS) is None  # out of the money
    assert early_assignment(POSITION, DEEP, 560.0, replace(COSTS, early_assignment=False)) is None


def test_european_and_long_legs_are_never_early_assigned() -> None:
    euro = [(put(5800, "SPX"), -1), (put(5750, "SPX"), 1)]
    deep = quotes(date(2025, 1, 7), (put(5800, "SPX"), 99.0, 100.0))
    assert early_assignment(euro, deep, 5700.0, COSTS) is None
    assert early_assignment([(put(580), 2)], DEEP, 560.0, COSTS) is None


def test_fixture_is_loaded_through_the_real_importer(chain: dict[ContractKey, Quote]) -> None:
    assert chain[put(580).key].bid == 3.10
    assert chain[put(5800, "SPX").key].contract.style == "european"
    assert Path(FIXTURES / "options" / "synthetic_spy_put_spread.csv").exists()
