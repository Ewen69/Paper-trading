# Live agents, risk engine and paper runner (Phase 3)

Nothing here is advice. Agents run backtests and paper cycles; they don't predict anything.

## Where to see them

The 3D City view was removed. Live status, the agent terminal and all controls are now in the
**Operations Center** ([`docs/OPERATIONS.md`](OPERATIONS.md)). The continuous search is done
by the learning optimizer daemon ([`docs/OPTIMIZER.md`](OPTIMIZER.md)). The agent runtime below
still serves manual sweeps, data checks and single paper cycles, and its autopilot is off by
default.

## Agents

| Agent | Sector | Job | What it does |
|---|---|---|---|
| Data Scout | Data | `data_check` | Data health and live-feed check (every 10 min on autopilot) |
| Equity Quant | Research | `sweep` | In-sample parameter sweeps of equity strategies |
| Options Quant | Research | `sweep` | In-sample sweeps of the put credit spread |
| Risk Officer | Risk | (none) | Evaluates every paper order; owns the kill switch |
| Paper Trader | Execution | `paper_cycle` | One paper cycle per active runner per session |

Runtime (`ptl/agents/runtime.py`):

- one asyncio worker and `asyncio.Queue` per agent
- blocking work runs in a thread
- job state and findings go to SQLite (`agent_jobs`, `agent_findings`, append-only)
- events go to an in-process bus that the WebSocket relays

### Sweep rules (the holdout stays honest)

- **Sweeps run in-sample only.** Every combination is a real backtest run in the append-only
  run log, so it counts toward the trial count and the Auditor sees it.
- **Re-running a sweep reuses existing runs.** It doesn't create duplicate trials.
- **The finalist** is the best in-sample Sharpe.
- **One OOS test, and only on request.** With `promote` on (the default for a sweep you start
  yourself), the finalist gets one counted out-of-sample test. If that finalist has already been
  tested out-of-sample, it's not re-tested; the holdout is looked at once.
- **Autopilot never promotes.** It only queues untried in-sample work, so it never touches
  out-of-sample data.
- **Limits:** grids are validated against each strategy's parameter specs and capped at
  `AGENTS_MAX_COMBINATIONS`.

## Strategy plugins

`ptl/strategy/base.py` defines `Strategy`, `EquityStrategy` (target exposure 0–1 per bar) and
`OptionStrategy` (entry/exit decisions).

- Register with `@register`. Files in `ptl/strategy/plugins/` load automatically; see
  `trend_filter.py`.
- Each strategy declares typed `params` (with bounds) and a `sweep_grid`.
- `explain()` must show the arithmetic behind each decision.

## Risk engine

`ptl/risk/engine.py` is a pure function: proposed order + account snapshot + limits → decision.
Every check is stored with its arithmetic.

| Check | Default (.env) |
|---|---|
| Kill switch off (closing orders are always allowed) | — |
| Defined risk only (no naked shorts) | — |
| Max loss per trade | `RISK_MAX_LOSS_PER_TRADE=1000` |
| Max daily loss: a breach **trips the kill switch** | `RISK_MAX_DAILY_LOSS=2000` |
| Max open positions | `RISK_MAX_OPEN_POSITIONS=5` |
| Max capital at risk (fraction of equity) | `RISK_MAX_CAPITAL_AT_RISK_PCT=0.5` |

You can engage or release the kill switch from the Operations Center (a reason is required) or with the
command line: `npm run ptl -- kill-switch on --reason "..."`.

## Paper runner

`ptl/paper/runner.py` runs one cycle: signal → size → risk check → submit → log.

- **Dry run is the default** (UI checkbox and the CLI's `--dry-run`). It logs the intended order
  and never sends it.
  - It reads the paper account read-only if keys exist; otherwise it uses
    `PAPER_DRY_RUN_CAPITAL`.
  - Without a live quote, it sizes off the last close and labels that.
- **Paper mode** sends a market DAY order to the **Alpaca paper account**.
  - It requires keys and a live quote.
  - `AlpacaPaperBroker` re-asserts the paper URL, on both the settings and the client, before
    every submit.
  - There is no live-trading code path.
- **Stale signals are refused.** If the newest bar is older than the last completed NYSE
  session, the cycle refuses ("Import fresh bars first").
- **Everything is logged** in append-only tables: the cycle, the risk decision, each order and
  each status event.
- **Graduation Gate** (tracking only) counts **paper-account fills only**. Dry runs never count.

Command line:

```
npm run ptl -- paper-cycle --strategy sma_crossover --dataset 1 --symbol SPY --param fast=50 --param slow=200
npm run ptl -- paper-cycle ... --paper   # sends to the Alpaca PAPER account
```

## Limitations

- **Single paper cycles are equity-only.** The manual "one paper cycle" control and
  `paper-cycle` CLI refuse option strategies. Options are paper traded by the runner daemon
  as multi-leg spreads (`docs/LIVE_OPS.md`).
- **The agents aren't AI.** They're deterministic job runners. AI-backed agents with a Claude
  lead reviewer are a later phase.
- **The runtime is single-process and in-memory.** Queued jobs don't survive a restart (stale
  jobs are marked failed at startup), and live status resets.
- **The paper runner trades whole shares at market** with no limit orders or partial-fill
  handling beyond status polling.
- **Daily P&L** for the risk check comes from the Alpaca paper account (equity vs last equity).
  In dry run without keys it is 0.
- **Signals need current bars.** You import them yourself; there is no automatic bar download
  yet.
