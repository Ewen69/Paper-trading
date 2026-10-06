"""Event-driven options backtest engine for defined-risk positions.

Timeline for each session (daily end-of-day option quotes, underlying daily bars):
  1. open:  shares left over from assignment/exercise are liquidated at the open, with stock
            slippage (so gap risk over a weekend or holiday is real)
  2. quote: orders decided yesterday fill at today's bid/ask (+/- slippage, + commissions);
            opening orders are rejected if collateral exceeds available cash
  3. close: short American legs with no time value left are assigned early
  4. close: positions expiring today settle at the underlying's close (worthless, exercised,
            assigned, or cash-settled); a between-strike finish leaves shares (pin risk)
  5. close: mark to market at the liquidation value: longs at bid, shorts at ask, shares at
            the close, and record equity
  6. close: the strategy sees today's chain and history up to today, and returns orders
            for tomorrow

On the last session every open position is closed at that day's quotes instead of being
settled, so no result depends on data after the window.

Underlying prices are RAW (not dividend-adjusted): strikes are in raw dollars.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from ptl.backtest.models import EquityPoint, History, PriceBar
from ptl.options.models import Contract, ContractKey, Leg, OptionCosts, Quote
from ptl.options.pricing import (
    LegFill,
    NakedShortError,
    Structure,
    UnfillableError,
    price_close,
    price_open,
)
from ptl.options.settlement import Settlement, early_assignment, settle_at_expiration

ChainSource = Callable[[date], Mapping[ContractKey, Quote]]


# ---- What strategies see and return ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenIntent:
    legs: tuple[Leg, ...]
    contracts: int
    reason: str


@dataclass(frozen=True, slots=True)
class CloseIntent:
    position_id: int
    reason: str


Intent = OpenIntent | CloseIntent


@dataclass(frozen=True, slots=True)
class PositionView:
    id: int
    legs: tuple[tuple[Contract, int], ...]
    structure: Structure
    contracts: int
    opened: date
    entry_premium: float  # + credit received, - debit paid (before commissions)
    close_value: float | None  # premium cash to close now at today's bid/ask, None if no quote


@dataclass(frozen=True, slots=True)
class OptionsView:
    day: date
    history: History
    chain: Mapping[ContractKey, Quote]
    positions: tuple[PositionView, ...]
    available_cash: float


class OptionsStrategy(Protocol):
    @property
    def warmup(self) -> int: ...

    def decide(self, view: OptionsView) -> Sequence[Intent]: ...


# ---- Results ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptionsEvent:
    day: date
    kind: str
    position_id: int | None
    text: str
    cash: float = 0.0


@dataclass(frozen=True, slots=True)
class OptionTrade:
    position_id: int
    opened: date
    closed: date
    description: str
    structure: Structure
    contracts: int
    entry_premium: float
    max_loss: float
    pnl: float  # every cash flow: premiums, commissions, settlement, share liquidation
    exit_kind: str
    sessions_held: int


@dataclass(frozen=True, slots=True)
class OptionsResult:
    initial_capital: float
    equity: Sequence[EquityPoint]
    trades: Sequence[OptionTrade]
    events: Sequence[OptionsEvent]
    total_costs: float
    rejected_orders: int
    stale_marks: int
    pin_events: int
    early_assignments: int
    margin_shortfalls: int
    peak_reserve: float


# ---- Internal state ---------------------------------------------------------------------------


@dataclass
class _Position:
    id: int
    legs: list[tuple[Contract, int]]
    structure: Structure
    contracts: int
    opened: date
    open_index: int
    entry_premium: float
    max_loss: float
    reserve: float
    description: str
    cash_flows: float = 0.0
    shares: int = 0
    unwinding: bool = False
    exit_kind: str = ""
    marks: dict[ContractKey, float] = field(default_factory=dict)

    @property
    def expiration(self) -> date | None:
        return min((c.expiration for c, _ in self.legs), default=None)


def _describe(legs: Sequence[Leg], structure: Structure, contracts: int) -> str:
    strikes = "/".join(f"{leg.contract.strike:g}" for leg in legs)
    first = legs[0].contract
    kind = {
        "credit_vertical": f"{first.option_type} credit spread",
        "debit_vertical": f"{first.option_type} debit spread",
        "long_option": f"long {first.option_type}",
    }[structure]
    return f"{first.root or first.underlying} {first.expiration} {strikes} {kind} x{contracts}"


def _usable_mark(quote: Quote | None, qty: int) -> float | None:
    if quote is None or quote.bid < 0 or quote.ask < 0 or quote.bid > quote.ask:
        return None
    if qty < 0 and quote.ask <= 0:
        return None
    return quote.bid if qty > 0 else quote.ask


class _Engine:
    def __init__(self, bars: Sequence[PriceBar], costs: OptionCosts, capital: float) -> None:
        self.bars = bars
        self.closes = [b.close for b in bars]
        self.costs = costs
        self.cash = capital
        self.reserved = 0.0
        self.shares = 0
        self.positions: dict[int, _Position] = {}
        self.next_id = 1
        self.events: list[OptionsEvent] = []
        self.trades: list[OptionTrade] = []
        self.costs_paid = 0.0
        self.counts = {"rejected": 0, "stale": 0, "pin": 0, "early": 0, "margin": 0}
        self.peak_reserve = 0.0

    # -- bookkeeping --

    def event(
        self, day: date, kind: str, pos: _Position | None, text: str, cash: float = 0.0
    ) -> None:
        self.events.append(OptionsEvent(day, kind, pos.id if pos else None, text, cash))

    def flow(self, pos: _Position, amount: float) -> None:
        self.cash += amount
        pos.cash_flows += amount

    def apply_fills(self, pos: _Position, fills: Sequence[LegFill]) -> float:
        net = sum(f.premium for f in fills) - sum(f.commission for f in fills)
        self.flow(pos, net)
        self.costs_paid += sum(f.commission + f.slippage_cost for f in fills)
        return net

    def release(self, pos: _Position) -> None:
        self.reserved -= pos.reserve
        pos.reserve = 0.0

    def finish(self, pos: _Position, day: date, index: int, kind: str) -> None:
        self.release(pos)
        self.trades.append(
            OptionTrade(
                position_id=pos.id,
                opened=pos.opened,
                closed=day,
                description=pos.description,
                structure=pos.structure,
                contracts=pos.contracts,
                entry_premium=pos.entry_premium,
                max_loss=pos.max_loss,
                pnl=pos.cash_flows,
                exit_kind=pos.exit_kind or kind,
                sessions_held=index - pos.open_index,
            )
        )
        del self.positions[pos.id]

    # -- session steps --

    def liquidate_shares(self, i: int) -> None:
        bar = self.bars[i]
        slip = self.costs.stock_slippage_bps / 10_000
        for pos in list(self.positions.values()):
            if pos.shares == 0:
                continue
            price = bar.open * (1 - slip) if pos.shares > 0 else bar.open * (1 + slip)
            proceeds = pos.shares * price
            self.flow(pos, proceeds)
            self.costs_paid += abs(pos.shares) * bar.open * slip
            side = "Sold" if pos.shares > 0 else "Bought back"
            self.event(
                bar.day,
                "liquidate",
                pos,
                f"{side} {abs(pos.shares)} shares at open {bar.open:g} "
                f"{'-' if pos.shares > 0 else '+'} {self.costs.stock_slippage_bps:g} bps "
                f"= {price:.4f}: {proceeds:+,.2f}.",
                proceeds,
            )
            self.shares -= pos.shares
            pos.shares = 0
            if not pos.legs:
                self.finish(pos, bar.day, i, "settled")

    def close_position(
        self, pos: _Position, chain: Mapping[ContractKey, Quote], i: int, reason: str
    ) -> bool:
        day = self.bars[i].day
        try:
            fills = price_close(pos.legs, chain, self.costs)
        except UnfillableError as exc:
            self.counts["rejected"] += 1
            self.event(day, "reject", pos, f"Close not filled: {exc}")
            return False
        net = self.apply_fills(pos, fills)
        parts = ", ".join(f"{f.side} {f.contract.label} @ {f.price:.2f}" for f in fills)
        self.event(day, "close", pos, f"{reason} Closed: {parts}; net {net:+,.2f}.", net)
        pos.legs = []
        if pos.shares == 0:
            self.finish(pos, day, i, "closed")
        return True

    def force_close(self, pos: _Position, chain: Mapping[ContractKey, Quote], i: int) -> None:
        """Last session: close at today's quotes, else the last valid mark (flagged stale)."""
        if self.close_position(pos, chain, i, "End of test."):
            return
        day = self.bars[i].day
        bar = self.bars[i]
        value = 0.0
        for contract, qty in pos.legs:
            mark = pos.marks.get(contract.key)
            per_share = mark if mark is not None else contract.intrinsic(bar.close)
            value += per_share * contract.multiplier * qty
        self.counts["stale"] += 1
        self.flow(pos, value)
        self.event(
            day,
            "forced_exit",
            pos,
            f"End of test with no usable quote: closed at last known marks {value:+,.2f} "
            "(flagged stale).",
            value,
        )
        pos.legs = []
        pos.exit_kind = "closed at stale marks (end of test)"
        if pos.shares == 0:
            self.finish(pos, day, i, "closed")

    def open_position(self, intent: OpenIntent, chain: Mapping[ContractKey, Quote], i: int) -> None:
        day = self.bars[i].day
        try:
            fill = price_open(intent.legs, intent.contracts, chain, self.costs)
        except (UnfillableError, NakedShortError) as exc:
            self.counts["rejected"] += 1
            self.event(day, "reject", None, f"Open not filled: {exc}")
            return
        available = self.cash - self.reserved
        if fill.cash_needed > available + 1e-9:
            self.counts["rejected"] += 1
            self.event(
                day,
                "reject",
                None,
                f"Open rejected: needs {fill.cash_needed:,.2f} (collateral "
                f"{fill.collateral_required:,.2f} + debit {max(-fill.net_premium, 0):,.2f} + "
                f"commissions {fill.commissions:,.2f}) but only {available:,.2f} is available.",
            )
            return
        pos = _Position(
            id=self.next_id,
            legs=[(leg.contract, leg.ratio * intent.contracts) for leg in intent.legs],
            structure=fill.structure,
            contracts=intent.contracts,
            opened=day,
            open_index=i,
            entry_premium=fill.net_premium,
            max_loss=fill.max_loss,
            reserve=fill.reserve,
            description=_describe(intent.legs, fill.structure, intent.contracts),
        )
        self.next_id += 1
        self.positions[pos.id] = pos
        self.reserved += fill.reserve
        self.peak_reserve = max(self.peak_reserve, self.reserved)
        net = self.apply_fills(pos, fill.legs)
        parts = ", ".join(f"{f.side} {f.contract.label} @ {f.price:.2f}" for f in fill.legs)
        self.event(
            day,
            "open",
            pos,
            f"{intent.reason} Opened {pos.description}: {parts}; premium "
            f"{fill.net_premium:+,.2f}, commissions {fill.commissions:,.2f}, collateral "
            f"{fill.collateral_required:,.2f}, max loss {fill.max_loss:,.2f}.",
            net,
        )

    def apply_settlement(self, pos: _Position, settlement: Settlement, i: int, kind: str) -> None:
        day = self.bars[i].day
        settled = {o.contract.key for o in settlement.legs}
        self.flow(pos, settlement.cash)
        self.costs_paid += settlement.fees
        pos.shares += settlement.shares
        self.shares += settlement.shares
        for o in settlement.legs:
            self.event(
                day,
                o.outcome,
                pos,
                f"{o.contract.label} x{o.quantity:+d} {o.outcome.replace('_', ' ')} at "
                f"{settlement.spot:g} (intrinsic {o.intrinsic:.2f}/share): cash {o.cash:+,.2f}, "
                f"shares {o.shares:+d}.",
                o.cash,
            )
        for note in settlement.notes:
            self.event(day, kind, pos, note)
        pos.legs = [(c, q) for c, q in pos.legs if c.key not in settled]
        self.release(pos)

    def early_assign(self, chain: Mapping[ContractKey, Quote], i: int) -> None:
        bar = self.bars[i]
        for pos in list(self.positions.values()):
            if not pos.legs or pos.expiration is None or pos.expiration <= bar.day:
                continue
            settlement = early_assignment(pos.legs, chain, bar.close, self.costs)
            if settlement is None:
                continue
            self.counts["early"] += 1
            pos.exit_kind = "early assignment"
            pos.unwinding = True
            self.apply_settlement(pos, settlement, i, "early_assignment")
            if not pos.legs and pos.shares == 0:
                self.finish(pos, bar.day, i, "early assignment")

    def expire(self, i: int) -> None:
        bar = self.bars[i]
        next_day = self.bars[i + 1].day if i + 1 < len(self.bars) else None
        for pos in list(self.positions.values()):
            expiry = pos.expiration
            if expiry is None:
                continue
            # Settle on the expiration session, or the last session before an expiration date
            # that isn't a trading day.
            if not (expiry <= bar.day or (next_day is not None and next_day > expiry)):
                continue
            settlement = settle_at_expiration(pos.legs, bar.close, self.costs)
            if settlement.notes:
                self.counts["pin"] += 1
                pos.exit_kind = "pin risk: assigned, shares liquidated next open"
            elif all(o.outcome == "expired_worthless" for o in settlement.legs):
                pos.exit_kind = pos.exit_kind or "expired worthless"
            else:
                pos.exit_kind = pos.exit_kind or "settled in the money at expiration"
            self.apply_settlement(pos, settlement, i, "pin")
            if pos.shares == 0:
                self.finish(pos, bar.day, i, pos.exit_kind)

    def mark(self, chain: Mapping[ContractKey, Quote], i: int) -> float:
        bar = self.bars[i]
        value = self.cash + self.shares * bar.close
        for pos in self.positions.values():
            for contract, qty in pos.legs:
                mark = _usable_mark(chain.get(contract.key), qty)
                if mark is None:
                    self.counts["stale"] += 1
                    mark = pos.marks.get(contract.key, contract.intrinsic(bar.close))
                pos.marks[contract.key] = mark
                value += mark * contract.multiplier * qty
        return value

    def view(self, chain: Mapping[ContractKey, Quote], i: int) -> OptionsView:
        views = []
        for pos in self.positions.values():
            if pos.unwinding or not pos.legs:
                continue
            try:
                close_value: float | None = sum(
                    f.premium for f in price_close(pos.legs, chain, self.costs)
                )
            except UnfillableError:
                close_value = None
            views.append(
                PositionView(
                    id=pos.id,
                    legs=tuple(pos.legs),
                    structure=pos.structure,
                    contracts=pos.contracts,
                    opened=pos.opened,
                    entry_premium=pos.entry_premium,
                    close_value=close_value,
                )
            )
        return OptionsView(
            day=self.bars[i].day,
            history=History(self.bars, self.closes, i),
            chain=chain,
            positions=tuple(views),
            available_cash=self.cash - self.reserved,
        )


