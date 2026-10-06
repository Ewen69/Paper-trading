// Test-only net-worth payloads with made-up amounts. Never imported by app code.

const AS_OF = '2026-10-06T15:00:00+00:00';
const categories = {
  asset: ['cash', 'brokerage', 'retirement', 'real_estate', 'vehicle', 'crypto', 'other'],
  liability: ['mortgage', 'student_loan', 'auto_loan', 'credit_card', 'other_loan', 'other'],
};

export const emptyNetWorth = {
  as_of: AS_OF,
  source: 'Your manual entries and CSV imports (local SQLite)',
  data_type: 'manual',
  stale_after_days: 45,
  categories,
  accounts: [],
  totals: { assets: 0, liabilities: 0, net_worth: 0, as_of: null, oldest_included: null, missing: [] },
  history: [],
  method: 'Net worth = assets minus liabilities, using the latest balance of each account.',
};

const acct = (over: Record<string, unknown>) => ({
  id: 1,
  name: 'Checking',
  kind: 'asset',
  category: 'cash',
  note: '',
  latest_amount: 12000,
  latest_as_of: '2026-09-30',
  latest_source: 'manual entry',
  age_days: 6,
  stale: false,
  ...over,
});

export const netWorthPayload = {
  ...emptyNetWorth,
  accounts: [
    acct({}),
    acct({
      id: 2,
      name: 'Old car',
      category: 'vehicle',
      latest_amount: 8000,
      latest_as_of: '2026-06-30',
      age_days: 98,
      stale: true,
    }),
    acct({
      id: 3,
      name: 'Card',
      kind: 'liability',
      category: 'credit_card',
      latest_amount: null,
      latest_as_of: null,
      latest_source: null,
      age_days: null,
      stale: true,
    }),
  ],
  totals: {
    assets: 20000,
    liabilities: 0,
    net_worth: 20000,
    as_of: '2026-09-30',
    oldest_included: '2026-06-30',
    missing: ['Card'],
  },
  history: [
    {
      as_of: '2026-06-30',
      assets: 8000,
      liabilities: 0,
      net_worth: 8000,
      carried_forward: 0,
      missing: ['Checking', 'Card'],
    },
    {
      as_of: '2026-09-30',
      assets: 20000,
      liabilities: 0,
      net_worth: 20000,
      carried_forward: 1,
      missing: ['Card'],
    },
  ],
};

export const projectionPayload = {
  as_of: AS_OF,
  source: 'Your manual entries and CSV imports (local SQLite); growth rates are your assumptions',
  start: 20000,
  start_as_of: '2026-09-30',
  years: 2,
  low_rate: 0.04,
  high_rate: 0.06,
  contribution: 0,
  points: [
    { year: 0, low: 20000, high: 20000 },
    { year: 1, low: 20800, high: 21200 },
    { year: 2, low: 21632, high: 22472 },
  ],
  note: 'Assumption, not a forecast: compounds the current net worth at a fixed nominal annual rate.',
};
