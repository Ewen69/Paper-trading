// Mocked fetch responses are test-only fixtures; production code never fabricates data.
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { parseHealth } from './api/health';
import { App } from './App';

const validPayload = {
  status: 'ok',
  version: '0.0.1',
  trading_mode: 'paper',
  broker_base_url: 'https://paper-api.alpaca.markets',
  broker_credentials_configured: false,
  source: 'backend server clock',
  as_of: '2026-10-05T12:00:00+00:00',
};

describe('App', () => {
  it('shows health with source and timestamp when the backend responds', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(validPayload)));
    render(<App />);

    expect(await screen.findByText('https://paper-api.alpaca.markets')).toBeInTheDocument();
    expect(screen.getByText('backend server clock')).toBeInTheDocument();
    expect(screen.getByText('not configured')).toBeInTheDocument();
    const time = document.querySelector('time');
    expect(time?.getAttribute('datetime')).toBe('2026-10-05T12:00:00.000Z');
  });

  it('shows "No data" when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    render(<App />);

    expect(await screen.findByRole('alert')).toHaveTextContent('No data');
  });
});

describe('parseHealth', () => {
  it('rejects a backend that is not in paper mode', () => {
    expect(() => parseHealth({ ...validPayload, trading_mode: 'live' })).toThrow(/paper/);
  });

  it('rejects a missing timestamp rather than guessing one', () => {
    const withoutTimestamp: Partial<typeof validPayload> = { ...validPayload };
    delete withoutTimestamp.as_of;
    expect(() => parseHealth(withoutTimestamp)).toThrow(/as_of/);
  });
});
