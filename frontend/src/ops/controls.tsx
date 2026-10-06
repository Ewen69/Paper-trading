/** Per-agent controls. Every action goes through the backend's rules; nothing here bypasses them. */
import { useState, type SyntheticEvent } from 'react';

import {
  fetchOptionsUniverse,
  fetchRiskState,
  fetchStrategies,
  fetchUniverse,
  setKillSwitch,
  startDataCheck,
  startPaperCycle,
  startSweep,
} from '../api/client';
import { useApi } from '../api/useApi';
import { inputClass } from '../components/fields';

type Status = { kind: 'idle' } | { kind: 'busy' } | { kind: 'ok'; text: string } | { kind: 'error'; text: string };

function useAction() {
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const run = (action: () => Promise<string>) => {
    setStatus({ kind: 'busy' });
    action()
      .then((text) => {
        setStatus({ kind: 'ok', text });
      })
      .catch((error: unknown) => {
        setStatus({ kind: 'error', text: error instanceof Error ? error.message : 'unknown' });
      });
  };
  return { status, run };
}

function StatusLine({ status }: { status: Status }) {
  if (status.kind === 'ok') return <p className="mt-1 text-xs text-emerald-300">{status.text}</p>;
  if (status.kind === 'error') return <p role="alert" className="mt-1 text-xs text-rose-300">{status.text}</p>;
  return null;
}

const buttonClass = 'btn-pixel bg-sky-600 px-3 py-2 text-white hover:bg-sky-500 disabled:opacity-50';

export function DataCheckControl() {
  const { status, run } = useAction();
  return (
    <div>
      <button
        type="button"
        className={buttonClass}
        disabled={status.kind === 'busy'}
        onClick={() => {
          run(() => startDataCheck().then((j) => `Data check queued as job #${String(j.job_id)}.`));
        }}
      >
        Run data check
      </button>
      <StatusLine status={status} />
    </div>
  );
}

export function EquitySweepControl() {
  const [universe] = useApi(fetchUniverse);
  const [strategies] = useApi(fetchStrategies);
  const [entry, setEntry] = useState('');
  const [strategy, setStrategy] = useState('sma_crossover');
  const [promote, setPromote] = useState(true);
  const { status, run } = useAction();
  if (universe.kind !== 'ready' || strategies.kind !== 'ready') {
    return <p className="text-xs text-slate-400">{universe.kind === 'error' ? 'No data: backend unreachable.' : 'Loading…'}</p>;
  }
  const entries = universe.data.entries;
  if (entries.length === 0) {
    return <p className="text-xs text-slate-400">No data: import equity bars to give this agent work.</p>;
  }
  const choices = strategies.data.filter((s) => s.asset === 'equity' && s.id !== 'buy_and_hold');
  const selected = entries.find((e) => `${String(e.dataset_id)}:${e.symbol}` === entry) ?? entries[0];
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!selected) return;
    run(() =>
      startSweep({ asset: 'equity', strategy, symbol: selected.symbol, dataset_id: selected.dataset_id, promote })
        .then((j) => `Sweep queued as job #${String(j.job_id)}.`),
    );
  };
  return (
    <form onSubmit={submit} className="space-y-2 text-xs" aria-label="Equity sweep">
      <select aria-label="Data" className={inputClass} value={entry} onChange={(e) => { setEntry(e.target.value); }}>
        {entries.map((e) => (
          <option key={`${String(e.dataset_id)}:${e.symbol}`} value={`${String(e.dataset_id)}:${e.symbol}`}>
            #{e.dataset_id} {e.symbol} · {e.first_date} → {e.last_date}
          </option>
        ))}
      </select>
      <select aria-label="Strategy" className={inputClass} value={strategy} onChange={(e) => { setStrategy(e.target.value); }}>
        {choices.map((s) => (
          <option key={s.id} value={s.id}>{s.name}</option>
        ))}
      </select>
      <label className="flex items-start gap-2 text-slate-300">
        <input type="checkbox" checked={promote} onChange={(e) => { setPromote(e.target.checked); }} />
        <span>Give the finalist its one counted out-of-sample test (never repeated)</span>
      </label>
      <button type="submit" className={buttonClass} disabled={status.kind === 'busy'}>Start in-sample sweep</button>
      <StatusLine status={status} />
    </form>
  );
}

