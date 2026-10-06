# Paper Trading Lab

A local learning and research tool for options strategies: honest backtesting, paper trading on
Alpaca's **paper** account, and local net-worth / portfolio tracking.
**It never places real-money trades.** Rules: [`CLAUDE.md`](CLAUDE.md). Plan: [`docs/PLAN.md`](docs/PLAN.md).

> Nothing in this app is financial advice. It reports computed numbers and their assumptions.

## Status: unified system (all phases built)

Start everything with one command:

```bash
npm run start:all
```

It checks Python, packages and Node, verifies the paper endpoint, migrates the database, then
runs four processes and opens the **Operations Center** at http://localhost:5173:

- the API
- the learning optimizer daemon
- the paper runner daemon (**dry run**)
- the web UI

Options: `-- --paper` (the runner sends to the Alpaca PAPER account), `--no-optimizer`,
`--no-runner`, `--no-web`, and `--check` (verify and migrate, then exit). Ctrl+C stops
everything.

### Architecture

| Piece | What it does | Docs |
|---|---|---|
| Operations Center (station 1) | Live telemetry dashboard: agent terminal, learning agent, Active Best equity curve, paper runner with risk utilization and slippage, net worth, allocation, Auditor, manual controls | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| Learning optimizer daemon | Genetic search over strategy parameters on in-sample data. Every trial is logged and counted, with a per-target budget. The top 5% get one counted out-of-sample look each. The Active Best is picked in-sample only and labeled by its holdout result | [`docs/OPTIMIZER.md`](docs/OPTIMIZER.md) |
| Paper runner daemon | Trades only a *validated* Active Best, once per session. Sized to the tightest risk limit and checked by the hard-coded risk engine. Logs quote, fill and slippage. Dry run by default, PAPER endpoint only | [`docs/OPTIMIZER.md`](docs/OPTIMIZER.md) |
| Backtesting, options engine | Honest fills, out-of-sample lock, trial counts, bootstrap CIs | [`docs/BACKTESTING.md`](docs/BACKTESTING.md), [`docs/OPTIONS.md`](docs/OPTIONS.md) |
| Net worth, portfolio | Manual entries and CSV only, local SQLite | [`docs/NETWORTH.md`](docs/NETWORTH.md), [`docs/PORTFOLIO.md`](docs/PORTFOLIO.md) |
| HQ, stations, Auditor, Graduation Gate | Game-style views of the same stored records | [`docs/GAME.md`](docs/GAME.md) |

### What the unification added

- **Phase 4 and Phase 5 are merged into `main`.**
- **The 3D City view and three.js are removed.** They're replaced by the dense dark Operations
  Center. The whole frontend is now one ~450 kB bundle (128 kB gzipped), with no 933 kB 3D
  chunk.
- **Optimizer daemon** (`ptl/agents/optimizer.py`) with the append-only `agent_learning_log`
  and Active Best history.
- **Paper runner daemon** (`ptl/runner/`), with a new **max position size** risk limit, sizing
  to the tightest limit, and slippage logging.
- **Telemetry hub** with `/ws/telemetry`, plus the orchestrator (`npm run start:all`).
- **The old agent autopilot is now off by default** (`AGENTS_AUTOPILOT=false`); the optimizer
  does the searching.

### Limitations of the unified system

- **No new data arrives on its own.** The optimizer idles when every target's trial budget is
  spent; import new data to continue. The runner refuses stale bars; there's no automatic bar
  download.
- **Paper trading is equity-only.** Options Active Bests have no curve and aren't paper traded.
- **Fitness is plain in-sample Sharpe.** Over-search is controlled by trial counts, budgets and
  the out-of-sample label, not by a deflated Sharpe.
- **No supervision.** `start:all` stops everything if any process exits; it doesn't restart
  crashed daemons.
- **Dense layout needs a wide screen.** On narrow screens the panels stack.

### What Phase 5 added

### What Phase 5 adds

Details: [`docs/PORTFOLIO.md`](docs/PORTFOLIO.md).

- **Portfolio station (key 7), run by the Analyst.** You enter or import holdings (stocks, ETFs,
  funds, bonds, cash, options, crypto), which are stored locally.
  - The Alpaca **paper** account's positions are read read-only, as a separate book, and only
    combined when you choose to.
