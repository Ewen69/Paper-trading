/**
 * Runtime schemas for every backend payload. A payload that doesn't match is an error that the UI
 * shows as "No data". We never patch over it with defaults.
 */
import { z } from 'zod';

const isoDateTime = z.iso.datetime({ offset: true }).transform((s) => new Date(s));
const isoDate = z.iso.date();

export const dataTypeSchema = z.enum(['real-time', 'delayed', 'end-of-day', 'manual']);
export type DataType = z.infer<typeof dataTypeSchema>;

export const provenanceSchema = z.object({
  source: z.string(),
  as_of: isoDateTime,
  data_type: dataTypeSchema,
  stale: z.boolean(),
  stale_reason: z.string().nullable(),
});
export type Provenance = z.infer<typeof provenanceSchema>;

export const healthSchema = z.object({
  status: z.literal('ok'),
  version: z.string(),
  trading_mode: z.literal('paper'),
  broker_base_url: z.string(),
  broker_credentials_configured: z.boolean(),
  source: z.string(),
  as_of: isoDateTime,
});
export type Health = z.infer<typeof healthSchema>;

const severitySchema = z.enum(['error', 'warning', 'info']);
export type Severity = z.infer<typeof severitySchema>;
const statusSchema = z.enum(['ok', 'warning', 'error']);
export type Status = z.infer<typeof statusSchema>;

export const issueSchema = z.object({
  check: z.string(),
  severity: severitySchema,
  symbol: z.string(),
  count: z.number().int(),
  first_date: isoDate,
  last_date: isoDate,
  detail: z.string(),
});
export type Issue = z.infer<typeof issueSchema>;

const feedSchema = z.object({ name: z.string(), data_type: dataTypeSchema, note: z.string() });
export type Feed = z.infer<typeof feedSchema>;

export const dataHealthSchema = z.object({
  overall: z.enum(['ok', 'warning', 'error', 'no data']),
  as_of: isoDateTime,
  source: z.string(),
  market_open: z.boolean(),
  last_completed_session: isoDate,
  live_source: z.object({
    name: z.string(),
    status: statusSchema,
    configured: z.boolean(),
    reachable: z.boolean().nullable(),
    account_status: z.string().nullable(),
    error: z.string().nullable(),
    checked_at: isoDateTime,
    stock_feed: feedSchema,
    option_feed: feedSchema,
  }),
  datasets: z.array(
    z.object({
      id: z.number().int(),
      kind: z.enum(['equity_bars', 'option_quotes']),
      file_name: z.string(),
      row_count: z.number().int(),
      symbol_count: z.number().int(),
      coverage_start: isoDate,
      coverage_end: isoDate,
      status: statusSchema,
      issues: z.array(issueSchema),
      provenance: provenanceSchema,
    }),
  ),
});
export type DataHealth = z.infer<typeof dataHealthSchema>;
export type Dataset = DataHealth['datasets'][number];

export const quoteSchema = z.object({
  symbol: z.string(),
  bid: z.number(),
  ask: z.number(),
  spread: z.number(),
  bid_size: z.number(),
  ask_size: z.number(),
  provenance: provenanceSchema,
  issues: z.array(issueSchema),
});
export type Quote = z.infer<typeof quoteSchema>;

// ---- Backtesting (Strategy Lab) ----

const intervalSchema = z.object({ low: z.number(), high: z.number() }).nullable();
const estimateSchema = z.object({ value: z.number().nullable(), ci95: intervalSchema });
export type Estimate = z.infer<typeof estimateSchema>;

export const strategySchema = z.object({
  id: z.string(),
  asset: z.enum(['equity', 'options']),
  name: z.string(),
  description: z.string(),
  params: z.array(
    z.object({
      name: z.string(),
      label: z.string(),
      default: z.number().int(),
      minimum: z.number().int(),
      maximum: z.number().int(),
      description: z.string(),
    }),
  ),
});
export type Strategy = z.infer<typeof strategySchema>;
export const strategiesSchema = z.array(strategySchema);

const lockSchema = z.object({
  symbol: z.string(),
  oos_start: isoDate,
  oos_fraction: z.number(),
  locked_at: isoDateTime,
  basis: z.string(),
});

export const universeSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  entries: z.array(
    z.object({
      dataset_id: z.number().int(),
      symbol: z.string(),
      source: z.string(),
      file_name: z.string(),
      first_date: isoDate,
      last_date: isoDate,
      sessions: z.number().int(),
      has_adj_close: z.boolean(),
      lock: lockSchema.nullable(),
      in_sample_runs: z.number().int(),
      oos_evaluations: z.number().int(),
    }),
  ),
});
export type Universe = z.infer<typeof universeSchema>;
export type UniverseEntry = Universe['entries'][number];

