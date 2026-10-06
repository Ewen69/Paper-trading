"""The learning optimizer: a background genetic search over strategy parameters.

How it stays honest (see docs/OPTIMIZER.md):
- Every candidate is a real backtest on IN-SAMPLE data from local SQLite, logged in the
  append-only backtest run log (so the trial count and the Auditor see it) and in
  `agent_learning_log`. A combination already tried is reused, never re-run.
- Fitness is the in-sample Sharpe; candidates with fewer than OPTIMIZER_MIN_TRADES trades are
  rejected. Each target (strategy x symbol) has a hard budget of distinct in-sample trials.
- At the end of a search, the top 5% by in-sample fitness of EVERY trial ever logged for that
  target each get ONE counted out-of-sample test. A parameter set already tested out-of-sample
  is never re-tested, so new holdout looks happen only when the top set itself changes.
- The Active Best is chosen by in-sample fitness only. Its own out-of-sample result is used as a
  pass/fail label ("validated"), never to rank. Out-of-sample numbers never feed the search.

Run: `python -m ptl.agents.optimizer [--once] [--seed N]` (or via the orchestrator).
"""

import argparse
import math
import random
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from ptl.agents import learning
from ptl.agents.learning import Asset, Trial
from ptl.agents.work import JobError, SweepSpec, _run
from ptl.backtest import repository as bt_repo
from ptl.backtest import service as eq
from ptl.backtest.repository import Period, canonical_json
from ptl.config import Settings
from ptl.db import connect, migrate
from ptl.market_calendar import MarketCalendar
from ptl.options.chain import option_universe
from ptl.safety import assert_paper_endpoint
from ptl.strategy.base import REGISTRY, Strategy, equity_strategies, option_strategies

DAEMON = "optimizer"
TOP_FRACTION = 0.05
MIN_SESSIONS = 120  # the minimum history to lock an out-of-sample period
CURVE_POINTS = 160
MIN_PARENTS = 2
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class Target:
    asset: Asset
    strategy: str
    symbol: str
    dataset_id: int
    options_dataset_id: int | None = None

    @property
    def label(self) -> str:
        return f"{self.strategy} on {self.symbol} (dataset #{self.dataset_id})"

    def spec(self) -> SweepSpec:
        return SweepSpec(
            self.asset, self.strategy, self.symbol, self.dataset_id, self.options_dataset_id
        )


def targets(conn: sqlite3.Connection) -> list[Target]:
    """Every strategy with a search space x every symbol with enough imported history."""
    out: list[Target] = []
    equity = bt_repo.equity_universe(conn)
    newest: dict[str, bt_repo.UniverseEntry] = {}
    for e in equity:  # one dataset per symbol: the one with the newest data
        if e.sessions >= MIN_SESSIONS and (
            e.symbol not in newest or e.last_date > newest[e.symbol].last_date
        ):
            newest[e.symbol] = e
    for sid, cls in sorted(equity_strategies().items()):
        if cls.space():
            out.extend(Target("equity", sid, e.symbol, e.dataset_id) for e in newest.values())
    for option in option_universe(conn):
        bars = newest.get(option.underlying)
        if bars is None or option.rows_missing_style:
            continue
        for sid, ocls in sorted(option_strategies().items()):
            if ocls.space():
                out.append(
                    Target("options", sid, option.underlying, bars.dataset_id, option.dataset_id)
                )
    return out


def tried(conn: sqlite3.Connection, target: Target) -> int:
    counts = bt_repo.run_counts(conn, target.symbol, target.strategy)
    return counts.in_sample_combinations_this_strategy


def tried_params(conn: sqlite3.Connection, target: Target) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT params FROM backtest_runs WHERE symbol = ? AND strategy = ? "
        "AND period = 'in-sample'",
        (target.symbol, target.strategy),
    )
    return {str(r["params"]) for r in rows}


