"""The paper execution daemon: data sync + paper cycles for the optimizer's Active Bests.

Each tick it:
1. syncs daily equity bars from Alpaca market data once a session has closed (and on startup
   if data is stale), then re-checks the equity Active Best on the grown holdout window (one
   counted look; no new search, no budget reset). See ptl/data/daily_sync.py;
2. for the equity Active Best, only if VALIDATED, runs one paper cycle per completed session;
3. for the options Active Best, only if VALIDATED and while the market is open, opens at most
   one round of spreads per day and checks open spreads for take-profit/expiry closes;
4. polls paper orders for fills and reconciles spread status;
5. publishes a heartbeat with balance, positions, open spreads and risk-limit usage.

Every order goes through the hard-coded risk engine and is logged. Dry run is the default and
never sends anything. Paper mode talks only to the Alpaca PAPER endpoint, re-checked before
every order (ptl.safety); there is no live-trading code path.
"""

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ptl.agents import learning
from ptl.agents.learning import ActiveBest
from ptl.agents.optimizer import revalidate_active_best
from ptl.config import Settings
from ptl.data import daily_sync
from ptl.data.daily_sync import DailyBarSource
from ptl.data.live import QuoteSource
from ptl.data.option_chain import OptionChainSource
from ptl.db import connect, migrate
from ptl.market_calendar import NEW_YORK, MarketCalendar
from ptl.paper import store
from ptl.paper.broker import Broker
from ptl.paper.options_cycle import OptionsCycleRequest, reconcile_spreads, run_options_cycle
from ptl.paper.runner import (
    CycleRefusedError,
    CycleRequest,
    CycleResult,
    run_cycle,
    sync_orders,
)
from ptl.risk import store as risk_store
from ptl.safety import assert_paper_endpoint

DAEMON = "paper-runner"
OCC_MIN_LENGTH = 10  # equity tickers are shorter than OCC option symbols
Clock = Callable[[], datetime]


@dataclass(slots=True)
class RunnerState:
    attempted: set[tuple[int, date]] = field(default_factory=set)
    runner_for_best: dict[tuple[int, bool], int] = field(default_factory=dict)
    last_note: str = ""
    last_outcome: str = ""
    synced_through: date | None = None
    last_sync_try: datetime | None = None
    last_sync: dict[str, Any] = field(default_factory=dict)


def _runner_id(
    conn: sqlite3.Connection, best: ActiveBest, dataset_id: int, dry_run: bool, now: datetime
) -> int:
    for r in store.runners(conn, active_only=True):
        if (r.strategy, r.params, r.dataset_id, r.symbol, r.dry_run) == (
            best.strategy,
            best.params,
            dataset_id,
            best.symbol,
            dry_run,
        ):
            return r.id
    return store.create_runner(
        conn,
        now=now,
        strategy=best.strategy,
        params=best.params,
        dataset_id=dataset_id,
        symbol=best.symbol,
        dry_run=dry_run,
    )


