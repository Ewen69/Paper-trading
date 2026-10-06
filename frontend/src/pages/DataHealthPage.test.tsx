// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { dataHealthPayload, quotePayload, stubFetch } from '../test/fakeFetch';
import { DataHealthPage } from './DataHealthPage';

describe('DataHealthPage', () => {
  it('shows live source, feeds with data types, and datasets with provenance', async () => {
    stubFetch({ '/data/health': dataHealthPayload });
    render(<DataHealthPage />);

    expect(await screen.findByText('Live source: Alpaca')).toBeInTheDocument();
    expect(screen.getByText('ACTIVE')).toBeInTheDocument();
    expect(screen.getByText('real-time')).toBeInTheDocument();
    expect(screen.getByText('delayed')).toBeInTheDocument();

    expect(screen.getByText('spy_2024.csv')).toBeInTheDocument();
    expect(screen.getByText('Vendor X')).toBeInTheDocument();
    expect(screen.getByText('stale')).toBeInTheDocument();
    expect(screen.getByText(/440 sessions/)).toBeInTheDocument();
    // Error datasets open their findings by default.
    expect(screen.getByText('crossed_market')).toBeInTheDocument();
  });

  it('says "No data" when there are no datasets', async () => {
    stubFetch({ '/data/health': { ...dataHealthPayload, overall: 'no data', datasets: [] } });
    render(<DataHealthPage />);
    expect(await screen.findByText(/No historical datasets imported yet/)).toBeInTheDocument();
  });

  it('looks up a quote and shows its provenance', async () => {
    stubFetch({ '/data/health': dataHealthPayload, '/quotes/stock/SPY': quotePayload });
    render(<DataHealthPage />);
    await screen.findByText('Live source: Alpaca');

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'SPY' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get quote' }));

    const bid = await screen.findByText('600.01');
    expect(bid).toBeInTheDocument();
    const quoteCard = bid.closest('section');
    expect(quoteCard).not.toBeNull();
    if (quoteCard) {
      expect(within(quoteCard).getByText('Alpaca IEX')).toBeInTheDocument();
      expect(within(quoteCard).getByText('quote time', { exact: false })).toBeInTheDocument();
    }
  });

  it('shows the backend reason when a quote is unavailable', async () => {
    stubFetch({
      '/data/health': dataHealthPayload,
      '/quotes/stock/SPY': {
        status: 503,
        body: { detail: 'No data: Alpaca paper API keys are not configured in .env' },
      },
    });
    render(<DataHealthPage />);
    await screen.findByText('Live source: Alpaca');
    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'SPY' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get quote' }));
    expect(await screen.findByText(/keys are not configured/)).toBeInTheDocument();
  });
});
