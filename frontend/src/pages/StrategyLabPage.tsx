import { useId, useState, type SyntheticEvent } from 'react';

import { fetchStrategies, fetchUniverse, runBacktest } from '../api/client';
import type { Period, Report, Strategy, UniverseEntry } from '../api/schemas';
import { useApi } from '../api/useApi';
import { Card, NoData } from '../components/Card';
import { EquityChart } from '../components/EquityChart';
import { MetricsTable } from '../components/MetricsTable';
import { Provenance } from '../components/Provenance';
import { RealityCheckPanel } from '../components/RealityCheckPanel';
import { pct, usd } from '../format';

type RunState =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'error'; message: string }
  | { kind: 'done'; report: Report };

const keyOf = (e: UniverseEntry) => `${String(e.dataset_id)}:${e.symbol}`;
const defaults = (s: Strategy | undefined) =>
  Object.fromEntries((s?.params ?? []).map((p) => [p.name, p.default]));

const inputClass =
  'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100';

function NumberField(props: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number | 'any';
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="text-sm">
      <label htmlFor={id} className="mb-1 block text-slate-400">
        {props.label}
      </label>
      <input
        id={id}
        type="number"
        className={inputClass}
        value={props.value}
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        aria-describedby={props.hint ? `${id}-hint` : undefined}
        onChange={(e) => {
          props.onChange(Number(e.target.value));
        }}
      />
      {props.hint && (
        <span id={`${id}-hint`} className="mt-1 block text-xs text-slate-500">
          {props.hint}
        </span>
      )}
    </div>
  );
}

function LabForm({
  universe,
  strategies,
  onDone,
}: {
  universe: UniverseEntry[];
  strategies: Strategy[];
  onDone: () => void;
}) {
  const [entryKey, setEntryKey] = useState(universe[0] ? keyOf(universe[0]) : '');
  const [strategyId, setStrategyId] = useState(
    strategies.find((s) => s.id === 'sma_crossover')?.id ?? strategies[0]?.id ?? '',
  );
  const strategy = strategies.find((s) => s.id === strategyId);
  const [params, setParams] = useState<Record<string, number>>(defaults(strategy));
  const [period, setPeriod] = useState<Period>('in-sample');
  const [slippage, setSlippage] = useState(5);
  const [perOrder, setPerOrder] = useState(0);
  const [commissionBps, setCommissionBps] = useState(0);
  const [capital, setCapital] = useState(100_000);
  const [run, setRun] = useState<RunState>({ kind: 'idle' });

  const entry = universe.find((e) => keyOf(e) === entryKey);

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!entry || !strategy) return;
    setRun({ kind: 'running' });
    runBacktest({
      dataset_id: entry.dataset_id,
      symbol: entry.symbol,
      strategy: strategy.id,
      period,
      params,
      slippage_bps: slippage,
      commission_per_order: perOrder,
      commission_bps: commissionBps,
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
      <Card title="Set up a backtest">
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block text-slate-400">Data (dataset · symbol)</span>
            <select
              className={inputClass}
              value={entryKey}
              onChange={(e) => {
                setEntryKey(e.target.value);
              }}
            >
              {universe.map((e) => (
                <option key={keyOf(e)} value={keyOf(e)}>
                  #{e.dataset_id} {e.symbol} · {e.first_date} → {e.last_date} · {e.source}
                </option>
              ))}
            </select>
            {entry && (
              <span className="mt-1 block text-xs text-slate-500">
                {entry.sessions.toLocaleString()} sessions ·{' '}
                {entry.has_adj_close ? 'adjusted prices' : 'raw prices (no dividends)'} ·{' '}
                {entry.lock
                  ? `out-of-sample locked from ${entry.lock.oos_start}`
                  : 'first run will permanently lock the most recent 30% as out-of-sample'}
                {' · '}
                {entry.in_sample_runs} in-sample run(s), {entry.oos_evaluations} OOS evaluation(s)
              </span>
            )}
          </label>

          <label className="block text-sm">
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
                    name="period"
                    checked={period === p}
                    onChange={() => {
                      setPeriod(p);
                    }}
                  />
                  {p}
                </label>
              ))}
            </div>
            {period === 'out-of-sample' && (
              <p className="mt-1 text-xs text-amber-300">
                Every out-of-sample run is counted and shown. Look at it once, after tuning.
              </p>
            )}
          </fieldset>

          <NumberField
            label="Slippage (bps per side)"
            value={slippage}
            min={0}
            max={500}
            step={0.5}
            onChange={setSlippage}
          />
          <NumberField
            label="Commission per order ($)"
            value={perOrder}
            min={0}
            step={0.5}
            onChange={setPerOrder}
          />
          <NumberField
            label="Commission (bps of notional)"
            value={commissionBps}
            min={0}
            step={0.5}
            onChange={setCommissionBps}
          />
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
              disabled={run.kind === 'running' || !entry || !strategy}
              className="rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            >
              {run.kind === 'running' ? 'Running…' : 'Run backtest'}
            </button>
          </div>
        </form>
      </Card>

      {run.kind === 'error' && (
        <Card title="Backtest refused">
          <NoData message={run.message} />
        </Card>
      )}
      {run.kind === 'done' && <Results report={run.report} />}
    </div>
  );
}