- **Every value names its price source and date:**
  - imported end-of-day close
  - otherwise your manual price
  - options at the bid if long and the ask if short
  - otherwise "not valued", listed and excluded
- **Stale prices are flagged.**
- **What's computed:**
  - allocation by asset class
  - concentration (HHI, effective N, top 5)
  - a risk replay of today's weights against a benchmark: volatility, max drawdown, beta,
    correlation, with dates, coverage and the price basis shown
  - option Greek totals from vendor Greeks in imported quotes
- **Rule checks** (the plan's "tips") are fixed rules. Each shows what it measured and its
  threshold, and is fired, clear, or not evaluated. They flag facts, not advice.
- **Holdings CSV import and export**, all-or-nothing, with an optional "replace all".

### Phase 5 limitations

- **End-of-day prices only;** no live quotes for holdings.
- **No transaction history,** so no cost basis, realized P&L or tax lots.
- **The short-option rule** doesn't recognize spreads.
- **The risk replay is backward-looking,** at constant weights, and only covers symbols with
  imported history.
- **USD only.**

### What Phase 4 added

Details: [`docs/NETWORTH.md`](docs/NETWORTH.md).

- **Net Worth station (key 6), run by the Accountant.** You add accounts (asset or liability,
  with a category) and dated balances by hand.
  - There are no bank logins, and everything stays in local SQLite.
  - The Accountant's HQ card shows counts and dates, never amounts.
- **Net worth = assets minus liabilities:**
  - each account's latest balance on or before the date, carried forward and never
    interpolated
  - accounts with no balance count as 0 and are listed
  - balances older than 45 days are flagged stale
- **History chart:** one point per entry date. The tooltip shows assets, liabilities, carried
  values and missing accounts.
- **CSV import and export** (`account,kind,category,as_of,amount`). Imports are all-or-nothing,
  with every bad line listed, and exports round-trip.
  - In the UI, or with `npm run ptl -- networth-import` / `networth-export`.
- **Projection range:**
  - low and high fixed rates (defaults 4%–6%), optional yearly contribution, up to 50 years
  - labeled as an assumption, not a forecast
  - refused when net worth is zero or negative
- **Exact storage:** amounts are integer cents, and future dates are refused. Re-entering a
  date replaces the old balance, and the app says so.

### Phase 4 limitations

- USD only.
- Holdings aren't priced automatically; a brokerage account is the balance you enter.
  Merging paper positions with holdings is Phase 5.
- Debts don't accrue interest between entries.
- Projections use one fixed rate per line, with no volatility.

### What Phase 3 added

Details: [`docs/AGENTS.md`](docs/AGENTS.md).

- **The City (since replaced by the Operations Center).** It was an isometric 3D view
  (three.js via react-three-fiber) with four sectors: Data Ingestion, Strategy Research, Options Risk and the
  Execution Hub.
  - Agents are geometric nodes. A running node pulses, streams particles, and floats a label
    with the exact parameters it's testing.
  - Empty slots wait for future sub-agents.
  - Panels below the view carry every detail and control, so the page works without WebGL.
  - Live over a WebSocket (`/api/ws/activity`), with every message validated.
- **Live agent runtime:**
  - one asyncio worker and queue per agent
  - jobs and findings (win rate, Sharpe, trades, run #) are logged to SQLite
  - autopilot queues untried work
- **Sweeps stay honest:**
  - in-sample only, and every combination is a counted trial
  - re-runs reuse existing runs
  - one finalist may get a single counted out-of-sample test, never repeated
  - autopilot never touches the holdout
- **Strategy plugin architecture:** a `Strategy` base class, typed params and sweep grids, an
  `@register` registry, and auto-loaded `ptl/strategy/plugins/` (example: `trend_filter`).
- **Risk engine in code:**
  - max loss per trade, max daily loss (a breach trips the kill switch), max open positions,
    max capital at risk
  - defined risk only
  - every decision is stored with its arithmetic
  - the kill switch is in the UI and the command line
- **Paper runner, with dry run as the default.** Paper mode sends to the Alpaca **paper**
  account only, and the paper URL is re-checked before every submit.
  - Stale signals are refused.
  - Cycles, risk decisions, orders and status events are append-only.
- **HQ and the Graduation Gate are updated.** Risk Officer and Paper Trader are unlocked, and
  the gate now counts real paper-account fills (dry runs never count).

### Phase 3 limitations

- **Options paper trading isn't built.** The paper runner is equity-only, and options stay
  backtest-only.
- **The agents are deterministic job runners, not AI.** AI agents with a Claude lead reviewer
  come later.
- **The runtime is single-process and in-memory.** Queued jobs don't survive a restart.
- **Orders are whole-share market DAY orders.** Limit orders and partial-fill logic aren't
  built.
- **Current bars must be imported by hand.** There is no automatic bar download.
- **The 3D view was a lazily loaded ~250 kB (gzipped) chunk.** It was later removed.

### What Phase 2b added

Details and a worked example: [`docs/OPTIONS.md`](docs/OPTIONS.md).

- **Contract math with the ×100 multiplier** on premiums, P&L, collateral, exercise and
  assignment.
  - Buys fill at ask + slippage, sells at bid − slippage, never mid.
  - Commission per contract; slippage per share plus an optional share of the spread.
- **Defined risk only:** single long options and vertical spreads. Naked shorts are rejected.
- **Collateral:** width × 100 × contracts − credit. Orders are rejected when it exceeds
  available cash.
- **Expiration at the underlying's close.**
  - Out-of-the-money legs expire worthless (OCC $0.01 rule).
  - In-the-money legs are exercised or assigned, and spreads net cleanly.
  - American options deliver shares; European options settle in cash.
- **Pin risk:** a between-strike finish leaves shares, which are sold at the next open, so
  losses can exceed the spread's "max loss". It's flagged.
- **Early assignment** of short American legs with no time value left; the in-the-money long
  leg is exercised alongside.
- **Put credit spread strategy:** each open and close is logged with its exact arithmetic.
- **Options mode in the Strategy Lab:**
  - a pre-run reality check, then the Reality Check first on every result
  - options counts (pin events, early assignments, rejected orders, stale marks, peak
    collateral)
  - positions and a full event log
- **Same rigor rules as stocks:** out-of-sample lock, trial counting, stored warnings, and a
  buy-and-hold benchmark.
- **Tests first:** 25 hand-computed mechanics tests on a synthetic CSV fixture, plus 10 engine
  scenarios and 6 service/API tests.

### Phase 2b limitations

- End-of-day quotes only, filled at the next day's snapshot.
- AM settlement and dividend-driven early assignment aren't modeled.
- No partial closes, and adjusted contracts aren't handled.
- You must import option quotes (with `exercise_style`) and underlying bars yourself.

### What Phase 2.5 added

#### Details

- **Game shell instead of a website.**
  - Pixel-font HUD with a "PAPER ONLY" shield, your level and process-XP bar, and NYSE status.
  - Hotbar of stations: press **1** HQ, **2** Data Scout, **3** Quant.
  - Command-deck look with framed panels. Animations turn off when your system asks for
    reduced motion.
- **HQ and your agent squad.** Pixel characters stand on pads, each with a status light and
  label (Ready / Caution / Alert / Idle), a level, and a speech bubble.
  - **Data Scout** reports data-health facts.
  - **Quant** reports backtest-log facts, including when too many tries have biased a symbol's
    results.
  - **Locked agents** show as silhouettes with the phase that unlocks them: Risk Officer, Paper
    Trader, Accountant, Analyst, Lead Reviewer.
- **Mission log.** Every import, out-of-sample lock and backtest run, with its source, time
  and the XP it earned (or why it earned none).
- **Process-only XP and badges.**
  - XP comes from importing data, sealing out-of-sample periods, and testing out-of-sample
    once after in-sample work.
  - Profit never earns XP, and repeat looks at out-of-sample data earn nothing.
  - Badges: First Contact, Clean Room, Sealed Vault, Restraint, One Shot (lost if you look
    again), Cost Realist.
  - Toasts pop when a new event lands.
- **Truth comes from the backend.** `GET /game/state` computes all of the above from local
  records and the live source check. The UI only renders it.
- **The Auditor (station 4).** Each backtest's warnings are now stored with a stable code. The
  Auditor turns them, plus data findings, into standing flags:
  - small samples and short histories
  - data gaps and impossible rows
  - over-tuning and reused holdouts
  - unadjusted prices
  - live feed down
  Each flag shows its severity, source and time, and links to the station that can fix it.
- **Graduation Gate** on HQ and the Audit station: 3 months of paper trading, 200+ paper
  trades, beating buy-and-hold on a first out-of-sample test, zero risk breaches.
  - It **only tracks** and can never enable live trading.
  - Paper items show "Not started" until Phase 3.
- **Reality Check first.** It now sits above the chart on every result: headline tiles
  flagged exactly where the backend raised a warning, then "Auditor says" with the warnings.
  - A "before you run" callout shows the selected data's lock status, run count and
    out-of-sample looks.

Details: [`docs/GAME.md`](docs/GAME.md).

#### Phase 2.5 limitations

- Agents are not AI yet. They restate computed facts. AI-backed agents reporting to a Claude
  reviewer come in a later phase (see the plan).
- XP and badges only cover what the app logs. Paper trading and risk badges arrive with
  Phase 3.
- Runs logged before this phase have no stored warnings or CIs. The Auditor and the gate treat
  them as unknown, not clean.
- Game state refreshes when you switch stations or finish a run; it doesn't poll.

### What Phase 2a added

How it works in detail: [`docs/BACKTESTING.md`](docs/BACKTESTING.md).

- **Event-driven engine (stocks/ETFs):**
  - decisions at the close, fills at the next open
  - slippage and commission on every fill
  - fractional shares, long only
  - forced exit at the end so every result is after costs
  - strategies see history only up to the decision day; a test proves future prices can't
    change past decisions
- **Strategies:** buy-and-hold (the benchmark) and a moving-average crossover.
- **Benchmark on every run:** buy-and-hold over the same window with the same costs.
- **Metrics:** total and annualized return, volatility, Sharpe, max drawdown, time in market,
  trade count, win rate, and expectancy (historical average P&L per trade).
  - 95% bootstrap confidence intervals, including a paired CI for excess return vs the
    benchmark.
- **Locked out-of-sample period:** the first run on a symbol permanently reserves its most
  recent 30% of sessions.
  - In-sample runs never receive those bars.
  - Every out-of-sample look is counted.
- **Trial counting:** every run is logged in an append-only table. The number of parameter
  combinations you've tried is shown next to every result.
- **Strategy Lab page:** run backtests and see an equity chart (hover or arrow keys, plus a
  table view), the metrics with CIs, the trades, and a **Reality Check** panel listing:
  - sample size and the CI on excess return
  - trial count and out-of-sample looks
  - costs, price basis and cash yield
  - automatic warnings
- **Blocked runs:** error-level data problems, too little data, or benchmark data that
  doesn't cover the window stop a run with a clear reason.

### Phase 2a limitations

- **No market data is included.** Import a daily CSV first (ideally with `adj_close`). Without
  `adj_close`, dividends are excluded and the report says so.
- Daily bars, one asset, long only. Options come in Phase 2b; parameter sweeps aren't built
  yet.
- Slippage is a flat number of basis points. Gaps, halts and spreads widening under stress
  aren't modeled.
- Idle cash earns 0%, which understates strategies that are often in cash. This is stated
  in every report.
- Trial counts only cover experiments run inside this app.

### What Phase 1 added

Details: [`docs/DATA.md`](docs/DATA.md).

- **Provenance everywhere:** every value the API serves has `source`, `as_of`, `data_type`
  (real-time / delayed / end-of-day / manual), and `stale`, plus a reason when it's stale.
- **Live quotes (Alpaca):** the quote source sits behind a swappable `QuoteSource` interface;
  `AlpacaQuoteSource` is the implementation. `GET /quotes/stock/{symbol}` and
  `GET /quotes/option/{occ_symbol}` return bid, ask, sizes and spread (never a mid), with the feed's
  data type and quality checks.
  - Missing keys, missing quotes or API failures return a "No data: …" error, never a guess.
  - Symbols are validated before any network call.
- **Paper keys verified:** Data Health reads the paper account status (read-only, cached for
  60s). The trading client can only be built through `make_paper_trading_client`, which checks the
  endpoint before and after construction.
- **CSV ingestion:** `npm run ptl -- import-csv` loads equity daily bars and option EOD quotes into
  local SQLite.
  - A file is imported all-or-nothing, with line-numbered errors.
  - Re-importing the same file does nothing (SHA-256 hash).
  - Column renaming (`--map`) and custom date formats are supported.
- **Data-quality checks:** gaps against the real NYSE calendar (holidays and special closures
  aren't gaps), impossible OHLC, crossed/locked markets, no offer, zero bid, quotes after expiry,
  repeated quotes, extreme moves (often unadjusted splits), and live-quote staleness.
  - Findings are stored and shown. Data is never fixed or dropped.
- **Data Health page:** overall status, live source and feed types, a live quote check, and each
  dataset with coverage, provenance, a stale flag and its findings. `GET /data/health` serves it.

### Phase 1 limitations

- **Free Alpaca data is limited.** Stock quotes come from IEX only (one venue, so spreads can be
  wider than the market) and option quotes are delayed, modified "indicative" quotes. Both are
  labeled. Neither should drive fill decisions.
- **Without paper keys, nothing live works**, and the app says so. The live path is tested
  against fake Alpaca clients, so the real connection is only proven once you add keys.
- **Daily data only.** No intraday bars yet, and quality checks run per file, not across
  overlapping datasets.
- **The calendar covers 1990 to about a year ahead.** Dates outside that range can't be
  checked for gaps and are flagged `outside_calendar`.
- **Import is command-line only.** There's no upload button in the UI yet.
- **No historical data ships with the app.** You bring your own CSVs. The files in
  `backend/tests/fixtures` are synthetic and exist only for tests.

### From Phase 0

- **Repo layout:** `backend/` (Python 3.12, FastAPI, uv), `frontend/` (React, Vite, TypeScript,
  Tailwind), `docs/`, and root scripts that run both.
- **Paper-only guard:** `backend/src/ptl/safety.py` accepts only
  `https://paper-api.alpaca.markets` (optionally `/` or `/v2`). The app factory calls it before the
  server exists, so with any other URL (the live host, lookalike domains, http, embedded
  credentials, odd ports or paths) the server **refuses to start**. There is no override flag.
- **Static guard test:** fails the build if any backend or frontend source file references the
  live trading host or `paper=False`.
- **Secrets:** keys are read from `.env` (gitignored) as `SecretStr`. Tests check that keys never
  appear in reprs, dumps, or API responses.
- **`GET /health`:** returns status, version, trading mode (`paper`), the broker endpoint, whether
  keys are present, and provenance (`source`, `as_of` in UTC).
- **UI:** a dark single page showing the health card with source and timestamp. If the backend is
  unreachable or returns an unexpected payload, it shows **"No data"**. It never fills in values.
- **Tooling:** ruff (lint + format), mypy `--strict`, pytest (warnings become errors). On the
  frontend: TypeScript strict (plus `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`),
  ESLint `strictTypeChecked` with `--max-warnings 0`, and Vitest.

Not built yet: backtesting, trading, net worth, and portfolio features.

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (tested on 24 LTS)

## Run it

```bash
npm install
```

That installs the root tools, then runs `uv sync` for the backend and `npm install` for the frontend.

```bash
npm run start:all
```

That starts the API (http://127.0.0.1:8000), both daemons, and the UI (http://localhost:5173).
`npm run dev` still starts just the API and UI with auto-reload; `npm run optimizer` and
`npm run runner -- --dry-run` start each daemon on its own.

Optional: copy `.env.example` to `.env` and add your **paper** keys. The app runs without them,
but live quotes will show "No data".

To import historical data:

```bash
npm run ptl -- import-csv path/to/spy.csv --kind equity-bars --source "Vendor name"
```

Your data stays in `data/ptl.sqlite3`, which git ignores.

## Check everything

```bash
npm run check
```

This runs ruff, the ruff format check, mypy, pytest, tsc, ESLint and Vitest. It must pass
cleanly before every commit.