def account_view(conn: sqlite3.Connection, broker: Broker, settings: Settings) -> dict[str, Any]:
    """Balance, positions, open spreads and how much of each risk limit is used. Read-only."""
    try:
        account = broker.account()
        positions = broker.positions()
    except Exception as exc:
        return {"error": f"No data: account unreachable ({type(exc).__name__})."}
    limits = risk_store.limits_from(settings)
    switch = risk_store.kill_switch(conn)
    stocks = [p for p in positions.values() if len(p.symbol) <= OCC_MIN_LENGTH]
    spreads = store.spreads(conn, statuses=("open", "closing"))
    spreads = [s for s in spreads if s.mode == broker.mode]
    at_risk = sum(abs(p.market_value) for p in stocks) + sum(s.collateral for s in spreads)
    largest = max(
        [abs(p.market_value) for p in stocks] + [s.collateral for s in spreads], default=0.0
    )
    day_pnl = account.equity - account.last_equity
    equity = account.equity or 1.0

    def use(value: float, cap: float) -> float:
        return value / cap if cap > 0 else 0.0

    open_count = sum(1 for p in stocks if p.qty) + len(spreads)
    return {
        "source": account.source,
        "equity": account.equity,
        "cash": account.cash,
        "day_pnl": day_pnl,
        "positions": [
            {"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value}
            for p in sorted(positions.values(), key=lambda p: p.symbol)
        ],
        "spreads": [
            {
                "label": s.label,
                "contracts": s.contracts,
                "credit": s.credit,
                "collateral": s.collateral,
                "status": s.status,
                "mode": s.mode,
            }
            for s in spreads
        ],
        "kill_switch": {"engaged": switch.engaged, "reason": switch.reason},
        "risk": [
            {
                "name": "Daily loss",
                "used": max(0.0, -day_pnl),
                "limit": limits.max_daily_loss,
                "utilization": use(max(0.0, -day_pnl), limits.max_daily_loss),
            },
            {
                "name": "Open positions",
                "used": float(open_count),
                "limit": float(limits.max_open_positions),
                "utilization": use(open_count, limits.max_open_positions),
            },
            {
                "name": "Capital at risk",
                "used": at_risk,
                "limit": limits.max_capital_at_risk_pct * equity,
                "utilization": use(at_risk, limits.max_capital_at_risk_pct * equity),
            },
            {
                "name": "Largest position",
                "used": largest,
                "limit": limits.max_position_pct * equity,
                "utilization": use(largest, limits.max_position_pct * equity),
            },
        ],
    }


def maybe_sync(  # noqa: PLR0913
    conn: sqlite3.Connection,
    state: RunnerState,
    *,
    settings: Settings,
    bars: DailyBarSource | None,
    calendar: MarketCalendar,
    clock: Clock,
) -> list[daily_sync.SymbolSync]:
    """Sync daily bars when a new session's bar should exist; retry on a timer until it does."""
    now = clock()
    if not settings.sync_enabled or bars is None:
        return []
    target = daily_sync.target_session(now, calendar, settings.sync_delay_minutes)
    if state.synced_through == target:
        return []
    if state.last_sync_try and now - state.last_sync_try < timedelta(
        minutes=settings.sync_retry_minutes
    ):
        return []
    state.last_sync_try = now
    results: list[daily_sync.SymbolSync] = []
    failures: list[str] = []
    for symbol in daily_sync.symbols_to_sync(conn, settings):
        try:
            results.append(
                daily_sync.sync_symbol(
                    conn, symbol, source=bars, settings=settings, calendar=calendar, now=now
                )
            )
        except Exception as exc:
            failures.append(f"{symbol} ({type(exc).__name__})")
    added = [r for r in results if r.added]
    current = all(r.through == target for r in results) and not failures
    if current:
        state.synced_through = target
    state.last_sync = {
        "at": now.isoformat(),
        "target_session": target.isoformat(),
        "symbols": len(results),
        "added_bars": sum(r.added for r in results),
        "current": current,
        "failures": failures,
    }
    if added or failures:
        parts = [f"{r.symbol} +{r.added} ({r.note}, through {r.through})" for r in added]
        refreshed = [r.symbol for r in results if r.adjusted_refresh]
        message = (
            f"Data sync ({bars.label}): "
            + ("; ".join(parts) if parts else "no new bars")
            + (
                f". Adjusted history refreshed after a corporate action: {', '.join(refreshed)}"
                if refreshed
                else ""
            )
            + (
                f". Failed: {', '.join(failures)}; retrying in {settings.sync_retry_minutes} min"
                if failures
                else ""
            )
            + ". The optimizer picks up new targets on its next pass."
        )
        learning.log(conn, DAEMON, message, now, "warn" if failures else "info", kind="data_sync")
    best = learning.active_best(conn, "equity")
    if best is not None:
        fresh = next((r for r in added if r.symbol == best.symbol and r.dataset_id), None)
        if fresh is not None and fresh.dataset_id is not None:
            try:
                revalidate_active_best(
                    conn,
                    dataset_id=fresh.dataset_id,
                    settings=settings,
                    calendar=calendar,
                    clock=clock,
                )
            except Exception as exc:
                learning.log(
                    conn, DAEMON, f"Re-validation failed: {exc!r}", now, "error", kind="error"
                )
    return results


def _kind(result: CycleResult) -> learning.Kind:
    if result.decision is not None and not result.decision.approved:
        return "kill_switch" if result.decision.trip_kill_switch else "risk"
    return "order" if result.order_id is not None else "info"


def _equity(  # noqa: PLR0913
    conn: sqlite3.Connection,
    state: RunnerState,
    best: ActiveBest,
    *,
    settings: Settings,
    broker: Broker,
    quotes: QuoteSource | None,
    calendar: MarketCalendar,
    now: datetime,
    note: Callable[..., None],
) -> None:
    session = calendar.last_completed_session(now)
    dataset_id = daily_sync.freshest_dataset(conn, best.symbol) or best.dataset_id
    key = (best.id, False)
    runner_id = state.runner_for_best.get(key) or _runner_id(
        conn, best, dataset_id, broker.mode == "dry_run", now
    )
    state.runner_for_best[key] = runner_id
    if store.last_session_for_runner(conn, runner_id) == session:
        note(f"Session {session} already handled for {best.strategy} on {best.symbol}.")
        return
    if (runner_id, session) in state.attempted:
        return
    state.attempted.add((runner_id, session))
    try:
        result = run_cycle(
            conn,
            CycleRequest(best.strategy, best.params, dataset_id, best.symbol, runner_id),
            settings=settings,
            broker=broker,
            quotes=quotes,
            calendar=calendar,
            now=now,
        )
        state.last_outcome = result.outcome
        note(
            f"Session {session}: {result.outcome} Rationale: {result.explanation}",
            kind=_kind(result),
        )
    except CycleRefusedError as exc:
        state.last_outcome = f"Refused: {exc}"
        note(f"Session {session}: cycle refused. {exc}", "warn")


def _options(  # noqa: PLR0913
    conn: sqlite3.Connection,
    state: RunnerState,
    best: ActiveBest,
    *,
    settings: Settings,
    broker: Broker,
    chain: OptionChainSource | None,
    calendar: MarketCalendar,
    now: datetime,
    note: Callable[..., None],
) -> None:
    if not calendar.is_open(now):
        return  # option orders only while the market is open
    today = now.astimezone(NEW_YORK).date()
    dataset_id = daily_sync.freshest_dataset(conn, best.symbol) or best.dataset_id
    key = (best.id, True)
    runner_id = state.runner_for_best.get(key) or _runner_id(
        conn, best, dataset_id, broker.mode == "dry_run", now
    )
    state.runner_for_best[key] = runner_id
    allow_open = (
        store.last_session_for_runner(conn, runner_id) != today
        and (
            runner_id,
            today,
        )
        not in state.attempted
    )
    has_open = bool(store.spreads(conn, statuses=("open",), runner_id=runner_id))
    if not allow_open and not has_open:
        return
    if allow_open:
        state.attempted.add((runner_id, today))
    try:
        results = run_options_cycle(
            conn,
            OptionsCycleRequest(best.strategy, best.params, dataset_id, best.symbol, runner_id),
            settings=settings,
            broker=broker,
            chain=chain,
            calendar=calendar,
            now=now,
            allow_open=allow_open,
        )
    except CycleRefusedError as exc:
        state.last_outcome = f"Options refused: {exc}"
        note(f"{today}: options cycle refused. {exc}", "warn")
        return
    for r in results:
        state.last_outcome = r.outcome
        note(f"{today} options: {r.outcome} Rationale: {r.explanation}", kind=_kind(r))


def tick(  # noqa: PLR0913
    conn: sqlite3.Connection,
    state: RunnerState,
    *,
    settings: Settings,
    broker: Broker,
    quotes: QuoteSource | None,
    calendar: MarketCalendar,
    now: datetime,
    chain: OptionChainSource | None = None,
    bars: DailyBarSource | None = None,
) -> dict[str, Any]:
    """One loop iteration. Returns the heartbeat detail."""

    def note(message: str, level: learning.Level = "info", kind: learning.Kind = "info") -> None:
        if message != state.last_note:  # don't repeat the same line every minute
            learning.log(conn, DAEMON, message, now, level, kind=kind)
            state.last_note = message

    maybe_sync(conn, state, settings=settings, bars=bars, calendar=calendar, clock=lambda: now)
    session = calendar.last_completed_session(now)
    bests = {asset: learning.active_best(conn, asset) for asset in ("equity", "options")}
    detail: dict[str, Any] = {
        "mode": broker.mode,
        "session": session.isoformat(),
        "sync": state.last_sync
        or ({"off": "no API keys or sync disabled"} if bars is None else {}),
        "active_best": None,
    }
    equity = bests["equity"]
    if equity is not None:
        detail["active_best"] = {
            "id": equity.id,
            "strategy": equity.strategy,
            "symbol": equity.symbol,
            "params": equity.params,
            "validated": equity.validated,
        }
    if equity is None and bests["options"] is None:
        note("Waiting: the optimizer has no Active Best yet.")
    for asset, best in bests.items():
        if best is None:
            continue
        if not best.validated:
            note(
                f"Waiting: the {asset} Active Best ({best.strategy} on {best.symbol}) failed its "
                "out-of-sample check, so it isn't paper traded."
            )
            continue
        if asset == "equity":
            _equity(
                conn,
                state,
                best,
                settings=settings,
                broker=broker,
                quotes=quotes,
                calendar=calendar,
                now=now,
                note=note,
            )
        else:
            _options(
                conn,
                state,
                best,
                settings=settings,
                broker=broker,
                chain=chain,
                calendar=calendar,
                now=now,
                note=note,
            )
    if broker.mode != "dry_run":
        try:
            changed = sync_orders(conn, broker, now)
            if changed:
                learning.log(
                    conn,
                    DAEMON,
                    f"{changed} paper order status change(s) logged.",
                    now,
                    kind="order",
                )
            for line in reconcile_spreads(conn, now):
                learning.log(conn, DAEMON, line, now, kind="order")
        except Exception as exc:
            learning.log(conn, DAEMON, f"Order sync failed: {exc!r}", now, "error", kind="error")
    detail["last_outcome"] = state.last_outcome
    detail["account"] = account_view(conn, broker, settings)
    return detail


def run_daemon(  # noqa: PLR0913
    settings: Settings,
    *,
    broker: Broker,
    quotes: QuoteSource | None,
    chain: OptionChainSource | None = None,
    bars: DailyBarSource | None = None,
    calendar: MarketCalendar | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    once: bool = False,
    stop: threading.Event | None = None,
) -> int:
    assert_paper_endpoint(settings.alpaca_base_url)
    calendar = calendar or MarketCalendar()
    stop = stop or threading.Event()
    conn = connect(settings.database_path)
    migrate(conn)
    started = clock()
    state = RunnerState()
    ticks = 0
    mode = "DRY RUN (nothing is sent)" if broker.mode == "dry_run" else "Alpaca PAPER account"
    sync = "on" if bars is not None and settings.sync_enabled else "off (no API keys or disabled)"
    learning.log(conn, DAEMON, f"Paper runner started: {mode}. Daily data sync {sync}.", started)
    try:
        while not stop.is_set():
            now = clock()
            try:
                detail = tick(
                    conn,
                    state,
                    settings=settings,
                    broker=broker,
                    quotes=quotes,
                    calendar=calendar,
                    now=now,
                    chain=chain,
                    bars=bars,
                )
                status = "running"
            except Exception as exc:
                detail = {"error": repr(exc)}
                status = "error"
                learning.log(conn, DAEMON, f"Tick failed: {exc!r}", now, "error", kind="error")
            learning.beat(conn, DAEMON, started_at=started, now=now, status=status, detail=detail)
            ticks += 1
            if once:
                break
            stop.wait(settings.runner_poll_seconds)
    finally:
        learning.beat(conn, DAEMON, started_at=started, now=clock(), status="stopped", detail={})
        learning.log(conn, DAEMON, "Paper runner stopped.", clock())
        conn.close()
    return ticks
