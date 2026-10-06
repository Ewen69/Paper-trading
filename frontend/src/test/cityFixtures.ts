// Test-only live-agent payloads. Never imported by app code.

const AS_OF = '2026-10-06T15:00:00+00:00';

export const agentPayload = (overrides: Record<string, unknown> = {}) => ({
  id: 'quant-equity',
  name: 'Equity Quant',
  sector: 'research',
  role: 'Sweeps equity strategies in-sample',
  status: 'idle',
  message: '',
  job_id: null,
  params: null,
  progress_done: null,
  progress_total: null,
  queued: 0,
  autopilot: true,
  updated_at: AS_OF,
  ...overrides,
});

export const findingPayload = {
  id: 7,
  job_id: 3,
  agent_id: 'quant-equity',
  created_at: AS_OF,
  symbol: 'SPY',
  strategy: 'sma_crossover',
  params: { fast: 20, slow: 150 },
  period: 'in-sample',
  run_id: 12,
  trades: 9,
  win_rate: 0.5556,
  sharpe: 0.81,
  total_return: 0.12,
  excess_annualized: -0.01,
  note: 'In-sample: optimistic by construction.',
};

export const snapshotPayload = {
  type: 'snapshot',
  as_of: AS_OF,
  source: 'Agent runtime (local SQLite)',
  sectors: [
    { id: 'data', name: 'Data Ingestion Sector', purpose: 'Imports and checks', slots: 4, agents: ['scout'] },
    {
      id: 'research',
      name: 'Strategy Research Sector',
      purpose: 'In-sample sweeps',
      slots: 6,
      agents: ['quant-equity'],
    },
    { id: 'risk', name: 'Options Risk Sector', purpose: 'Limits', slots: 4, agents: ['risk-officer'] },
  ],
  agents: [
    agentPayload({ id: 'scout', name: 'Data Scout', sector: 'data', role: 'Checks data' }),
    agentPayload(),
    agentPayload({
      id: 'risk-officer',
      name: 'Risk Officer',
      sector: 'risk',
      role: 'Evaluates every paper order',
      status: 'watching',
      autopilot: false,
    }),
  ],
  findings: [],
  kill_switch: { engaged: false, reason: '', changed_at: null },
};

export const riskStatePayload = {
  as_of: AS_OF,
  source: 'Risk settings (.env) and local SQLite',
  kill_switch_engaged: false,
  kill_switch_reason: '',
  kill_switch_changed_at: null,
  limits: {
    max_loss_per_trade: 1000,
    max_daily_loss: 2000,
    max_open_positions: 5,
    max_capital_at_risk_pct: 0.5,
  },
  decisions: [],
};
