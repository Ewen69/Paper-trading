"""One paper-trading cycle: signal -> size -> risk engine -> broker (or dry run) -> log.

Rules:
- Equity strategies only for now (options paper orders are not built yet).
- Signals must use the latest completed session: if the imported history is behind, the cycle
  is refused rather than trading on a stale signal.
- Sizing uses whole shares at the live ask (conservative for buys). In dry-run without a live
  quote, the last close is used and labeled as such; paper mode requires a live quote.
- Every cycle, risk decision, order and broker response is written to SQLite.
"""

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

from ptl.backtest import repository as bt_repo
from ptl.backtest.models import History
from ptl.backtest.service import to_price_bars
from ptl.config import Settings
from ptl.data.live import QuoteSource, QuoteSourceError
from ptl.market_calendar import MarketCalendar
from ptl.paper import store
from ptl.paper.broker import Broker, OrderTicket
from ptl.risk import store as risk_store
from ptl.risk.engine import AccountSnapshot, ProposedOrder, RiskDecision, evaluate
from ptl.strategy.base import equity_strategies, option_strategies


class CycleRefusedError(Exception):
    """The cycle can't run honestly (stale data, no quote, unknown strategy). Message says why."""


@dataclass(frozen=True, slots=True)
class CycleRequest:
    strategy_id: str
    params: dict[str, int]
    dataset_id: int
    symbol: str
    runner_id: int | None = None


@dataclass(frozen=True, slots=True)
class CycleResult:
    cycle_id: int
    session: date
    mode: str
    outcome: str
    explanation: str
    order_id: int | None
    decision: RiskDecision | None


