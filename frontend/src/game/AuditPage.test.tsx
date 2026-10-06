// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { healthPayload, stubFetch } from '../test/fakeFetch';
import { gameStatePayload } from '../test/gameFixtures';
import { GameShell } from './GameShell';

describe('Auditor station', () => {
  beforeEach(() => {
    window.location.hash = '#audit';
  });

  it('lists flags with severity labels, sources, and links to the right station', async () => {
    stubFetch({ '/health': healthPayload, '/game/state': gameStatePayload });
    render(<GameShell />);
    const flags = await screen.findByRole('list', { name: 'Open flags' });
    expect(screen.getByText('Open flags (3)')).toBeInTheDocument();
    expect(screen.getByText('0 alert · 2 caution · 1 note')).toBeInTheDocument();
    const gap = within(flags).getByText('Data gaps in spy.csv').closest('li');
    expect(gap).not.toBeNull();
    if (gap) {
      expect(gap).toHaveTextContent('Caution');
      expect(gap).toHaveTextContent('Dataset #1 quality checks (Vendor X) ·');
      expect(within(gap).getByRole('link', { name: /Open Data Scout/ })).toHaveAttribute(
        'href',
        '#data-health',
      );
    }
  });

  it('shows the graduation gate as tracking-only with honest statuses', async () => {
    stubFetch({ '/health': healthPayload, '/game/state': gameStatePayload });
    render(<GameShell />);
    expect(await screen.findByText('Graduation gate 0/2')).toBeInTheDocument();
    expect(screen.getByText(/cannot, enable live trading/)).toBeInTheDocument();
    const trades = screen.getByText('200+ paper trades').closest('li');
    expect(trades).toHaveTextContent('Not started');
    expect(trades).toHaveTextContent('0 of 200 trades.');
    const edge = screen.getByText('Beats buy-and-hold out-of-sample').closest('li');
    expect(edge).toHaveTextContent('Not met');
  });
});
