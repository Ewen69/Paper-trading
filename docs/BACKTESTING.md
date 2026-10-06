# How backtests work (Phase 2a)

The goal is honest numbers. Each rule below targets one common way backtests flatter a
strategy.

## Timeline: no look-ahead

For every session the engine does three things, in this order:

1. **Open:** fill any order decided at the previous close.
2. **Close:** mark the position to market and record equity.
3. **Close:** ask the strategy for a target exposure (0 to 100% invested) using bars up to
   **and including today only**. A change becomes an order for **tomorrow's open**.

Strategies get a `History` object that has no way to reach future bars. They are also
stateless: the same history always gives the same answer, so there's nowhere to hide
information. A test proves that changing future prices can't change past decisions.

## Costs and fills

Daily bars have no bid/ask, so **slippage** stands in for half the spread plus market
impact:

- Buys pay `open × (1 + slippage)` and sells receive `open × (1 − slippage)`. Default: 5 bps per
  side.
- Commission is a fixed amount per order plus basis points of notional. Default: $0, which
  matches Alpaca stock trading. Change it to match your broker.
- Fractional shares, long only, no leverage, no shorting.
- Idle cash earns **0%**, so a strategy that sits in cash is not credited with T-bill
  interest. The reality check says so.
- Any open position is **sold at the final close, with costs**, for both the strategy and
  the benchmark. Every result is after all costs.

**Prices.** When every bar has `adj_close`, the engine scales open, high and low by
`adj_close / close`. That gives a total-return series: splits are handled and dividends
reinvested. With fractional shares and percentage costs this is exact. If `adj_close` is
missing, raw prices are used and the report warns that dividends are excluded.

## Benchmark

Every run is compared with **buy-and-hold over the same window, with the same costs**. The
default benchmark is `SPY` from the same dataset if it's there, otherwise buy-and-hold of the
traded symbol. Runs are refused if the benchmark data doesn't cover the window.

## Locked out-of-sample (OOS) period

- The **first** backtest on a symbol permanently locks its most recent **30%** of sessions
  as out-of-sample. The lock is per symbol, across all datasets.
  - To choose a different fraction (10% to 50%), call `POST /backtest/locks` before the
    first run.
- **In-sample runs never receive OOS bars.** The engine is handed only the bars before the
  lock date, and a test proves it.
- **OOS runs** test from the lock date to the end of the data. Earlier bars are used only
  for indicator warm-up. Every OOS run is counted, and from the second look on the report
  warns that the holdout is no longer clean.
- Locks and the run log are **permanent**. Database triggers block edits and deletes, and
  locks are keyed by symbol, so deleting and re-importing data does not reset anything.

## Counting tries (anti p-hacking)

Every run is logged. The reality check shows:
- how many distinct parameter combinations you've tried for this strategy on this symbol
  in-sample
- the same count across all strategies
- the number of in-sample runs and OOS evaluations

From the second combination on, the report warns that the best of several tries is biased
upward.

## Metrics

All metrics are after costs.

| Metric | How it's computed |
|---|---|
| Total / annualized return | From daily equity; annualized over 252 sessions per year. |
| Volatility, Sharpe | Daily returns × √252. Sharpe uses a 0% risk-free rate. |
| Max drawdown | Largest fall from a running peak of equity, with its date. |
| Time in market | Share of sessions with a position at the close. |
| Win rate, expectancy | Per round-trip trade (flat → invested → flat). Expectancy is the **historical** average P&L per trade, not a forecast. |

**Confidence intervals** are 95% bootstrap percentiles:
- **Daily-return metrics** use a moving-block bootstrap with blocks of about n^(1/3)
  sessions, which keeps short-range autocorrelation. Strategy and benchmark are resampled on
  the same blocks, so the excess-return interval is a true paired comparison.
- **Trade metrics** use an i.i.d. bootstrap over trades. There's no interval below 5 trades.
- 2000 resamples with a fixed, reported seed, so every run is reproducible. The bootstrap
  resamples your real returns; it never invents prices.

## Automatic warnings

Every warning comes from a number in the same report:
- fewer than 30 trades
- less than one year of data
- an excess-return CI that includes 0
- the strategy spending its warm-up period in cash
- multiple parameter combinations tried
- repeated OOS looks
- raw (unadjusted) prices, or a price-basis mismatch with the benchmark
- data-quality warnings in the window

**Error-level data problems** (impossible OHLC, non-positive prices) block the run outright.

Each warning is **stored with its run** in the append-only `run_warnings` table, under a stable
code: `low_trades`, `short_sample`, `no_ci`, `ci_includes_zero`, `many_trials`, `oos_repeat`,
`raw_prices`, `basis_mismatch`, `warmup_cash` or `data_quality`. The run summary also stores
the excess-return CI. The Auditor and the Graduation Gate read both; see
[GAME.md](GAME.md).

## Limitations

- Daily bars only, one asset per strategy, long only. Options arrive in Phase 2b.
- Slippage is a flat assumption. Real spreads widen in stressed markets, and the model
  doesn't capture that.
- Fills at the open assume your order would have filled there. Gaps and halts aren't
  modeled.
- The block bootstrap assumes returns are roughly stationary. Regime changes make every
  interval optimistic.
- Trial counting can't see experiments you ran outside this app.