const performanceSchema = z.object({
  sessions: z.number().int(),
  final_equity: z.number(),
  total_return: z.number(),
  annualized_return: estimateSchema,
  annual_volatility: z.number().nullable(),
  sharpe: estimateSchema,
  max_drawdown: z.number(),
  max_drawdown_date: isoDate.nullable(),
  exposure: z.number(),
  trade_count: z.number().int(),
  win_rate: estimateSchema,
  expectancy: estimateSchema,
  avg_win: z.number().nullable(),
  avg_loss: z.number().nullable(),
  total_costs: z.number(),
});
export type Performance = z.infer<typeof performanceSchema>;

const periodSchema = z.enum(['in-sample', 'out-of-sample']);
export type Period = z.infer<typeof periodSchema>;

export const realityCheckSchema = z.object({
  period: periodSchema,
  window_start: isoDate,
  window_end: isoDate,
  sessions: z.number().int(),
  trade_count: z.number().int(),
  parameter_combinations_tried: z.number().int(),
  parameter_combinations_tried_all_strategies: z.number().int(),
  in_sample_runs: z.number().int(),
  oos_evaluations: z.number().int(),
  oos_start: isoDate,
  oos_locked_at: isoDateTime,
  oos_lock_basis: z.string(),
  cost_lines: z.array(z.object({ label: z.string(), value: z.string() })),
  initial_capital: z.number(),
  price_basis: z.enum(['adjusted', 'raw']),
  benchmark_price_basis: z.enum(['adjusted', 'raw']),
  fill_model: z.string(),
  cash_yield: z.string(),
  sharpe_risk_free: z.string(),
  bootstrap: z.object({
    method: z.string(),
    resamples: z.number().int(),
    block_length: z.number().int(),
    seed: z.number().int(),
    confidence: z.number(),
  }),
  warnings: z.array(z.object({ code: z.string(), text: z.string() })),
});
export type RealityCheck = z.infer<typeof realityCheckSchema>;

export const reportSchema = z.object({
  run_id: z.number().int(),
  symbol: z.string(),
  benchmark_symbol: z.string(),
  strategy: z.object({ id: z.string(), name: z.string(), params: z.record(z.string(), z.number()) }),
  strategy_metrics: performanceSchema,
  benchmark_metrics: performanceSchema,
  excess_annualized_return: estimateSchema,
  curve: z.array(
    z.object({ day: isoDate, strategy: z.number().nullable(), benchmark: z.number().nullable() }),
  ),
  trades: z.array(
    z.object({
      entry_date: isoDate,
      exit_date: isoDate,
      cash_out: z.number(),
      cash_in: z.number(),
      pnl: z.number(),
      return_pct: z.number(),
      sessions_held: z.number().int(),
      exit_reason: z.string(),
    }),
  ),
  reality_check: realityCheckSchema,
  provenance: provenanceSchema,
  benchmark_provenance: provenanceSchema,
});
export type Report = z.infer<typeof reportSchema>;

// ---- Game layer (agents, XP, badges) ----

export const agentStatusSchema = z.enum(['ok', 'warning', 'error', 'idle', 'locked']);
export type AgentStatus = z.infer<typeof agentStatusSchema>;

const reportLineSchema = z.object({ text: z.string(), source: z.string(), as_of: isoDateTime });

export const agentSchema = z.object({
  id: z.string(),
  name: z.string(),
  role: z.string(),
  station: z.string().nullable(),
  status: agentStatusSchema,
  unlocks_in: z.string().nullable(),
  xp: z.number().int(),
  level: z.number().int(),
  report: z.array(reportLineSchema),
});
export type Agent = z.infer<typeof agentSchema>;

export const gameEventSchema = z.object({
  at: isoDateTime,
  agent: z.string(),
  kind: z.string(),
  text: z.string(),
  xp: z.number().int(),
  xp_note: z.string().nullable(),
  source: z.string(),
});
export type GameEvent = z.infer<typeof gameEventSchema>;

