// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { healthSchema } from './api/schemas';
import { App } from './App';
import { healthPayload, stubFetch } from './test/fakeFetch';

describe('Home', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  it('shows health with source and timestamp when the backend responds', async () => {
    stubFetch({ '/health': healthPayload });
    render(<App />);

    expect(await screen.findByText('https://paper-api.alpaca.markets')).toBeInTheDocument();
    expect(screen.getByText('backend server clock')).toBeInTheDocument();
    expect(screen.getByText('not configured')).toBeInTheDocument();
    expect(document.querySelector('time')?.getAttribute('datetime')).toBe(
      '2026-10-05T12:00:00.000Z',
    );
  });

  it('shows "No data" when the backend is unreachable', async () => {
    stubFetch({ '/health': new TypeError('Failed to fetch') });
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('No data');
  });

  it('shows "No data" when the payload does not match the schema', async () => {
    stubFetch({ '/health': { ...healthPayload, as_of: undefined } });
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('unexpected response');
  });
});

describe('healthSchema', () => {
  it('rejects a backend that is not in paper mode', () => {
    expect(healthSchema.safeParse({ ...healthPayload, trading_mode: 'live' }).success).toBe(false);
  });

  it('rejects a timestamp without a timezone rather than guessing one', () => {
    expect(healthSchema.safeParse({ ...healthPayload, as_of: '2026-10-05T12:00:00' }).success).toBe(
      false,
    );
  });
});