function Results({ report }: { report: Report }) {
  const s = report.strategy_metrics;
  const params = Object.entries(report.strategy.params)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(', ');
  return (
    <div className="space-y-6">
      <Card title={`Result #${String(report.run_id)}: ${report.strategy.name} on ${report.symbol}`}>
        <p className="mb-1 text-sm text-slate-300">
          {params || 'no parameters'} · {report.reality_check.period} · {s.trade_count} trades ·
          final equity {usd(s.final_equity)} ({pct(s.total_return)}) vs buy &amp; hold{' '}
          {usd(report.benchmark_metrics.final_equity)} (
          {pct(report.benchmark_metrics.total_return)})
        </p>
        <div className="mb-4 space-y-1">
          <Provenance
            source={report.provenance.source}
            asOf={report.provenance.as_of}
            dataType={report.provenance.data_type}
            asOfLabel="computed"
          />
          <Provenance
            source={report.benchmark_provenance.source}
            asOf={report.benchmark_provenance.as_of}
            dataType={report.benchmark_provenance.data_type}
            asOfLabel="computed"
          />
        </div>
        <EquityChart
          curve={report.curve}
          labels={{ strategy: report.symbol + ' strategy', benchmark: report.benchmark_symbol + ' buy & hold' }}
        />
      </Card>
      <RealityCheckPanel report={report} />
      <Card title="Metrics">
        <MetricsTable report={report} />
      </Card>
      <Card title={`Trades (${String(report.trades.length)})`}>
        {report.trades.length === 0 ? (
          <p className="text-sm text-slate-400">No trades in this window.</p>
        ) : (
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[32rem] text-sm">
              <thead className="sticky top-0 bg-slate-900 text-left text-xs text-slate-400">
                <tr>
                  <th className="py-1 pr-3 font-normal">Entry</th>
                  <th className="py-1 pr-3 font-normal">Exit</th>
                  <th className="py-1 pr-3 font-normal">Sessions</th>
                  <th className="py-1 pr-3 font-normal">P&amp;L after costs</th>
                  <th className="py-1 pr-3 font-normal">Return</th>
                  <th className="py-1 font-normal">Exit reason</th>
                </tr>
              </thead>
              <tbody className="font-mono text-slate-200">
                {report.trades.map((t) => (
                  <tr key={`${t.entry_date}-${t.exit_date}`} className="border-t border-slate-800/60">
                    <td className="py-1 pr-3">{t.entry_date}</td>
                    <td className="py-1 pr-3">{t.exit_date}</td>
                    <td className="py-1 pr-3">{t.sessions_held}</td>
                    <td className="py-1 pr-3">{usd(t.pnl)}</td>
                    <td className="py-1 pr-3">{pct(t.return_pct)}</td>
                    <td className="py-1 font-sans text-slate-400">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export function StrategyLabPage() {
  const [universe, reloadUniverse] = useApi(fetchUniverse);
  const [strategies] = useApi(fetchStrategies);

  if (universe.kind === 'loading' || strategies.kind === 'loading') {
    return <p className="text-slate-400">Loading…</p>;
  }
  if (universe.kind === 'error' || strategies.kind === 'error') {
    const message = universe.kind === 'error' ? universe.message : 'strategies unavailable';
    return (
      <Card title="Strategy Lab">
        <NoData message={`Backend unreachable or invalid: ${message}`} />
      </Card>
    );
  }
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Strategy Lab</h2>
        <Provenance source={universe.data.source} asOf={universe.data.as_of} asOfLabel="loaded" />
      </div>
      {universe.data.entries.length === 0 ? (
        <Card title="No data">
          <NoData
            message={
              'No equity datasets imported yet. Import daily bars first (see docs/DATA.md): ' +
              'npm run ptl -- import-csv spy.csv --kind equity-bars --source <vendor> ' +
              '--map adj_close="Adj Close"'
            }
          />
        </Card>
      ) : (
        <LabForm
          universe={universe.data.entries}
          strategies={strategies.data}
          onDone={reloadUniverse}
        />
      )}
    </div>
  );
}
