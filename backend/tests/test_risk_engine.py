"""Risk engine: every rule, with hand-computed numbers."""

from dataclasses import replace

from ptl.risk.engine import AccountSnapshot, ProposedOrder, RiskLimits, evaluate

LIMITS = RiskLimits(
    max_loss_per_trade=1_000,
    max_daily_loss=2_000,
    max_open_positions=3,
    max_capital_at_risk_pct=0.5,
)
ACCOUNT = AccountSnapshot(equity=10_000, day_pnl=-150, open_positions=1, capital_at_risk=2_000)
BUY = ProposedOrder(
    symbol="SPY",
    description="buy 1 SPY at ~600.00",
    opens_risk=True,
    max_loss=600,
    defined_risk=True,
    new_position=True,
)


def failed(
    order: ProposedOrder, account: AccountSnapshot = ACCOUNT, kill: bool = False
) -> list[str]:
    return [c.name for c in evaluate(order, account, LIMITS, kill).checks if not c.passed]


def test_approves_within_every_limit_and_shows_the_math() -> None:
    decision = evaluate(BUY, ACCOUNT, LIMITS, kill_switch=False)
    assert decision.approved
    details = {c.name: c.detail for c in decision.checks}
    assert details["max_loss_per_trade"] == "worst case $600.00 vs limit $1,000.00"
    assert details["max_daily_loss"] == "today's P&L $-150.00 vs limit -$2,000.00"
    assert details["max_open_positions"] == "2 open position(s) after this order vs limit 3"
    # 2,000 + 600 = 2,600 <= 50% x 10,000 = 5,000
    assert details["max_capital_at_risk"] == (
        "capital at risk $2,000.00 + $600.00 = $2,600.00 vs 50% of equity $10,000.00 = $5,000.00"
    )


def test_each_limit_rejects() -> None:
    assert failed(replace(BUY, max_loss=1_000.01)) == ["max_loss_per_trade"]
    assert failed(BUY, replace(ACCOUNT, open_positions=3)) == ["max_open_positions"]
    assert failed(replace(BUY, new_position=False), replace(ACCOUNT, open_positions=3)) == []
    assert failed(BUY, replace(ACCOUNT, capital_at_risk=4_500)) == ["max_capital_at_risk"]
    assert failed(replace(BUY, defined_risk=False)) == ["defined_risk"]


def test_daily_loss_breach_rejects_and_trips_the_kill_switch() -> None:
    decision = evaluate(BUY, replace(ACCOUNT, day_pnl=-2_000), LIMITS, kill_switch=False)
    assert not decision.approved
    assert decision.trip_kill_switch
    assert "breached: kill switch trips" in decision.summary


def test_kill_switch_blocks_new_risk_but_allows_closing() -> None:
    assert failed(BUY, kill=True) == ["kill_switch"]
    sell = replace(BUY, opens_risk=False, max_loss=0, new_position=False, description="sell 1")
    decision = evaluate(sell, replace(ACCOUNT, day_pnl=-5_000), LIMITS, kill_switch=True)
    assert decision.approved  # reducing risk is always allowed
    assert not decision.trip_kill_switch
