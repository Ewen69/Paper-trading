import type { z } from 'zod';

import {
  dataHealthSchema,
  gameStateSchema,
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
