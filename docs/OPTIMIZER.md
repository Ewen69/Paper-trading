# Learning optimizer, paper runner daemon, telemetry

Nothing here is advice or a forecast. The optimizer searches historical data and records what
it tried. The paper runner trades simulated money on the Alpaca **paper** account, or logs what
it would do in dry run.

## Architecture

```
npm run start:all  (python -m ptl.orchestrator)
 ├─ api        FastAPI :8000  REST + /ws/telemetry + /ws/activity
 ├─ optimizer  python -m ptl.agents.optimizer   (separate process; writes SQLite)
 ├─ runner     python -m ptl.runner --dry-run   (separate process; writes SQLite)
 └─ web        Vite :5173     Operations Center (station 1)
```

- **Separate processes.** The daemons run as their own processes, so CPU-heavy backtests never
  block the API.
- **SQLite is the shared state.** Data goes in append-only logs (`agent_learning_log`,
  `optimizer_active_best`, `daemon_log`, the paper and risk logs) plus one heartbeat row per
  daemon.
- **The API tails those tables.** Its telemetry hub checks them every second and streams new
  rows to the dashboard. Every 15 seconds it recomputes the slower panels: Auditor, Graduation
  Gate, net worth, portfolio and risk guard.

## The optimizer (`ptl/agents/optimizer.py`)

**Genetic search.** Each target is a strategy × symbol with enough history (120+ sessions).

1. The population starts from the two best parameter sets found so far, then adds random
   untried ones inside each strategy's `search_space`.
2. Each generation keeps two elites and breeds the rest: tournament selection, uniform
   crossover and Gaussian mutation.
3. A child is only accepted if it is valid and has never been tried on that symbol.

**Fitness** is the in-sample Sharpe. Candidates with fewer than `OPTIMIZER_MIN_TRADES` trades
(default 10) are rejected.

**Rigor:**

- **Every candidate is a real backtest on in-sample data,** logged in the backtest run log. The
  Strategy Lab's trial counts and the Auditor's over-tuning flags see every one.
- **Nothing is re-run.** A parameter set already tried is reused and logged as `reused`. It
  isn't a new trial and doesn't count against the budget.
- **Hard budget:** `OPTIMIZER_MAX_TRIALS_PER_TARGET` (default 120) distinct in-sample trials
  per target. When every target is spent, the daemon idles and says so; import new data to
  continue.
- **Top 5% get the holdout, once each.** After each search, the top 5% (at least one) of all
  scored in-sample trials for the target get **one** counted out-of-sample test each. A
  parameter set is never tested out-of-sample twice, so new holdout looks happen only when the
  top set changes.
- **The holdout never steers the search.**
  - **Active Best** = the highest in-sample fitness. Its own out-of-sample result only labels
    it as validated (enough trades and an out-of-sample Sharpe above 0) or not.
  - Out-of-sample numbers never become fitness and never rank candidates.
  - A newer finalist replaces the Active Best if it ranks higher by (validated, in-sample
    fitness).
- **This is a deliberate deviation.** The request was to "run the top 5% against the locked
  out-of-sample window" and keep an evolving Active Best. If out-of-sample results picked the
  Active Best, the holdout would become a tuning set and break the CLAUDE.md rule. So the
  holdout is used only as a pass/fail label.
- **Caveat: the best of many in-sample trials is biased upward.** The Active Best's
  out-of-sample result is the honest estimate, and it's still one noisy sample.

Run it alone with `npm run optimizer` (add `-- --once` for a single search, or
`-- --seed N` for a reproducible run).

## The paper runner daemon (`ptl/runner/`)

**Each tick** (`RUNNER_POLL_SECONDS`, default 60) it:

1. reads the account read-only and publishes balance, positions and risk-limit usage;
2. takes the equity Active Best, and **only if it is validated**, runs one paper cycle per
   completed NYSE session;
3. polls open paper orders for fills.

**Hard-coded risk rules** are checked before any order:

- max loss per trade
- daily loss limit (a breach trips the kill switch)
- max open positions
- max capital at risk
- **max position size** (`RISK_MAX_POSITION_PCT`, default 25% of equity)
- kill switch
- defined risk only

**Sizing** takes the tightest of:

- the strategy's target
- max position size
- max loss per trade (a long with no stop can lose its whole notional)
- the capital-at-risk headroom

The rationale names which limit bound the order.

**Logged for every order:**

- the intent and rationale
- the quote at submission (with its source)
- the risk decision and its arithmetic
- the broker response, every status change and the fill price
- **slippage** (fill vs quote, in $/share and basis points, positive = worse)

**Paper only.**

- `--dry-run` is the default and sends nothing.
- `--paper` uses the Alpaca PAPER endpoint, re-asserted before every submit. There is no
  live-trading code path.
- Run it alone with `npm run runner -- --dry-run`, or `npm run runner -- --paper` once paper
  keys are in `.env`.

## Telemetry (`/ws/telemetry`)

On connect the server sends `hello`: the full state plus the last 150 log lines and 60 trials.
After that it sends:

- `trial`: each new learning-log row
- `log`: each new daemon log line
- `daemons`: heartbeat changes
- `state`: the slower panels, every 15 seconds

`GET /telemetry/state` returns the same state once.

## Limitations

- **Equity curves for the Active Best are equity-only.** Options Active Bests have no curve.
  The paper runner is equity-only too.
- **Bars must be current.** The runner refuses to trade on stale bars; you import them
  yourself.
- **Fitness is plain in-sample Sharpe.** It's not deflated for the number of trials; the trial
  counts and the out-of-sample label are the guard.
- **No supervision.** A daemon that crashes shows "no heartbeat" in the dashboard;
  `start:all` stops everything when any one process exits.
