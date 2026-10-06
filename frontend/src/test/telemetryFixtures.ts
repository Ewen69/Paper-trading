// Test-only telemetry payloads with made-up values. Never imported by app code.

const AS_OF = '2026-10-06T15:00:00+00:00';

export const trialPayload = (over: Record<string, unknown> = {}) => ({
  id: 1,
  created_at: AS_OF,
  search_id: 's-test',
  generation: 2,
  asset: 'equity',
  strategy: 'sma_crossover',
  symbol: 'QQQ',
  params: { fast: 12, slow: 180 },
  period: 'in-sample',
  run_id: 5,
  reused: false,
  trades: 14,
  win_rate: 0.5,
  sharpe: 0.84,
  max_drawdown: -0.12,
  fitness: 0.84,
  verdict: 'evaluated',
  ...over,
});

const curve = [
  { day: '2024-01-02', strategy: 100000, benchmark: 100000 },
  { day: '2024-06-03', strategy: 104000, benchmark: 102000 },
];

export const statePayload = {
  as_of: AS_OF,
  source: 'Local SQLite: learning log, daemon heartbeats, paper log, risk state, your entries',
  daemons: [
    {
      daemon: 'optimizer',
      status: 'running',
      alive: true,
      pid: 101,
      started_at: AS_OF,
      beat_at: AS_OF,
      age_seconds: 1,
      detail: { state: 'searching', target: 'sma_crossover on QQQ (dataset #1)', generation: 2, fresh_trials: 9, budget: 120 },
    },
    { daemon: 'paper-runner', status: 'not running', alive: false, beat_at: null, detail: {} },
  ],
  optimizer: {
    counts: { total: 31, in_sample: 30, out_of_sample: 1, reused: 2, searches: 1 },
    active_best: {
      equity: {
        id: 1,
        created_at: AS_OF,
        asset: 'equity',
        strategy: 'sma_crossover',
        symbol: 'QQQ',
        dataset_id: 1,
        params: { fast: 12, slow: 180 },
        in_sample_fitness: 0.84,
        validated: true,
        reason: 'Highest in-sample fitness so far (0.84); out-of-sample Sharpe 0.40 over 6 trades -> validated.',
        in_sample: trialPayload(),
        out_of_sample: trialPayload({ id: 2, period: 'out-of-sample', sharpe: 0.4, trades: 6, fitness: null }),
        curve: { 'in-sample': curve, 'out-of-sample': curve },
      },
      options: null,
    },
    history: [],
  },
  paper: { orders: [], filled_paper_trades: 0, kill_switch_trips: 0 },
  risk: {
    kill_switch: { engaged: false, reason: '' },
    limits: { max_loss_per_trade: 1000, max_daily_loss: 2000, max_open_positions: 5, max_capital_at_risk_pct: 0.5, max_position_pct: 0.25 },
  },
  auditor: {
    data_health: 'warning',
    flags: [
      {
        code: 'over_tuning',
        severity: 'warning',
        title: 'Over-tuning risk',
        summary: '30 combinations tried on QQQ.',
        source: 'Backtest run log',
        as_of: AS_OF,
        station: 'strategy-lab',
        refs: [],
      },
    ],
    graduation: {
      items: [
        {
          id: 'paper_trades',
          title: '200+ paper trades',
          requirement: 'At least 200 filled paper trades.',
          status: 'not_started',
          progress: '0 of 200 trades.',
          evidence: null,
          source: 'Paper trading log',
          as_of: AS_OF,
        },
      ],
      met: 0,
      total: 4,
      note: 'Tracking only.',
      as_of: AS_OF,
      source: 'Backtest run log and paper trading log (local SQLite)',
    },
  },
  portfolio: { error: 'No data: OperationalError: test' },
  net_worth: {
    totals: { assets: 0, liabilities: 0, net_worth: 0, as_of: null, oldest_included: null, missing: [] },
    history: [],
    source: 'Your manual entries and CSV imports (local SQLite)',
    as_of: AS_OF,
  },
};

export const helloPayload = {
  type: 'hello',
  state: statePayload,
  log: [
    { id: 1, created_at: AS_OF, daemon: 'optimizer', level: 'info', message: 'Gen 1: best in-sample Sharpe 0.84 (fast=12, slow=180).' },
  ],
  trials: [trialPayload()],
};
