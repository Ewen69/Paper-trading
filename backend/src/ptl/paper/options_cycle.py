"""Options paper cycle: the Phase 2b strategy logic, on live quotes, sent as ONE multi-leg order.

Per call (market hours only):
1. Signals use the underlying's latest completed session (stale history is refused).
2. Open spreads from `paper_spreads` are priced to close at live quotes: buy the short back at
   the ask, sell the long at the bid. A spread within OPTIONS_CLOSE_DAYS_BEFORE_EXPIRY of
   expiration is closed to avoid assignment and pin risk. The strategy's take-profit rule runs
   on the same numbers as in the backtest.
3. If `allow_open`, the strategy picks a new vertical from the live chain. Credit = short bid -
   long ask (never mid). Collateral = width x 100 x contracts - credit x 100 x contracts.
   Contracts are sized to the tightest of the strategy's count, max loss per trade, max
   position size and capital-at-risk headroom, then the risk engine checks the order.
4. Both legs go as one mleg limit order: a credit we must receive at least (open), or a debit
   we pay at most (close). Dry run logs the same order and sends nothing.

Every step is logged: cycle (quote at submission and its source), risk decision, order, broker
status and the spread record.
"""

import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ptl.backtest import repository as bt_repo
from ptl.backtest.models import History
from ptl.backtest.service import to_price_bars
from ptl.config import Settings
from ptl.data.option_chain import ChainQuote, OptionChainSource
from ptl.market_calendar import NEW_YORK, MarketCalendar
from ptl.options.engine import CloseIntent, OpenIntent, OptionsView, PositionView
from ptl.options.models import Contract, Quote
from ptl.options.strategies import OPTIONS_STRATEGIES
from ptl.paper import store
from ptl.paper.broker import Broker, SpreadLeg, SpreadTicket
from ptl.paper.runner import CycleRefusedError, CycleResult
from ptl.risk import store as risk_store
from ptl.risk.engine import AccountSnapshot, ProposedOrder, evaluate

MULTIPLIER = 100
LEGS = 2
OCC_MIN_LENGTH = 10  # equity tickers are shorter than OCC option symbols


@dataclass(frozen=True, slots=True)
class OptionsCycleRequest:
    strategy_id: str
    params: dict[str, int]
    dataset_id: int  # the underlying's daily bars
    symbol: str
    runner_id: int | None = None


def _key(c: Contract) -> tuple[str, date, float]:
    return (c.root, c.expiration, round(c.strike, 4))


