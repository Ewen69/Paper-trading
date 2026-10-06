# Live ops: data sync, re-validation, options paper execution, toasts (Phase 7)

Nothing here is advice. Paper trading uses simulated money on the Alpaca **paper** account,
and dry run (the default) sends nothing.

## Daily equity data sync (`ptl/data/daily_sync.py`)

**When:** inside the runner daemon, once the newest session's bar should exist (the close plus
`SYNC_DELAY_MINUTES`, default 20). That includes startup, when the data is stale. If Alpaca
doesn't have the bar yet, it retries every `SYNC_RETRY_MINUTES` (default 15). It needs paper
API keys (market data only) and `SYNC_ENABLED=true`.

**What:**

- **Symbols:** every symbol the app uses: imported equity data, holdings (stocks, ETFs and
  option underlyings), Active Bests and the benchmark. The list is capped by
  `SYNC_MAX_SYMBOLS`.
- **One sync dataset per symbol** (`alpaca-sync:SYMBOL`):
  - The first sync backfills from `SYNC_BACKFILL_START`; later syncs append each newly
    completed session. Partial days are never stored.
  - Each row has raw OHLC and volume, plus a split- and dividend-adjusted close.
- **Corporate actions:** if the adjusted close of the last stored session changes (a split or
  dividend), the whole adjusted series is re-fetched so it stays consistent.
- **Quality checks** re-run after every sync and feed Data Health and the Auditor.
- **Provenance:**
  - source: "Alpaca market data (<feed>); daily sync"
  - data type: end-of-day
  - On the free IEX feed, prices and volume come from one venue, not the consolidated tape.

## After a sync: re-check, never reset

Each symbol's out-of-sample start date is locked, so new bars land in the **out-of-sample**
window. In-sample backtests give the same answers as before. Resetting the trial budget would
only allow more searching of unchanged data, which is the over-searching the budget prevents.
So, after new bars arrive for the equity Active Best's symbol:

1. The Active Best's parameters get **one more counted out-of-sample run** on the fresh
   dataset. It's logged in the backtest run log, the learning log
   ("re-validation look (counted)") and the Auditor's holdout count.
2. A new Active Best row is recorded, with the same in-sample evidence and the fresh dataset,
   **validated or not** by the same rule as before (out-of-sample Sharpe > 0 with enough
   trades).
3. The paper runner trades it only if validated.

The optimizer daemon picks up new targets (for example a newly synced symbol with enough
history) on its next pass. Spent budgets stay spent.

## Options paper execution (`ptl/paper/options_cycle.py`)

**When it runs:** for a **validated** options Active Best, only while the market is open, at
most one opening round per day. Open spreads are checked every tick.

**Opening a spread:**

1. **Signal:** the strategy's own logic, the same code as in the backtest, runs on live
   indicative quotes. For a put credit spread: the first expiration at least `dte` days out,
   short strike ≈ close × (1 - otm%), long strike = short - width.
2. **Price:** credit = short **bid** - long **ask** per share, never mid. No credit means no
   order.
3. **Collateral:** width × 100 × contracts - credit × 100 × contracts. That's the spread's
   maximum loss.
4. **Sizing:** contracts = the tightest of the strategy's count, max loss per trade (with
   commissions), max position size and capital-at-risk headroom. The rationale names the
   binding limit.
5. **Risk engine:** defined risk only; max loss, daily loss, open positions, capital at risk,
   position size and kill switch. A daily-loss breach trips the kill switch.
6. **Order:** ONE multi-leg (mleg) limit order through the Alpaca **paper** endpoint,
   re-checked before every submit.
   - Opening: sell-to-open the short, buy-to-open the long, with a credit limit (negative
     price).
   - Both legs fill together or not at all.

**Closing a spread:**

- **When:** the strategy's take-profit rule (captured ≥ X% of the credit), or
  `OPTIONS_CLOSE_DAYS_BEFORE_EXPIRY` (default 1) days before expiration, to avoid assignment
  and pin risk. Closing only reduces risk, so it's allowed even with the kill switch on.
- **How:** buy-to-close the short and sell-to-close the long, with a debit limit (short ask -
  long bid).

**Logs:**

- the cycle, with the quote at submission and its source
- the risk decision and its arithmetic
- the order and every status change, with the fill price
- slippage against the quoted credit
- a `paper_spreads` row tracking open → closing → closed. A canceled or rejected opening order
  marks the spread "void".
- **Dry run** logs the same tickets and assumes fills at the quoted prices. This is labeled.

## Toasts (frontend `src/game/toasts.ts`)

- **No toast** for optimizer trials, routine log lines, or zero-XP research events. They
  scroll through the agent terminal and the HQ mission log.
- **Toast** for: new Active Best, re-validation, trial budget exhausted, optimizer idle, a risk
  limit blocking an order, a kill-switch trip, data sync, paper orders, XP earned, and holdout
  warnings.
- **Merging:** repeats of the same kind within 15 seconds merge into one toast with a counter
  (x3). At most 3 are visible, each for 6 seconds. More than 3 research events at once
  collapse into one summary.
- **Classification happens in the backend.** Each daemon log line carries a `kind`.
