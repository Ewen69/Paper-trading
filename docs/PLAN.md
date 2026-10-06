# Paper Trading Lab — Architectural Plan

A local research tool: options strategy research, honest backtesting, paper trading on
Alpaca's **paper** endpoint, and a local net-worth / portfolio tracker. It never places
real-money trades. Rules live in [`CLAUDE.md`](../CLAUDE.md).

## Shape of the system

```
frontend/  React + Vite + TS + Tailwind  ──(/api via Vite proxy)──▶  backend/  FastAPI
                                                                       │
                     ┌─────────────────────────────────────────────────┼──────────────────┐
                     ▼                    ▼                   ▼        ▼                  ▼
               data/ (sources)      backtest/ (engine)   strategies/  broker/ (paper)  networth/
               - LiveQuoteSource    - event loop         - plugin API - PaperBroker    portfolio/
                 (Alpaca impl)      - fill models        - registry   - risk module    - SQLite
               - CSV ingestion      - options lifecycle               - kill switch
               - quality checks     - metrics + bootstrap
                     │                    │                   │            │              │
                     └──────────────── SQLite (data/ptl.sqlite3, local only) ─────────────┘
```

Backend package: `backend/src/ptl/`. Every module is strictly typed (mypy `--strict`) and
linted (ruff). Every value served to the UI carries a `Provenance` record
(`source`, `as_of`, `data_type` ∈ {real-time, delayed, end-of-day, manual}, `stale`).

### Cross-cutting guarantees

| Guarantee | Mechanism |
|---|---|
| Paper only | `ptl.safety.assert_paper_endpoint()` at app construction **and** again inside the broker adapter before any order call. A test scans the source tree for live-endpoint strings. |
| No fake data | UI-facing services return `None`/"no data" states; mocks live under `tests/` only. |
| Provenance | Shared `Provenance` model attached to every API payload; staleness computed server-side. |
| Secrets | `pydantic.SecretStr`, `.env` gitignored, a test asserts secrets never appear in reprs or responses. |
| Honest backtests | Engine only hands strategies a time-sliced view of data (no look-ahead); fills at bid/ask + slippage + commission. |
| Rigor | Locked out-of-sample window stored in DB; trial counter persisted per strategy; bootstrap CIs; benchmark after costs. |

## Phases

**Phase 0 — Scaffold.** Repo layout, `CLAUDE.md`, `.env.example`, uv-managed Python 3.12
backend (ruff, mypy strict, pytest), Vite/React/TS/Tailwind frontend (ESLint strict-type-checked,
Vitest), paper-only assertion, `/health` endpoint, one-command dev runner (`npm run dev`) and one-command
checker (`npm run check`).

**Phase 1 — Data layer.** `Provenance` model. `QuoteSource` protocol with an Alpaca paper/market-data
implementation (alpaca-py). CSV ingestion for historical equities and options chains (schema
validation, timezone normalization, idempotent loads into SQLite). Data-quality checks: gaps
vs. trading calendar, stale quotes, crossed/locked markets, bid > ask, zero/negative prices.
`/data/health` endpoint summarizing per-dataset status.

**Phase 2a — Core backtester (equities/ETFs).** Event-driven loop over bars; strategies see a
`MarketView` truncated at the current timestamp. Order → fill model (next-bar, bid/ask when
available, slippage, commission). Buy-and-hold benchmark and one rules-based strategy
(e.g. moving-average trend filter). Metrics: total/annualized return, max drawdown, Sharpe,
win rate, expectancy, trade count, bootstrap CIs; low-trade-count warnings. Locked OOS window
and trial counter introduced here.

**Phase 2b — Options backtesting.** Option contract model, multi-leg defined-risk orders
(verticals). Fills: buy at ask, sell at bid, plus slippage + per-contract commission. Expiration
(ITM/OTM settlement), assignment, and early-exercise handling (configurable policy, e.g. short
ITM call before ex-div). Hand-computed CSV fixtures drive unit tests for fills, expiry, assignment.

**Phase 2.5 — Game UI (agents).** Added 2026-10-05 at the user's request; it pulls the visual
part of Phase 6 forward. The UI becomes game-like rather than a web page. Each "agent" character
is bound to a real module: Data Scout → data layer, Backtester → engine, Risk Officer → risk
module, and so on. An agent's status, XP and messages come only from that module's real outputs.
No AI calls in this phase.

**Later — AI-backed agents.** Agents can be powered by Claude (Anthropic API), local models
(Ollama), OpenAI GPT, or Google Gemini. Every agent reports to a Claude "lead" that is the final
reviewer. Rules for this:
- Agents may only cite numbers the app computed. The reviewer rejects any claim it can't trace
  to one.
- No advice and no "expected profit" language.
- Net-worth data never goes to a cloud model unless the user explicitly opts in.
- API keys live in `.env`.

**Phase 3 — Strategy interface & paper runner.** *(Done: see `docs/AGENTS.md`. Also adds the live agent runtime (the City view was later replaced by the Operations Center). Options paper trading is deferred.)* Strategy plugin protocol + registry.
Risk module (max loss/trade, max loss/day, max open positions, max capital at risk, kill switch,
defined-risk-only validator that rejects naked short options). Paper runner using
`alpaca-py` `TradingClient(paper=True)` with a second paper-URL assertion; audit log of every
order, fill, rejection, and the reason. Dry-run mode that logs intended orders without sending.

**Phase 4 — Net worth tracker.** *(Done: see `docs/NETWORTH.md`.)* SQLite schema for accounts, assets, liabilities, and dated
snapshots. Manual entry and CSV import/export. Net worth = assets − liabilities, history chart.
Projections as a scenario range (defaults 4%–6% nominal), clearly labeled as assumptions.

**Phase 5 — Portfolio analysis & tips.** *(Done: see `docs/PORTFOLIO.md`. Tips are rule checks that show inputs and thresholds.)* Merge paper positions with manual holdings.
Allocation, concentration (HHI / top-N weight), volatility, drawdown, correlation/beta to
benchmark, aggregate delta/theta/vega. Tips are deterministic rules that display the inputs and
threshold that fired them.

**Phase 6 — UI/UX.** *(Done as the unification: the Operations Center telemetry dashboard,
the learning optimizer and paper runner daemons, and `npm run start:all`. See
`docs/OPERATIONS.md` and `docs/OPTIMIZER.md`.)* Dark dashboard: Home, Strategy Lab, Paper Trader, Net Worth, Portfolio,
Data Health. Process-only gamification (XP/badges). Graduation Gate checklist (tracks only, never
enables live trading). Reality Check panel on every results screen (sample size, CI, trial count,
cost assumptions).

## Process

Each phase ends with: tests, lint, typecheck all green → README updated (what it does and its
limitations) → commit → **stop and wait for written approval**.
