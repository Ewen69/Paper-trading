"""Event-driven, long-only, single-asset backtest engine.

Timeline for each session i:
  1. open:  fill any order decided at the previous close (slippage + commission applied)
  2. close: mark the position to market and record equity
  3. close: ask the strategy for a target exposure, using history up to and including i only;
            a change in target becomes an order for session i + 1's open

Bars before `start` are warm-up history: the strategy may read them, but no trading or equity
is recorded there. Any open position is sold at the final close (with costs) so results are
after all costs. Fractional shares are allowed, so results don't depend on the price level.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from ptl.backtest.models import (
    CostModel,
    EngineResult,
    EquityPoint,
    Fill,
    History,
    PriceBar,
    Trade,
)
from ptl.backtest.strategies import Strategy

_EPSILON = 1e-9


@dataclass
class _OpenTrade:
    entry_date: date
    entry_index: int
    cash_out: float = 0.0
    cash_in: float = 0.0


class _Book:
    def __init__(self, initial_capital: float, costs: CostModel) -> None:
        self.cash = initial_capital
        self.shares = 0.0
        self.costs = costs
        self.fills: list[Fill] = []
        self.trades: list[Trade] = []
        self.open_trade: _OpenTrade | None = None

    def rebalance(self, day: date, index: int, price: float, target: float, reason: str) -> None:
        equity = self.cash + self.shares * price
        delta_value = target * equity - self.shares * price
        if delta_value > _EPSILON:
            self._buy(day, index, price, delta_value / price, reason)
        elif delta_value < -_EPSILON:
            shares = self.shares if target <= 0 else min(self.shares, -delta_value / price)
            self._sell(day, index, price, shares, reason)

    def _buy(self, day: date, index: int, price: float, shares: float, reason: str) -> None:
        fill_price = price * (1 + self.costs.slippage)
        unit_cost = fill_price * (1 + self.costs.commission_bps / 10_000)
        affordable = (self.cash - self.costs.commission_per_order) / unit_cost
        shares = min(shares, affordable)
        if shares <= _EPSILON:
            return
        commission = self.costs.commission(shares * fill_price)
        paid = shares * fill_price + commission
        self.cash -= paid
        self.shares += shares
        self.fills.append(Fill(day, "buy", shares, price, fill_price, commission, reason))
        if self.open_trade is None:
            self.open_trade = _OpenTrade(day, index)
        self.open_trade.cash_out += paid

    def _sell(self, day: date, index: int, price: float, shares: float, reason: str) -> None:
        if shares <= _EPSILON:
            return
        fill_price = price * (1 - self.costs.slippage)
        commission = self.costs.commission(shares * fill_price)
        received = shares * fill_price - commission
        self.cash += received
        self.shares -= shares
        if self.shares < _EPSILON:
            self.shares = 0.0
        self.fills.append(Fill(day, "sell", shares, price, fill_price, commission, reason))
        if self.open_trade is None:  # pragma: no cover - long-only: sells need a position
            raise RuntimeError("sell without an open trade")
        self.open_trade.cash_in += received
        if self.shares == 0.0:
            t = self.open_trade
            self.trades.append(
                Trade(t.entry_date, day, t.cash_out, t.cash_in, index - t.entry_index, reason)
            )
            self.open_trade = None


def run_backtest(
    bars: Sequence[PriceBar],
    start: int,
    strategy: Strategy,
    costs: CostModel,
    initial_capital: float,
) -> EngineResult:
    if not 0 <= start < len(bars):
        raise ValueError("start must index a bar")
    if initial_capital <= 0:
        raise ValueError("initial capital must be positive")
    if any(b.day >= n.day for b, n in pairwise(bars)):
        raise ValueError("bars must be strictly increasing by date")

    closes = [b.close for b in bars]
    book = _Book(initial_capital, costs)
    equity: list[EquityPoint] = []
    pending: float | None = None
    current_target = 0.0
    last = len(bars) - 1

    for i, bar in enumerate(bars):
        if i >= start:
            if pending is not None:
                book.rebalance(bar.day, i, bar.open, pending, "signal")
                pending = None
            value = book.shares * bar.close
            total = book.cash + value
            equity.append(EquityPoint(bar.day, total, value / total if total > 0 else 0.0))
        if start - 1 <= i < last:
            target = min(1.0, max(0.0, strategy.target_exposure(History(bars, closes, i))))
            if target != current_target:
                pending, current_target = target, target

    if book.shares > 0:
        final = bars[last]
        book.rebalance(final.day, last, final.close, 0.0, "end of test (forced exit at close)")
        equity[-1] = EquityPoint(final.day, book.cash, 0.0)

    return EngineResult(initial_capital, equity, book.fills, book.trades)