def run_options_backtest(  # noqa: PLR0912, PLR0913 - the session recipe above, step by step
    bars: Sequence[PriceBar],
    start: int,
    strategy: OptionsStrategy,
    chain_for: ChainSource,
    *,
    costs: OptionCosts,
    initial_capital: float,
) -> OptionsResult:
    if not 0 <= start < len(bars):
        raise ValueError("start must index a bar")
    if initial_capital <= 0:
        raise ValueError("initial capital must be positive")
    engine = _Engine(bars, costs, initial_capital)
    equity: list[EquityPoint] = []
    pending: Sequence[Intent] = ()
    last = len(bars) - 1

    for i in range(max(start - 1, 0), len(bars)):
        bar = bars[i]
        chain = chain_for(bar.day)
        if i >= start:
            engine.liquidate_shares(i)
            for pos in list(engine.positions.values()):
                if pos.unwinding and pos.legs:
                    engine.close_position(pos, chain, i, "Closing what's left after assignment.")
            for intent in pending:
                if isinstance(intent, CloseIntent):
                    target = engine.positions.get(intent.position_id)
                    if target is not None and target.legs and not target.unwinding:
                        engine.close_position(target, chain, i, intent.reason)
                else:
                    engine.open_position(intent, chain, i)
            pending = ()
            if i == last:
                for pos in list(engine.positions.values()):
                    if pos.legs:
                        engine.force_close(pos, chain, i)
            else:
                engine.early_assign(chain, i)
                engine.expire(i)
            if engine.cash < 0:
                engine.counts["margin"] += 1
                engine.event(
                    bar.day,
                    "margin",
                    None,
                    f"Cash is {engine.cash:,.2f} after settlement: a real broker would issue a "
                    "margin call before the shares are sold.",
                )
            total = engine.mark(chain, i)
            invested = bool(engine.positions) or engine.shares != 0
            equity.append(EquityPoint(bar.day, total, 1.0 if invested else 0.0))
        if i < last:
            pending = tuple(strategy.decide(engine.view(chain, i)))

    return OptionsResult(
        initial_capital=initial_capital,
        equity=equity,
        trades=engine.trades,
        events=engine.events,
        total_costs=engine.costs_paid,
        rejected_orders=engine.counts["rejected"],
        stale_marks=engine.counts["stale"],
        pin_events=engine.counts["pin"],
        early_assignments=engine.counts["early"],
        margin_shortfalls=engine.counts["margin"],
        peak_reserve=engine.peak_reserve,
    )