export const gameStateSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  market_open: z.boolean(),
  last_completed_session: isoDate,
  player: z.object({
    xp: z.number().int(),
    level: z.number().int(),
    level_start_xp: z.number().int(),
    next_level_xp: z.number().int(),
  }),
  agents: z.array(agentSchema),
  events: z.array(gameEventSchema),
  badges: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      description: z.string(),
      earned: z.boolean(),
      earned_at: isoDateTime.nullable(),
      detail: z.string().nullable(),
    }),
  ),
  xp_rules: z.array(z.object({ id: z.string(), description: z.string(), xp: z.number().int() })),
  xp_policy: z.string(),
  flags: z.array(
    z.object({
      code: z.string(),
      severity: z.enum(['error', 'warning', 'info']),
      title: z.string(),
      summary: z.string(),
      source: z.string(),
      as_of: isoDateTime,
      station: z.string().nullable(),
      refs: z.array(z.string()),
    }),
  ),
  graduation: z.object({
    items: z.array(
      z.object({
        id: z.string(),
        title: z.string(),
        requirement: z.string(),
        status: z.enum(['met', 'not_met', 'not_started']),
        progress: z.string(),
        evidence: z.string().nullable(),
        source: z.string(),
        as_of: isoDateTime,
      }),
    ),
    met: z.number().int(),
    total: z.number().int(),
    note: z.string(),
    as_of: isoDateTime,
    source: z.string(),
  }),
});
export type GameState = z.infer<typeof gameStateSchema>;
export type Flag = GameState['flags'][number];
export type Graduation = GameState['graduation'];

// ---- Options backtesting ----

export const optionsUniverseSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  entries: z.array(
    z.object({
      dataset_id: z.number().int(),
      underlying: z.string(),
      source: z.string(),
      file_name: z.string(),
      first_date: isoDate,
      last_date: isoDate,
      quote_days: z.number().int(),
      rows: z.number().int(),
      rows_missing_style: z.number().int(),
      lock: lockSchema.nullable(),
      underlying_datasets: z.array(
        z.object({
          dataset_id: z.number().int(),
          first_date: isoDate,
          last_date: isoDate,
          sessions: z.number().int(),
        }),
      ),
    }),
  ),
});
export type OptionsUniverse = z.infer<typeof optionsUniverseSchema>;
export type OptionsUniverseEntry = OptionsUniverse['entries'][number];

export const optionsReportSchema = reportSchema
  .omit({ trades: true, benchmark_provenance: true })
  .extend({
    trades: z.array(
      z.object({
        position_id: z.number().int(),
        opened: isoDate,
        closed: isoDate,
        description: z.string(),
        structure: z.string(),
        contracts: z.number().int(),
        entry_premium: z.number(),
        max_loss: z.number(),
        pnl: z.number(),
        exit_kind: z.string(),
        sessions_held: z.number().int(),
      }),
    ),
    events: z.array(
      z.object({
        day: isoDate,
        kind: z.string(),
        position_id: z.number().int().nullable(),
        text: z.string(),
        cash: z.number(),
      }),
    ),
    counts: z.object({
      rejected_orders: z.number().int(),
      stale_marks: z.number().int(),
      pin_events: z.number().int(),
      early_assignments: z.number().int(),
      margin_shortfalls: z.number().int(),
      peak_collateral: z.number(),
    }),
    underlying_provenance: provenanceSchema,
    benchmark_provenance: provenanceSchema,
  });
export type OptionsReport = z.infer<typeof optionsReportSchema>;

/** The fields every results screen shares (equity or options). */
export type ResultCore = Pick<
  Report,
  | 'run_id'
  | 'symbol'
  | 'benchmark_symbol'
  | 'strategy'
  | 'strategy_metrics'
  | 'benchmark_metrics'
  | 'excess_annualized_return'
  | 'curve'
  | 'reality_check'
  | 'provenance'
>;

// ---- Live agents (WebSocket + REST) ----

export const liveAgentSchema = z.object({
  id: z.string(),
  name: z.string(),
  sector: z.string(),
  role: z.string(),
  status: z.enum(['idle', 'queued', 'running', 'error', 'watching']),
  message: z.string(),
  job_id: z.number().int().nullable(),
  params: z.record(z.string(), z.number()).nullable(),
  progress_done: z.number().int().nullable(),
  progress_total: z.number().int().nullable(),
  queued: z.number().int(),
  autopilot: z.boolean(),
  updated_at: isoDateTime,
});
export type LiveAgent = z.infer<typeof liveAgentSchema>;

