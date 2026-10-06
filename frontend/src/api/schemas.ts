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
