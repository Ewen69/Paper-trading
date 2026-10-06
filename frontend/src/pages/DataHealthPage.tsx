import { useState } from 'react';

import { fetchDataHealth } from '../api/client';
import type { DataHealth, Dataset } from '../api/schemas';
import { useApi } from '../api/useApi';
import { Badge } from '../components/Badge';
import { Card, NoData } from '../components/Card';
import { IssueList } from '../components/IssueList';
import { Provenance } from '../components/Provenance';
import { QuoteLookup } from '../components/QuoteLookup';
import { statusTone } from '../components/tones';

const KIND_LABEL: Record<Dataset['kind'], string> = {
  equity_bars: 'Equity daily bars',
  option_quotes: 'Option EOD quotes',
};

function LiveSource({ live }: { live: DataHealth['live_source'] }) {
  const reachability =
    live.reachable === null ? 'not checked' : live.reachable ? 'reachable' : 'unreachable';
  return (
    <Card title={`Live source: ${live.name}`} actions={<Badge tone={statusTone(live.status)}>{live.status}</Badge>}>
      <dl className="mb-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
        <dt className="text-slate-400">Paper keys</dt>
        <dd>{live.configured ? 'configured' : 'not configured'}</dd>
        <dt className="text-slate-400">Connection</dt>
        <dd>{reachability}</dd>
        <dt className="text-slate-400">Paper account</dt>
        <dd>{live.account_status ?? '—'}</dd>
        {live.error && (
          <>
            <dt className="text-slate-400">Problem</dt>
            <dd className="text-amber-300">{live.error}</dd>
          </>
        )}
      </dl>
      <div className="mb-3 grid gap-3 sm:grid-cols-2">
        {(
          [
            ['Stocks / ETFs', live.stock_feed],
            ['Options', live.option_feed],
          ] as const
        ).map(([label, feed]) => (
          <div key={label} className="rounded-lg bg-slate-950/60 p-3 text-sm">
            <p className="text-xs text-slate-400">{label}</p>
            <p className="flex flex-wrap items-center gap-2 font-medium">
              {feed.name} <Badge tone="muted">{feed.data_type}</Badge>
            </p>
            <p className="mt-1 text-xs text-slate-400">{feed.note}</p>
          </div>
        ))}
      </div>
      <Provenance source="Alpaca paper account check" asOf={live.checked_at} asOfLabel="checked" />
    </Card>
  );
}

function DatasetRow({ dataset }: { dataset: Dataset }) {
  const [open, setOpen] = useState(dataset.status === 'error');
  return (
    <li className="rounded-lg border border-slate-800 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(dataset.status)}>{dataset.status}</Badge>
          <span className="font-medium">
            #{dataset.id} {KIND_LABEL[dataset.kind]}
          </span>
          <span className="font-mono text-sm text-slate-400">{dataset.file_name}</span>
        </div>
        <button
          type="button"
          onClick={() => {
            setOpen((o) => !o);
          }}
          className="text-sm text-sky-400 hover:text-sky-300"
          aria-expanded={open}
        >
          {open ? 'Hide' : 'Show'} findings ({dataset.issues.length})
        </button>
      </div>
      <p className="mt-2 text-sm text-slate-300">
        {dataset.row_count.toLocaleString()} rows · {dataset.symbol_count.toLocaleString()}{' '}
        symbol(s) · {dataset.coverage_start} → {dataset.coverage_end}
      </p>
      <div className="mt-2">
        <Provenance
          source={dataset.provenance.source}
          asOf={dataset.provenance.as_of}
          dataType={dataset.provenance.data_type}
          stale={dataset.provenance.stale}
          staleReason={dataset.provenance.stale_reason}
          asOfLabel="imported"
        />
      </div>
      {open && (
        <div className="mt-3">
          <IssueList issues={dataset.issues} />
        </div>
      )}
    </li>
  );
}

export function DataHealthPage() {
  const [state, reload] = useApi(fetchDataHealth);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold">Data Health</h2>
          {state.kind === 'ready' && (
            <Badge tone={statusTone(state.data.overall)}>{state.data.overall}</Badge>
          )}
        </div>
        <button
          type="button"
          onClick={reload}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>

      {state.kind === 'loading' && <p className="text-slate-400">Loading…</p>}
      {state.kind === 'error' && (
        <Card title="Data Health">
          <NoData message={`Backend unreachable or invalid: ${state.message}`} />
        </Card>
      )}
      {state.kind === 'ready' && (
        <>
          <Provenance source={state.data.source} asOf={state.data.as_of} asOfLabel="report at" />
          <p className="text-sm text-slate-400">
            NYSE is {state.data.market_open ? 'open' : 'closed'} · last completed session{' '}
            {state.data.last_completed_session}
          </p>
          <LiveSource live={state.data.live_source} />
          <QuoteLookup />
          <Card title="Historical datasets (CSV)">
            {state.data.datasets.length === 0 ? (
              <NoData message="No historical datasets imported yet. See docs/DATA.md, then run: npm run ptl -- import-csv <file> --kind equity-bars --source <vendor>" />
            ) : (
              <ul className="space-y-3">
                {state.data.datasets.map((d) => (
                  <DatasetRow key={d.id} dataset={d} />
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