export const findingSchema = z.object({
  id: z.number().int(),
  job_id: z.number().int(),
  agent_id: z.string(),
  created_at: isoDateTime,
  symbol: z.string(),
  strategy: z.string(),
  params: z.record(z.string(), z.number()),
  period: z.string(),
  run_id: z.number().int().nullable(),
  trades: z.number().int().nullable(),
  win_rate: z.number().nullable(),
  sharpe: z.number().nullable(),
  total_return: z.number().nullable(),
  excess_annualized: z.number().nullable(),
  note: z.string(),
});
export type Finding = z.infer<typeof findingSchema>;

export const sectorSchema = z.object({
  id: z.string(),
  name: z.string(),
  purpose: z.string(),
  slots: z.number().int(),
  agents: z.array(z.string()),
});
export type Sector = z.infer<typeof sectorSchema>;

const killSwitchSchema = z.object({
  engaged: z.boolean(),
  reason: z.string(),
  changed_at: isoDateTime.nullable(),
});

export const snapshotSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  sectors: z.array(sectorSchema),
  agents: z.array(liveAgentSchema),
  findings: z.array(findingSchema),
  kill_switch: killSwitchSchema,
});
export type Snapshot = z.infer<typeof snapshotSchema>;

export const activityEventSchema = z.discriminatedUnion('type', [
  snapshotSchema.extend({ type: z.literal('snapshot') }),
  z.object({ type: z.literal('agent'), agent: liveAgentSchema }),
  z.object({ type: z.literal('finding'), finding: findingSchema }),
  z.object({ type: z.literal('kill_switch'), engaged: z.boolean(), reason: z.string() }),
]);
export type ActivityEvent = z.infer<typeof activityEventSchema>;

export const jobSchema = z.object({
  job_id: z.number().int(),
  agent_id: z.string(),
  kind: z.string(),
});

export const riskStateSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  kill_switch_engaged: z.boolean(),
  kill_switch_reason: z.string(),
  kill_switch_changed_at: isoDateTime.nullable(),
  limits: z.object({
    max_loss_per_trade: z.number(),
    max_daily_loss: z.number(),
    max_open_positions: z.number().int(),
    max_capital_at_risk_pct: z.number(),
  }),
  decisions: z.array(
    z.object({
      id: z.number().int(),
      created_at: isoDateTime,
      source: z.string(),
      symbol: z.string(),
      order_text: z.string(),
      approved: z.boolean(),
      checks: z.array(z.object({ name: z.string(), passed: z.boolean(), detail: z.string() })),
      tripped: z.boolean(),
    }),
  ),
});
export type RiskState = z.infer<typeof riskStateSchema>;

export const paperLogSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  runners: z.array(
    z.object({
      id: z.number().int(),
      created_at: isoDateTime,
      strategy: z.string(),
      params: z.record(z.string(), z.number()),
      dataset_id: z.number().int(),
      symbol: z.string(),
      dry_run: z.boolean(),
      active: z.boolean(),
    }),
  ),
  cycles: z.array(
    z.object({
      id: z.number().int(),
      runner_id: z.number().int().nullable(),
      created_at: isoDateTime,
      session: isoDate,
      mode: z.string(),
      strategy: z.string(),
      symbol: z.string(),
      explanation: z.string(),
      target: z.number().nullable(),
      price: z.number().nullable(),
      price_source: z.string(),
      current_qty: z.number().nullable(),
      desired_qty: z.number().nullable(),
      outcome: z.string(),
    }),
  ),
  orders: z.array(
    z.object({
      id: z.number().int(),
      cycle_id: z.number().int(),
      created_at: isoDateTime,
      mode: z.string(),
      symbol: z.string(),
      side: z.string(),
      qty: z.number(),
      reason: z.string(),
      risk_decision_id: z.number().int(),
      broker_order_id: z.string().nullable(),
      status: z.string(),
      latest_status: z.string(),
    }),
  ),
});
export type PaperLog = z.infer<typeof paperLogSchema>;

// ---- Net worth (manual entry / CSV only; stays local) ----

export const netWorthAccountSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  kind: z.enum(['asset', 'liability']),
  category: z.string(),
  note: z.string(),
  latest_amount: z.number().nullable(),
  latest_as_of: isoDate.nullable(),
  latest_source: z.string().nullable(),
  age_days: z.number().int().nullable(),
  stale: z.boolean(),
});
export type NetWorthAccount = z.infer<typeof netWorthAccountSchema>;

