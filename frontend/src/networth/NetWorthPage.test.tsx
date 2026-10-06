// Fetch is stubbed with test-only fixtures; production code never fabricates data.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { stubFetch } from '../test/fakeFetch';
import { emptyNetWorth, netWorthPayload, projectionPayload } from '../test/netWorthFixtures';
import { NetWorthPage } from './NetWorthPage';

const bodyOf = (mock: ReturnType<typeof stubFetch>, path: string): unknown => {
  const call = mock.mock.calls.find(([url]) => url === `/api${path}`);
  const init = (call as unknown as [string, RequestInit] | undefined)?.[1];
  return typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
};

describe('NetWorthPage', () => {
  it('says "no data" before any balance and offers no projection', async () => {
    stubFetch({ '/networth': emptyNetWorth });
    render(<NetWorthPage />);
    expect(await screen.findByText(/No balances yet/)).toBeInTheDocument();
    expect(screen.getByText('No data: enter a balance to start the history.')).toBeInTheDocument();
    expect(screen.getByText('No data: enter balances first.')).toBeInTheDocument();
    expect(screen.getByText(/Your manual entries and CSV imports/)).toBeInTheDocument();
    expect(screen.getByText(/No bank logins/)).toBeInTheDocument();
  });

  it('shows totals with dates, missing and stale accounts', async () => {
    stubFetch({ '/networth': netWorthPayload });
    render(<NetWorthPage />);
    expect(await screen.findByText(/newest 2026-09-30, oldest 2026-06-30/)).toBeInTheDocument();
    expect(screen.getByText('No balance yet (counted as 0): Card')).toBeInTheDocument();
    expect(screen.getByText('Stale (older than 45 days): Old car')).toBeInTheDocument();
    const car = screen.getByRole('heading', { name: 'Old car' }).closest('li') as HTMLElement;
    expect(within(car).getByText('stale')).toBeInTheDocument();
    const card = screen.getByRole('heading', { name: 'Card' }).closest('li') as HTMLElement;
    expect(within(card).getByText('No data')).toBeInTheDocument();
    expect(within(card).getByText('never entered')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Net worth history' })).toBeInTheDocument();
  });

  it('saves a balance and rejects a negative one before sending', async () => {
    const mock = stubFetch({
      '/networth': netWorthPayload,
      '/networth/balances': {
        id: 9,
        account_id: 1,
        as_of: '2026-10-01',
        amount: 12500.5,
        source: 'manual entry',
        entered_at: '2026-10-06T15:00:00+00:00',
        replaced: false,
      },
    });
    render(<NetWorthPage />);
    const form = await screen.findByRole('form', { name: 'New balance for Checking' });
    const amount = within(form).getByLabelText('Balance ($)');
    fireEvent.change(amount, { target: { value: '-5' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save balance' }));
    expect(await screen.findByText(/Enter a positive amount/)).toBeInTheDocument();
    expect(bodyOf(mock, '/networth/balances')).toBeUndefined();
    fireEvent.change(within(form).getByLabelText('Date'), { target: { value: '2026-10-01' } });
    fireEvent.change(amount, { target: { value: '12500.50' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save balance' }));
    expect(await screen.findByText(/Saved .* for 2026-10-01\./)).toBeInTheDocument();
    expect(bodyOf(mock, '/networth/balances')).toEqual({
      account_id: 1,
      as_of: '2026-10-01',
      amount: 12500.5,
    });
  });

  it('imports a CSV and shows a rejection in full', async () => {
    const mock = stubFetch({
      '/networth': netWorthPayload,
      '/networth/import': {
        status: 422,
        body: { detail: 'Import rejected; nothing was saved.\nLine 3: bad date' },
      },
    });
    render(<NetWorthPage />);
    const input = (await screen.findByText('Import CSV')).querySelector('input');
    const file = new File(['account,kind,category,as_of,amount\n'], 'mine.csv', { type: 'text/csv' });
    if (!input) throw new Error('file input missing');
    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText(/Line 3: bad date/)).toBeInTheDocument();
    expect(bodyOf(mock, '/networth/import')).toEqual({
      file_name: 'mine.csv',
      content: 'account,kind,category,as_of,amount\n',
    });
    expect(screen.getByRole('link', { name: 'Export CSV' })).toHaveAttribute(
      'href',
      '/api/networth/export',
    );
  });

  it('shows the projection as a labeled assumption range', async () => {
    stubFetch({
      '/networth': netWorthPayload,
      '/networth/projection?years=2&low=0.04&high=0.06&contribution=0': projectionPayload,
    });
    render(<NetWorthPage />);
    const form = await screen.findByRole('form', { name: 'Projection inputs' });
    fireEvent.change(within(form).getByLabelText('Years'), { target: { value: '2' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Show range' }));
    expect(await screen.findByText(/After 2 years: \$21,632 to \$22,472/)).toBeInTheDocument();
    expect(screen.getByText(/Assumption, not a forecast/)).toBeInTheDocument();
    expect(screen.getByText('4.0% a year')).toBeInTheDocument();
    expect(screen.getByText('6.0% a year')).toBeInTheDocument();
  });

  it('shows the backend refusal for a negative net worth', async () => {
    stubFetch({
      '/networth': netWorthPayload,
      '/networth/projection?years=10&low=0.04&high=0.06&contribution=0': {
        status: 422,
        body: { detail: 'Projection needs a positive net worth.' },
      },
    });
    render(<NetWorthPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Show range' }));
    expect(await screen.findByText('Projection needs a positive net worth.')).toBeInTheDocument();
  });
});
