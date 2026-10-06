// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { stubFetch, universePayload } from '../test/fakeFetch';
import { portfolioPayload } from '../test/portfolioFixtures';
import { PortfolioPage } from './PortfolioPage';

const manualUrl = '/portfolio?book=manual&benchmark=&window=252';

const bodyOf = (mock: ReturnType<typeof stubFetch>, path: string): unknown => {
  const call = mock.mock.calls.find(([url]) => url === `/api${path}`);
  const init = (call as unknown as [string, RequestInit] | undefined)?.[1];
  return typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
};

describe('PortfolioPage', () => {
  it('shows values with their sources, unpriced holdings and rule checks', async () => {
    stubFetch({ [manualUrl]: portfolioPayload, '/backtest/universe': universePayload });
    render(<PortfolioPage />);
    expect(await screen.findByText('Rule checks (2 fired)')).toBeInTheDocument();
    expect(screen.getByText('QQQ is 100.0% of non-cash long value')).toBeInTheDocument();
    expect(screen.getByText('(fires above 20.0%)')).toBeInTheDocument();
    expect(screen.getByText(/they are not advice/)).toBeInTheDocument();
    const zzz = screen.getByText('ZZZ').closest('tr') as HTMLElement;
    expect(within(zzz).getByText('No data')).toBeInTheDocument();
    expect(within(zzz).getByText('No price source')).toBeInTheDocument();
    expect(screen.getByText(/Imported end-of-day close, dataset #1 · 2026-10-05/)).toBeInTheDocument();
    expect(screen.getByText('252 daily returns, 2025-10-03 to 2026-10-05. Covers 100.0% of long value.')).toBeInTheDocument();
    expect(screen.getByText('18.3%')).toBeInTheDocument(); // volatility
    expect(screen.getByRole('list', { name: 'Allocation by asset class' })).toHaveTextContent('etf');
  });

  it('switches to the paper book and says when it has no data', async () => {
    stubFetch({
      [manualUrl]: portfolioPayload,
      '/portfolio?book=paper&benchmark=&window=252': {
        ...portfolioPayload,
        book: 'paper',
        paper: { status: 'not_configured', detail: 'No data: Alpaca paper API keys are not set in .env.' },
        positions: [],
        allocation: [],
        concentration: null,
        risk: null,
        risk_note: 'No priced stock, ETF, fund, bond or crypto position with history to replay.',
        tips: [],
      },
      '/backtest/universe': universePayload,
    });
    render(<PortfolioPage />);
    await screen.findByText('Rule checks (2 fired)');
    fireEvent.click(screen.getByRole('button', { name: 'Paper account (simulated)' }));
    expect(await screen.findByText(/Paper account: No data: Alpaca paper API keys are not set/)).toBeInTheDocument();
    expect(screen.getByText('No positions in this view.')).toBeInTheDocument();
    expect(screen.getByText(/No priced stock, ETF/)).toBeInTheDocument();
  });

  it('adds an option holding with its contract terms', async () => {
    const mock = stubFetch({
      [manualUrl]: portfolioPayload,
      '/backtest/universe': universePayload,
      '/portfolio/holdings': {
        id: 9,
        created_at: '2026-10-06T15:00:00+00:00',
        symbol: 'SPY',
        asset_class: 'option',
        quantity: -1,
        account: '',
        option_type: 'put',
        strike: 540,
        expiration: '2026-12-18',
        manual_price: null,
        manual_price_as_of: null,
        note: '',
      },
    });
    render(<PortfolioPage />);
    const form = await screen.findByRole('form', { name: 'New holding' });
    fireEvent.change(within(form).getByLabelText('Asset class'), { target: { value: 'option' } });
    fireEvent.change(within(form).getByLabelText('Underlying'), { target: { value: 'SPY' } });
    fireEvent.change(within(form).getByLabelText('Contracts (negative = short)'), { target: { value: '-1' } });
    fireEvent.change(within(form).getByLabelText('Strike'), { target: { value: '540' } });
    fireEvent.change(within(form).getByLabelText('Expiration'), { target: { value: '2026-12-18' } });
    fireEvent.submit(form);
    expect(await screen.findByText('Added SPY.')).toBeInTheDocument();
    expect(bodyOf(mock, '/portfolio/holdings')).toEqual({
      symbol: 'SPY',
      asset_class: 'option',
      quantity: -1,
      account: '',
      option_type: 'put',
      strike: 540,
      expiration: '2026-12-18',
      manual_price: null,
      manual_price_as_of: null,
    });
  });

  it('shows a holdings import rejection in full', async () => {
    stubFetch({
      [manualUrl]: portfolioPayload,
      '/backtest/universe': universePayload,
      '/portfolio/import': { status: 422, body: { detail: 'Import rejected; nothing was saved.\nLine 2: bad' } },
    });
    render(<PortfolioPage />);
    const input = (await screen.findByText('Import CSV')).querySelector('input');
    if (!input) throw new Error('file input missing');
    fireEvent.change(input, { target: { files: [new File(['x'], 'h.csv')] } });
    expect(await screen.findByText(/Line 2: bad/)).toBeInTheDocument();
  });
});
