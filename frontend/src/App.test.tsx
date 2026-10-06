// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { healthSchema } from './api/schemas';
import { App } from './App';
import { healthPayload, stubFetch } from './test/fakeFetch';
import { gameStatePayload } from './test/gameFixtures';

describe('HQ', () => {
  beforeEach(() => {
    window.location.hash = '#hq';
  });

  it('shows backend health with source and timestamp', async () => {
    stubFetch({ '/health': healthPayload, '/game/state': gameStatePayload });
    render(<App />);
    expect(await screen.findByText('https://paper-api.alpaca.markets')).toBeInTheDocument();
    expect(screen.getByText('backend server clock')).toBeInTheDocument();
    expect(screen.getByText('not configured')).toBeInTheDocument();
  });

  it('says "No data" for each part that is unreachable', async () => {
    stubFetch({ '/health': new TypeError('Failed to fetch'), '/game/state': new TypeError('down') });
    render(<App />);
    await screen.findByText(/Backend unreachable/);
    const alerts = await screen.findAllByRole('alert');
    expect(alerts.map((a) => a.textContent)).toEqual([
      expect.stringContaining('Game state unavailable'),
      expect.stringContaining('Backend unreachable'),
    ]);
    expect(screen.getByText('XP: no data')).toBeInTheDocument();
  });

  it('rejects a health payload that does not match the schema', async () => {
    stubFetch({
      '/health': { ...healthPayload, as_of: undefined },
      '/game/state': gameStatePayload,
    });
    render(<App />);
    expect(await screen.findByText(/unexpected response/)).toBeInTheDocument();
  });

  it('renders agents with real report lines, sources, and locked silhouettes', async () => {
    stubFetch({ '/health': healthPayload, '/game/state': gameStatePayload });
    render(<App />);
    const scout = await screen.findByRole('article', { name: 'Data Scout' });
    expect(within(scout).getByText('Caution')).toBeInTheDocument();
    expect(within(scout).getByText(/Live feed offline/)).toBeInTheDocument();
    expect(within(scout).getByText(/Alpaca paper account check ·/)).toBeInTheDocument();
    expect(within(scout).getByRole('link', { name: /Enter station/ })).toHaveAttribute(
      'href',
      '#data-health',
    );
    const risk = screen.getByRole('article', { name: 'Accountant (locked)' });
    expect(within(risk).getByText(/Unlocks in Phase 4/)).toBeInTheDocument();
    expect(within(risk).queryByRole('link')).toBeNull();
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
