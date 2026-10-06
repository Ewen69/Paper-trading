/** Typed client for the backend `/health` endpoint. Never invents values: bad payload → error. */

export interface Health {
  status: 'ok';
  version: string;
  tradingMode: 'paper';
  brokerBaseUrl: string;
  brokerCredentialsConfigured: boolean;
  source: string;
  asOf: Date;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function parseHealth(raw: unknown): Health {
  if (!isRecord(raw)) throw new Error('health payload is not an object');
  const {
    status,
    version,
    trading_mode: tradingMode,
    broker_base_url: brokerBaseUrl,
    broker_credentials_configured: brokerCredentialsConfigured,
    source,
    as_of: asOfRaw,
  } = raw;
  if (status !== 'ok') throw new Error('unexpected status');
  if (tradingMode !== 'paper') throw new Error('backend is not in paper mode');
  if (typeof version !== 'string') throw new Error('missing version');
  if (typeof brokerBaseUrl !== 'string') throw new Error('missing broker_base_url');
  if (typeof brokerCredentialsConfigured !== 'boolean') {
    throw new Error('missing broker_credentials_configured');
  }
  if (typeof source !== 'string') throw new Error('missing source');
  if (typeof asOfRaw !== 'string') throw new Error('missing as_of');
  const asOf = new Date(asOfRaw);
  if (Number.isNaN(asOf.getTime())) throw new Error('invalid as_of');

  return {
    status,
    version,
    tradingMode,
    brokerBaseUrl,
    brokerCredentialsConfigured,
    source,
    asOf,
  };
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch('/api/health', signal ? { signal } : {});
  if (!response.ok) throw new Error(`backend returned HTTP ${String(response.status)}`);
  return parseHealth(await response.json());
}
