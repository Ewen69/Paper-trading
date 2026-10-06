// Test-only game-state payload. Never imported by app code.

const AS_OF = '2026-10-05T15:00:00+00:00';

const line = (text: string, source = 'Imported datasets (local SQLite)') => ({
  text,
  source,
  as_of: AS_OF,
});

const lockedAgent = (id: string, name: string, unlocks: string) => ({
  id,
  name,
  role: `${name} role`,
  station: null,
  status: 'locked',
  unlocks_in: unlocks,
  xp: 0,
  level: 0,
  report: [],
});

export const repeatLookEvent = {
  at: '2026-10-05T16:00:00+00:00',
  agent: 'quant',
  kind: 'run_out-of-sample',
  text: 'Out-of-sample test #4 sma_crossover (fast=50, slow=200) on SPY.',
  xp: 0,
  xp_note: 'repeat look at the holdout: no XP',
  source: 'Backtest run log (local SQLite), run #4',
};

export const gameStatePayload = {
  as_of: AS_OF,
  source: 'Computed from local records',
  market_open: false,
  last_completed_session: '2026-10-05',
  player: { xp: 115, level: 2, level_start_xp: 100, next_level_xp: 200 },
  agents: [
    {
      id: 'scout',
      name: 'Data Scout',
      role: 'Imports data and checks it',
      station: 'data-health',
      status: 'warning',
      unlocks_in: null,
      xp: 20,
      level: 1,
      report: [
        line('1 dataset(s), 2,515 rows. 1 not current, 0 with error-level findings.'),
        line(
          'Live feed offline: paper API keys are not set in .env.',
          'Alpaca paper account check',
        ),
      ],
    },
    {
      id: 'quant',
      name: 'Quant',
      role: 'Runs honest backtests',
      station: 'strategy-lab',
      status: 'ok',
      unlocks_in: null,
      xp: 95,
      level: 2,
      report: [
        line(
          '3 backtest(s) logged (2 in-sample, 1 out-of-sample) on 1 symbol(s).',
          'Backtest run log (local SQLite)',
        ),
      ],
    },
    lockedAgent('lead', 'Lead Reviewer', 'AI agents phase'),
  ],
  events: [
    {
      at: '2026-10-05T14:00:00+00:00',
      agent: 'quant',
      kind: 'run_out-of-sample',
      text: 'Out-of-sample test #3 sma_crossover (fast=50, slow=200) on SPY.',
      xp: 50,
      xp_note: null,
      source: 'Backtest run log (local SQLite), run #3',
    },
    {
      at: '2026-10-05T13:00:00+00:00',
      agent: 'scout',
      kind: 'import',
      text: 'Imported spy.csv: 2,515 rows of daily bars from Vendor X.',
      xp: 20,
      xp_note: null,
      source: 'Dataset #1 (local SQLite)',
    },
  ],
  badges: [
    {
      id: 'first_contact',
      name: 'First Contact',
      description: 'Import your first dataset.',
      earned: true,
      earned_at: '2026-10-05T13:00:00+00:00',
      detail: 'spy.csv',
    },
    {
      id: 'cost_realist',
      name: 'Cost Realist',
      description: 'Run with 10+ bps slippage.',
      earned: false,
      earned_at: null,
      detail: null,
    },
  ],
  xp_rules: [{ id: 'import', description: 'Import a historical dataset', xp: 20 }],
  xp_policy: 'XP is earned for research process only. Returns, wins and profit never earn XP.',
  flags: [
    {
      code: 'data_gaps',
      severity: 'warning',
      title: 'Data gaps in spy.csv',
      summary: 'SPY: 4 missing NYSE sessions (2025-01-03..2025-01-08).',
      source: 'Dataset #1 quality checks (Vendor X)',
      as_of: AS_OF,
      station: 'data-health',
      refs: ['dataset #1'],
    },
    {
      code: 'low_trades',
      severity: 'warning',
      title: 'Small sample',
      summary: '1 current result(s): #3 SPY sma_crossover (out-of-sample). Latest: Only 4 trade(s).',
      source: 'Backtest run log, warnings stored with run(s) #3 SPY sma_crossover (out-of-sample)',
      as_of: AS_OF,
      station: 'strategy-lab',
      refs: ['run #3'],
    },
    {
      code: 'live_off',
      severity: 'info',
      title: 'Live quotes off',
      summary: 'Paper API keys are not set in .env, so live quotes show no data.',
      source: 'Alpaca paper account check',
      as_of: AS_OF,
      station: 'data-health',
      refs: [],
    },
  ],
  graduation: {
    items: [
      {
        id: 'paper_trades',
        title: '200+ paper trades',
        requirement: 'At least 200 filled paper trades, all logged.',
        status: 'not_started',
        progress: '0 of 200 trades.',
        evidence: null,
        source: 'Paper trading log: Alpaca paper-account fills only; dry runs never count (local SQLite)',
        as_of: AS_OF,
      },
      {
        id: 'oos_edge',
        title: 'Beats buy-and-hold out-of-sample',
        requirement: "On a symbol's first out-of-sample test, the 95% CI is entirely above 0.",
        status: 'not_met',
        progress: '1 first look(s); none with a 95% CI entirely above 0.',
        evidence: null,
        source: 'Backtest run log (first out-of-sample run per symbol)',
        as_of: AS_OF,
      },
    ],
    met: 0,
    total: 2,
    note: 'Tracking only. Meeting every item does not, and cannot, enable live trading in this app.',
    as_of: AS_OF,
    source: 'Backtest run log and paper trading log (local SQLite)',
  },
};
