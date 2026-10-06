import { useState, type SyntheticEvent } from 'react';

import { fetchQuote } from '../api/client';
import type { Quote } from '../api/schemas';
import { Card, NoData } from './Card';
import { IssueList } from './IssueList';
import { Provenance } from './Provenance';

type Kind = 'stock' | 'option';
type State =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; quote: Quote };

const PLACEHOLDER: Record<Kind, string> = { stock: 'SPY', option: 'SPY261218C00600000' };

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 4 });

export function QuoteLookup() {
  const [kind, setKind] = useState<Kind>('stock');
  const [symbol, setSymbol] = useState('');
  const [state, setState] = useState<State>({ kind: 'idle' });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!symbol.trim()) return;
    setState({ kind: 'loading' });
    fetchQuote(kind, symbol)
      .then((quote) => {
        setState({ kind: 'ready', quote });
      })
      .catch((error: unknown) => {
        setState({ kind: 'error', message: error instanceof Error ? error.message : 'unknown' });
      });
  };

  return (
    <Card title="Live quote check">
      <form onSubmit={submit} className="mb-4 flex flex-wrap gap-2">
        <select
          aria-label="Instrument type"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value === 'option' ? 'option' : 'stock');
          }}
          className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
        >
          <option value="stock">Stock / ETF</option>
          <option value="option">Option (OCC symbol)</option>
        </select>
        <input
          aria-label="Symbol"
          value={symbol}
          onChange={(e) => {
            setSymbol(e.target.value);
          }}
          placeholder={PLACEHOLDER[kind]}
          className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm uppercase"
        />
        <button
          type="submit"
          disabled={state.kind === 'loading'}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
        >
          {state.kind === 'loading' ? 'Fetching…' : 'Get quote'}
        </button>
      </form>

      {state.kind === 'error' && <NoData message={state.message} />}
      {state.kind === 'ready' && (
        <div className="space-y-3">
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            {(
              [
                ['Bid', fmt(state.quote.bid), `size ${fmt(state.quote.bid_size)}`],
                ['Ask', fmt(state.quote.ask), `size ${fmt(state.quote.ask_size)}`],
                ['Spread', fmt(state.quote.spread), 'ask − bid'],
                ['Symbol', state.quote.symbol, ''],
              ] as const
            ).map(([label, value, sub]) => (
              <div key={label} className="rounded-lg bg-slate-950/60 p-3">
                <dt className="text-xs text-slate-400">{label}</dt>
                <dd className="font-mono text-lg text-slate-100">{value}</dd>
                {sub && <dd className="text-xs text-slate-500">{sub}</dd>}
              </div>
            ))}
          </dl>
          <Provenance
            source={state.quote.provenance.source}
            asOf={state.quote.provenance.as_of}
            dataType={state.quote.provenance.data_type}
            stale={state.quote.provenance.stale}
            staleReason={state.quote.provenance.stale_reason}
            asOfLabel="quote time"
          />
          {state.quote.issues.length > 0 && <IssueList issues={state.quote.issues} />}
        </div>
      )}
    </Card>
  );
}
