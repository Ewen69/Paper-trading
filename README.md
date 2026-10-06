# Paper Trading Lab

A local learning and research tool for options strategies: honest backtesting, paper trading on
Alpaca's **paper** account, and local net-worth / portfolio tracking.
**It never places real-money trades.** Rules: [`CLAUDE.md`](CLAUDE.md). Plan: [`docs/PLAN.md`](docs/PLAN.md).

> Nothing in this app is financial advice. It reports computed numbers and their assumptions.

## Status: Phase 0 (scaffold)

### What Phase 0 does

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

### Limitations (by design, for this phase)

- No broker connection yet. "Paper API keys: present" means the keys are **set**, not that
  they are **valid**. Phase 1 verifies them.
- No market data, backtesting, trading, net worth, or portfolio features yet.
- `as_of` on `/health` is the backend's clock at response time. There's no market data to date yet.

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

Optional: copy `.env.example` to `.env` and add your **paper** keys. The app runs without them.

## Check everything

```bash
npm run check
```

This runs ruff, the ruff format check, mypy, pytest, tsc, ESLint and Vitest. It must pass
cleanly before every commit.
