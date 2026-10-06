// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { healthPayload, strategiesPayload, stubFetch, universePayload } from '../test/fakeFetch';
import { stubSocket } from '../test/fakeSocket';
import { gameStatePayload, repeatLookEvent } from '../test/gameFixtures';
import { helloPayload, trialPayload } from '../test/telemetryFixtures';
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
    window.location.hash = '#hq';
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
      fireEvent.keyDown(window, { key: '4' });
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(window.location.hash).toBe('#strategy-lab');
    const capital = await screen.findByLabelText('Starting capital ($)');
    fireEvent.keyDown(capital, { key: '3' });
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

  it('keeps optimizer trials silent and merges repeated major events into one toast', async () => {
    stubFetch(routes);
    const sockets = stubSocket();
    render(<GameShell />);
    await screen.findByText('LV 2');
    const socket = sockets.at(-1);
    if (!socket) throw new Error('no telemetry socket');
    act(() => {
      socket.push(helloPayload);
    });
    act(() => {
      for (let i = 10; i < 40; i++) socket.push({ type: 'trial', trial: trialPayload({ id: i }) });
    });
    expect(screen.queryByRole('status')).toBeNull();
    const line = (id: number, kind: string, message: string) => ({
      type: 'log',
      line: { id, created_at: '2026-10-06T15:00:00+00:00', daemon: 'paper-runner', level: 'info', message, kind },
    });
    act(() => {
      socket.push(line(50, 'info', 'Session handled.'));
      socket.push(line(51, 'risk', 'Order blocked: max position size.'));
      socket.push(line(52, 'risk', 'Order blocked: max loss per trade.'));
      socket.push(line(53, 'risk', 'Order blocked: capital at risk.'));
    });
    const toast = await screen.findByRole('status');
    expect(toast).toHaveTextContent('Risk limit blocked an order');
    expect(toast).toHaveTextContent('x3');
    expect(toast).toHaveTextContent('Order blocked: capital at risk.');
    expect(toast).not.toHaveTextContent('Session handled.');
  });
});
