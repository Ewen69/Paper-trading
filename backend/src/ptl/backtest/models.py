"""Engine-level value types."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

MAX_SLIPPAGE_BPS = 500.0


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One session as the engine sees it (adjusted or raw, see the report's price basis)."""

    day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class CostModel:
    """Daily bars have no bid/ask, so slippage stands in for half-spread plus market impact.

    Buys fill at open * (1 + slippage), sells at open * (1 - slippage). Commission is a fixed
    amount per order plus basis points of the traded notional.
    """

    slippage_bps: float = 5.0
    commission_per_order: float = 0.0
    commission_bps: float = 0.0

    def __post_init__(self) -> None:
        if not 0 <= self.slippage_bps <= MAX_SLIPPAGE_BPS:
            raise ValueError(f"slippage_bps must be between 0 and {MAX_SLIPPAGE_BPS:g}")
        if self.commission_per_order < 0 or self.commission_bps < 0:
            raise ValueError("commissions cannot be negative")

    @property
    def slippage(self) -> float:
        return self.slippage_bps / 10_000

    def commission(self, notional: float) -> float:
        return self.commission_per_order + notional * self.commission_bps / 10_000


@dataclass(frozen=True, slots=True)
class Fill:
    day: date
    side: Literal["buy", "sell"]
    shares: float
    reference_price: float  # the bar's open (or close for the forced end-of-test exit)
    fill_price: float  # after slippage
    commission: float
    reason: str


@dataclass(frozen=True, slots=True)
class Trade:
    """A round trip: from flat to invested and back to flat. P&L includes all costs."""

    entry_date: date
    exit_date: date
    cash_out: float  # total paid for buys, including slippage and commissions
    cash_in: float  # total received from sells, net of slippage and commissions
    sessions_held: int
    exit_reason: str

    @property
    def pnl(self) -> float:
        return self.cash_in - self.cash_out

    @property
    def return_pct(self) -> float:
        return self.pnl / self.cash_out if self.cash_out else 0.0


@dataclass(frozen=True, slots=True)
class EquityPoint:
    day: date
    equity: float
    exposure: float  # position value / equity at the close


@dataclass(frozen=True, slots=True)
class EngineResult:
    initial_capital: float
    equity: Sequence[EquityPoint]
    fills: Sequence[Fill]
    trades: Sequence[Trade]

    @property
    def total_costs(self) -> float:
        slippage = sum(abs(f.fill_price - f.reference_price) * f.shares for f in self.fills)
        return slippage + sum(f.commission for f in self.fills)


class History:
    """What a strategy may see at a decision point: bars up to and including `now`, nothing later.

    There is no API to reach past `now`. That is the engine's no-look-ahead guarantee.
    """

    __slots__ = ("_bars", "_closes", "_now")

    def __init__(self, bars: Sequence[PriceBar], closes: Sequence[float], now: int) -> None:
        if not 0 <= now < len(bars):
            raise IndexError("decision index out of range")
        self._bars = bars
        self._closes = closes
        self._now = now

    def __len__(self) -> int:
        return self._now + 1

    @property
    def current(self) -> PriceBar:
        return self._bars[self._now]

    def closes(self, count: int) -> Sequence[float]:
        """The last `count` closes up to now (fewer if history is shorter)."""
        if count <= 0:
            raise ValueError("count must be positive")
        return self._closes[max(0, self._now + 1 - count) : self._now + 1]
