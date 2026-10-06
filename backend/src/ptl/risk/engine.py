"""The risk engine: a pure function from (order, account, limits, kill switch) to a decision.

Checks, each reported with its arithmetic:
- kill switch: when engaged, nothing that adds risk passes (reducing or closing still does)
- defined risk: no naked short options, no short stock
- max loss per trade: the order's worst case <= limit
- max daily loss: today's P&L must be above -limit; a breach also trips the kill switch
- max open positions: positions after the order <= limit
- max position size: the symbol's position value after the order <= pct x equity
- max capital at risk: worst-case loss of open positions + this order <= pct x equity

Worst case for a long stock position with no stop is its full notional. That's deliberately
conservative: it's what can actually be lost.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_loss_per_trade: float
    max_daily_loss: float
    max_open_positions: int
    max_capital_at_risk_pct: float
    max_position_pct: float = 1.0


@dataclass(frozen=True, slots=True)
class ProposedOrder:
    symbol: str
    description: str
    opens_risk: bool  # False for orders that only reduce or close a position
    max_loss: float  # worst case in dollars for the new risk, including commissions
    defined_risk: bool
    new_position: bool  # True when it opens a position that doesn't exist yet
    position_value_after: float = 0.0  # the symbol's position value if the order fills


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    equity: float
    day_pnl: float  # equity now - equity at the previous close
    open_positions: int
    capital_at_risk: float  # sum of worst-case losses of open positions


@dataclass(frozen=True, slots=True)
class RiskCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    checks: tuple[RiskCheck, ...]
    trip_kill_switch: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        failed = [c for c in self.checks if not c.passed]
        if not failed:
            return "Approved: " + "; ".join(c.detail for c in self.checks)
        return "Rejected: " + "; ".join(c.detail for c in failed)


def _money(x: float) -> str:
    return f"${x:,.2f}"


def evaluate(
    order: ProposedOrder, account: AccountSnapshot, limits: RiskLimits, kill_switch: bool
) -> RiskDecision:
    checks: list[RiskCheck] = []
    trip = False

    if order.opens_risk:
        checks.append(
            RiskCheck(
                "kill_switch",
                not kill_switch,
                "kill switch is engaged: no new risk" if kill_switch else "kill switch off",
            )
        )
    else:
        checks.append(RiskCheck("kill_switch", True, "reduces risk: allowed even with kill switch"))

    checks.append(
        RiskCheck(
            "defined_risk",
            order.defined_risk,
            "defined risk" if order.defined_risk else "not defined-risk (naked short) - forbidden",
        )
    )

    if order.opens_risk:
        checks.append(
            RiskCheck(
                "max_loss_per_trade",
                order.max_loss <= limits.max_loss_per_trade,
                f"worst case {_money(order.max_loss)} vs limit {_money(limits.max_loss_per_trade)}",
            )
        )
        daily_ok = account.day_pnl > -limits.max_daily_loss
        trip = not daily_ok
        checks.append(
            RiskCheck(
                "max_daily_loss",
                daily_ok,
                f"today's P&L {_money(account.day_pnl)} vs limit -{_money(limits.max_daily_loss)}"
                + ("" if daily_ok else " (breached: kill switch trips)"),
            )
        )
        after = account.open_positions + (1 if order.new_position else 0)
        checks.append(
            RiskCheck(
                "max_open_positions",
                after <= limits.max_open_positions,
                f"{after} open position(s) after this order vs limit {limits.max_open_positions}",
            )
        )
        size_cap = limits.max_position_pct * account.equity
        checks.append(
            RiskCheck(
                "max_position_size",
                order.position_value_after <= size_cap,
                f"{order.symbol} position {_money(order.position_value_after)} after this order vs "
                f"{limits.max_position_pct:.0%} of equity {_money(account.equity)} = "
                f"{_money(size_cap)}",
            )
        )
        cap = limits.max_capital_at_risk_pct * account.equity
        total = account.capital_at_risk + order.max_loss
        checks.append(
            RiskCheck(
                "max_capital_at_risk",
                total <= cap,
                f"capital at risk {_money(account.capital_at_risk)} + {_money(order.max_loss)} = "
                f"{_money(total)} vs {limits.max_capital_at_risk_pct:.0%} of equity "
                f"{_money(account.equity)} = {_money(cap)}",
            )
        )

    approved = all(c.passed for c in checks)
    return RiskDecision(approved=approved, checks=tuple(checks), trip_kill_switch=trip)
