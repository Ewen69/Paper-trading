# Options backtesting (Phase 2b)

The options engine follows the same rules as the stock backtester:
- no look-ahead
- fills at bid/ask, never mid
- costs on everything
- a permanent out-of-sample lock (per underlying, shared with stock backtests)
- logged and counted runs
- bootstrap CIs
- a buy-and-hold benchmark after costs

It adds contract math, collateral, expiration, assignment and pin risk.

## Inputs

- **Option quotes:** end-of-day CSV with an **`exercise_style` column** (`american` or
  `european`). Runs that touch quotes without one are refused; the engine won't guess.
- **Underlying daily bars:** used for settlement, share liquidation, strategy signals and the
  benchmark. Strikes are in raw dollars, so the engine uses **raw** prices. The benchmark uses
  adjusted prices when `adj_close` is present.

## Contract math

- **×100 multiplier everywhere:** premiums, P&L, collateral, exercise and assignment.
  `cash = price per share × 100 × contracts`.
- **Fills:** buys pay **ask + slippage**, sells receive **bid − slippage**, never mid.
  - Slippage = `$ per share + fraction × (ask − bid)`, both configurable.
  - Commission is per contract per leg.
- **Rejected fills:**
  - crossed quotes (bid > ask)
  - a buy with no ask
  - a sale that opens a position when bid − slippage ≤ 0
  - any order that isn't defined-risk
- **Defined risk only:** single long options, or vertical spreads (same underlying, root,
  expiration, type and style; one long and one short; different strikes). **Naked shorts are
  rejected.**

## Collateral

| Structure | Collateral required | Max loss (before commissions) |
|---|---|---|
| Credit vertical | width × 100 × contracts − net credit | same |
| Debit vertical / long option | none (the debit is paid in cash) | the debit |

Before opening, the engine checks
`collateral + debit + commissions ≤ available cash` (cash minus collateral already held). If
the check fails, the order is rejected and logged with the numbers.

## Timeline (each session)

1. **Open:** shares delivered by assignment or exercise are sold (or bought back) at the open,
   with stock slippage.
2. **Quotes:** yesterday's orders fill at today's bid/ask. Strategies decide on day *t*; fills
   happen on day *t+1*.
3. **Close, early assignment:** a short **American** leg in the money with time value
   (ask − intrinsic) ≤ $0.05 (configurable) is assigned. An in-the-money long leg of the same
   spread is exercised with it, so shares net out.
4. **Close, expiration:** settled at the underlying's close on the expiration session.
   - Legs less than **$0.01** in the money (the OCC rule) expire worthless.
   - Long legs in the money are exercised; short legs in the money are assigned.
   - American options deliver shares at the strike; European options settle in cash at
     intrinsic.
   - All legs of a position settle together, so a fully in-the-money spread nets to cash with
     no shares.
   - An expiration that falls on a market holiday settles on the session before it.
5. **Close, mark to market:** longs at bid, shorts at ask (liquidation value), shares at the
   close. A missing quote reuses the last known mark and is counted as a stale mark.
6. **Close, decide:** the strategy sees today's chain and history up to today only.

On the **last session**, open positions are closed at that day's quotes rather than settled,
so no result depends on data after the window.

## Pin risk

When the underlying finishes between the strikes, the short leg is assigned and the long leg
expires worthless. The position ends up holding shares. They're sold at the **next session's
open**, so a weekend or holiday gap can push the loss **beyond the spread's defined max
loss**. This is flagged as `pin_risk`. If assignment leaves cash negative until the shares are
sold, it's also flagged as `assignment_margin`, because a real broker would issue a margin
call.

## Worked example (the unit-test fixture)

**Entry.** 2× SPY 17-Jan-2025 580/575 put credit spread, with slippage $0.02/share and
commission $0.65/contract. Fills at the 3-Jan quotes (580P 3.10/3.20, 575P 2.00/2.08):

| | Calculation | Result |
|---|---|---|
| Sell 580P | 3.10 − 0.02 | 3.08 |
| Buy 575P | 2.08 + 0.02 | 2.10 |
| Net credit | 0.98 × 100 × 2 | $196.00 |
| Commissions | 0.65 × 2 contracts × 2 legs | $2.60 |
| Collateral | 5 × 100 × 2 − 196 | $804.00 |
| Cash needed | 804.00 + 2.60 | $806.60 |

**Outcomes, depending on SPY's close on 17-Jan:**

| SPY close on 17-Jan | What happens | P&L |
|---|---|---|
| 590 (both out of the money) | Both expire worthless | +$193.40 |
| 570 (both in the money) | Buy 200 @ 580, sell 200 @ 575, settled together: −$1,000, no shares | −$806.60 |
| 577 (pin) | Buy 200 @ 580; long expires; sold 21-Jan (after MLK Day) at open 574 × (1 − 5 bps) | −$1,064.00 |
| Early: 7-Jan close 560, 580P ask 20.03 | Time value 0.03 ≤ 0.05: assigned; 575P exercised with it | −$806.60 |

## Strategy: put credit spread

Parameters (all counted as trials when changed):
- `dte`: minimum days to expiration
- `otm_percent`: short strike = highest put strike ≤ close × (1 − otm%)
- `width`: distance to the long strike
- `contracts`
- `take_profit`: close when credit − cost to close ≥ that share of the credit; 0 = hold to
  expiry
- `max_open`

Every open and close is logged with the exact arithmetic that triggered it.

## Warnings added for options

These are stored per run and surfaced by the Auditor:
- `pin_risk`
- `assignment_margin`
- `rejected_fills`
- `stale_marks`
- `quote_quality` (error-level quotes in the window)
- `early_assignment`

## Limitations

- **End-of-day quotes only.** Fills happen at the next day's snapshot, not intraday.
- **Settlement assumptions.**
  - Expiration uses the closing price. AM-settled index options (e.g. SPX monthly) settle on
    the opening print, which isn't modeled.
  - European is assumed to mean cash-settled, and American to mean physical delivery.
- **No dividend data.** Early assignment uses a time-value rule rather than ex-dividend dates.
- **Simplified sizing.** Fixed 100-share multiplier; adjusted contracts after corporate actions
  aren't modeled. No partial closes; positions close whole.
- **Margin.** Only collateral for defined-risk spreads is modeled. A margin call during pin
  risk is flagged, not simulated.
- **No market data ships with the app.** You need to import historical option quotes yourself.
