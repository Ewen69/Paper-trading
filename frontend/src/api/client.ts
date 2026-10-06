import type { z } from 'zod';

import { dataHealthSchema, healthSchema, quoteSchema } from './schemas';

/** Fetch JSON from the backend and validate it. Throws with a user-readable message. */
export async function getJson<S extends z.ZodType>(
  path: string,
  schema: S,
  signal?: AbortSignal,
): Promise<z.output<S>> {
  const response = await fetch(`/api${path}`, signal ? { signal } : {});
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
