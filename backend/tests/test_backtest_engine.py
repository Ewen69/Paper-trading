"""Engine tests with hand-computed expectations. Bars here are synthetic test fixtures."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from ptl.backtest.engine import run_backtest
from ptl.backtest.models import CostModel, History, PriceBar
from ptl.backtest.strategies import STRATEGIES, BuyAndHold, SmaCrossover

FREE = CostModel(slippage_bps=0, commission_per_order=0, commission_bps=0)
D0 = date(2025, 1, 6)


def bars(*ohlc: tuple[float, float]) -> list[PriceBar]:
    """(open, close) pairs on consecutive days; high/low wrap them."""
    return [
        PriceBar(D0 + timedelta(days=i), o, max(o, c) + 1, min(o, c) - 1, c)
        for i, (o, c) in enumerate(ohlc)
    ]


class Scripted:
    """Test strategy: returns preset targets by decision index and records what it saw."""

    def __init__(self, targets: dict[int, float], warmup: int = 0) -> None:
        self.targets = targets
        self.warmup = warmup
        self.seen: list[tuple[int, float]] = []  # (history length, latest close)

    def target_exposure(self, history: History) -> float:
        self.seen.append((len(history), history.current.close))
        index = len(history) - 1
        return self.targets.get(index, self.targets.get(-1, 0.0))


def test_buy_and_hold_without_costs() -> None:
    # Decide at day-0 close, fill at day-1 open (100), forced exit at the final close (121).
    result = run_backtest(bars((100, 100), (100, 110), (110, 121)), 0, BuyAndHold(), FREE, 100_000)
    assert [p.equity for p in result.equity] == pytest.approx([100_000, 110_000, 121_000])
    buy, sell = result.fills
    assert (buy.day, buy.side, buy.fill_price, buy.shares) == (D0 + timedelta(1), "buy", 100, 1000)
    assert (sell.day, sell.side, sell.fill_price) == (D0 + timedelta(2), "sell", 121)
    assert "end of test" in sell.reason
    [trade] = result.trades
    assert trade.pnl == pytest.approx(21_000)
    assert trade.return_pct == pytest.approx(0.21)
    assert trade.sessions_held == 1
    assert result.total_costs == 0


def test_slippage_and_commission_are_charged_on_both_sides() -> None:
    costs = CostModel(slippage_bps=10, commission_per_order=5, commission_bps=0)
    result = run_backtest(bars((100, 100), (100, 110), (110, 121)), 0, BuyAndHold(), costs, 100_000)
    buy_price = 100 * 1.001
    shares = (100_000 - 5) / buy_price  # all cash, after the $5 commission
    sell_price = 121 * 0.999
    final = shares * sell_price - 5
    buy, sell = result.fills
    assert buy.fill_price == pytest.approx(buy_price)
    assert buy.shares == pytest.approx(shares)
    assert sell.fill_price == pytest.approx(sell_price)
    assert result.equity[1].equity == pytest.approx(shares * 110)
    assert result.equity[-1].equity == pytest.approx(final)
    assert result.trades[0].pnl == pytest.approx(final - 100_000)
    expected_costs = shares * (buy_price - 100) + shares * (121 - sell_price) + 10
    assert result.total_costs == pytest.approx(expected_costs)


def test_signal_fills_at_next_open_never_same_bar() -> None:
    strategy = Scripted({1: 1.0, 2: 1.0, 3: 0.0})
    data = bars((10, 10), (10, 11), (12, 12), (13, 14), (15, 16), (16, 16))
    result = run_backtest(data, 0, strategy, FREE, 1_000)
    buy, sell = result.fills
    assert (buy.day, buy.reference_price) == (data[2].day, 12)  # decided at bar 1 close
    assert (sell.day, sell.reference_price) == (data[4].day, 15)  # decided at bar 3 close
    [trade] = result.trades
    assert trade.pnl == pytest.approx(1_000 / 12 * 15 - 1_000)
    assert trade.exit_reason == "signal"


def test_strategy_never_sees_future_bars() -> None:
    strategy = Scripted({-1: 1.0})
    data = bars((10, 10), (10, 11), (11, 12), (12, 13))
    run_backtest(data, 0, strategy, FREE, 1_000)
    # Decisions happen at closes of bars 0..2 (no decision at the last bar: nothing to fill).
    assert strategy.seen == [(1, 10), (2, 11), (3, 12)]


def test_changing_the_future_cannot_change_past_decisions() -> None:
    base = bars(*[(100 + i, 100 + i + (i % 3) - 1) for i in range(30)])
    altered = [*base[:20], *(replace(b, close=b.close * 3, open=b.open * 3) for b in base[20:])]
    altered = [
        replace(b, high=max(b.open, b.close) + 1, low=min(b.open, b.close) - 1) for b in altered
    ]
    strategy = SmaCrossover(fast=2, slow=5)
    fills_base = run_backtest(base, 0, strategy, FREE, 10_000).fills
    fills_altered = run_backtest(altered, 0, strategy, FREE, 10_000).fills
    before = [f for f in fills_base if f.day <= base[20].day]
    assert before == [f for f in fills_altered if f.day <= base[20].day]


def test_history_cannot_reach_past_now() -> None:
    data = bars((1, 1), (2, 2), (3, 3))
    history = History(data, [b.close for b in data], 1)
    assert list(history.closes(10)) == [1, 2]
    assert history.current.close == 2
    with pytest.raises(IndexError):
        History(data, [b.close for b in data], 3)


def test_warm_up_bars_are_visible_but_not_traded() -> None:
    strategy = Scripted({-1: 1.0})
    data = bars((10, 10), (10, 11), (12, 12), (12, 13))
    result = run_backtest(data, 2, strategy, FREE, 1_000)
    assert [p.day for p in result.equity] == [data[2].day, data[3].day]
    assert result.fills[0].day == data[2].day  # decided at warm-up bar 1, filled at window start
    assert strategy.seen[0] == (2, 11)


def test_sma_crossover_trades_on_hand_computed_signals() -> None:
    # closes: 10, 10, 10, 12, 14, 12, 9, 8. fast=2, slow=3.
    # i=2: fast 10.0 vs slow 10.0 -> 0. i=3: 11 vs 10.67 -> 1 (buy at bar 4 open 14).
    # i=4: 13 vs 12 -> 1. i=5: 13 vs 12.67 -> 1. i=6: 10.5 vs 11.67 -> 0 (sell at bar 7 open 8).
    closes = [10, 10, 10, 12, 14, 12, 9, 8]
    data = bars(*[(c, c) for c in closes])
    result = run_backtest(data, 0, SmaCrossover(fast=2, slow=3), FREE, 1_400)
    buy, sell = result.fills
    assert (buy.day, buy.reference_price) == (data[4].day, 14)
    assert buy.shares == pytest.approx(100)
    assert (sell.day, sell.reference_price) == (data[7].day, 8)
    assert result.trades[0].pnl == pytest.approx(100 * 8 - 1_400)
    assert result.trades[0].sessions_held == 3


def test_flat_strategy_never_trades() -> None:
    result = run_backtest(bars((1, 1), (1, 1), (1, 1)), 0, Scripted({}), FREE, 500)
    assert result.fills == []
    assert result.trades == []
    assert [p.equity for p in result.equity] == [500, 500, 500]


@pytest.mark.parametrize(
    ("data", "start", "message"),
    [
        (bars((1, 1)), 1, "start"),
        ([*bars((1, 1), (1, 1))][::-1], 0, "increasing"),
    ],
)
def test_rejects_bad_inputs(data: list[PriceBar], start: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run_backtest(data, start, BuyAndHold(), FREE, 100)


def test_cost_model_validation() -> None:
    with pytest.raises(ValueError, match="slippage"):
        CostModel(slippage_bps=-1)
    with pytest.raises(ValueError, match="negative"):
        CostModel(commission_per_order=-1)


def test_strategy_param_validation() -> None:
    spec = STRATEGIES["sma_crossover"]
    _, params = spec.create({})
    assert params == {"fast": 50, "slow": 200}
    with pytest.raises(ValueError, match="shorter"):
        spec.create({"fast": 50, "slow": 50})
    with pytest.raises(ValueError, match="between"):
        spec.create({"fast": 1})
    with pytest.raises(ValueError, match="unknown"):
        spec.create({"lookback": 3})
