"""Expiration, exercise and assignment. Pure functions over signed leg positions.

Expiration happens at the close of the expiration session, using the underlying's close:
- Legs less than $0.01 in the money expire worthless.
- Long legs in the money are exercised; short legs in the money are assigned.
- American (equity/ETF) options settle physically: calls deliver shares at the strike, puts
  take shares at the strike. All legs of a position settle together, so a spread that finishes
  fully in the money nets to cash with zero shares.
- European (index) options settle in cash at intrinsic value.

If only one leg of a spread finishes in the money (pin risk), the position ends up holding
shares. The engine sells or buys them back at the next session's open, so gap risk is real.

Early assignment: a short American leg that is in the money is assumed assigned at a session's
close when its remaining time value (ask - intrinsic) is at or below a threshold. An in-the-money
long leg of the same spread is exercised at the same time so the shares net out.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ptl.options.models import (
    AUTO_EXERCISE_THRESHOLD,
    PRICE_EPSILON,
    Contract,
    ContractKey,
    OptionCosts,
    Quote,
)

Outcome = Literal["expired_worthless", "exercised", "assigned", "cash_settled"]


@dataclass(frozen=True, slots=True)
class LegOutcome:
    contract: Contract
    quantity: int  # signed contracts
    outcome: Outcome
    intrinsic: float  # per share
    cash: float  # cash from this leg's settlement
    shares: int  # shares received (+) or delivered (-)


@dataclass(frozen=True, slots=True)
class Settlement:
    spot: float
    legs: tuple[LegOutcome, ...]
    fees: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cash(self) -> float:
        return sum(leg.cash for leg in self.legs) - self.fees

    @property
    def shares(self) -> int:
        return sum(leg.shares for leg in self.legs)


def settle_leg(contract: Contract, quantity: int, spot: float) -> LegOutcome:
    """Settle one signed leg at `spot`. Long legs exercise, short legs are assigned."""
    intrinsic = contract.intrinsic(spot)
    if intrinsic < AUTO_EXERCISE_THRESHOLD - PRICE_EPSILON:
        return LegOutcome(contract, quantity, "expired_worthless", intrinsic, 0.0, 0)
    units = quantity * contract.multiplier
    if not contract.physical:
        return LegOutcome(contract, quantity, "cash_settled", intrinsic, units * intrinsic, 0)
    outcome: Outcome = "exercised" if quantity > 0 else "assigned"
    if contract.option_type == "call":  # long call buys shares at K; short call delivers them
        return LegOutcome(contract, quantity, outcome, intrinsic, -units * contract.strike, units)
    # long put sells shares at K; short put must buy them at K
    return LegOutcome(contract, quantity, outcome, intrinsic, units * contract.strike, -units)


def settle_at_expiration(
    legs: Sequence[tuple[Contract, int]], spot: float, costs: OptionCosts
) -> Settlement:
    outcomes = tuple(settle_leg(c, q, spot) for c, q in legs)
    fees = sum(
        abs(o.quantity) * costs.assignment_fee_per_contract
        for o in outcomes
        if o.outcome in ("exercised", "assigned")
    )
    settlement = Settlement(spot, outcomes, fees)
    return Settlement(spot, outcomes, fees, _pin_notes(settlement))


def _pin_notes(settlement: Settlement) -> tuple[str, ...]:
    if settlement.shares == 0:
        return ()
    active = [o for o in settlement.legs if o.outcome in ("exercised", "assigned")]
    idle = [o for o in settlement.legs if o.outcome == "expired_worthless"]
    if active and idle:
        return (
            f"Pin risk: {settlement.spot:g} finished between the strikes. "
            + ", ".join(f"{o.contract.label} {o.outcome}" for o in active)
            + "; "
            + ", ".join(f"{o.contract.label} expired worthless" for o in idle)
            + f". Net {settlement.shares:+d} shares to liquidate.",
        )
    return ()


def early_assignment(
    legs: Sequence[tuple[Contract, int]],
    quotes: Mapping[ContractKey, Quote],
    spot: float,
    costs: OptionCosts,
) -> Settlement | None:
    """Assign in-the-money short American legs with little time value left, if any.

    Returns the settlement of the assigned legs plus any in-the-money long legs exercised to
    net the shares; None when nothing is assigned.
    """
    if not costs.early_assignment:
        return None
    assigned: list[tuple[Contract, int, str]] = []
    for contract, qty in legs:
        quote = quotes.get(contract.key)
        if qty >= 0 or not contract.physical or quote is None:
            continue
        intrinsic = contract.intrinsic(spot)
        extrinsic = quote.ask - intrinsic
        itm = intrinsic >= AUTO_EXERCISE_THRESHOLD - PRICE_EPSILON
        if itm and extrinsic <= costs.early_assignment_extrinsic + PRICE_EPSILON:
            assigned.append(
                (
                    contract,
                    qty,
                    f"{contract.label}: time value = ask {quote.ask:g} - intrinsic "
                    f"{intrinsic:.2f} = {extrinsic:.2f} <= {costs.early_assignment_extrinsic:.2f}",
                )
            )
    if not assigned:
        return None
    outcomes = [settle_leg(c, q, spot) for c, q, _ in assigned]
    assigned_keys = {c.key for c, _, _ in assigned}
    for contract, qty in legs:  # exercise ITM longs alongside so shares net out
        if qty > 0 and contract.key not in assigned_keys and contract.physical:
            outcome = settle_leg(contract, qty, spot)
            if outcome.outcome == "exercised":
                outcomes.append(outcome)
    fees = sum(abs(o.quantity) * costs.assignment_fee_per_contract for o in outcomes)
    notes = tuple(f"Early assignment: {why}" for _, _, why in assigned)
    return Settlement(spot, tuple(outcomes), fees, notes)
