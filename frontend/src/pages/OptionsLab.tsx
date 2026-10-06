import { useState, type SyntheticEvent } from 'react';

import { fetchOptionsUniverse, runOptionsBacktest } from '../api/client';
import type { OptionsReport, OptionsUniverseEntry, Period, Strategy } from '../api/schemas';
import { useApi } from '../api/useApi';
import { Card, NoData } from '../components/Card';
import { EquityChart } from '../components/EquityChart';
import { inputClass, NumberField } from '../components/fields';
import { MetricsTable } from '../components/MetricsTable';
import { Provenance } from '../components/Provenance';
import { RealityCheckPanel } from '../components/RealityCheckPanel';
import { pct, usd } from '../format';
import { AgentSprite } from '../game/AgentSprite';

type RunState =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; report: OptionsReport };

const keyOf = (e: OptionsUniverseEntry) => `${String(e.dataset_id)}:${e.underlying}`;
const defaults = (s: Strategy | undefined) =>
  Object.fromEntries((s?.params ?? []).map((p) => [p.name, p.default]));

function BeforeYouRun({ entry }: { entry: OptionsUniverseEntry }) {
  const facts = [
    `${entry.quote_days.toLocaleString()} quote days, ${entry.rows.toLocaleString()} quotes (${entry.first_date} → ${entry.last_date})`,
    entry.rows_missing_style > 0
      ? `${entry.rows_missing_style.toLocaleString()} quotes have no exercise_style: runs touching them are refused`
      : 'every quote has an exercise style (American settles in shares, European in cash)',
    entry.underlying_datasets.length > 0
      ? `underlying ${entry.underlying} bars: ${String(entry.underlying_datasets.length)} dataset(s)`
      : `no ${entry.underlying} daily bars imported: needed for settlement and the benchmark`,
    entry.lock
      ? `out-of-sample sealed from ${entry.lock.oos_start}`
      : `your first run permanently seals the most recent 30% of ${entry.underlying} as out-of-sample`,
  ];
  return (
    <div
      className="flex gap-3 border-2 border-amber-500/40 bg-amber-500/5 p-3 md:col-span-2"
      aria-label="Reality check before you run"
    >
      <div className="shrink-0">
        <AgentSprite agentId="auditor" size={32} animate={false} />
      </div>
      <div className="min-w-0 text-sm">
        <p className="font-pixel text-[8px] uppercase text-amber-300">
          Reality check before you run · {entry.underlying} options
        </p>
        <ul className="mt-1.5 list-inside list-disc text-slate-300">
          {facts.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
        <p className="mt-1.5 text-[11px] text-slate-500">
          Source: {entry.source} ({entry.file_name}, dataset #{entry.dataset_id})
        </p>
      </div>
    </div>
  );
}

function OptionsResults({ report }: { report: OptionsReport }) {
  const s = report.strategy_metrics;
  const c = report.counts;
  const tiles: [string, string][] = [
    ['Pin-risk finishes', String(c.pin_events)],
    ['Early assignments', String(c.early_assignments)],
    ['Orders not filled', String(c.rejected_orders)],
    ['Stale marks', String(c.stale_marks)],
    ['Margin shortfalls', String(c.margin_shortfalls)],
    ['Peak collateral held', usd(c.peak_collateral)],
  ];
  return (
    <div className="space-y-6">
      <RealityCheckPanel report={report} />
      <Card title={`Result #${String(report.run_id)}: ${report.strategy.name} on ${report.symbol}`}>
        <p className="mb-2 text-sm text-slate-300">
          {report.reality_check.period} · {s.trade_count} positions · final equity{' '}
          {usd(s.final_equity)} ({pct(s.total_return)}) vs buy &amp; hold{' '}
          {usd(report.benchmark_metrics.final_equity)} ({pct(report.benchmark_metrics.total_return)})
        </p>
        <div className="mb-4 space-y-1">
          {[report.provenance, report.underlying_provenance, report.benchmark_provenance].map((p) => (
            <Provenance
              key={p.source}
              source={p.source}
              asOf={p.as_of}
              dataType={p.data_type}
              asOfLabel="computed"
            />
          ))}
        </div>
        <EquityChart
          curve={report.curve}
          labels={{
            strategy: `${report.symbol} options`,
            benchmark: `${report.benchmark_symbol} buy & hold`,
          }}
        />
      </Card>
      <Card title="Options mechanics in this run">
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {tiles.map(([label, value]) => (
            <div key={label} className="border-2 border-slate-800 bg-slate-950/60 p-3">
              <dt className="text-[11px] text-slate-400">{label}</dt>
              <dd className="font-mono text-lg text-slate-100">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-[11px] text-slate-500">Source: event log of run #{report.run_id}</p>
      </Card>
      <Card title="Metrics">
        <MetricsTable report={report} />
      </Card>
      <Card title={`Positions (${String(report.trades.length)})`}>
        {report.trades.length === 0 ? (
          <p className="text-sm text-slate-400">No positions in this window.</p>
        ) : (
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead className="sticky top-0 bg-slate-900 text-left text-xs text-slate-400">
                <tr>
                  <th className="py-1 pr-3 font-normal">Position</th>
                  <th className="py-1 pr-3 font-normal">Opened → closed</th>
                  <th className="py-1 pr-3 font-normal">Premium</th>
                  <th className="py-1 pr-3 font-normal">Max loss</th>
                  <th className="py-1 pr-3 font-normal">P&amp;L after costs</th>
                  <th className="py-1 font-normal">Exit</th>
                </tr>
              </thead>
              <tbody className="text-slate-200">
                {report.trades.map((t) => (
                  <tr key={t.position_id} className="border-t border-slate-800/60 align-top">
                    <td className="py-1 pr-3">{t.description}</td>
                    <td className="py-1 pr-3 font-mono">
                      {t.opened} → {t.closed}
                    </td>
                    <td className="py-1 pr-3 font-mono">{usd(t.entry_premium)}</td>
                    <td className="py-1 pr-3 font-mono">{usd(t.max_loss)}</td>
                    <td className="py-1 pr-3 font-mono">{usd(t.pnl)}</td>
                    <td className="py-1 text-slate-400">{t.exit_kind}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title={`Event log (${String(report.events.length)})`}>
        <ol className="max-h-96 space-y-1.5 overflow-auto text-sm" aria-label="Event log">
          {report.events.map((e, i) => (
            <li key={`${e.day}-${String(i)}`} className="border-l-2 border-slate-700 pl-3">
              <span className="font-mono text-xs text-slate-500">{e.day}</span>{' '}
              <span className="font-pixel text-[8px] uppercase text-sky-300">{e.kind}</span>
              <p className="text-slate-300">{e.text}</p>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

function OptionsForm({
  entries,
  strategies,
  onDone,
}: {
  entries: OptionsUniverseEntry[];
  strategies: Strategy[];
  onDone: () => void;
}) {
  const [entryKey, setEntryKey] = useState(entries[0] ? keyOf(entries[0]) : '');
  const entry = entries.find((e) => keyOf(e) === entryKey);
  const [barsId, setBarsId] = useState<number | null>(
    entries[0]?.underlying_datasets[0]?.dataset_id ?? null,
  );
  const [strategyId, setStrategyId] = useState(strategies[0]?.id ?? '');
  const strategy = strategies.find((s) => s.id === strategyId);
  const [params, setParams] = useState<Record<string, number>>(defaults(strategy));
  const [period, setPeriod] = useState<Period>('in-sample');
  const [slip, setSlip] = useState(0.01);
  const [spreadPct, setSpreadPct] = useState(0);
  const [commission, setCommission] = useState(0.65);
  const [fee, setFee] = useState(0);
  const [stockBps, setStockBps] = useState(5);
  const [early, setEarly] = useState(true);
  const [earlyLimit, setEarlyLimit] = useState(0.05);
  const [capital, setCapital] = useState(100_000);
  const [run, setRun] = useState<RunState>({ kind: 'idle' });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!entry || !strategy || barsId === null) return;
    setRun({ kind: 'running' });
    runOptionsBacktest({
      options_dataset_id: entry.dataset_id,
      underlying_dataset_id: barsId,
      symbol: entry.underlying,
      strategy: strategy.id,
      period,
      params,
      slippage_per_share: slip,
      slippage_spread_fraction: spreadPct / 100,
      commission_per_contract: commission,
      assignment_fee_per_contract: fee,
      stock_slippage_bps: stockBps,
      early_assignment: early,
      early_assignment_extrinsic: earlyLimit,
      initial_capital: capital,
    })
      .then((report) => {
        setRun({ kind: 'done', report });
        onDone();
      })
      .catch((error: unknown) => {
        setRun({ kind: 'error', message: error instanceof Error ? error.message : 'unknown' });
      });
  };

  return (
    <div className="space-y-6">
      <Card title="Set up an options backtest">
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-slate-400">Option quotes (dataset · underlying)</span>
            <select
              className={inputClass}
              value={entryKey}
              onChange={(e) => {
                setEntryKey(e.target.value);
                const next = entries.find((x) => keyOf(x) === e.target.value);
                setBarsId(next?.underlying_datasets[0]?.dataset_id ?? null);
              }}
            >
              {entries.map((e) => (
                <option key={keyOf(e)} value={keyOf(e)}>
                  #{e.dataset_id} {e.underlying} · {e.first_date} → {e.last_date} · {e.source}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-400">Underlying daily bars</span>
            <select
              className={inputClass}
              value={barsId ?? ''}
              onChange={(e) => {
                setBarsId(Number(e.target.value));
              }}
            >
              {(entry?.underlying_datasets ?? []).map((u) => (
                <option key={u.dataset_id} value={u.dataset_id}>
                  #{u.dataset_id} · {u.first_date} → {u.last_date} · {u.sessions} sessions
                </option>
              ))}
            </select>
          </label>
          {entry && <BeforeYouRun entry={entry} />}
          <label className="block text-sm md:col-span-2">
            <span className="mb-1 block text-slate-400">Strategy</span>
            <select
              className={inputClass}
              value={strategyId}
              onChange={(e) => {
                setStrategyId(e.target.value);
                setParams(defaults(strategies.find((s) => s.id === e.target.value)));
              }}
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {strategy && (
              <span className="mt-1 block text-xs text-slate-500">{strategy.description}</span>
            )}
          </label>
          {strategy?.params.map((p) => (
            <NumberField
              key={p.name}
              label={p.label}
              value={params[p.name] ?? p.default}
              min={p.minimum}
              max={p.maximum}
              hint={p.description}
              onChange={(v) => {
                setParams((old) => ({ ...old, [p.name]: v }));
              }}
            />
          ))}
          <fieldset className="text-sm md:col-span-2">
            <legend className="mb-1 text-slate-400">Period</legend>
            <div className="flex flex-wrap gap-4">
              {(['in-sample', 'out-of-sample'] as const).map((p) => (
                <label key={p} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="options-period"
                    checked={period === p}
                    onChange={() => {
                      setPeriod(p);
                    }}
                  />
                  {p}
                </label>
              ))}
            </div>
          </fieldset>
          <NumberField label="Slippage ($ per share)" value={slip} min={0} step="any" onChange={setSlip} />
          <NumberField
            label="Extra slippage (% of bid-ask spread)"
            value={spreadPct}
            min={0}
            max={100}
            step="any"
            onChange={setSpreadPct}
          />
          <NumberField
            label="Commission ($ per contract)"
            value={commission}
            min={0}
            step="any"
            onChange={setCommission}
          />
          <NumberField
            label="Assignment/exercise fee ($ per contract)"
            value={fee}
            min={0}
            step="any"
            onChange={setFee}
          />
          <NumberField
            label="Share liquidation slippage (bps)"
            value={stockBps}
            min={0}
            step="any"
            onChange={setStockBps}
          />
          <div className="text-sm">
            <label className="flex items-center gap-2 text-slate-300">
              <input
                type="checkbox"
                checked={early}
                onChange={(e) => {
                  setEarly(e.target.checked);
                }}
              />
              Model early assignment of short American legs
            </label>
            {early && (
              <div className="mt-2">
                <NumberField
                  label="Assigned when time value ≤ ($/share)"
                  value={earlyLimit}
                  min={0}
                  step="any"
                  onChange={setEarlyLimit}
                />
              </div>
            )}
          </div>
          <NumberField
            label="Starting capital ($)"
            value={capital}
            min={1}
            step="any"
            onChange={setCapital}
          />
          <div className="md:col-span-2">
            <button
              type="submit"
              disabled={run.kind === 'running' || !entry || !strategy || barsId === null}
              className="btn-pixel bg-sky-600 px-5 py-3 text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {run.kind === 'running' ? 'Running…' : 'Run options backtest'}
            </button>
          </div>
        </form>
      </Card>
      {run.kind === 'error' && (
        <Card title="Backtest refused">
          <NoData message={run.message} />
        </Card>
      )}
      {run.kind === 'done' && <OptionsResults report={run.report} />}
    </div>
  );
}

export function OptionsLab({ strategies, onDone }: { strategies: Strategy[]; onDone: () => void }) {
  const [universe, reload] = useApi(fetchOptionsUniverse);
  if (universe.kind === 'loading') return <p className="text-slate-400">Loading option data…</p>;
  if (universe.kind === 'error') {
    return (
      <Card title="Options">
        <NoData message={`Backend unreachable or invalid: ${universe.message}`} />
      </Card>
    );
  }
  if (universe.data.entries.length === 0) {
    return (
      <Card title="No data">
        <NoData
          message={
            'No option quotes imported yet. Import end-of-day option quotes with an ' +
            'exercise_style column, plus daily bars for the underlying (see docs/DATA.md): ' +
            'npm run ptl -- import-csv spy_options.csv --kind option-quotes --source <vendor>'
          }
        />
      </Card>
    );
  }
  return (
    <div className="space-y-2">
      <Provenance source={universe.data.source} asOf={universe.data.as_of} asOfLabel="loaded" />
      <OptionsForm
        entries={universe.data.entries}
        strategies={strategies}
        onDone={() => {
          reload();
          onDone();
        }}
      />
    </div>
  );
}
