"""Option contracts, legs, quotes and the cost model.

Prices are per share. Every cash amount is per share x multiplier (100) x contracts.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

MULTIPLIER = 100
# OCC exercises any equity option that finishes at least $0.01 in the money.
AUTO_EXERCISE_THRESHOLD = 0.01
# Float tolerance for threshold comparisons (580 - 579.99 is 0.0099999... in binary).
PRICE_EPSILON = 1e-9

OptionType = Literal["call", "put"]
ExerciseStyle = Literal["american", "european"]
ContractKey = tuple[str, str, date, float, OptionType]


@dataclass(frozen=True, slots=True)
class Contract:
    underlying: str
    root: str
    expiration: date
    strike: float
    option_type: OptionType
    style: ExerciseStyle
    multiplier: int = MULTIPLIER

    @property
    def key(self) -> ContractKey:
        return (self.underlying, self.root, self.expiration, self.strike, self.option_type)

    @property
    def label(self) -> str:
        kind = self.option_type[0].upper()
        return f"{self.root or self.underlying} {self.expiration} {self.strike:g}{kind}"

    @property
    def physical(self) -> bool:
        """American equity/ETF options deliver shares; European (index) options settle in cash."""
        return self.style == "american"

    def intrinsic(self, spot: float) -> float:
        if self.option_type == "call":
            return max(spot - self.strike, 0.0)
        return max(self.strike - spot, 0.0)


@dataclass(frozen=True, slots=True)
class Quote:
    contract: Contract
    day: date
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class Leg:
    """One leg of an order: +1 = long (buy), -1 = short (sell), per unit of the structure."""

    contract: Contract
    ratio: Literal[1, -1]


@dataclass(frozen=True, slots=True)
class OptionCosts:
    """Execution and settlement assumptions. Every field is shown in the Reality Check.

    Fills: buys pay ask + slippage, sells receive bid - slippage, where
    slippage = slippage_per_share + slippage_spread_fraction x (ask - bid). Never mid.
    """

    slippage_per_share: float = 0.01
    slippage_spread_fraction: float = 0.0
    commission_per_contract: float = 0.65
    assignment_fee_per_contract: float = 0.0
    stock_slippage_bps: float = 5.0  # liquidating shares delivered by assignment/exercise
    early_assignment: bool = True
    early_assignment_extrinsic: float = 0.05  # short American legs assigned at or below this

    def __post_init__(self) -> None:
        amounts = (
            self.slippage_per_share,
            self.commission_per_contract,
            self.assignment_fee_per_contract,
            self.stock_slippage_bps,
            self.early_assignment_extrinsic,
        )
        if any(a < 0 for a in amounts):
            raise ValueError("option costs cannot be negative")
        if not 0 <= self.slippage_spread_fraction <= 1:
            raise ValueError("slippage_spread_fraction must be between 0 and 1")

    def slippage(self, quote: Quote) -> float:
        return self.slippage_per_share + self.slippage_spread_fraction * (quote.ask - quote.bid)
