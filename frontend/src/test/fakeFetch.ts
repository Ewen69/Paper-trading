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
