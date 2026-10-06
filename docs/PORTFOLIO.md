# Portfolio analysis (Phase 5)

Station 7 (`#portfolio`), run by the Analyst. Nothing here is advice. The rule checks flag facts
to look at; they don't tell you what to do.

## Books

- **My holdings:** what you enter or import. Stored in local SQLite only.
- **Paper account:** positions read **read-only** from the Alpaca paper account (simulated
  money). Nothing is stored, and no order is placed from this page. Without keys it shows "no
  data".
- **Combined:** both, each position tagged with its book. Simulated paper positions are never
  mixed in silently.

## Holdings

Each holding has these fields:

- **symbol** (or a name for funds), **asset class** (stock, etf, fund, bond, cash, option,
  crypto, other), **quantity** and an **account label**.
- **Cash:** the quantity is dollars.
- **Options:** the underlying symbol, type, strike, expiration and whole contracts. A negative
  quantity means short.
- **Manual price + date** (optional): used only when no imported bars exist for the symbol.
  Handy for funds with no market feed.

CSV import and export (all-or-nothing; "replace all" is optional):

```
symbol,asset_class,quantity,account,option_type,strike,expiration,manual_price,manual_price_as_of
VTI,etf,120,Brokerage,,,,,
SPY,option,-1,Brokerage,put,540,2026-12-18,,
Target 2050,fund,40,401k,,,,61.20,2026-09-30
Cash,cash,2500,Bank,,,,,
```

## How each holding is valued

| Holding | Price | Source shown |
|---|---|---|
| Stock / ETF / fund / bond / crypto / other | Newest imported end-of-day close | dataset # and date; stale after `DATASET_STALE_AFTER_SESSIONS` |
| ...with no imported bars | Your manual price | "Manual price you entered" and its date; stale after `NETWORTH_STALE_AFTER_DAYS` |
| ...with neither | **Not valued**; listed and excluded from every total | "No price source" |
| Cash | Face value | — |
| Option | Newest imported quote: **bid for a long, ask for a short** (what closing would get), × 100 × contracts | dataset # and quote date |
| Paper position | Alpaca's market value | "Alpaca paper account (simulated money, read-only)" |

## What's computed

- **Allocation** by asset class, as a share of the net total. Short options show as negative.
- **Concentration** over non-cash long positions, grouped by symbol:
  - HHI (sum of squared weights)
  - effective N (1 / HHI)
  - the top 5 positions and their combined weight
- **Risk replay** against a benchmark you choose (`PORTFOLIO_BENCHMARK`, default SPY). It
  reports annualized volatility, max drawdown, beta, correlation and the benchmark's own
  volatility.
  - It replays **today's weights** over the last N sessions (`PORTFOLIO_WINDOW_SESSIONS`,
    default 252), rebalanced daily. It shows how the current mix would have moved, not how
    your account did.
  - It only uses dates on which every included symbol and the benchmark have a price, and
    needs at least 20 returns.
  - Cash earns 0. Options and unpriced holdings are excluded and listed, and coverage is
    shown.
  - Adjusted closes are used when every bar has them; otherwise the page says the prices are
    unadjusted.
- **Option Greeks:** delta (share-equivalents), theta ($/day) and vega (per 1 IV point, as the
  vendor quotes it). Totals come from vendor Greeks on the newest imported quote, × 100 ×
  contracts. Contracts with no Greeks are listed; nothing is estimated.

## Rule checks

Each rule shows the observed value and its threshold. A rule is **fired**, **clear**, or **not
evaluated** (not enough data).

| Rule | Fires when |
|---|---|
| Largest position | a non-cash position is above 20% of non-cash long value |
| Diversification | effective N is below 5 |
| Volatility | replayed volatility is above 25% a year |
| Beta to benchmark | beta is above 1.3 |
| Risk coverage | risk metrics cover less than 80% of long value |
| Stale prices | any price is stale |
| Unpriced holdings | any holding has no price source |
| Short options | any option quantity is negative (an undefined-risk warning) |
| Expired options | any option is past expiration |

## Limitations

- **End-of-day prices only.** Holdings use imported bars, not live quotes.
- **No transaction history,** so there's no realized P&L, cost basis or tax lots.
- **Short legs are checked one at a time.** The short-option rule doesn't pair them with longs
  to recognize a spread.
- **The risk replay is backward-looking.** It assumes constant weights and covers only symbols
  with imported history.
- **USD only.**
