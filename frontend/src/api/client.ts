import type { z } from 'zod';

import {
  balanceSchema,
  holdingSchema,
  holdingsImportSchema,
  portfolioSchema,
  netWorthImportSchema,
  netWorthAccountSchema,
  netWorthSchema,
  projectionSchema,
  dataHealthSchema,
  gameStateSchema,
  jobSchema,
  liveAgentSchema,
  paperLogSchema,
  riskStateSchema,
  optionsReportSchema,
  optionsUniverseSchema,
  healthSchema,
  quoteSchema,
  reportSchema,
  strategiesSchema,
  universeSchema,
  type Period,
} from './schemas';

/** Fetch JSON from the backend and validate it. Throws with a user-readable message. */
export async function getJson<S extends z.ZodType>(
  path: string,
  schema: S,
  signal?: AbortSignal,
): Promise<z.output<S>> {
  const response = await fetch(`/api${path}`, signal ? { signal } : {});
  return parseResponse(response, schema);
}

async function parseResponse<S extends z.ZodType>(
  response: Response,
  schema: S,
): Promise<z.output<S>> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body && typeof body.detail === 'string'
        ? body.detail
        : `HTTP ${String(response.status)}`;
    throw new Error(detail);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new Error(`unexpected response from backend (${String(parsed.error.issues.length)} problem(s))`);
  }
  return parsed.data;
}

export const fetchHealth = (signal?: AbortSignal) => getJson('/health', healthSchema, signal);

export const fetchDataHealth = (signal?: AbortSignal) =>
  getJson('/data/health', dataHealthSchema, signal);

export const fetchQuote = (kind: 'stock' | 'option', symbol: string, signal?: AbortSignal) =>
  getJson(`/quotes/${kind}/${encodeURIComponent(symbol.trim())}`, quoteSchema, signal);

/** POST JSON and validate the response, with the same error handling as getJson. */
export async function postJson<S extends z.ZodType>(
  path: string,
  payload: unknown,
  schema: S,
): Promise<z.output<S>> {
  const response = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseResponse(response, schema);
}

export const fetchStrategies = (signal?: AbortSignal) =>
  getJson('/backtest/strategies', strategiesSchema, signal);

export const fetchUniverse = (signal?: AbortSignal) =>
  getJson('/backtest/universe', universeSchema, signal);

export interface RunPayload {
  dataset_id: number;
  symbol: string;
  strategy: string;
  period: Period;
  params: Record<string, number>;
  slippage_bps: number;
  commission_per_order: number;
  commission_bps: number;
  initial_capital: number;
}

export const runBacktest = (payload: RunPayload) => postJson('/backtest/runs', payload, reportSchema);

export const fetchGameState = (signal?: AbortSignal) =>
  getJson('/game/state', gameStateSchema, signal);

export const fetchOptionsUniverse = (signal?: AbortSignal) =>
  getJson('/backtest/options/universe', optionsUniverseSchema, signal);

export interface OptionsRunPayload {
  options_dataset_id: number;
  underlying_dataset_id: number;
  symbol: string;
  strategy: string;
  period: Period;
  params: Record<string, number>;
  slippage_per_share: number;
  slippage_spread_fraction: number;
  commission_per_contract: number;
  assignment_fee_per_contract: number;
  stock_slippage_bps: number;
  early_assignment: boolean;
  early_assignment_extrinsic: number;
  initial_capital: number;
}

export const runOptionsBacktest = (payload: OptionsRunPayload) =>
  postJson('/backtest/options/runs', payload, optionsReportSchema);

// ---- Live agents, risk, paper runner ----

export const startSweep = (body: {
  asset: 'equity' | 'options';
  strategy: string;
  symbol: string;
  dataset_id: number;
  options_dataset_id?: number;
  promote: boolean;
}) => postJson('/agents/sweeps', body, jobSchema);

export const startDataCheck = () => postJson('/agents/data-check', {}, jobSchema);

export const setAutopilot = (agentId: string, enabled: boolean) =>
  postJson(`/agents/${encodeURIComponent(agentId)}/autopilot`, { enabled }, liveAgentSchema);

export const fetchRiskState = (signal?: AbortSignal) =>
  getJson('/risk/state', riskStateSchema, signal);

export const setKillSwitch = (engaged: boolean, reason: string) =>
  postJson('/risk/kill-switch', { engaged, reason }, riskStateSchema);

export const startPaperCycle = (body: {
  strategy: string;
  dataset_id: number;
  symbol: string;
  dry_run: boolean;
}) => postJson('/paper/cycles', body, jobSchema);

export const fetchPaperLog = (signal?: AbortSignal) =>
  getJson('/paper/log', paperLogSchema, signal);

/** ws:// URL for the live telemetry stream, through the Vite proxy. */
export const telemetryUrl = () =>
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/api/ws/telemetry`;

// ---- Net worth ----

async function deleteJson(path: string): Promise<void> {
  const response = await fetch(`/api${path}`, { method: 'DELETE' });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      typeof body === 'object' && body !== null && 'detail' in body && typeof body.detail === 'string'
        ? body.detail
        : `HTTP ${String(response.status)}`;
    throw new Error(detail);
  }
}

export const fetchNetWorth = (signal?: AbortSignal) => getJson('/networth', netWorthSchema, signal);

export const createAccount = (body: { name: string; kind: string; category: string; note: string }) =>
  postJson('/networth/accounts', body, netWorthAccountSchema);

export const deleteAccount = (id: number) => deleteJson(`/networth/accounts/${String(id)}`);

export const addBalance = (body: { account_id: number; as_of: string; amount: number }) =>
  postJson('/networth/balances', body, balanceSchema);

export const fetchBalances = (accountId: number, signal?: AbortSignal) =>
  getJson(`/networth/accounts/${String(accountId)}/balances`, balanceSchema.array(), signal);

export const deleteBalance = (id: number) => deleteJson(`/networth/balances/${String(id)}`);

export const importNetWorth = (fileName: string, content: string) =>
  postJson('/networth/import', { file_name: fileName, content }, netWorthImportSchema);

/** Plain link target; the browser downloads the CSV. */
export const netWorthExportUrl = '/api/networth/export';

export const fetchProjection = (
  params: { years: number; low: number; high: number; contribution: number },
  signal?: AbortSignal,
) => {
  const query = new URLSearchParams({
    years: String(params.years),
    low: String(params.low),
    high: String(params.high),
    contribution: String(params.contribution),
  });
  return getJson(`/networth/projection?${query.toString()}`, projectionSchema, signal);
};

// ---- Portfolio ----

export type PortfolioBook = 'manual' | 'paper' | 'combined';

export const fetchPortfolio = (
  params: { book: PortfolioBook; benchmark: string; window: number },
  signal?: AbortSignal,
) => {
  const query = new URLSearchParams({
    book: params.book,
    benchmark: params.benchmark,
    window: String(params.window),
  });
  return getJson(`/portfolio?${query.toString()}`, portfolioSchema, signal);
};

export interface HoldingPayload {
  symbol: string;
  asset_class: string;
  quantity: number;
  account: string;
  option_type: 'call' | 'put' | null;
  strike: number | null;
  expiration: string | null;
  manual_price: number | null;
  manual_price_as_of: string | null;
}

export const addHolding = (body: HoldingPayload) => postJson('/portfolio/holdings', body, holdingSchema);

export const deleteHolding = (id: number) => deleteJson(`/portfolio/holdings/${String(id)}`);

export const importHoldings = (content: string, replace: boolean) =>
  postJson('/portfolio/import', { content, replace }, holdingsImportSchema);

export const holdingsExportUrl = '/api/portfolio/export';
