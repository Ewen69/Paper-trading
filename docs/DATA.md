# Data guide

Paper Trading Lab uses two kinds of data. Each one is stored and shown with its **source**, its
**as-of time**, its **data type** (real-time, delayed, end-of-day, manual), and a **stale** flag.

| Kind | Where it comes from | Used for |
|---|---|---|
| Live quotes | Alpaca market data (needs paper API keys in `.env`) | Data Health checks; paper-runner sizing (paper mode requires a live quote) |
| Historical data | CSV files you import | Backtesting (Phase 2) |

## Live quotes (Alpaca)

| Setting | Free plan default | What you get |
|---|---|---|
| `ALPACA_STOCK_FEED=iex` | yes | Real-time quotes from the **IEX exchange only**. That's a small share of US volume, so spreads can look wider than the true national best bid/offer. |
| `ALPACA_STOCK_FEED=sip` | paid | Real-time consolidated quotes. |
| `ALPACA_STOCK_FEED=delayed_sip` | | Consolidated quotes, delayed 15 minutes. |
| `ALPACA_OPTIONS_FEED=indicative` | yes | **Delayed and modified** option quotes. Fine for checking the pipeline, but not for judging fills. |
| `ALPACA_OPTIONS_FEED=opra` | paid | Real-time OPRA option quotes. |

Quotes are flagged **stale** when they're older than `REAL_TIME_MAX_AGE_SECONDS` (default 120)
or `DELAYED_MAX_AGE_SECONDS` (default 1800) while the market is open. When the market is closed,
a quote is stale if it wasn't updated within that window of the last close. The NYSE calendar
(holidays, early closes, special closures) comes from `exchange_calendars`.

The Data Health page checks your keys by reading the paper account's status (read-only). Phase 1
places no orders.

## Importing historical CSV files

```bash
npm run ptl -- import-csv path/to/file.csv --kind equity-bars --source "Where it came from"
npm run ptl -- import-csv path/to/options.csv --kind option-quotes --source "Vendor name"
npm run ptl -- datasets
npm run ptl -- delete-dataset 3
```

- `--source` is required. Name the vendor or site so every number can be traced back to it.
- `--data-type` is `end-of-day` (default) or `manual`.
- `--date-format` takes a Python `strptime` pattern. The default is `%Y-%m-%d`; for US-style
  dates use `%m/%d/%Y`.
- `--map canonical=Header` renames one of your file's columns to the name below, e.g.
  `--map adj_close="Adj Close"`. Repeat it for each column. Headers are case-insensitive.

**All or nothing.** If any row can't be parsed (a missing value, a non-number, a bad date, or a
duplicate key), the whole file is rejected with line numbers and nothing is stored. Importing
the same file twice does nothing; files are identified by SHA-256 hash.

**Quality problems are kept, not fixed.** Crossed markets, gaps, zero volume and similar findings
are stored next to the dataset and shown on the Data Health page. The importer never fills,
smooths or drops rows. In Phase 2 the backtester must refuse to fill against error-level rows.

### Equity daily bars (`--kind equity-bars`)

| Column | Required | Notes |
|---|---|---|
| `symbol` | yes | Uppercased automatically. |
| `date` | yes | Session date. |
| `open`, `high`, `low`, `close` | yes | Raw (unadjusted) prices. |
| `volume` | yes | Whole number. |
| `adj_close` | no | Split- and dividend-adjusted close, if your source provides it. |

### Option end-of-day quotes (`--kind option-quotes`)

| Column | Required | Notes |
|---|---|---|
| `underlying` | yes | e.g. `SPY` |
| `quote_date` | yes | Date of the quote snapshot. |
| `expiration` | yes | Contract expiration date. |
| `strike` | yes | |
| `option_type` | yes | `call`/`put` or `C`/`P` |
| `bid`, `ask` | yes | The backtester fills at these, never at mid or last. |
| `root` | no | Option root when it differs from the underlying (e.g. `SPXW`). |
| `exercise_style` | no (import) / **yes for options backtests** | `american` (physical delivery, can be assigned early) or `european` (cash-settled). Options backtests refuse quotes without it; see [OPTIONS.md](OPTIONS.md). |
| `bid_size`, `ask_size`, `volume`, `open_interest` | no | Whole numbers. |
| `last`, `underlying_price`, `implied_volatility`, `delta`, `gamma`, `theta`, `vega` | no | Stored as the vendor provided them. |

Empty cells and `NA`, `N/A`, `NaN`, `null`, `none`, `-` all count as missing. A missing optional
value is stored as missing; it is never replaced with zero.

### Quality checks

| Check | Severity | Meaning |
|---|---|---|
| `inconsistent_ohlc`, `non_positive_price`, `negative_volume` | error | Bar is internally impossible. |
| `crossed_market`, `negative_price`, `quote_after_expiration`, `non_positive_strike` | error | Quote can't be used for a fill. |
| `missing_sessions` | warning | NYSE sessions with no rows between a symbol's first and last date. Holidays don't count. |
| `non_session_date` | warning | Row dated on a day the NYSE was closed. |
| `zero_volume`, `extreme_move` | warning | Suspicious bar. An extreme move is often an unadjusted split. |
| `locked_market`, `no_offer`, `repeated_quote` | warning | Bid equals ask, no ask, or identical quotes 5+ days in a row (possibly stale). |
| `zero_bid` | info | Normal for far out-of-the-money options. |
| `outside_calendar` | info | Date before 1990 or past the calendar's range, so gaps can't be checked. |

A dataset is flagged **stale** when its last date is more than `DATASET_STALE_AFTER_SESSIONS`
(default 5) sessions behind the last completed session. For a deliberately historical
backtesting file, that just means "not current", and the flag says so.

## Where to get data

Alpaca does not offer free historical options data. Free daily stock/ETF bars can be downloaded
from several sites, and historical options EOD data is usually sold by specialist vendors. Check
each source's license and quality before relying on it. Whatever you use, record it in `--source`.
