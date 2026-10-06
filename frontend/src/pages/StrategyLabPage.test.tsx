// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { fireEvent, render, screen } from '@testing-library/react';
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
  it('explains the lock before the first run and defaults to in-sample', async () => {
    stubFetch(routes);
    render(<StrategyLabPage />);
    expect(await screen.findByText(/permanently lock the most recent 30%/)).toBeInTheDocument();
    expect(screen.getByLabelText('in-sample')).toBeChecked();
    expect(screen.getByLabelText('Fast window')).toHaveValue(50);
  });

  it('runs a backtest and shows the reality check with trial count and CIs', async () => {
    const fetchMock = stubFetch({ ...routes, '/backtest/runs': reportPayload });
    render(<StrategyLabPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run backtest' }));

    expect(await screen.findByText('Reality check')).toBeInTheDocument();
    expect(screen.getByText(/3 for this strategy on SPY/)).toBeInTheDocument();
    expect(screen.getByText(/Only 4 trade\(s\)/)).toBeInTheDocument();
    expect(screen.getByText(/-1.0% · 95% CI -6.0% to 4.0%/)).toBeInTheDocument();
    expect(screen.getByText('Backtest on Vendor X (spy.csv, dataset #1)')).toBeInTheDocument();
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
