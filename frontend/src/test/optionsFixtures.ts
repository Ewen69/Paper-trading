// Test-only options payloads. Never imported by app code.
import { reportPayload } from './fakeFetch';

const AS_OF = '2026-10-05T15:00:00+00:00';

export const putSpreadStrategy = {
  id: 'put_credit_spread',
  asset: 'options',
  name: 'Put credit spread',
  description: 'Sell an out-of-the-money put vertical (defined risk).',
  params: [
    { name: 'dte', label: 'Min days to expiration', default: 30, minimum: 1, maximum: 120, description: 'Calendar days.' },
    { name: 'width', label: 'Spread width ($)', default: 5, minimum: 1, maximum: 100, description: 'Strike distance.' },
  ],
};

export const optionsUniversePayload = {
  as_of: AS_OF,
  source: 'Imported option and equity datasets (local SQLite)',
  entries: [
    {
      dataset_id: 2,
      underlying: 'SPY',
      source: 'Vendor Y',
      file_name: 'spy_options.csv',
      first_date: '2025-01-02',
      last_date: '2025-01-17',
      quote_days: 11,
      rows: 26,
      rows_missing_style: 0,
      lock: null,
      underlying_datasets: [
        { dataset_id: 1, first_date: '2024-06-03', last_date: '2025-01-21', sessions: 160 },
      ],
    },
  ],
};

const provenance = (source: string) => ({
  source,
  as_of: AS_OF,
  data_type: 'end-of-day',
  stale: false,
  stale_reason: null,
});

export const optionsReportPayload = {
  ...reportPayload,
  run_id: 11,
  strategy: { id: 'put_credit_spread', name: 'Put credit spread', params: { dte: 10, width: 5 } },
  reality_check: {
    ...reportPayload.reality_check,
    price_basis: 'raw',
    cost_lines: [
      { label: 'Option slippage', value: '$0.02/share + 0% of the bid-ask spread' },
      { label: 'Commission', value: '$0.65 per contract' },
      { label: 'Assignment/exercise fee', value: '$0.00 per contract' },
    ],
    warnings: [
      {
        code: 'pin_risk',
        text: '1 position(s) finished between their strikes. Shares were assigned and sold at the next open.',
      },
    ],
  },
  trades: [
    {
      position_id: 1,
      opened: '2025-01-03',
      closed: '2025-01-21',
      description: 'SPY 2025-01-17 580/575 put credit spread x2',
      structure: 'credit_vertical',
      contracts: 2,
      entry_premium: 196,
      max_loss: 804,
      pnl: -1064,
      exit_kind: 'pin risk: assigned, shares liquidated next open',
      sessions_held: 11,
    },
  ],
  events: [
    {
      day: '2025-01-17',
      kind: 'pin',
      position_id: 1,
      text: 'Pin risk: 577 finished between the strikes. Net +200 shares to liquidate.',
      cash: 0,
    },
  ],
  counts: {
    rejected_orders: 0,
    stale_marks: 0,
    pin_events: 1,
    early_assignments: 0,
    margin_shortfalls: 1,
    peak_collateral: 1000,
  },
  provenance: provenance('Option quotes: Vendor Y (spy_options.csv, dataset #2)'),
  underlying_provenance: provenance('SPY raw bars: Vendor X (spy.csv, dataset #1)'),
  benchmark_provenance: provenance('SPY buy-and-hold on Vendor X (spy.csv, dataset #1)'),
};
