// Test-only helper: routes fetch calls to canned responses. Never imported by app code.
import { vi } from 'vitest';

/** A JSON body, an Error to reject with, or { status, body } for a non-200 response. */
type Route = unknown;

export function stubFetch(routes: Record<string, Route>) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);
    const match = Object.entries(routes).find(([path]) => url === `/api${path}`);
    if (!match) return Promise.resolve(Response.json({ detail: 'not stubbed' }, { status: 404 }));
    const route = match[1];
    if (route instanceof Error) return Promise.reject(route);
    if (typeof route === 'object' && route !== null && 'status' in route && 'body' in route) {
      const { status, body } = route as { status: number; body: unknown };
      return Promise.resolve(Response.json(body, { status }));
    }
    return Promise.resolve(Response.json(route));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

export const healthPayload = {
  status: 'ok',
  version: '0.1.0',
  trading_mode: 'paper',
  broker_base_url: 'https://paper-api.alpaca.markets',
  broker_credentials_configured: false,
  source: 'backend server clock',
  as_of: '2026-10-05T12:00:00+00:00',
};

const feed = (name: string, data_type: string) => ({ name, data_type, note: `${name} note` });

export const dataHealthPayload = {
  overall: 'warning',
  as_of: '2026-10-05T15:00:00+00:00',
  source: 'Paper Trading Lab data layer',
  market_open: true,
  last_completed_session: '2026-10-02',
  live_source: {
    name: 'Alpaca',
    status: 'ok',
    configured: true,
    reachable: true,
    account_status: 'ACTIVE',
    error: null,
    checked_at: '2026-10-05T15:00:00+00:00',
    stock_feed: feed('Alpaca IEX', 'real-time'),
    option_feed: feed('Alpaca options (indicative)', 'delayed'),
  },
  datasets: [
    {
      id: 1,
      kind: 'option_quotes',
      file_name: 'spy_2024.csv',
      row_count: 1234,
      symbol_count: 1,
      coverage_start: '2024-01-02',
      coverage_end: '2024-12-31',
      status: 'error',
      issues: [
        {
          check: 'crossed_market',
          severity: 'error',
          symbol: 'SPY',
          count: 3,
          first_date: '2024-03-01',
          last_date: '2024-05-02',
          detail: 'Bid above ask (crossed market).',
        },
      ],
      provenance: {
        source: 'Vendor X',
        as_of: '2026-10-01T10:00:00+00:00',
        data_type: 'end-of-day',
        stale: true,
        stale_reason: 'Data ends 2024-12-31, which is 440 sessions before the last completed session.',
      },
    },
  ],
};

export const quotePayload = {
  symbol: 'SPY',
  bid: 600.01,
  ask: 600.03,
  spread: 0.02,
  bid_size: 3,
  ask_size: 2,
  provenance: {
    source: 'Alpaca IEX',
    as_of: '2026-10-05T14:59:58+00:00',
    data_type: 'real-time',
    stale: false,
    stale_reason: null,
  },
  issues: [],
};

export const strategiesPayload = [
  { id: 'buy_and_hold', name: 'Buy and hold', description: 'Benchmark.', params: [] },
  {
    id: 'sma_crossover',
    name: 'Moving-average crossover',
    description: 'Fast above slow means invested.',
    params: [
      { name: 'fast', label: 'Fast window', default: 50, minimum: 2, maximum: 399, description: 'f' },
      { name: 'slow', label: 'Slow window', default: 200, minimum: 3, maximum: 400, description: 's' },
    ],
  },
];

export const universePayload = {
  as_of: '2026-10-05T15:00:00+00:00',
  source: 'Imported equity datasets (local SQLite)',
  entries: [
    {
      dataset_id: 1,
      symbol: 'SPY',
      source: 'Vendor X',
      file_name: 'spy.csv',
      first_date: '2016-01-04',
      last_date: '2025-12-31',
      sessions: 2515,
      has_adj_close: true,
      lock: null,
      in_sample_runs: 0,
      oos_evaluations: 0,
    },
  ],
};

const estimate = (value: number, low: number, high: number) => ({ value, ci95: { low, high } });
const perf = (finalEquity: number, trades: number) => ({
  sessions: 3,
  final_equity: finalEquity,
  total_return: finalEquity / 100_000 - 1,
  annualized_return: estimate(0.05, -0.02, 0.12),
  annual_volatility: 0.15,
  sharpe: estimate(0.4, -0.3, 1.1),
  max_drawdown: -0.2,
  max_drawdown_date: '2020-03-23',
  exposure: 0.7,
  trade_count: trades,
  win_rate: { value: 0.5, ci95: null },
  expectancy: { value: 120, ci95: null },
  avg_win: 400,
  avg_loss: -160,
  total_costs: 85,
});

export const reportPayload = {
  run_id: 7,
  symbol: 'SPY',
  benchmark_symbol: 'SPY',
  strategy: { id: 'sma_crossover', name: 'Moving-average crossover', params: { fast: 50, slow: 200 } },
  strategy_metrics: perf(104_000, 4),
  benchmark_metrics: perf(110_000, 1),
  excess_annualized_return: estimate(-0.01, -0.06, 0.04),
  curve: [
    { day: '2020-01-02', strategy: 100_000, benchmark: 100_000 },
    { day: '2020-01-03', strategy: 101_000, benchmark: 102_000 },
    { day: '2020-02-03', strategy: 104_000, benchmark: 110_000 },
  ],
  trades: [
    {
      entry_date: '2020-01-03',
      exit_date: '2020-02-03',
      cash_out: 100_000,
      cash_in: 104_000,
      pnl: 4_000,
      return_pct: 0.04,
      sessions_held: 21,
      exit_reason: 'signal',
    },
  ],
  reality_check: {
    period: 'in-sample',
    window_start: '2016-01-04',
    window_end: '2022-12-30',
    sessions: 1760,
    trade_count: 4,
    parameter_combinations_tried: 3,
    parameter_combinations_tried_all_strategies: 4,
    in_sample_runs: 5,
    oos_evaluations: 0,
    oos_start: '2023-01-03',
    oos_locked_at: '2026-10-05T15:00:00+00:00',
    oos_lock_basis: 'Most recent 30% of 2515 sessions.',
    costs: { slippage_bps: 5, commission_per_order: 0, commission_bps: 0 },
    initial_capital: 100_000,
    price_basis: 'adjusted',
    benchmark_price_basis: 'adjusted',
    fill_model: 'Signals use closes; fills at next open.',
    cash_yield: '0%: idle cash earns nothing in this backtest',
    sharpe_risk_free: '0%',
    bootstrap: {
      method: 'moving-block bootstrap',
      resamples: 2000,
      block_length: 12,
      seed: 20251005,
      confidence: 0.95,
    },
    warnings: ['Only 4 trade(s). Win rate and expectancy are unreliable below 30 trades.'],
  },
  provenance: {
    source: 'Backtest on Vendor X (spy.csv, dataset #1)',
    as_of: '2026-10-05T15:00:00+00:00',
    data_type: 'end-of-day',
    stale: false,
    stale_reason: null,
  },
  benchmark_provenance: {
    source: 'SPY buy-and-hold on Vendor X (spy.csv, dataset #1)',
    as_of: '2026-10-05T15:00:00+00:00',
    data_type: 'end-of-day',
    stale: false,
    stale_reason: null,
  },
};
