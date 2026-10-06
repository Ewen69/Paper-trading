# CLAUDE.md — Paper Trading Lab

Learning and research tool: options strategy research, honest backtesting, paper trading,
net-worth and portfolio tracking. **It must never place real-money trades.**
Architecture and phase plan: [`docs/PLAN.md`](docs/PLAN.md).

## Execution protocol

- Work one phase at a time. At the end of each phase: run `npm run check`, summarize what works
  and what fails, update the README, commit, then **stop entirely**. Do not start the next
  phase without explicit written approval from the user. Never chain phases (unless the user
  explicitly asks for an end-to-end run, as they did for the unification).

## Non-negotiable rules

1. **Paper only.** Use the broker's paper endpoint only (`https://paper-api.alpaca.markets`).
   `ptl.safety.assert_paper_endpoint` runs at startup and the app refuses to run otherwise.
   Never write any code path that submits live orders. Any broker client must be constructed in
   paper mode and re-check the endpoint before sending an order.
2. **Real data only.** No fabricated, random, or mock data in any code path the UI reads from.
   Mocks are allowed only inside tests. If data is missing, show "no data", never a guess.
3. **Data provenance.** Store and display source, as-of timestamp, and data type (real-time,
   delayed, end-of-day, manual entry). Flag stale data.
4. **Honest backtesting.** No look-ahead: signals use only data available at that time. Fill
   options at bid/ask with a configurable slippage model and per-contract commission, never at
   mid or last. Handle expiration, assignment, and early exercise explicitly.
5. **Scientific rigor.** Keep a locked out-of-sample period that tuning never touches. Count
   every parameter combination tried and display that count next to results. Report bootstrap
   confidence intervals, warn on low trade counts, and always compare to a buy-and-hold index
   benchmark after costs.
6. **Risk limits are code.** Max loss per trade, max loss per day, max open positions, max total
   capital at risk, and a hard kill switch. Defined-risk strategies only (e.g. vertical spreads).
   No naked short options.
7. **Secrets & privacy.** Keys live in `.env` (gitignored) and are typed as `SecretStr`. Never
   log or commit keys. Net-worth data stays local in SQLite. No bank logins: manual entry and
   CSV import only.
8. **Honesty over hype.** No advice, tips, or claims that can't be traced to computed numbers.
   No "guaranteed" or "expected profit" language.

## Stack and conventions

- Backend: Python 3.12 (uv-managed, `backend/`), FastAPI, SQLite, pytest, mypy `--strict`, ruff.
  Package lives at `backend/src/ptl/`. Timestamps are always timezone-aware UTC.
- Frontend: React + Vite + TypeScript (strict) + Tailwind, ESLint `strictTypeChecked`, Vitest.
  The frontend calls the backend via `/api/*` (Vite proxy strips `/api`).
- Live data sources sit behind interfaces so they can be swapped. Historical data comes from
  local CSV ingestion (Alpaca does not serve free historical options data).
- Game UI (`frontend/src/game`, backend `ptl/game.py`):
  - Agent lines, events, XP and badges are computed by the backend from stored records only.
    No flavor text that states a fact.
  - XP rewards process, never returns, wins or profit. No streaks or leaderboards.
  - Agents for unbuilt modules stay locked. A new module's agent unlocks in the phase that
    builds it.
- Live agents (`ptl/agents`):
  - Sweeps run in-sample only, and every combination is a counted run.
  - Only an explicit `promote` may spend the single out-of-sample test on the finalist.
  - Autopilot never promotes (and is off by default).
- Optimizer, runner and telemetry (`ptl/agents/optimizer.py`, `ptl/runner`, `ptl/telemetry.py`,
  frontend `src/ops`; see `docs/OPTIMIZER.md`):
  - The optimizer searches in-sample only, within a per-target trial budget, and logs every
    trial.
  - The top 5% get one out-of-sample look each, never repeated.
  - The Active Best is chosen in-sample only; out-of-sample results only label it as validated.
  - The runner trades only a validated Active Best, dry run by default.
  - The daemons are separate processes that talk through SQLite. The telemetry hub tails it.
- Risk and paper (`ptl/risk`, `ptl/paper`):
  - Risk limits are code (`risk/engine.py`). Every order passes `evaluate` and is logged.
  - Dry run is the default.
  - `AlpacaPaperBroker` re-asserts the paper URL before each submit.
  - Log tables are append-only (DB triggers).
- Net worth (`ptl/networth`, frontend `src/networth`):
  - Amounts are integer cents.
  - Balances are carried forward, never interpolated.
  - Projections are labeled assumptions and are refused for net worth of zero or less.
  - Agents and HQ never show net-worth amounts.
- Portfolio (`ptl/portfolio`, frontend `src/portfolio`):
  - Every value names its price source and date.
  - Unpriced holdings are listed, never guessed.
  - Paper positions are read-only and kept as a separate book.
  - Tips are rules that show the observed value and threshold, never advice.
- Commits use the GitHub no-reply email (the repo is public).

## Commands (run from repo root)

- `npm install` — first-time setup (installs root tools, runs `uv sync` and frontend install).
- `npm run start:all` — verify, migrate, and start API, optimizer, paper runner (dry run) and UI.
- `npm run dev` — start backend (:8000) and frontend (:5173) together, with auto-reload.
- `npm run ptl -- <command>` — CLI (`import-csv`, `datasets`, `delete-dataset`, `paper-cycle`,
  `kill-switch`, `networth-import`, `networth-export`); see `docs/DATA.md`, `docs/AGENTS.md`
  and `docs/NETWORTH.md`.
- `npm run check` — ruff, ruff format check, mypy, pytest, ESLint, tsc, Vitest. Must be clean
  (zero warnings) before any commit.

## Definition of done (per phase)

Tests pass; the app runs locally with one command; every displayed number shows its source and
timestamp; no type-checker or linter warnings; README explains what the phase does and its
limitations.
