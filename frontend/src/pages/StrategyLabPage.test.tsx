// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  reportPayload,
  strategiesPayload,
  stubFetch,
  universePayload,
} from '../test/fakeFetch';
import { StrategyLabPage } from './StrategyLabPage';

const routes = {
  '/backtest/strategies': strategiesPayload,
  '/backtest/universe': universePayload,
};

describe('StrategyLabPage', () => {
  it('shows a reality check before the first run and defaults to in-sample', async () => {
    stubFetch(routes);
    render(<StrategyLabPage />);
    const before = await screen.findByLabelText('Reality check before you run');
    expect(before).toHaveTextContent('permanently seals the most recent 30%');
    expect(before).toHaveTextContent('0 in-sample run(s) so far');
    expect(before).toHaveTextContent('Source: Vendor X (spy.csv, dataset #1)');
    expect(screen.getByLabelText('in-sample')).toBeChecked();
    expect(screen.getByLabelText('Fast window')).toHaveValue(50);
  });

  it('puts the reality check first and flags exactly what the backend flagged', async () => {
    const fetchMock = stubFetch({ ...routes, '/backtest/runs': reportPayload });
    render(<StrategyLabPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run backtest' }));

    const reality = await screen.findByRole('region', { name: /Reality check: read this first/ });
    const chart = screen.getByRole('group', { name: /Equity curve/ });
    // The reality check precedes the chart in reading order.
    expect(reality.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const tile = (label: string) => within(reality).getByText(label).closest('div');
    expect(tile('Trades')).toHaveTextContent('⚠ flagged'); // low_trades raised
    expect(tile('Combinations tried')).toHaveTextContent('⚠ flagged'); // many_trials raised
    expect(tile('Out-of-sample looks')).not.toHaveTextContent('flagged'); // no oos_repeat
    expect(within(reality).getByText(/-6.0% to 4.0%/)).toBeInTheDocument();
    expect(within(reality).getByText('Auditor says')).toBeInTheDocument();
    expect(within(reality).getByText(/Only 4 trade\(s\)/)).toBeInTheDocument();
    expect(within(reality).getByText(/Run #7 · Backtest on Vendor X/)).toBeInTheDocument();
    expect(screen.getAllByText(/n\/a \(too little data\)/).length).toBeGreaterThan(0);

    expect(fetchMock.mock.calls.some(([input]) => input === '/api/backtest/runs')).toBe(true);
  });

  it('shows the reason when a backtest is refused', async () => {
    stubFetch({
      ...routes,
      '/backtest/runs': { status: 409, body: { detail: 'SPY data has error-level quality issues' } },
    });
    render(<StrategyLabPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run backtest' }));
    expect(await screen.findByText(/error-level quality issues/)).toBeInTheDocument();
  });

  it('says "No data" when nothing is imported', async () => {
    stubFetch({ ...routes, '/backtest/universe': { ...universePayload, entries: [] } });
    render(<StrategyLabPage />);
    expect(await screen.findByText(/No equity datasets imported yet/)).toBeInTheDocument();
  });
});
