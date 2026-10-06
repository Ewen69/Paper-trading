// The WebSocket is a test-only fake; production code never fabricates data.
import { act, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { stubFetch } from '../test/fakeFetch';
import { stubSocket, type FakeSocket } from '../test/fakeSocket';
import { helloPayload, statePayload, trialPayload } from '../test/telemetryFixtures';
import { OpsCenterLive as OpsCenter } from './OpsCenter';

let sockets: FakeSocket[];

function send(payload: unknown) {
  const socket = sockets.at(-1);
  if (!socket) throw new Error('no socket opened');
  act(() => {
    socket.push(payload);
  });
}

describe('OpsCenter', () => {
  beforeEach(() => {
    sockets = stubSocket();
    stubFetch({});
  });

  it('shows connecting and "no data" before the first message', () => {
    render(<OpsCenter url="ws://test/telemetry" />);
    const status = screen.getByRole('list', { name: 'System status' });
    expect(within(status).getByText('connecting')).toBeInTheDocument();
    expect(within(status).getAllByText('not running')).toHaveLength(2);
    expect(screen.getByText(/No agent activity yet/)).toBeInTheDocument();
    expect(sockets.at(-1)?.url).toBe('ws://test/telemetry');
  });

  it('renders daemons, the terminal, the Active Best and auditor from the hello message', () => {
    render(<OpsCenter url="ws://test/telemetry" />);
    send(helloPayload);
    const status = screen.getByRole('list', { name: 'System status' });
    expect(within(status).getByText('live')).toBeInTheDocument();
    expect(within(status).getByText('running')).toBeInTheDocument();
    expect(within(status).getByText('armed')).toBeInTheDocument();
    expect(within(status).getByText('1 flag(s)')).toBeInTheDocument();
    expect(within(status).getByText('0/4 met')).toBeInTheDocument();
    const terminal = screen.getByRole('log', { name: 'Agent terminal' });
    expect(terminal).toHaveTextContent('[optimizer] Gen 1: best in-sample Sharpe 0.84');
    expect(terminal).toHaveTextContent('IS g2 sma_crossover QQQ fast=12 slow=180 → Sharpe 0.84 · win 50% · 14 tr · dd -12% · evaluated');
    expect(screen.getByText('validated')).toBeInTheDocument();
    expect(screen.getByText(/out-of-sample Sharpe 0.40 over 6 trades/)).toBeInTheDocument();
    expect(screen.getByText(/No data: the paper runner daemon isn.t running/)).toBeInTheDocument();
    expect(screen.getByText('No data: OperationalError: test')).toBeInTheDocument(); // portfolio panel
    expect(screen.getByRole('group', { name: 'Equity curve, strategy versus benchmark' })).toBeInTheDocument();
  });

  it('streams new trials and raises the kill-switch banner on a state update', () => {
    render(<OpsCenter url="ws://test/telemetry" />);
    send(helloPayload);
    send({ type: 'trial', trial: trialPayload({ id: 9, period: 'out-of-sample', sharpe: 0.4, verdict: 'holdout look (counted)' }) });
    expect(screen.getByRole('log', { name: 'Agent terminal' })).toHaveTextContent('OOS sma_crossover QQQ');
    send({
      type: 'state',
      state: { ...statePayload, risk: { ...statePayload.risk, kill_switch: { engaged: true, reason: 'manual stop' } } },
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Kill switch engaged');
    expect(screen.getByRole('alert')).toHaveTextContent('manual stop');
  });

  it('counts invalid messages and goes offline when the stream drops', () => {
    render(<OpsCenter url="ws://test/telemetry" />);
    send('nope');
    send({ type: 'trial', trial: { id: 'x' } });
    expect(screen.getByText(/2 invalid message\(s\) ignored/)).toBeInTheDocument();
    act(() => {
      sockets.at(-1)?.drop();
    });
    expect(screen.getByText(/Telemetry offline/)).toBeInTheDocument();
  });
});
