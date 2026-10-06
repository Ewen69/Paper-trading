/**
 * Operations Center: live telemetry for the optimizer daemon, the paper runner, the risk guard
 * and the Auditor, plus net worth, allocation and the Active Best's equity curves.
 *
 * Everything comes from /ws/telemetry (validated with zod). A daemon that isn't running shows
 * "not running"; a panel without data says "no data". Nothing here places an order; the paper
 * runner daemon does that, through the risk engine, on the PAPER account or in dry run.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

import type { ActiveBestT, Daemon, TelemetryState, TelemetryTrial } from '../api/schemas';
import { useTelemetry, type FeedItem, type TelemetryView } from '../api/useTelemetry';
import { Badge } from '../components/Badge';
import { EquityChart } from '../components/EquityChart';
import { num, pct, usd } from '../format';
import { HistoryChart } from '../networth/charts';
import { DataCheckControl, EquitySweepControl, OptionsSweepControl, PaperCycleControl, RiskControl } from './controls';

type Section<K extends keyof TelemetryState> = Exclude<TelemetryState[K], { error: string }>;

type Sectioned = Exclude<keyof TelemetryState, 'as_of' | 'source' | 'daemons'>;

function ok<K extends Sectioned>(state: TelemetryState | null, key: K): Section<K> | null {
  if (!state) return null;
  const value = state[key];
  return 'error' in value ? null : (value as Section<K>);
}

function errorOf(state: TelemetryState | null, key: Sectioned): string | null {
  if (!state) return null;
  const value = state[key];
  return 'error' in value ? value.error : null;
}

const time = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString() : '—');
const params = (p: Record<string, number>) =>
  Object.entries(p)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(' ') || 'defaults';

function Hud({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="panel p-3">
      <header className="mb-2 flex items-center justify-between gap-2 border-b border-slate-800 pb-1.5">
        <h2 className="font-pixel text-[9px] uppercase tracking-wider text-sky-300">{title}</h2>
        {right}
      </header>
      {children}
    </section>
  );
}

function Light({ tone, label }: { tone: 'ok' | 'warn' | 'bad' | 'off'; label: string }) {
  const color = { ok: 'bg-emerald-400', warn: 'bg-amber-400', bad: 'bg-rose-500', off: 'bg-slate-600' }[tone];
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${color} ${tone === 'ok' ? 'animate-pulse' : ''}`} aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

function daemonTone(d: Daemon | undefined): 'ok' | 'warn' | 'bad' | 'off' {
  if (!d?.alive) return 'off';
  if (d.status === 'error') return 'bad';
  if (d.status === 'idle') return 'warn';
  return 'ok';
}

function StatusStrip({ view }: { view: TelemetryView }) {
  const opt = view.daemons.find((d) => d.daemon === 'optimizer');
  const run = view.daemons.find((d) => d.daemon === 'paper-runner');
  const risk = ok(view.state, 'risk');
  const auditor = ok(view.state, 'auditor');
  const mode = run?.detail.mode ?? null;
  const cells: { label: string; value: ReactNode; sub: string }[] = [
    {
      label: 'Telemetry stream',
      value: <Light tone={view.connection === 'live' ? 'ok' : view.connection === 'connecting' ? 'warn' : 'bad'} label={view.connection} />,
      sub: `last event ${view.lastEventAt ? view.lastEventAt.toLocaleTimeString() : '—'}`,
    },
    {
      label: 'Optimizer daemon',
      value: <Light tone={daemonTone(opt)} label={opt?.status ?? 'not running'} />,
      sub: opt?.alive ? `pid ${String(opt.pid ?? '')} · beat ${time(opt.beat_at)}` : 'start with npm run start:all',
    },
    {
      label: 'Paper runner',
      value: <Light tone={daemonTone(run)} label={run?.alive ? `${run.status}${mode ? ` · ${mode === 'dry_run' ? 'DRY RUN' : 'PAPER'}` : ''}` : (run?.status ?? 'not running')} />,
      sub: run?.alive ? `beat ${time(run.beat_at)}` : 'never live; dry run by default',
    },
    {
      label: 'Risk guard',
      value: risk ? (
        <Light tone={risk.kill_switch.engaged ? 'bad' : 'ok'} label={risk.kill_switch.engaged ? 'KILL SWITCH ON' : 'armed'} />
      ) : (
        <Light tone="off" label="no data" />
      ),
      sub: risk?.kill_switch.engaged ? risk.kill_switch.reason : 'limits enforced in code',
    },
    {
      label: 'Auditor',
      value: auditor ? (
        <Light
          tone={auditor.flags.some((f) => f.severity === 'error') ? 'bad' : auditor.flags.length ? 'warn' : 'ok'}
          label={`${String(auditor.flags.length)} flag(s)`}
        />
      ) : (
        <Light tone="off" label="no data" />
      ),
      sub: auditor ? `data health: ${auditor.data_health}` : (errorOf(view.state, 'auditor') ?? ''),
    },
    {
      label: 'Graduation gate',
      value: auditor ? `${String(auditor.graduation.met)}/${String(auditor.graduation.total)} met` : '—',
      sub: 'tracking only; never enables live',
    },
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6" role="list" aria-label="System status">
      {cells.map((c) => (
        <div key={c.label} role="listitem" className="panel px-3 py-2 text-xs">
          <p className="font-pixel text-[8px] uppercase text-slate-500">{c.label}</p>
          <div className="mt-1 text-sm text-slate-100">{c.value}</div>
          <p className="mt-0.5 truncate text-[10px] text-slate-500" title={c.sub}>
            {c.sub}
          </p>
        </div>
      ))}
    </div>
  );
}

function trialText(t: TelemetryTrial): string {
  const where = t.period === 'out-of-sample' ? 'OOS' : `IS g${String(t.generation)}`;
  const stats =
    t.sharpe === null
      ? 'no Sharpe'
      : `Sharpe ${t.sharpe.toFixed(2)} · win ${pct(t.win_rate, 0)} · ${String(t.trades ?? 0)} tr · dd ${pct(t.max_drawdown, 0)}`;
  return `${where} ${t.strategy} ${t.symbol} ${params(t.params)} → ${stats} · ${t.verdict}${t.reused ? ' (reused)' : ''}`;
}

function Terminal({ feed }: { feed: FeedItem[] }) {
  const [show, setShow] = useState({ optimizer: true, 'paper-runner': true, trials: true });
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const items = feed.filter((i) =>
    i.kind === 'trial' ? show.trials : i.line.daemon === 'paper-runner' ? show['paper-runner'] : show.optimizer,
  );
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items.length]);
  return (
    <Hud
      title="Agent terminal"
      right={
        <div className="flex gap-3 text-[11px] text-slate-400">
          {(['optimizer', 'paper-runner', 'trials'] as const).map((k) => (
            <label key={k} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={show[k]}
                onChange={(e) => {
                  setShow({ ...show, [k]: e.target.checked });
                }}
              />
              {k}
            </label>
          ))}
        </div>
      }
    >
      <div
        ref={box}
        role="log"
        aria-label="Agent terminal"
        aria-live="off"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        className="h-80 overflow-y-auto bg-black/60 p-2 font-mono text-[11px] leading-relaxed"
      >
        {items.length === 0 && <p className="text-slate-500">No agent activity yet. Start the daemons with npm run start:all.</p>}
        {items.map((i) =>
          i.kind === 'log' ? (
            <p key={i.key} className={i.line.level === 'error' ? 'text-rose-300' : i.line.level === 'warn' ? 'text-amber-300' : 'text-slate-300'}>
              <span className="text-slate-600">{time(i.at)}</span>{' '}
              <span className={i.line.daemon === 'optimizer' ? 'text-sky-400' : 'text-violet-300'}>[{i.line.daemon}]</span>{' '}
              {i.line.message}
            </p>
          ) : (
            <p key={i.key} className={i.trial.period === 'out-of-sample' ? 'text-amber-200' : 'text-emerald-200/80'}>
              <span className="text-slate-600">{time(i.at)}</span> <span className="text-emerald-500">[trial]</span>{' '}
              {trialText(i.trial)}
            </p>
          ),
        )}
      </div>
    </Hud>
  );
}

function BestCard({ best }: { best: ActiveBestT }) {
  return (
    <div className="space-y-1 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-100">
          {best.strategy} · {best.symbol}
        </span>
        <Badge tone={best.validated ? 'good' : 'warn'}>{best.validated ? 'validated' : 'not validated'}</Badge>
      </div>
      <p className="font-mono text-sky-300">{params(best.params)}</p>
      <dl className="grid grid-cols-2 gap-x-3 text-slate-300">
        <dt className="text-slate-500">In-sample Sharpe</dt>
        <dd>{num(best.in_sample?.sharpe)}</dd>
        <dt className="text-slate-500">In-sample win rate</dt>
        <dd>{pct(best.in_sample?.win_rate)}</dd>
        <dt className="text-slate-500">Out-of-sample Sharpe</dt>
        <dd>{num(best.out_of_sample?.sharpe)}</dd>
        <dt className="text-slate-500">Out-of-sample trades</dt>
        <dd>{best.out_of_sample?.trades ?? '—'}</dd>
      </dl>
      <p className="text-slate-500">{best.reason}</p>
    </div>
  );
}

function Learning({ view }: { view: TelemetryView }) {
  const opt = ok(view.state, 'optimizer');
  const daemon = view.daemons.find((d) => d.daemon === 'optimizer');
  const detail = daemon?.detail;
  const trial = view.latestTrial;
  return (
    <Hud title="Learning agent">
      <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-slate-300">
        <dt className="text-slate-500">State</dt>
        <dd>{detail?.state ?? (daemon?.alive ? '—' : 'not running')}</dd>
        <dt className="text-slate-500">Target</dt>
        <dd className="truncate">{detail?.target ?? '—'}</dd>
        <dt className="text-slate-500">Generation</dt>
        <dd>{detail?.generation ?? '—'}</dd>
        <dt className="text-slate-500">Budget used</dt>
        <dd>
          {detail?.fresh_trials !== undefined && detail.budget !== undefined
            ? `${String(detail.fresh_trials)}/${String(detail.budget)}`
            : '—'}
        </dd>
      </dl>
      {trial && (
        <p className="mt-2 border-l-2 border-emerald-500/60 pl-2 font-mono text-[11px] text-emerald-200/90">
          {trialText(trial)}
        </p>
      )}
      {opt ? (
        <>
          <dl className="mt-3 grid grid-cols-4 gap-2 text-center text-xs">
            {(
              [
                ['In-sample', opt.counts.in_sample],
                ['Holdout looks', opt.counts.out_of_sample],
                ['Reused', opt.counts.reused],
                ['Searches', opt.counts.searches],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="border border-slate-800 p-1.5">
                <dt className="text-[9px] uppercase text-slate-500">{label}</dt>
                <dd className="text-base text-slate-100">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-1 text-[10px] text-slate-500">
            Every trial is a logged backtest and counts toward the Auditor&apos;s over-tuning checks. Holdout results
            only label the Active Best; they never steer the search.
          </p>
          <h3 className="mt-3 font-pixel text-[8px] uppercase text-slate-400">Active Best (equity)</h3>
          {opt.active_best.equity ? (
            <BestCard best={opt.active_best.equity} />
          ) : (
            <p className="text-xs text-slate-500">No data: no search has produced a finalist yet.</p>
          )}
        </>
      ) : (
        <p className="mt-2 text-xs text-slate-500">{errorOf(view.state, 'optimizer') ?? 'No data yet.'}</p>
      )}
    </Hud>
  );
}

function StrategyCurve({ view }: { view: TelemetryView }) {
  const best = ok(view.state, 'optimizer')?.active_best.equity ?? null;
  const [period, setPeriod] = useState<'in-sample' | 'out-of-sample'>('in-sample');
  const curve = best?.curve[period] ?? [];
  return (
    <Hud
      title="Active Best equity curve"
      right={
        <div className="flex gap-1 text-[11px]">
          {(['in-sample', 'out-of-sample'] as const).map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={period === p}
              className={`border px-2 py-0.5 ${period === p ? 'border-sky-400 text-sky-300' : 'border-slate-700 text-slate-400'}`}
              onClick={() => {
                setPeriod(p);
              }}
            >
              {p}
            </button>
          ))}
        </div>
      }
    >
      {best && curve.length > 0 ? (
        <EquityChart curve={curve} labels={{ strategy: `${best.strategy} ${best.symbol}`, benchmark: 'Buy & hold benchmark' }} />
      ) : (
        <p className="text-sm text-slate-500">No data: {best ? `no ${period} run for the Active Best yet.` : 'no Active Best yet.'}</p>
      )}
      <p className="mt-1 text-[10px] text-slate-500">
        Backtest after costs (fills at next open with slippage and commission). In-sample results are optimistic by
        construction.
      </p>
    </Hud>
  );
}

function utilTone(u: number) {
  return u < 0.7 ? 'bg-sky-500' : u < 0.9 ? 'bg-amber-400' : 'bg-rose-500';
}

function Paper({ view }: { view: TelemetryView }) {
  const run = view.daemons.find((d) => d.daemon === 'paper-runner');
  const account = run?.alive ? run.detail.account : undefined;
  const paper = ok(view.state, 'paper');
  return (
    <Hud title="Paper runner">
      {!account ? (
        <p className="text-xs text-slate-500">No data: the paper runner daemon isn&apos;t running.</p>
      ) : account.error ? (
        <p className="text-xs text-amber-300">{account.error}</p>
      ) : (
        <>
          <dl className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <dt className="text-[9px] uppercase text-slate-500">Equity</dt>
              <dd className="text-base text-slate-100">{usd(account.equity)}</dd>
            </div>
            <div>
              <dt className="text-[9px] uppercase text-slate-500">Cash</dt>
              <dd className="text-base text-slate-100">{usd(account.cash)}</dd>
            </div>
            <div>
              <dt className="text-[9px] uppercase text-slate-500">Today</dt>
              <dd className="text-base text-slate-100">{usd(account.day_pnl)}</dd>
            </div>
          </dl>
          <p className="mt-1 text-[10px] text-slate-500">{account.source}</p>
          <ul className="mt-2 space-y-1" aria-label="Risk limit utilization">
            {(account.risk ?? []).map((r) => (
              <li key={r.name} className="text-[11px] text-slate-300">
                <div className="flex justify-between">
                  <span>{r.name}</span>
                  <span>
                    {r.name === 'Open positions' ? `${String(r.used)} / ${String(r.limit)}` : `${usd(r.used)} / ${usd(r.limit)}`} ·{' '}
                    {pct(r.utilization, 0)}
                  </span>
                </div>
                <div className="h-1.5 bg-slate-800">
                  <div className={`h-full ${utilTone(r.utilization)}`} style={{ width: `${String(Math.min(100, r.utilization * 100))}%` }} />
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-slate-400">
            Positions:{' '}
            {(account.positions ?? []).length === 0
              ? 'none'
              : (account.positions ?? []).map((p) => `${p.symbol} ${String(p.qty)} (${usd(p.market_value)})`).join(', ')}
          </p>
        </>
      )}
      {run?.detail.last_outcome && (
        <p className="mt-2 text-[11px] text-slate-300">Last cycle: {run.detail.last_outcome}</p>
      )}
      {paper && paper.orders.length > 0 && (
        <table className="mt-2 w-full text-left text-[11px] text-slate-300">
          <thead className="text-slate-500">
            <tr>
              <th>Order</th>
              <th className="text-right">Quote</th>
              <th className="text-right">Fill</th>
              <th className="text-right">Slippage</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {paper.orders.map((o) => (
              <tr key={o.id} className="border-t border-slate-800">
                <td>
                  {o.mode === 'dry_run' ? 'dry ' : ''}
                  {o.side} {o.qty} {o.symbol}
                </td>
                <td className="text-right">{num(o.quote_price)}</td>
                <td className="text-right">{num(o.fill_price)}</td>
                <td className="text-right">{o.slippage_bps === null ? '—' : `${o.slippage_bps.toFixed(1)} bps`}</td>
                <td>{o.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {paper && (
        <p className="mt-1 text-[10px] text-slate-500">
          {paper.filled_paper_trades} paper-account fill(s) logged; dry runs never count.
        </p>
      )}
    </Hud>
  );
}

function NetWorthLive({ view }: { view: TelemetryView }) {
  const nw = ok(view.state, 'net_worth');
  return (
    <Hud title="Net worth" right={nw?.totals.as_of ? <span className="text-sm text-slate-100">{usd(nw.totals.net_worth)}</span> : undefined}>
      {nw ? <HistoryChart points={nw.history} /> : <p className="text-xs text-slate-500">{errorOf(view.state, 'net_worth') ?? 'No data.'}</p>}
      {nw?.totals.as_of && <p className="text-[10px] text-slate-500">Latest balance {nw.totals.as_of} · {nw.source}</p>}
    </Hud>
  );
}

function AllocationLive({ view }: { view: TelemetryView }) {
  const pf = ok(view.state, 'portfolio');
  const max = Math.max(0, ...(pf?.allocation.map((a) => a.value) ?? [0]));
  return (
    <Hud title="Portfolio allocation" right={pf ? <span className="text-sm text-slate-100">{usd(pf.total_value)}</span> : undefined}>
      {!pf || pf.allocation.length === 0 ? (
        <p className="text-xs text-slate-500">{errorOf(view.state, 'portfolio') ?? 'No data: no priced holdings.'}</p>
      ) : (
        <ul className="space-y-1.5" aria-label="Allocation">
          {pf.allocation.map((a) => (
            <li key={a.asset_class} className="grid grid-cols-[4.5rem_1fr_7rem] items-center gap-2 text-[11px]">
              <span className="text-slate-300">{a.asset_class}</span>
              <span className="h-2.5 bg-slate-900">
                {a.value > 0 && <span className="block h-full rounded-r bg-[#3987e5]" style={{ width: `${String((a.value / max) * 100)}%` }} />}
              </span>
              <span className="text-right text-slate-200">
                {usd(a.value)} · {pct(a.weight)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {pf && (
        <p className="mt-1 text-[10px] text-slate-500">
          {pf.positions} holding(s), {pf.unpriced} unpriced, {pf.rules_fired} rule check(s) fired.
        </p>
      )}
    </Hud>
  );
}

function AuditorLive({ view }: { view: TelemetryView }) {
  const a = ok(view.state, 'auditor');
  const tone = { error: 'bad', warning: 'warn', info: 'info' } as const;
  return (
    <Hud title="Auditor">
      {!a ? (
        <p className="text-xs text-slate-500">{errorOf(view.state, 'auditor') ?? 'No data.'}</p>
      ) : (
        <>
          {a.flags.length === 0 ? (
            <p className="text-xs text-slate-400">No open flags.</p>
          ) : (
            <ul className="max-h-40 space-y-1 overflow-y-auto text-[11px]">
              {a.flags.map((f) => (
                <li key={`${f.code}-${f.title}`} className="text-slate-300">
                  <Badge tone={tone[f.severity]}>{f.severity}</Badge> {f.title}: <span className="text-slate-400">{f.summary}</span>
                </li>
              ))}
            </ul>
          )}
          <ul className="mt-2 space-y-0.5 text-[11px]" aria-label="Graduation gate">
            {a.graduation.items.map((g) => (
              <li key={g.id} className="flex justify-between gap-2 text-slate-300">
                <span>{g.title}</span>
                <span className={g.status === 'met' ? 'text-emerald-300' : 'text-slate-500'}>{g.progress}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Hud>
  );
}

export function OpsCenter({ url }: { url?: string }) {
  const view = useTelemetry(url);
  const risk = ok(view.state, 'risk');
  return (
    <div className="space-y-3">
      <StatusStrip view={view} />
      {risk?.kill_switch.engaged && (
        <div role="alert" className="border-2 border-rose-500 bg-rose-950/60 px-3 py-2 text-sm text-rose-200">
          <strong className="font-pixel text-[9px] uppercase">Kill switch engaged</strong>: no new risk will be opened.
          Reason: {risk.kill_switch.reason || 'none given'}
        </div>
      )}
      {view.connection === 'offline' && (
        <p className="panel px-3 py-2 text-xs text-rose-300">
          Telemetry offline: backend unreachable. Showing the last data received, if any. Retrying…
        </p>
      )}
      <div className="grid gap-3 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <Terminal feed={view.feed} />
        </div>
        <Learning view={view} />
      </div>
      <div className="grid gap-3 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <StrategyCurve view={view} />
        </div>
        <Paper view={view} />
      </div>
      <div className="grid gap-3 xl:grid-cols-3">
        <NetWorthLive view={view} />
        <AllocationLive view={view} />
        <AuditorLive view={view} />
      </div>
      <details className="panel p-3">
        <summary className="cursor-pointer font-pixel text-[9px] uppercase text-sky-300">Manual controls</summary>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <Hud title="Risk guard: kill switch">
            <RiskControl engaged={risk?.kill_switch.engaged ?? null} />
          </Hud>
          <Hud title="Data scout">
            <DataCheckControl />
          </Hud>
          <Hud title="Manual equity sweep (in-sample)">
            <EquitySweepControl />
          </Hud>
          <Hud title="Manual options sweep (in-sample)">
            <OptionsSweepControl />
          </Hud>
          <Hud title="One paper cycle">
            <PaperCycleControl />
          </Hud>
        </div>
      </details>
      <p className="text-[10px] text-slate-500">
        Source: {view.state?.source ?? 'no data'} · state as of {view.state ? view.state.as_of.toLocaleTimeString() : '—'}
        {view.invalidMessages > 0 && ` · ${String(view.invalidMessages)} invalid message(s) ignored`}
        {view.streamError && ` · ${view.streamError}`}
      </p>
    </div>
  );
}
