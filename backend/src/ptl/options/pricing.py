"""Order validation, bid/ask fills and collateral. Pure functions; no account state here.

Rules:
- Defined risk only: a single long option, or a vertical spread (same underlying, root,
  expiration, type and style; one long and one short; different strikes). Naked shorts are
  rejected.
- Buys fill at ask + slippage, sells at bid - slippage. Never mid, never last.
- No fills against unusable quotes: crossed (bid > ask), no ask for a buy, or no bid left
  after slippage for a sell that opens a position.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ptl.options.models import Contract, ContractKey, Leg, OptionCosts, Quote

Structure = Literal["long_option", "credit_vertical", "debit_vertical"]
Side = Literal["buy", "sell"]


class UnfillableError(Exception):
    """The order can't be filled at the available quotes. The message says why."""


class NakedShortError(ValueError):
    """The order is not defined-risk."""


@dataclass(frozen=True, slots=True)
class LegFill:
    contract: Contract
    side: Side
    contracts: int
    price: float  # per share, after slippage
    bid: float
    ask: float
    commission: float

    @property
    def premium(self) -> float:
        """Cash from premium alone: + received for sells, - paid for buys."""
        sign = 1 if self.side == "sell" else -1
        return sign * self.price * self.contract.multiplier * self.contracts

    @property
    def slippage_cost(self) -> float:
        ref = self.bid if self.side == "sell" else self.ask
        return abs(self.price - ref) * self.contract.multiplier * self.contracts


@dataclass(frozen=True, slots=True)
class OpenFill:
    structure: Structure
    contracts: int
    legs: tuple[LegFill, ...]
    net_premium: float  # + net credit received, - net debit paid
    commissions: float
    collateral_required: float  # credit verticals: width x 100 x contracts - net credit
    reserve: float  # margin held: width x 100 x contracts for credit verticals, else 0
    max_loss: float  # worst case at expiration, before commissions and pin risk

    @property
    def cash_needed(self) -> float:
        """Available cash this order consumes: collateral + debit + commissions."""
        debit = max(-self.net_premium, 0.0)
        return self.collateral_required + debit + self.commissions


def classify(legs: Sequence[Leg]) -> Literal["long_option", "vertical"]:
    """Reject anything that isn't defined-risk. Credit vs debit is decided after pricing."""
    if len(legs) == 1:
        if legs[0].ratio != 1:
            raise NakedShortError("A single short option is naked; only defined-risk orders.")
        return "long_option"
    if len(legs) == 2:  # noqa: PLR2004
        a, b = (leg.contract for leg in legs)
        same = (
            a.underlying == b.underlying
            and a.root == b.root
            and a.expiration == b.expiration
            and a.option_type == b.option_type
            and a.style == b.style
            and a.multiplier == b.multiplier
        )
        if not same or a.strike == b.strike or {legs[0].ratio, legs[1].ratio} != {1, -1}:
            raise NakedShortError(
                "Two legs must form a vertical spread: same underlying, expiration and type, "
                "one long and one short, different strikes."
            )
        return "vertical"
    raise NakedShortError("Only single long options and vertical spreads are supported.")


def _usable(quote: Quote | None, contract: Contract) -> Quote:
    if quote is None:
        raise UnfillableError(f"No quote for {contract.label}.")
    if quote.bid < 0 or quote.ask < 0 or quote.bid > quote.ask:
        raise UnfillableError(
            f"{contract.label} quote is unusable (bid {quote.bid}, ask {quote.ask})."
        )
    return quote


def fill_leg(  # noqa: PLR0913
    contract: Contract,
    side: Side,
    contracts: int,
    quote: Quote | None,
    costs: OptionCosts,
    *,
    opening: bool,
) -> LegFill:
    q = _usable(quote, contract)
    slip = costs.slippage(q)
    if side == "buy":
        if q.ask <= 0:
            raise UnfillableError(f"{contract.label} has no ask; it can't be bought.")
        price = q.ask + slip
    else:
        price = q.bid - slip
        if opening and price <= 0:
            raise UnfillableError(
                f"{contract.label} bid {q.bid} minus slippage {slip:.2f} leaves no credit."
            )
        price = max(price, 0.0)
    return LegFill(
        contract, side, contracts, price, q.bid, q.ask, costs.commission_per_contract * contracts
    )


def price_open(
    legs: Sequence[Leg],
    contracts: int,
    quotes: Mapping[ContractKey, Quote],
    costs: OptionCosts,
) -> OpenFill:
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    shape = classify(legs)
    fills = tuple(
        fill_leg(
            leg.contract,
            "buy" if leg.ratio == 1 else "sell",
            contracts,
            quotes.get(leg.contract.key),
            costs,
            opening=True,
        )
        for leg in legs
    )
    net = sum(f.premium for f in fills)
    commissions = sum(f.commission for f in fills)

    def result(
        structure: Structure, collateral: float, reserve: float, max_loss: float
    ) -> OpenFill:
        return OpenFill(
            structure=structure,
            contracts=contracts,
            legs=fills,
            net_premium=net,
            commissions=commissions,
            collateral_required=collateral,
            reserve=reserve,
            max_loss=max_loss,
        )

    if shape == "long_option":
        return result("long_option", 0.0, 0.0, -net)
    a, b = (leg.contract for leg in legs)
    width = abs(a.strike - b.strike) * a.multiplier * contracts
    if net > 0:  # credit vertical: broker holds the width; the credit offsets it
        collateral = max(width - net, 0.0)
        return result("credit_vertical", collateral, width, collateral)
    return result("debit_vertical", 0.0, 0.0, -net)


def price_close(
    legs: Sequence[tuple[Contract, int]],
    quotes: Mapping[ContractKey, Quote],
    costs: OptionCosts,
) -> tuple[LegFill, ...]:
    """Close signed positions: longs sell at bid - slippage (never below 0), shorts buy at ask."""
    return tuple(
        fill_leg(
            contract,
            "sell" if qty > 0 else "buy",
            abs(qty),
            quotes.get(contract.key),
            costs,
            opening=False,
        )
        for contract, qty in legs
    )
