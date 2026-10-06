// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { strategiesPayload, stubFetch, universePayload } from '../test/fakeFetch';
import {
  optionsReportPayload,
  optionsUniversePayload,
  putSpreadStrategy,
} from '../test/optionsFixtures';
import { StrategyLabPage } from './StrategyLabPage';

const routes = {
  '/backtest/strategies': [...strategiesPayload, putSpreadStrategy],
  '/backtest/universe': universePayload,
  '/backtest/options/universe': optionsUniversePayload,
};

async function openOptions() {
  fireEvent.click(await screen.findByRole('tab', { name: 'Options' }));
}

describe('Options lab', () => {
  it('shows options strategies only, with a pre-run reality check', async () => {
    stubFetch(routes);
    render(<StrategyLabPage />);
    await openOptions();
    const before = await screen.findByLabelText('Reality check before you run');
    expect(before).toHaveTextContent('every quote has an exercise style');
    expect(before).toHaveTextContent('underlying SPY bars: 1 dataset(s)');
    expect(screen.getByRole('option', { name: 'Put credit spread' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Moving-average crossover' })).toBeNull();
    expect(screen.getByLabelText('Commission ($ per contract)')).toHaveValue(0.65);
  });

  it('runs and shows the reality check first, mechanics counts, positions and events', async () => {
    const fetchMock = stubFetch({ ...routes, '/backtest/options/runs': optionsReportPayload });
    render(<StrategyLabPage />);
    await openOptions();
    fireEvent.click(await screen.findByRole('button', { name: 'Run options backtest' }));

    const reality = await screen.findByRole('region', { name: /Reality check: read this first/ });
    // Shown as the costs tile and again in the full assumptions list.
    expect(within(reality).getAllByText('Option slippage')).toHaveLength(2);
    expect(within(reality).getByText(/finished between their strikes/)).toBeInTheDocument();
    const chart = screen.getByRole('group', { name: /Equity curve/ });
    expect(reality.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    expect(screen.getByText('Pin-risk finishes').nextSibling).toHaveTextContent('1');
    expect(screen.getByText('SPY 2025-01-17 580/575 put credit spread x2')).toBeInTheDocument();
    expect(screen.getByText('pin risk: assigned, shares liquidated next open')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Event log' })).getByText(/Pin risk: 577/)).toBeInTheDocument();
    expect(screen.getByText('SPY raw bars: Vendor X (spy.csv, dataset #1)')).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([input]) => input === '/api/backtest/options/runs');
    expect(call).toBeDefined();
  });

  it('says "No data" when no option quotes are imported', async () => {
    stubFetch({ ...routes, '/backtest/options/universe': { ...optionsUniversePayload, entries: [] } });
    render(<StrategyLabPage />);
    await openOptions();
    expect(await screen.findByText(/No option quotes imported yet/)).toBeInTheDocument();
  });
});