export function OptionsSweepControl() {
  const [universe] = useApi(fetchOptionsUniverse);
  const [promote, setPromote] = useState(true);
  const { status, run } = useAction();
  if (universe.kind !== 'ready') {
    return <p className="text-xs text-slate-400">{universe.kind === 'error' ? 'No data: backend unreachable.' : 'Loading…'}</p>;
  }
  const entry = universe.data.entries.find((e) => e.underlying_datasets.length > 0 && e.rows_missing_style === 0);
  const bars = entry?.underlying_datasets[0];
  if (!entry || !bars) {
    return <p className="text-xs text-slate-400">No data: import option quotes (with exercise_style) and the underlying's bars.</p>;
  }
  return (
    <div className="space-y-2 text-xs">
      <p className="text-slate-300">
        Put credit spread on {entry.underlying} (quotes #{entry.dataset_id}, bars #{bars.dataset_id})
      </p>
      <label className="flex items-start gap-2 text-slate-300">
        <input type="checkbox" checked={promote} onChange={(e) => { setPromote(e.target.checked); }} />
        <span>Give the finalist its one counted out-of-sample test</span>
      </label>
      <button
        type="button"
        className={buttonClass}
        disabled={status.kind === 'busy'}
        onClick={() => {
          run(() =>
            startSweep({ asset: 'options', strategy: 'put_credit_spread', symbol: entry.underlying,
                         dataset_id: bars.dataset_id, options_dataset_id: entry.dataset_id, promote })
              .then((j) => `Sweep queued as job #${String(j.job_id)}.`),
          );
        }}
      >
        Start in-sample sweep
      </button>
      <StatusLine status={status} />
    </div>
  );
}

export function RiskControl({ engaged }: { engaged: boolean | null }) {
  const [risk, reload] = useApi(fetchRiskState);
  const [reason, setReason] = useState('');
  const { status, run } = useAction();
  return (
    <div className="space-y-2 text-xs">
      {risk.kind === 'ready' && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-slate-300">
          <dt className="text-slate-400">Max loss / trade</dt>
          <dd>${risk.data.limits.max_loss_per_trade.toLocaleString()}</dd>
          <dt className="text-slate-400">Max daily loss</dt>
          <dd>${risk.data.limits.max_daily_loss.toLocaleString()}</dd>
          <dt className="text-slate-400">Max open positions</dt>
          <dd>{risk.data.limits.max_open_positions}</dd>
          <dt className="text-slate-400">Max capital at risk</dt>
          <dd>{(risk.data.limits.max_capital_at_risk_pct * 100).toFixed(0)}% of equity</dd>
          <dt className="col-span-2 mt-1 text-[11px] text-slate-500">
            {risk.data.source} · {risk.data.as_of.toLocaleString()}
          </dt>
        </dl>
      )}
      <input
        aria-label="Kill switch reason"
        className={inputClass}
        placeholder="Reason (required)"
        value={reason}
        onChange={(e) => { setReason(e.target.value); }}
      />
      <button
        type="button"
        disabled={status.kind === 'busy' || reason.trim().length < 3}
        className={`btn-pixel px-3 py-2 text-white disabled:opacity-50 ${engaged ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-rose-600 hover:bg-rose-500'}`}
        onClick={() => {
          run(() =>
            setKillSwitch(!engaged, reason.trim()).then((s) => {
              reload();
              return `Kill switch ${s.kill_switch_engaged ? 'engaged' : 'released'}.`;
            }),
          );
        }}
      >
        {engaged ? 'Release kill switch' : 'Engage kill switch'}
      </button>
      <StatusLine status={status} />
    </div>
  );
}

export function PaperCycleControl() {
  const [universe] = useApi(fetchUniverse);
  const [strategies] = useApi(fetchStrategies);
  const [strategy, setStrategy] = useState('buy_and_hold');
  const [dryRun, setDryRun] = useState(true);
  const { status, run } = useAction();
  if (universe.kind !== 'ready' || strategies.kind !== 'ready') {
    return <p className="text-xs text-slate-400">{universe.kind === 'error' ? 'No data: backend unreachable.' : 'Loading…'}</p>;
  }
  const entry = universe.data.entries[0];
  if (!entry) {
    return <p className="text-xs text-slate-400">No data: import current equity bars to paper trade.</p>;
  }
  return (
    <div className="space-y-2 text-xs">
      <select aria-label="Paper strategy" className={inputClass} value={strategy} onChange={(e) => { setStrategy(e.target.value); }}>
        {strategies.data.filter((s) => s.asset === 'equity').map((s) => (
          <option key={s.id} value={s.id}>{s.name}</option>
        ))}
      </select>
      <label className="flex items-center gap-2 text-slate-300">
        <input type="checkbox" checked={dryRun} onChange={(e) => { setDryRun(e.target.checked); }} />
        Dry run (log only, never send)
      </label>
      {!dryRun && <p className="text-amber-300">Sends a real order to the Alpaca PAPER account (no real money).</p>}
      <button
        type="button"
        className={buttonClass}
        disabled={status.kind === 'busy'}
        onClick={() => {
          run(() =>
            startPaperCycle({ strategy, dataset_id: entry.dataset_id, symbol: entry.symbol, dry_run: dryRun })
              .then((j) => `Paper cycle queued as job #${String(j.job_id)} on ${entry.symbol}.`),
          );
        }}
      >
        Run one cycle on {entry.symbol}
      </button>
      <StatusLine status={status} />
    </div>
  );
}
