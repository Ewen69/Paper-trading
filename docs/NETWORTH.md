# Net worth tracker (Phase 4)

Station 6 (`#net-worth`), run by the Accountant. Nothing here is advice.

## Privacy

- Data is **manual entry or CSV import only**. There are no bank logins and no account
  aggregators.
- Everything lives in the local SQLite file (`data/`, gitignored).
- Nothing is sent to a cloud service.
- The HQ card for the Accountant shows counts and dates only, never amounts.
- Exported CSVs contain your financial data. Keep them out of the repo.

## Accounts and balances

- An **account** has a name (unique, case-insensitive), a kind (`asset` or `liability`) and a
  category:
  - assets: cash, brokerage, retirement, real_estate, vehicle, crypto, other
  - liabilities: mortgage, student_loan, auto_loan, credit_card, other_loan, other
- A **balance** is an amount on a date.
  - Amounts are entered as positive numbers; the account's kind sets the sign.
  - They're stored as integer cents, so there's no float drift.
  - One balance per account per date. Entering the same date again replaces it, and the UI
    says so.
  - Future dates are refused, because balances record what was true.
  - Each balance keeps its source (`manual entry` or `CSV import: <file>`) and when it was
    entered.
- **Deleting an account** deletes its balances (the UI asks you to confirm).

## How net worth is computed

Net worth on a date = sum of each asset's **latest balance on or before that date**, minus the
same for liabilities.

- Balances are **carried forward** until a newer one is entered. They're never interpolated.
- Accounts with no balance yet count as **0** and are listed as missing.
- The summary shows the newest and oldest balance dates it used.
- The history chart has one point per date on which you entered any balance. Its tooltip shows
  assets, liabilities, how many values were carried forward, and what was missing.
- A balance older than `NETWORTH_STALE_AFTER_DAYS` (default 45) is flagged **stale**.

## CSV import and export

```
account,kind,category,as_of,amount
Checking,asset,cash,2026-09-30,4200.15
Mortgage,liability,mortgage,2026-09-30,212000
```

- **The header is required**, and so is the `YYYY-MM-DD` date format. Amounts are plain
  positive numbers.
- **Imports are all-or-nothing.** Any bad row rejects the whole file and lists each problem by
  line:
  - wrong column count, bad date or amount
  - a future date or a negative amount
  - a duplicate row in the file
  - a kind or category that conflicts with an existing account
- **Unknown accounts are created.** Rows for an existing account and date replace that balance
  and are counted as "replaced".
- **Exports use the same format,** so they round-trip.

From the UI (Import CSV / Export CSV) or the command line:

```
npm run ptl -- networth-import path/to/balances.csv
npm run ptl -- networth-export path/to/backup.csv
```

## Projection (assumptions)

A what-if range, **not a forecast**:

- It compounds today's net worth at a low and a high fixed nominal annual rate (defaults 4%
  and 6%) over 1–50 years.
- It can add an optional fixed contribution at each year end.
- Formula: `NW × (1 + r)^t + C × ((1 + r)^t − 1) / r` (or `NW + C × t` when r = 0).
- Results are before taxes, fees and inflation. Real returns vary and can be negative.
- **No projection if net worth is zero or negative.** Compounding a negative net worth would
  treat debt as an investment, so nothing is projected.

## Limitations

- USD only; no currency conversion.
- No automatic prices for holdings. A brokerage account is whatever balance you enter.
  Merging paper positions with holdings is Phase 5.
- Debts don't accrue interest between entries; the last balance is carried forward.
- Projections use a fixed rate, with no volatility or sequence-of-returns risk.