def run_options_cycle(  # noqa: PLR0912, PLR0913, PLR0915 - the recipe above, step by step
    conn: sqlite3.Connection,
    request: OptionsCycleRequest,
    *,
    settings: Settings,
    broker: Broker,
    chain: OptionChainSource | None,
    calendar: MarketCalendar,
    now: datetime,
    allow_open: bool,
) -> list[CycleResult]:
    spec = OPTIONS_STRATEGIES.get(request.strategy_id)
    if spec is None:
        raise CycleRefusedError(f"Unknown options strategy {request.strategy_id!r}.")
    try:
        strategy, params = spec.create(request.params)
    except ValueError as exc:
        raise CycleRefusedError(str(exc)) from exc
    if chain is None:
        raise CycleRefusedError(
            "Options paper trading needs live option quotes: set Alpaca paper API keys in .env."
        )
    if not calendar.is_open(now):
        raise CycleRefusedError("Option orders are only placed while the market is open.")

    symbol = request.symbol.strip().upper()
    bars = bt_repo.load_equity_bars(conn, request.dataset_id, symbol)
    last_session = calendar.last_completed_session(now)
    if not bars:
        raise CycleRefusedError(f"No data: dataset #{request.dataset_id} has no bars for {symbol}.")
    if bars[-1].session_date < last_session and allow_open:
        # Opening needs a current signal. Closing open spreads (take profit, expiry) doesn't,
        # so a stale history only blocks new positions.
        raise CycleRefusedError(
            f"{symbol} history ends {bars[-1].session_date}, but the last completed session is "
            f"{last_session}. Sync or import fresh bars first."
        )
    window = [b for b in bars if b.session_date <= last_session]
    price_bars, _ = to_price_bars(window)
    history = History(price_bars, [b.close for b in price_bars], len(price_bars) - 1)
    today = now.astimezone(NEW_YORK).date()
    mode = broker.mode
    results: list[CycleResult] = []

    def cycle(explanation: str, price: float | None, source: str, outcome: str) -> int:
        return store.record_cycle(
            conn,
            runner_id=request.runner_id,
            now=now,
            session=today,
            mode=mode,
            strategy=spec.id,
            params=params,
            symbol=symbol,
            explanation=explanation,
            target=None,
            price=price,
            price_source=source,
            current_qty=None,
            desired_qty=None,
            outcome=outcome,
        )

    # ---- open spreads: price them to close ----------------------------------------------
    open_spreads = [
        s
        for s in store.spreads(conn, statuses=("open",), runner_id=request.runner_id)
        if s.underlying == symbol
    ]
    leg_quotes = chain.quotes(
        symbol, [x for s in open_spreads for x in (s.short_symbol, s.long_symbol)]
    )
    positions: list[PositionView] = []
    by_id = {s.id: s for s in open_spreads}
    forced: list[CloseIntent] = []
    for s in open_spreads:
        short, long_ = leg_quotes.get(s.short_symbol), leg_quotes.get(s.long_symbol)
        close_value = (
            None
            if short is None or long_ is None or short.ask <= 0
            else (long_.bid - short.ask) * MULTIPLIER * s.contracts
        )
        positions.append(
            PositionView(
                id=s.id,
                legs=(
                    (short.contract if short else _contract(s, short=True), -s.contracts),
                    (long_.contract if long_ else _contract(s, short=False), s.contracts),
                ),
                structure="credit_vertical",
                contracts=s.contracts,
                opened=s.created_at.astimezone(NEW_YORK).date(),
                entry_premium=s.credit * MULTIPLIER * s.contracts,
                close_value=close_value,
            )
        )
        days_left = (s.expiration - today).days
        if days_left <= settings.options_close_days_before_expiry:
            forced.append(
                CloseIntent(
                    s.id,
                    f"{days_left} day(s) to expiration <= "
                    f"{settings.options_close_days_before_expiry}: closing early to avoid "
                    "assignment and pin risk.",
                )
            )

    account = broker.account()
    held = broker.positions()
    stocks = [p for p in held.values() if len(p.symbol) <= OCC_MIN_LENGTH]
    equity_notional = sum(abs(p.market_value) for p in stocks)
    all_open = store.spreads(conn, statuses=("open", "closing"))
    snapshot = AccountSnapshot(
        equity=account.equity,
        day_pnl=account.equity - account.last_equity,
        open_positions=sum(1 for p in stocks if p.qty) + len(all_open),
        capital_at_risk=equity_notional + sum(s.collateral for s in all_open),
    )

    # ---- the live chain, as the strategy sees it -----------------------------------------
    chain_quotes: list[ChainQuote] = []
    if allow_open:
        chain_quotes = chain.put_chain(symbol, today, today + timedelta(days=120))
    by_contract = {_key(q.contract): q for q in chain_quotes}
    view = OptionsView(
        day=today,
        history=history,
        chain={q.contract.key: Quote(q.contract, today, q.bid, q.ask) for q in chain_quotes},
        positions=tuple(positions),
        available_cash=account.cash,
    )
    intents = list(forced) + [
        i
        for i in strategy.decide(view)
        if not isinstance(i, CloseIntent) or i.position_id not in {f.position_id for f in forced}
    ]
    if not allow_open:
        intents = [i for i in intents if isinstance(i, CloseIntent)]

    limits = risk_store.limits_from(settings)
    switch = risk_store.kill_switch(conn)
    commission = LEGS * settings.options_commission_per_contract

    for intent in intents:
        if isinstance(intent, CloseIntent):
            s = by_id[intent.position_id]
            short, long_ = leg_quotes.get(s.short_symbol), leg_quotes.get(s.long_symbol)
            if short is None or long_ is None or short.ask <= 0:
                outcome = f"Can't close {s.label}: no live quote for both legs."
                results.append(
                    CycleResult(
                        cycle(intent.reason, None, chain.label, outcome),
                        today,
                        mode,
                        outcome,
                        intent.reason,
                        None,
                        None,
                    )
                )
                continue
            debit = short.ask - long_.bid
            source = (
                f"{chain.label}: buy {s.short_symbol} at ask {short.ask:.2f} - sell "
                f"{s.long_symbol} at bid {long_.bid:.2f} = debit {debit:.2f}/share"
            )
            proposed = ProposedOrder(
                symbol=s.label,
                description=f"close {s.contracts} x {s.label} for a debit of {debit:.2f}",
                opens_risk=False,
                max_loss=0.0,
                defined_risk=True,
                new_position=False,
            )
            decision = evaluate(proposed, snapshot, limits, switch.engaged)
            decision_id = risk_store.record_decision(
                conn, now=now, source=f"paper runner ({mode})", order=proposed, decision=decision
            )
            sent = broker.submit_spread(
                SpreadTicket(
                    legs=(SpreadLeg(s.short_symbol, "buy"), SpreadLeg(s.long_symbol, "sell")),
                    contracts=s.contracts,
                    net_limit=debit,
                    opening=False,
                )
            )
            verb = "Would close (dry run)" if mode == "dry_run" else "Close sent to Alpaca paper"
            outcome = f"{verb}: {proposed.description}. Broker status: {sent.status}."
            cycle_id = cycle(intent.reason, debit, source, outcome)
            order_id = store.record_order(
                conn,
                cycle_id=cycle_id,
                now=now,
                mode=mode,
                symbol=s.label,
                side="buy",
                qty=float(s.contracts),
                reason=intent.reason,
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
            if mode == "dry_run":
                store.update_spread(
                    conn,
                    s.id,
                    status="closed",
                    close_order_id=order_id,
                    closed_at=now,
                    close_debit=debit,
                    note="Dry run: assumed closed at the quoted debit.",
                )
            else:
                store.update_spread(conn, s.id, status="closing", close_order_id=order_id)
            results.append(
                CycleResult(cycle_id, today, mode, outcome, intent.reason, order_id, decision)
            )
            continue

        assert isinstance(intent, OpenIntent)  # noqa: S101 - the only other intent type
        short_leg = next(leg for leg in intent.legs if leg.ratio == -1)
        long_leg = next(leg for leg in intent.legs if leg.ratio == 1)
        short = by_contract.get(_key(short_leg.contract))
        long_ = by_contract.get(_key(long_leg.contract))
        if short is None or long_ is None:
            continue
        credit = short.bid - long_.ask
        width = short.contract.strike - long_.contract.strike
        strikes = f"{short.contract.strike:g}/{long_.contract.strike:g}P"
        label = f"{symbol} {short.contract.expiration} {strikes}"
        source = (
            f"{chain.label}: sell {short.symbol} at bid {short.bid:.2f} - buy {long_.symbol} at "
            f"ask {long_.ask:.2f} = credit {credit:.2f}/share"
        )
        if credit <= 0 or width <= 0:
            outcome = f"No order: {label} offers no credit at the bid/ask ({credit:.2f})."
            results.append(
                CycleResult(
                    cycle(intent.reason, credit, source, outcome),
                    today,
                    mode,
                    outcome,
                    intent.reason,
                    None,
                    None,
                )
            )
            continue
        per_spread = (width - credit) * MULTIPLIER  # collateral = max loss per spread
        per_spread_all_in = per_spread + commission
        caps = [
            ("max loss per trade", limits.max_loss_per_trade),
            ("max position size", limits.max_position_pct * account.equity),
            (
                "capital-at-risk headroom",
                max(
                    0.0, limits.max_capital_at_risk_pct * account.equity - snapshot.capital_at_risk
                ),
            ),
        ]
        binding, budget = min(caps, key=lambda c: c[1])
        contracts = min(intent.contracts, math.floor(budget / per_spread_all_in))
        sizing = (
            f"Collateral per spread = width {width:g} x 100 - credit {credit:.2f} x 100 = "
            f"${per_spread:,.2f} (+ ${commission:.2f} commissions). Strategy wants "
            f"{intent.contracts}; sized by {binding} (${budget:,.2f}) = {contracts}."
        )
        explanation = f"{intent.reason} {sizing}"
        if contracts <= 0:
            outcome = (
                f"No order: one {label} needs ${per_spread_all_in:,.2f}, more than the "
                f"{binding} allows."
            )
            results.append(
                CycleResult(
                    cycle(explanation, credit, source, outcome),
                    today,
                    mode,
                    outcome,
                    explanation,
                    None,
                    None,
                )
            )
            continue
        collateral = per_spread * contracts
        proposed = ProposedOrder(
            symbol=label,
            description=f"sell {contracts} x {label} for a credit of {credit:.2f}",
            opens_risk=True,
            max_loss=collateral + commission * contracts,
            defined_risk=True,  # a vertical: the long leg caps the loss
            new_position=True,
            position_value_after=collateral,
        )
        decision = evaluate(proposed, snapshot, limits, switch.engaged)
        decision_id = risk_store.record_decision(
            conn, now=now, source=f"paper runner ({mode})", order=proposed, decision=decision
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
            results.append(
                CycleResult(
                    cycle(explanation, credit, source, outcome),
                    today,
                    mode,
                    outcome,
                    explanation,
                    None,
                    decision,
                )
            )
            continue
        sent = broker.submit_spread(
            SpreadTicket(
                legs=(SpreadLeg(short.symbol, "sell"), SpreadLeg(long_.symbol, "buy")),
                contracts=contracts,
                net_limit=-credit,
                opening=True,
            )
        )
        verb = "Would submit (dry run)" if mode == "dry_run" else "Submitted to Alpaca paper"
        outcome = (
            f"{verb}: {proposed.description} (collateral ${collateral:,.2f}). "
            f"Broker status: {sent.status}."
        )
        cycle_id = cycle(explanation, credit, source, outcome)
        order_id = store.record_order(
            conn,
            cycle_id=cycle_id,
            now=now,
            mode=mode,
            symbol=label,
            side="sell",
            qty=float(contracts),
            reason=explanation,
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
        store.create_spread(
            conn,
            now=now,
            runner_id=request.runner_id,
            cycle_id=cycle_id,
            mode=mode,
            underlying=symbol,
            expiration=short.contract.expiration,
            option_type="put",
            short_symbol=short.symbol,
            long_symbol=long_.symbol,
            short_strike=short.contract.strike,
            long_strike=long_.contract.strike,
            contracts=contracts,
            credit=credit,
            collateral=collateral,
            open_order_id=order_id,
            note="Dry run: assumed filled at the quoted credit." if mode == "dry_run" else "",
        )
        snapshot = AccountSnapshot(
            equity=snapshot.equity,
            day_pnl=snapshot.day_pnl,
            open_positions=snapshot.open_positions + 1,
            capital_at_risk=snapshot.capital_at_risk + collateral,
        )
        results.append(CycleResult(cycle_id, today, mode, outcome, explanation, order_id, decision))

    if allow_open and not results:
        outcome = "No order: the strategy found nothing to open or close."
        results.append(
            CycleResult(
                cycle("No signal.", None, chain.label, outcome),
                today,
                mode,
                outcome,
                "No signal.",
                None,
                None,
            )
        )
    return results


def _contract(s: store.SpreadRecord, *, short: bool) -> Contract:
    return Contract(
        underlying=s.underlying,
        root=s.underlying,
        expiration=s.expiration,
        strike=s.short_strike if short else s.long_strike,
        option_type="put" if s.option_type == "put" else "call",
        style="american",
    )


def reconcile_spreads(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """Update paper (not dry-run) spreads from their orders' latest statuses. Returns notes."""
    notes: list[str] = []
    final_unfilled = {"canceled", "expired", "rejected"}
    for s in store.spreads(conn, statuses=("open", "closing")):
        if s.mode != "paper":
            continue
        if s.status == "open":
            status = store.latest_status(conn, s.open_order_id)
            if status in final_unfilled:
                store.update_spread(conn, s.id, status="void", note=f"Opening order {status}.")
                notes.append(f"{s.label}: opening order {status}; no position.")
        elif s.close_order_id is not None:
            status = store.latest_status(conn, s.close_order_id)
            if status == "filled":
                store.update_spread(
                    conn,
                    s.id,
                    status="closed",
                    closed_at=now,
                    close_debit=store.last_fill(conn, s.close_order_id),
                )
                notes.append(f"{s.label}: closed.")
            elif status in final_unfilled:
                store.update_spread(
                    conn, s.id, status="open", note=f"Closing order {status}; still open."
                )
                notes.append(f"{s.label}: closing order {status}; still open.")
    return notes


def option_symbols(spreads: Sequence[store.SpreadRecord]) -> set[str]:
    return {x for s in spreads for x in (s.short_symbol, s.long_symbol)}
