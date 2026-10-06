// The WebSocket and fetch are stubbed with test-only fixtures; production code never fabricates data.
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { agentPayload, findingPayload, riskStatePayload, snapshotPayload } from '../test/cityFixtures';
import { strategiesPayload, stubFetch, universePayload } from '../test/fakeFetch';
import { stubSocket, type FakeSocket } from '../test/fakeSocket';
import { CityPage } from './CityPage';

const routes = {
  '/backtest/universe': universePayload,
  '/backtest/strategies': strategiesPayload,
  '/backtest/options/universe': { as_of: '2026-10-06T15:00:00+00:00', source: 'x', entries: [] },
  '/risk/state': riskStatePayload,
};

let sockets: FakeSocket[];

function server(): FakeSocket {
  const socket = sockets.at(-1);
  if (!socket) throw new Error('no socket opened');
  return socket;
}

function send(payload: unknown) {
  act(() => {
    server().push(payload);
  });
}

describe('CityPage', () => {
  beforeEach(() => {
    sockets = stubSocket();
  });

  it('shows nothing but a connecting state until the snapshot arrives', () => {
    stubFetch(routes);
    render(<CityPage url="ws://test/activity" />);
    expect(screen.getByText('Connecting to the agent stream…')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Stream connecting');
    expect(server().url).toBe('ws://test/activity');
  });

  it('renders sectors, agents, free slots and provenance from the snapshot', async () => {
    stubFetch(routes);
    render(<CityPage url="ws://test/activity" />);
    send(snapshotPayload);
    expect(screen.getByRole('status')).toHaveTextContent('Stream live');
    expect(screen.getByText('Source: Agent runtime (local SQLite)')).toBeInTheDocument();
    const research = screen.getByRole('region', { name: 'Strategy Research Sector' });
    expect(within(research).getByText('Equity Quant')).toBeInTheDocument();
    expect(within(research).getByText('5 free slots reserved for sub-agents')).toBeInTheDocument();
    // jsdom has no WebGL: the page says so and keeps every detail in the DOM.
    expect(screen.getByText(/3D view unavailable/)).toBeInTheDocument();
    // The risk officer has no jobs of its own, so no autopilot toggle.
    const risk = screen.getByRole('region', { name: 'Options Risk Sector' });
    expect(within(risk).queryByLabelText('Autopilot')).toBeNull();
    expect(await within(risk).findByText('$1,000')).toBeInTheDocument();
    expect(screen.getByText('No findings yet.')).toBeInTheDocument();
  });

  it('streams what a running agent is testing and logs each finding', () => {
    stubFetch(routes);
    render(<CityPage url="ws://test/activity" />);
    send(snapshotPayload);
    send({
      type: 'agent',
      agent: agentPayload({
        status: 'running',
        message: 'In-sample test 2/9: sma_crossover (fast=20, slow=150) on SPY',
        params: { fast: 20, slow: 150 },
        progress_done: 2,
        progress_total: 9,
      }),
    });
    expect(screen.getByText('In-sample test 2/9: sma_crossover (fast=20, slow=150) on SPY')).toBeInTheDocument();
    expect(screen.getByText('Testing fast=20, slow=150')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Equity Quant progress' })).toHaveAttribute('aria-valuenow', '2');
    send({ type: 'finding', finding: findingPayload });
    const row = screen.getByRole('row', { name: /sma_crossover SPY/ });
    expect(row).toHaveTextContent('55.6%');
    expect(row).toHaveTextContent('0.81');
    expect(row).toHaveTextContent('#12');
  });

  it('raises a banner when the kill switch is engaged', () => {
    stubFetch(routes);
    render(<CityPage url="ws://test/activity" />);
    send(snapshotPayload);
    send({ type: 'kill_switch', engaged: true, reason: 'manual stop' });
    expect(screen.getByRole('alert')).toHaveTextContent('Kill switch engaged');
    expect(screen.getByRole('alert')).toHaveTextContent('manual stop');
    expect(screen.getByRole('button', { name: 'Release kill switch' })).toBeDisabled(); // needs a reason
  });

  it('ignores invalid messages and goes offline when the stream drops', () => {
    stubFetch(routes);
    render(<CityPage url="ws://test/activity" />);
    send('not json');
    send({ type: 'agent', agent: { id: 'x' } });
    expect(screen.getByText('2 invalid message(s) ignored')).toBeInTheDocument();
    act(() => {
      server().drop();
    });
    expect(screen.getByRole('status')).toHaveTextContent('Stream offline');
    expect(screen.getByText('No data: the agent stream is offline.')).toBeInTheDocument();
  });

  it('sends the kill switch only with a reason', async () => {
    const fetchMock = stubFetch({
      ...routes,
      '/risk/kill-switch': { ...riskStatePayload, kill_switch_engaged: true, kill_switch_reason: 'pause' },
    });
    render(<CityPage url="ws://test/activity" />);
    send(snapshotPayload);
    fireEvent.change(screen.getByLabelText('Kill switch reason'), { target: { value: 'pause' } });
    fireEvent.click(screen.getByRole('button', { name: 'Engage kill switch' }));
    expect(await screen.findByText('Kill switch engaged.')).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/risk/kill-switch');
    const init = (call as unknown as [string, RequestInit] | undefined)?.[1];
    expect(init?.body).toBe(JSON.stringify({ engaged: true, reason: 'pause' }));
  });
});