export const netWorthSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  data_type: z.literal('manual'),
  stale_after_days: z.number().int(),
  categories: z.object({ asset: z.array(z.string()), liability: z.array(z.string()) }),
  accounts: z.array(netWorthAccountSchema),
  totals: z.object({
    assets: z.number(),
    liabilities: z.number(),
    net_worth: z.number(),
    as_of: isoDate.nullable(),
    oldest_included: isoDate.nullable(),
    missing: z.array(z.string()),
  }),
  history: z.array(
    z.object({
      as_of: isoDate,
      assets: z.number(),
      liabilities: z.number(),
      net_worth: z.number(),
      carried_forward: z.number().int(),
      missing: z.array(z.string()),
    }),
  ),
  method: z.string(),
});
export type NetWorth = z.infer<typeof netWorthSchema>;
export type NetWorthPoint = NetWorth['history'][number];

export const balanceSchema = z.object({
  id: z.number().int(),
  account_id: z.number().int(),
  as_of: isoDate,
  amount: z.number(),
  source: z.string(),
  entered_at: isoDateTime,
  replaced: z.boolean(),
});
export type BalanceEntry = z.infer<typeof balanceSchema>;

export const netWorthImportSchema = z.object({
  rows: z.number().int(),
  accounts_created: z.number().int(),
  inserted: z.number().int(),
  updated: z.number().int(),
  source: z.string(),
});

export const projectionSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  start: z.number(),
  start_as_of: isoDate,
  years: z.number().int(),
  low_rate: z.number(),
  high_rate: z.number(),
  contribution: z.number(),
  points: z.array(z.object({ year: z.number().int(), low: z.number(), high: z.number() })),
  note: z.string(),
});
export type Projection = z.infer<typeof projectionSchema>;

// ---- Portfolio (manual holdings + read-only paper positions) ----

const greeksSchema = z.object({ delta: z.number(), theta: z.number(), vega: z.number() });

export const portfolioSchema = z.object({
  as_of: isoDateTime,
  source: z.string(),
  book: z.enum(['manual', 'paper', 'combined']),
  benchmark: z.string(),
  window: z.number().int(),
  paper: z.object({
    status: z.enum(['included', 'not_requested', 'not_configured', 'error']),
    detail: z.string(),
  }),
  positions: z.array(
    z.object({
      key: z.string(),
      book: z.enum(['manual', 'paper']),
      holding_id: z.number().int().nullable(),
      symbol: z.string(),
      label: z.string(),
      asset_class: z.string(),
      account: z.string(),
      quantity: z.number(),
      price: z.number().nullable(),
      price_source: z.string().nullable(),
      price_as_of: isoDate.nullable(),
      stale: z.boolean(),
      stale_reason: z.string().nullable(),
      value: z.number().nullable(),
      weight: z.number().nullable(),
      greeks: greeksSchema.nullable(),
      notes: z.array(z.string()),
    }),
  ),
  total_value: z.number(),
  long_value: z.number(),
  allocation: z.array(z.object({ asset_class: z.string(), value: z.number(), weight: z.number() })),
  concentration: z
    .object({
      hhi: z.number(),
      effective_n: z.number(),
      top: z.array(z.tuple([z.string(), z.number()])),
      top5_weight: z.number(),
    })
    .nullable(),
  risk: z
    .object({
      start: isoDate,
      end: isoDate,
      returns: z.number().int(),
      volatility: z.number(),
      max_drawdown: z.number(),
      benchmark_volatility: z.number().nullable(),
      beta: z.number().nullable(),
      correlation: z.number().nullable(),
    })
    .nullable(),
  risk_note: z.string(),
  risk_included: z.array(z.string()),
  risk_excluded: z.array(z.string()),
  coverage: z.number().nullable(),
  price_basis: z.string(),
  greeks_total: greeksSchema.nullable(),
  greeks_missing: z.array(z.string()),
  tips: z.array(
    z.object({
      id: z.string(),
      title: z.string(),
      status: z.enum(['fired', 'clear', 'not_evaluated']),
      observed: z.string(),
      threshold: z.string(),
      detail: z.string(),
    }),
  ),
  method: z.string(),
});
export type Portfolio = z.infer<typeof portfolioSchema>;
export type PortfolioPosition = Portfolio['positions'][number];

export const holdingSchema = z.object({
  id: z.number().int(),
  created_at: isoDateTime,
  symbol: z.string(),
  asset_class: z.string(),
  quantity: z.number(),
  account: z.string(),
  option_type: z.enum(['call', 'put']).nullable(),
  strike: z.number().nullable(),
  expiration: isoDate.nullable(),
  manual_price: z.number().nullable(),
  manual_price_as_of: isoDate.nullable(),
  note: z.string(),
});

export const holdingsImportSchema = z.object({ imported: z.number().int(), removed: z.number().int() });
