// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { healthPayload, strategiesPayload, stubFetch, universePayload } from '../test/fakeFetch';
import { gameStatePayload, repeatLookEvent } from '../test/gameFixtures';
import { announceActivity } from './activity';
import { GameShell } from './GameShell';

const routes = {
  '/health': healthPayload,
  '/game/state': gameStatePayload,
  '/backtest/strategies': strategiesPayload,
  '/backtest/universe': universePayload,
};

describe('GameShell', () => {
  beforeEach(() => {
    window.location.hash = '';
  });

  it('shows level and process XP with its source in the HUD', async () => {
    stubFetch(routes);
    render(<GameShell />);
    expect(await screen.findByText('LV 2')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '115');
    expect(screen.getByText(/115 \/ 200 process XP · local records/)).toBeInTheDocument();
    expect(screen.getByText('Badges 1/2')).toBeInTheDocument();
    expect(screen.getByText(/never earn XP/)).toBeInTheDocument();
    expect(screen.getByText('+50 XP')).toBeInTheDocument();
  });

  it('switches stations with number keys but not while typing', async () => {
    stubFetch(routes);
    render(<GameShell />);
    await screen.findByText('LV 2');
    act(() => {
      fireEvent.keyDown(window, { key: '3' });
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(window.location.hash).toBe('#strategy-lab');
    const capital = await screen.findByLabelText('Starting capital ($)');
    fireEvent.keyDown(capital, { key: '2' });
    expect(window.location.hash).toBe('#strategy-lab');
  });

  it('pops a toast for each new real event after activity', async () => {
    stubFetch(routes);
    render(<GameShell />);
    await screen.findByText('LV 2');
    stubFetch({
      ...routes,
      '/game/state': { ...gameStatePayload, events: [repeatLookEvent, ...gameStatePayload.events] },
    });
    act(() => {
      announceActivity();
    });
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent('+0 XP');
    expect(toast).toHaveTextContent('repeat look at the holdout: no XP');
  });
});