def _fmt(params: Mapping[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(params.items()))


# ---- the genetic search ---------------------------------------------------------------------


@dataclass(slots=True)
class Search:
    conn: sqlite3.Connection
    target: Target
    settings: Settings
    calendar: MarketCalendar
    clock: Clock
    rng: random.Random
    search_id: str
    beat: Callable[[dict[str, Any]], None]
    budget: int
    evaluated: dict[str, Trial] = field(default_factory=dict)
    known: set[str] = field(default_factory=set)  # params already in the run log (in-sample)
    fresh: int = 0
    generation: int = 0

    @property
    def cls(self) -> type[Strategy]:
        return REGISTRY[self.target.strategy]

    def valid(self, genes: Mapping[str, int]) -> dict[str, int] | None:
        try:
            _, resolved = self.cls.create(dict(genes))
        except ValueError:
            return None
        return resolved

    def random_genes(self) -> dict[str, int]:
        return {k: self.rng.randint(lo, hi) for k, (lo, hi) in self.cls.space().items()}

    def mutate(self, genes: Mapping[str, int]) -> dict[str, int]:
        out = dict(genes)
        for k, (lo, hi) in self.cls.space().items():
            if self.rng.random() < 0.35:  # noqa: PLR2004
                step = round(self.rng.gauss(0, max(1.0, 0.15 * (hi - lo))))
                out[k] = min(hi, max(lo, out.get(k, lo) + step))
        return out

    def crossover(self, a: Mapping[str, int], b: Mapping[str, int]) -> dict[str, int]:
        return {k: a[k] if self.rng.random() < 0.5 else b[k] for k in self.cls.space()}  # noqa: PLR2004

    def novel(self, make: Callable[[], dict[str, int]]) -> dict[str, int] | None:
        for _ in range(60):
            params = self.valid(make())
            key = None if params is None else canonical_json(params)
            if params is not None and key not in self.evaluated and key not in self.known:
                return params
        return None

    def fitness(self, summary: Mapping[str, Any]) -> tuple[float | None, str]:
        trades = summary.get("trades")
        sharpe = summary.get("sharpe")
        if trades is None or int(trades) < self.settings.optimizer_min_trades:
            return None, f"rejected: {trades or 0} trades < {self.settings.optimizer_min_trades}"
        if sharpe is None:
            return None, "rejected: no Sharpe (flat returns)"
        return float(sharpe), "evaluated"

    def evaluate(self, params: dict[str, int], period: Period) -> Trial:
        key = canonical_json(params)
        spec = self.target.spec()
        existing = bt_repo.find_run(
            self.conn, self.target.symbol, self.target.strategy, params, period
        )
        if existing is not None:
            run_id, summary, reused = existing.id, existing.summary, True
        else:
            run_id, summary = _run(
                self.conn, spec, params, period, calendar=self.calendar, clock=self.clock
            )
            reused = False
            if period == "in-sample":
                self.fresh += 1
        fit, verdict = self.fitness(summary)
        if period == "out-of-sample":
            fit = None  # out-of-sample results never become fitness
            verdict = "holdout look (counted)" if not reused else "holdout already looked at"
        trial = learning.log_trial(
            self.conn,
            now=self.clock(),
            search_id=self.search_id,
            generation=self.generation,
            asset=self.target.asset,
            strategy=self.target.strategy,
            symbol=self.target.symbol,
            dataset_id=self.target.dataset_id,
            options_dataset_id=self.target.options_dataset_id,
            params=params,
            period=period,
            run_id=run_id,
            reused=reused,
            summary=summary,
            fitness=fit,
            verdict=verdict,
            note=(
                "Reused an existing run (not a new trial)." if reused else "New backtest, logged."
            ),
        )
        if period == "in-sample":
            self.evaluated[key] = trial
        self.beat(
            {
                "state": "searching",
                "search_id": self.search_id,
                "target": self.target.label,
                "generation": self.generation,
                "trial": {"params": params, "period": period, "verdict": verdict},
                "fresh_trials": self.fresh,
                "budget": self.budget,
                "best": self.best_summary(),
            }
        )
        return trial

    def ranked(self) -> list[Trial]:
        scored = [t for t in self.evaluated.values() if t.fitness is not None]
        return sorted(scored, key=lambda t: t.fitness or 0.0, reverse=True)

    def best_summary(self) -> dict[str, Any] | None:
        ranked = self.ranked()
        if not ranked:
            return None
        b = ranked[0]
        return {
            "params": b.params,
            "fitness": b.fitness,
            "win_rate": b.win_rate,
            "trades": b.trades,
        }

    def breed(self, pool: list[Trial]) -> dict[str, int]:
        return self.mutate(self.crossover(self.tournament(pool), self.tournament(pool)))

    def tournament(self, pool: list[Trial]) -> dict[str, int]:
        picks = self.rng.sample(pool, min(3, len(pool)))
        return max(picks, key=lambda t: t.fitness or -math.inf).params


def run_search(  # noqa: PLR0912, PLR0913, PLR0915 - the search recipe, step by step
    conn: sqlite3.Connection,
    target: Target,
    *,
    settings: Settings,
    calendar: MarketCalendar,
    clock: Clock,
    seed: int,
    beat: Callable[[dict[str, Any]], None],
    stop: threading.Event | None = None,
) -> learning.ActiveBest | None:
    budget = settings.optimizer_max_trials_per_target - tried(conn, target)
    search_id = f"s-{uuid.uuid4().hex[:8]}"
    s = Search(
        conn=conn,
        target=target,
        settings=settings,
        calendar=calendar,
        clock=clock,
        rng=random.Random(seed),  # noqa: S311 - search heuristics, not security
        search_id=search_id,
        beat=beat,
        budget=budget,
    )
    if not s.cls.space() or budget <= 0:
        return None

    def say(message: str, level: learning.Level = "info") -> None:
        learning.log(conn, DAEMON, message, clock(), level)

    say(
        f"Search {search_id}: {target.label}, seed {seed}. Budget: {budget} new in-sample "
        f"trial(s) left of {settings.optimizer_max_trials_per_target}; fitness = in-sample "
        f"Sharpe with at least {settings.optimizer_min_trades} trades."
    )

    s.known = tried_params(conn, target)
    # Carry learning across searches: the two best parameter sets so far (reused, not re-run)
    # seed the population; everything else must be untried.
    population: list[dict[str, int]] = [
        t.params for t in learning.scored_in_sample(conn, target.strategy, target.symbol)[:2]
    ]
    while len(population) < settings.optimizer_population:
        genes = s.novel(s.random_genes)
        if genes is None or genes in population:
            break
        population.append(genes)

    for generation in range(1, settings.optimizer_generations + 1):
        s.generation = generation
        for params in population:
            if stop is not None and stop.is_set():
                say(f"Search {search_id} stopped early.", "warn")
                return None
            if s.fresh >= budget:
                break
            if canonical_json(params) in s.evaluated:
                continue
            try:
                s.evaluate(params, "in-sample")
            except JobError as exc:
                say(f"Search {search_id}: {target.label} can't run: {exc}", "error")
                return None
        ranked = s.ranked()
        rejected = sum(1 for t in s.evaluated.values() if t.fitness is None)
        if ranked:
            top = ranked[0]
            say(
                f"Gen {generation}: best in-sample Sharpe {top.fitness:.2f} ({_fmt(top.params)}), "
                f"win rate {_pct(top.win_rate)}, {top.trades} trades. {len(s.evaluated)} "
                f"evaluated, {rejected} rejected; {s.fresh}/{budget} new trials used."
            )
        else:
            say(f"Gen {generation}: no candidate has {settings.optimizer_min_trades}+ trades yet.")
        if s.fresh >= budget:
            say(f"Search {search_id}: trial budget reached for {target.label}.")
            break
        # Next generation: two elites, then tournament children (crossover + mutation).
        pool = ranked or list(s.evaluated.values())
        nxt: list[dict[str, int]] = [t.params for t in ranked[:2]]
        while len(nxt) < settings.optimizer_population:
            make = partial(s.breed, pool) if len(pool) >= MIN_PARENTS else s.random_genes
            child = s.novel(make)
            if child is None:
                break
            nxt.append(child)
        population = nxt

    ranked = learning.scored_in_sample(conn, target.strategy, target.symbol)
    if not ranked:
        say(
            f"Search {search_id}: no candidate met the minimum trade count; nothing promoted.",
            "warn",
        )
        return None

    # Top 5% (at least one) each get one counted out-of-sample test.
    top_n = max(1, math.ceil(TOP_FRACTION * len(ranked)))
    oos: dict[str, Trial] = {}
    s.generation = settings.optimizer_generations + 1
    for t in ranked[:top_n]:
        if bt_repo.find_run(conn, target.symbol, target.strategy, t.params, "out-of-sample"):
            previous = learning.oos_trial(conn, target.strategy, target.symbol, t.params)
            if previous is not None:
                oos[canonical_json(t.params)] = previous
            continue  # looked at once already; never again
        try:
            oos[canonical_json(t.params)] = s.evaluate(t.params, "out-of-sample")
        except JobError as exc:
            say(f"Out-of-sample test failed for {_fmt(t.params)}: {exc}", "error")
    looks = bt_repo.run_counts(conn, target.symbol, target.strategy).oos_evaluations
    say(
        f"Search {search_id}: top {top_n} of {len(ranked)} scored candidates went to the "
        f"out-of-sample window ({looks} out-of-sample evaluation(s) on {target.symbol} so far; "
        "every look is counted)."
    )

    candidate = ranked[0]
    holdout = oos.get(canonical_json(candidate.params))
    min_oos = max(3, settings.optimizer_min_trades // 3)
    validated = bool(
        holdout is not None
        and holdout.trades is not None
        and holdout.trades >= min_oos
        and holdout.sharpe is not None
        and holdout.sharpe > 0
    )
    current = learning.active_best(conn, target.asset)
    better = current is None or (validated, candidate.fitness or 0.0) > (
        current.validated,
        current.in_sample_fitness,
    )
    if not better:
        say(
            f"Search {search_id}: finalist ({_fmt(candidate.params)}, in-sample Sharpe "
            f"{candidate.fitness:.2f}) doesn't beat the Active Best "
            f"({current.strategy if current else '-'} on {current.symbol if current else '-'}, "
            f"{current.in_sample_fitness if current else 0:.2f}). Active Best unchanged."
        )
        return None
    oos_text = (
        "no out-of-sample result"
        if holdout is None
        else f"out-of-sample Sharpe {_num(holdout.sharpe)} over {holdout.trades} trades"
    )
    reason = f"Highest in-sample fitness so far ({candidate.fitness:.2f}); {oos_text} -> " + (
        "validated." if validated else f"NOT validated (needs {min_oos}+ trades and Sharpe > 0)."
    )
    best = learning.record_active_best(
        conn,
        now=clock(),
        search_id=search_id,
        trial=candidate,
        oos=holdout,
        validated=validated,
        reason=reason,
        curve=_curves(conn, target, candidate.params, calendar, clock),
    )
    say(f"New Active Best ({target.asset}): {target.label} ({_fmt(candidate.params)}). {reason}")
    return best


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _num(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def _curves(
    conn: sqlite3.Connection,
    target: Target,
    params: dict[str, int],
    calendar: MarketCalendar,
    clock: Clock,
) -> dict[str, list[dict[str, Any]]]:
    """Equity curves of runs already in the log, replayed without logging. Equity only."""
    if target.asset != "equity":
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for period in ("in-sample", "out-of-sample"):
        if bt_repo.find_run(conn, target.symbol, target.strategy, params, period) is None:
            continue
        report = eq.run(
            conn,
            eq.RunRequest(target.dataset_id, target.symbol, target.strategy, period, params),
            calendar,
            clock(),
            record=False,
        )
        step = max(1, math.ceil(len(report.curve) / CURVE_POINTS))
        points = report.curve[::step]
        if report.curve and points[-1] is not report.curve[-1]:
            points.append(report.curve[-1])
        out[period] = [
            {"day": p.day.isoformat(), "strategy": p.strategy, "benchmark": p.benchmark}
            for p in points
        ]
    return out


# ---- the daemon loop -----------------------------------------------------------------------


def next_target(
    conn: sqlite3.Connection, settings: Settings, skip: frozenset[Target] = frozenset()
) -> Target | None:
    """The target with the most budget left (ties: alphabetical), or None when all are spent."""
    open_targets = [
        (settings.optimizer_max_trials_per_target - tried(conn, t), t)
        for t in targets(conn)
        if t not in skip
    ]
    open_targets = [(left, t) for left, t in open_targets if left > 0]
    if not open_targets:
        return None
    return sorted(open_targets, key=lambda lt: (-lt[0], lt[1].label))[0][1]


def run_daemon(  # noqa: PLR0913
    settings: Settings,
    *,
    calendar: MarketCalendar | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    once: bool = False,
    seed: int | None = None,
    stop: threading.Event | None = None,
) -> int:
    """Loop forever (or one search with `once`). Returns the number of searches run."""
    assert_paper_endpoint(settings.alpaca_base_url)
    calendar = calendar or MarketCalendar()
    stop = stop or threading.Event()
    conn = connect(settings.database_path)
    migrate(conn)
    started = clock()
    searches = 0
    idle_logged = False
    stalled: set[Target] = set()  # targets that can't make progress; retried after a restart
    seeds = random.Random(seed)  # noqa: S311

    def beat(detail: dict[str, Any], status: str = "running") -> None:
        learning.beat(conn, DAEMON, started_at=started, now=clock(), status=status, detail=detail)

    learning.log(conn, DAEMON, "Optimizer daemon started (in-sample search only).", started)
    try:
        while not stop.is_set():
            target = next_target(conn, settings, frozenset(stalled))
            if target is None:
                if not idle_logged:
                    learning.log(
                        conn,
                        DAEMON,
                        "Idle: every target has used its trial budget, or no symbol has "
                        f"{MIN_SESSIONS}+ sessions of imported bars. Import new data to continue.",
                        clock(),
                    )
                    idle_logged = True
                beat({"state": "idle", "counts": learning.trial_counts(conn)}, "idle")
                if once:
                    break
                stop.wait(settings.optimizer_idle_seconds)
                continue
            idle_logged = False
            before = tried(conn, target)
            try:
                run_search(
                    conn,
                    target,
                    settings=settings,
                    calendar=calendar,
                    clock=clock,
                    seed=seeds.randrange(2**31),
                    beat=beat,
                    stop=stop,
                )
            except Exception as exc:  # one bad target mustn't stop the daemon
                stalled.add(target)
                learning.log(
                    conn, DAEMON, f"Search on {target.label} failed: {exc!r}", clock(), "error"
                )
            searches += 1
            if tried(conn, target) == before and target not in stalled:
                stalled.add(target)
                learning.log(
                    conn,
                    DAEMON,
                    f"{target.label}: no new trial was possible; skipping it until restart.",
                    clock(),
                    "warn",
                )
            beat({"state": "between searches", "counts": learning.trial_counts(conn)})
            if once:
                break
    finally:
        beat({"state": "stopped"}, "stopped")
        learning.log(conn, DAEMON, "Optimizer daemon stopped.", clock())
        conn.close()
    return searches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ptl-optimizer", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run one search and exit")
    parser.add_argument("--seed", type=int, default=None, help="seed for reproducible searches")
    args = parser.parse_args(argv)
    try:
        run_daemon(Settings(), once=args.once, seed=args.seed)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
