# Paper Trading Lab

A local learning and research tool for options strategies: honest backtesting, paper trading on
Alpaca's **paper** account, and local net-worth / portfolio tracking.
**It never places real-money trades.** Rules: [`CLAUDE.md`](CLAUDE.md). Plan: [`docs/PLAN.md`](docs/PLAN.md).

> Nothing in this app is financial advice. It reports computed numbers and their assumptions.

## Status: Phase 2.5 (game UI)

Next: options backtesting (Phase 2b). See [`docs/PLAN.md`](docs/PLAN.md).

### What Phase 2.5 adds

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

### Phase 2.5 limitations

- Agents are not AI yet. They restate computed facts. AI-backed agents reporting to a Claude
  reviewer come in a later phase (see the plan).
- XP and badges only cover what the app logs. Paper trading and risk badges arrive with
  Phase 3.
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
npm run dev
```

The API runs at http://127.0.0.1:8000 and the UI at http://localhost:5173.

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