def run_cycle(  # noqa: PLR0913, PLR0915 - one linear recipe; each step is logged
    conn: sqlite3.Connection,
    request: CycleRequest,
    *,
    settings: Settings,
    broker: Broker,
    quotes: QuoteSource | None,
    calendar: MarketCalendar,
    now: datetime,
) -> CycleResult:
    if request.strategy_id in option_strategies():
        raise CycleRefusedError(
            "Options paper trading isn't built yet; the paper runner supports equity strategies."
        )
    cls = equity_strategies().get(request.strategy_id)
    if cls is None:
        raise CycleRefusedError(f"Unknown strategy {request.strategy_id!r}.")
    try:
        strategy, params = cls.create(request.params)
    except ValueError as exc:
        raise CycleRefusedError(str(exc)) from exc

    symbol = request.symbol.strip().upper()
    bars = bt_repo.load_equity_bars(conn, request.dataset_id, symbol)
    if not bars:
        raise CycleRefusedError(f"No data: dataset #{request.dataset_id} has no bars for {symbol}.")
    session = calendar.last_completed_session(now)
    if bars[-1].session_date < session:
        raise CycleRefusedError(
            f"{symbol} history ends {bars[-1].session_date}, but the last completed session is "
            f"{session}. Import fresh bars first: signals must use the latest close."
        )
    window = [b for b in bars if b.session_date <= session]
    price_bars, _basis = to_price_bars(window)
    history = History(price_bars, [b.close for b in price_bars], len(price_bars) - 1)
    target = min(1.0, max(0.0, strategy.target_exposure(history)))
    explanation = strategy.explain(history)

    account = broker.account()
    positions = broker.positions()
    current = positions[symbol].qty if symbol in positions else 0.0

    try:
        if quotes is None:
            raise QuoteSourceError("no quote source")
        quote = quotes.latest_stock_quote(symbol)
        price, price_source = (
            quote.ask,
            f"live ask {quote.ask:,.2f} ({quote.timestamp:%Y-%m-%d %H:%M}Z)",
        )
        if price <= 0:
            raise QuoteSourceError("no ask in the live quote")
    except QuoteSourceError as exc:
        if broker.mode == "paper":
            raise CycleRefusedError(f"No live quote for {symbol}: {exc}") from exc
        price = window[-1].close
        price_source = f"last close {window[-1].session_date} (no live quote; dry run)"

    desired = float(math.floor(target * account.equity / price))
    delta = desired - current
    sizing = (
        f"Target {target:.0%} of equity ${account.equity:,.2f} ({account.source}) at "
        f"{price:,.2f} = {desired:g} shares; holding {current:g}."
    )

    def cycle(outcome: str) -> int:
        return store.record_cycle(
            conn,
            runner_id=request.runner_id,
            now=now,
            session=session,
            mode=broker.mode,
            strategy=cls.id,
            params=params,
            symbol=symbol,
            explanation=f"{explanation} {sizing}",
            target=target,
            price=price,
            price_source=price_source,
            current_qty=current,
            desired_qty=desired,
            outcome=outcome,
        )

    if delta == 0:
        outcome = f"No order: already holding the target {current:g} shares."
        return CycleResult(cycle(outcome), session, broker.mode, outcome, explanation, None, None)

    side: str = "buy" if delta > 0 else "sell"
    qty = abs(delta)
    proposed = ProposedOrder(
        symbol=symbol,
        description=f"{side} {qty:g} {symbol} at ~{price:,.2f}",
        opens_risk=side == "buy",
        max_loss=qty * price if side == "buy" else 0.0,  # long stock, no stop: notional
        defined_risk=True,  # long only; short stock is never proposed
        new_position=current == 0 and side == "buy",
    )
    snapshot = AccountSnapshot(
        equity=account.equity,
        day_pnl=account.equity - account.last_equity,
        open_positions=sum(1 for p in positions.values() if p.qty != 0),
        capital_at_risk=sum(abs(p.market_value) for p in positions.values()),
    )
    switch = risk_store.kill_switch(conn)
    decision = evaluate(proposed, snapshot, risk_store.limits_from(settings), switch.engaged)
    decision_id = risk_store.record_decision(
        conn, now=now, source=f"paper runner ({broker.mode})", order=proposed, decision=decision
    )
    if decision.trip_kill_switch and not switch.engaged:
        risk_store.set_kill_switch(
            conn,
            True,
            f"Tripped automatically: today's P&L ${snapshot.day_pnl:,.2f} breached the daily "
            f"loss limit ${settings.risk_max_daily_loss:,.2f}.",
            now,
        )
    if not decision.approved:
        outcome = f"Order {proposed.description} blocked by the risk engine. {decision.summary}"
        return CycleResult(
            cycle(outcome), session, broker.mode, outcome, explanation, None, decision
        )

    reason = f"{explanation} {sizing} -> {side} {qty:g}."
    ticket = OrderTicket(symbol=symbol, side="buy" if side == "buy" else "sell", qty=qty)
    sent = broker.submit(ticket)
    verb = "Would submit (dry run)" if broker.mode == "dry_run" else "Submitted to Alpaca paper"
    outcome = f"{verb}: {proposed.description}. Broker status: {sent.status}."
    cycle_id = cycle(outcome)
    order_id = store.record_order(
        conn,
        cycle_id=cycle_id,
        now=now,
        mode=broker.mode,
        symbol=symbol,
        side=ticket.side,
        qty=qty,
        reason=reason,
        risk_decision_id=decision_id,
        broker_order_id=sent.id,
        status=sent.status,
    )
    store.record_event(
        conn,
        order_id=order_id,
        now=now,
        status=sent.status,
        filled_qty=sent.filled_qty,
        fill_price=sent.fill_price,
        detail=verb,
    )
    return CycleResult(cycle_id, session, broker.mode, outcome, explanation, order_id, decision)


def sync_orders(conn: sqlite3.Connection, broker: Broker, now: datetime) -> int:
    """Poll the broker for paper orders that aren't final; log every status change."""
    changed = 0
    for order in store.open_paper_orders(conn):
        if order.broker_order_id is None:
            continue
        status = broker.order_status(order.broker_order_id)
        if status.status != store.latest_status(conn, order.id):
            store.record_event(
                conn,
                order_id=order.id,
                now=now,
                status=status.status,
                filled_qty=status.filled_qty,
                fill_price=status.fill_price,
                detail="status update from Alpaca paper",
            )
            changed += 1
    return changed
