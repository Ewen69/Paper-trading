# Game layer: agents, Auditor, Graduation Gate (Phase 2.5)

The game layer makes good research habits visible and rewarding. It never makes results look
better than they are.

**Rules:**
- Every agent line, flag, event, XP point and badge is computed by the backend
  (`GET /game/state`) from stored records or the live-source check. The UI only renders it.
  Every line shows its source and timestamp.
- XP rewards process: importing data, sealing an out-of-sample period, and testing
  out-of-sample once after in-sample work.
- **Returns, wins and profit never earn XP.** There are no win streaks, no leaderboards, and no
  celebration animations for P&L.

## Stations (hotkeys 1–4)

| Key | Station | Agent | Shows |
|---|---|---|---|
| 1 | HQ | the whole squad | Agents, Graduation Gate, mission log, badges, XP rules, backend health |
| 2 | Data Health | Data Scout | Datasets, quality findings, live feed, quote check |
| 3 | Strategy Lab | Quant | Backtests, with the **Reality Check first** on every result |
| 4 | Audit | Auditor | Every open flag, plus the Graduation Gate |

Agents for modules that don't exist yet appear as locked silhouettes with the phase that
unlocks them: Risk Officer, Paper Trader, Accountant, Analyst, Lead Reviewer.

## The Auditor

Backtest warnings used to live only inside a single report. Now each run's warnings are
**stored with a stable code** in the append-only `run_warnings` table. The Auditor turns them,
together with data findings, into standing flags.

| Flag | Severity | Comes from |
|---|---|---|
| Impossible rows in a dataset | error | error-level data findings; backtests on those symbols are blocked |
| Live feed unreachable | error | Alpaca paper account check (keys set, but the call failed) |
| Data gaps | warning | `missing_sessions` findings (NYSE sessions with no rows) |
| Suspicious rows | warning | other warning-level data findings |
| Small sample | warning | run warning `low_trades` (fewer than 30 trades) |
| Short history | warning | `short_sample` (under one year) |
| No confidence interval | warning | `no_ci` |
| Over-tuning risk | warning | `many_trials` (several parameter combinations tried in-sample) |
| Holdout reused | warning | `oos_repeat` (out-of-sample looked at more than once) |
| Unadjusted prices / Unfair comparison | warning | `raw_prices` / `basis_mismatch` |
| Data issues inside a backtest window | warning | `data_quality` |
| No clear edge | info | `ci_includes_zero` (excess-return CI includes 0) |
| Warm-up spent in cash | info | `warmup_cash` |
| Pin risk realized / Assignment beyond available cash | warning | options: `pin_risk` / `assignment_margin` |
| Orders not filled / Stale option marks / Bad option quotes | warning | options: `rejected_fills` / `stale_marks` / `quote_quality` |
| Early assignment | info | options: `early_assignment` |
| Dataset not current | info | dataset staleness |
| Live quotes off | info | no paper keys in `.env` |

**Which runs count.** Only the **latest run of each (symbol, strategy, parameters, period)**
is considered, so re-running the same test doesn't pile up flags. Flags are sorted errors
first, then warnings, then notes. Each flag links to the station that can fix it.

## Reality Check

On every Strategy Lab result the Reality Check comes **first, above the chart**.
- **Headline tiles:** sample, trades, excess return with its 95% CI, combinations tried,
  out-of-sample looks, and costs.
- **Flagged tiles:** a tile is marked "⚠ flagged" exactly when the backend raised the matching
  warning code, so the UI never re-implements thresholds.
- **Auditor says:** the run's warnings, word for word.
- **All assumptions:** price basis, cash yield, bootstrap settings, fill model and lock basis,
  in a section you can expand.

**Before you run:** selecting data shows a callout with its sessions, price basis,
out-of-sample lock status, and how many runs and out-of-sample looks it already has.

## Graduation Gate

A checklist of the evidence you'd want before trusting a strategy with real money. **It only
tracks.** Nothing reads it to change behavior, and no code path can enable live trading.

| Item | Met when | Status today |
|---|---|---|
| 3 months of paper trading | 90+ days between first and latest paper-account fill | From the paper log (fills only; dry runs never count) |
| 200+ paper trades | 200 filled, logged paper-account trades | From the paper log (fills only; dry runs never count) |
| Beats buy-and-hold out-of-sample | A symbol's **first** out-of-sample test has a 95% CI for annualized excess return (after costs) entirely above 0 | Computed from the run log |
| Zero risk-limit breaches | No kill-switch trips from paper-account cycles (rejected orders are the limits working, not breaches) | From the risk decision log, once there are fills |

Only first looks count. A later out-of-sample run that passes after an earlier one failed does
not meet the item. Runs logged before CIs were stored count as "no stored CI", never as a pass.

## XP and badges

| XP | For |
|---|---|
| +20 | importing a dataset |
| +30 | sealing a symbol's out-of-sample period |
| +15 | the first in-sample backtest on a symbol |
| +50 | the first out-of-sample test of a symbol, after in-sample work on it |
| 0 | everything else, including repeat looks at a holdout (the mission log says why) |

Badges:
- **First Contact:** import your first dataset.
- **Clean Room:** import a dataset with no quality findings.
- **Sealed Vault:** lock out-of-sample data.
- **Restraint:** try 3 or fewer combinations before an out-of-sample test.
- **One Shot:** look at a holdout exactly once. You lose this badge if you look again.
- **Cost Realist:** run a backtest with 10+ bps slippage.

## Limitations

- Agents restate computed facts; they are not AI yet. See the plan for AI-backed agents.
- The paper-trading gate items stay "not started" until the Alpaca paper account has fills.
  Dry runs never count.
- Risk Officer and Paper Trader report on HQ. Their live status and controls are in the City
  (`docs/AGENTS.md`).
- Runs logged before this phase have no stored warnings or CIs. The Auditor and the gate treat
  them as unknown, never as clean.
