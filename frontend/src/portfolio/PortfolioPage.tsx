/**
 * Portfolio station: manual holdings (local) plus read-only Alpaca paper positions.
 * Every value names its price source and date; unpriced holdings are listed, never guessed.
 * Tips are fixed rules that show what they measured and the threshold. They are not advice.
 */
import { useCallback, useState, type ChangeEvent, type SyntheticEvent } from 'react';

import {
  addHolding,
  deleteHolding,
  fetchPortfolio,
  fetchUniverse,
  holdingsExportUrl,
  importHoldings,
  type PortfolioBook,
} from '../api/client';
import type { Portfolio, PortfolioPosition } from '../api/schemas';
import { useApi } from '../api/useApi';
import { Badge } from '../components/Badge';
import { Card, NoData } from '../components/Card';
import { inputClass } from '../components/fields';
import { Provenance } from '../components/Provenance';
import { num, pct, usd } from '../format';

const buttonClass = 'btn-pixel bg-sky-600 px-3 py-2 text-white hover:bg-sky-500 disabled:opacity-50';
const quietButton = 'border border-slate-600 px-2 py-1 text-xs text-slate-300 hover:border-slate-400';
const ASSET_CLASSES = ['stock', 'etf', 'fund', 'bond', 'cash', 'option', 'crypto', 'other'] as const;
const BOOKS: { id: PortfolioBook; label: string }[] = [
  { id: 'manual', label: 'My holdings' },
  { id: 'paper', label: 'Paper account (simulated)' },
  { id: 'combined', label: 'Combined' },
];

const message = (e: unknown) => (e instanceof Error ? e.message : 'unknown error');
const money = (x: number | null) =>
  x === null ? '—' : x.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border border-slate-700 bg-slate-950/50 p-3">
      <dt className="font-pixel text-[8px] uppercase text-slate-400">{label}</dt>
      <dd className="mt-2 text-xl font-semibold text-slate-100">{value}</dd>
      {sub && <dd className="mt-1 text-[11px] text-slate-500">{sub}</dd>}
    </div>
  );
}

function Tips({ data }: { data: Portfolio }) {
  const tone = { fired: 'warn', clear: 'good', not_evaluated: 'muted' } as const;
  const text = { fired: 'Rule fired', clear: 'Clear', not_evaluated: 'Not evaluated' } as const;
  const fired = data.tips.filter((t) => t.status === 'fired').length;
  return (
    <Card title={`Rule checks (${String(fired)} fired)`}>
      <p className="mb-3 text-xs text-slate-400">
        Fixed rules on computed numbers. Each shows what it measured and its threshold. They flag facts to look at;
        they are not advice.
      </p>
      <ul className="space-y-2">
        {data.tips.map((t) => (
          <li key={t.id} className="border border-slate-700 bg-slate-950/50 p-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-slate-100">{t.title}</span>
              <Badge tone={tone[t.status]}>{text[t.status]}</Badge>
            </div>
            <p className="mt-1 text-slate-300">
              {t.observed} <span className="text-slate-500">({t.threshold})</span>
            </p>
            {t.detail && <p className="mt-1 text-slate-500">{t.detail}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Allocation({ data }: { data: Portfolio }) {
  const max = Math.max(...data.allocation.map((a) => a.value), 0);
  return (
    <Card title="Allocation by asset class">
      {data.allocation.length === 0 ? (
        <p className="text-sm text-slate-400">No data: nothing is priced.</p>
      ) : (
        <ul className="space-y-2" aria-label="Allocation by asset class">
          {data.allocation.map((a) => (
            <li key={a.asset_class} className="grid grid-cols-[6rem_1fr_9rem] items-center gap-3 text-xs">
              <span className="text-slate-300">{a.asset_class}</span>
              <span className="h-3 bg-slate-900" aria-hidden="true">
                {a.value > 0 && (
                  <span
                    className="block h-full rounded-r bg-[#3987e5]"
                    style={{ width: `${String(max > 0 ? (a.value / max) * 100 : 0)}%` }}
                  />
                )}
              </span>
              <span className="text-right text-slate-200">
                {usd(a.value)} · {pct(a.weight)}
                {a.value < 0 && <span className="block text-amber-300">short (a liability)</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-slate-500">Weights are of the net total {usd(data.total_value)}.</p>
    </Card>
  );
}

function Concentration({ data }: { data: Portfolio }) {
  const c = data.concentration;
  return (
    <Card title="Concentration">
      {c === null ? (
        <p className="text-sm text-slate-400">No data: no priced non-cash long positions.</p>
      ) : (
        <>
          <dl className="grid gap-3 sm:grid-cols-3">
            <Tile label="HHI" value={c.hhi.toFixed(3)} sub="sum of squared weights" />
            <Tile label="Effective N" value={c.effective_n.toFixed(1)} sub="1 / HHI" />
            <Tile label="Top 5 weight" value={pct(c.top5_weight)} />
          </dl>
          <ol className="mt-3 space-y-1 text-xs text-slate-300">
            {c.top.map(([name, weight]) => (
              <li key={name} className="flex justify-between border-b border-slate-800 py-1">
                <span>{name}</span>
                <span>{pct(weight)}</span>
              </li>
            ))}
          </ol>
          <p className="mt-2 text-[11px] text-slate-500">Over non-cash long positions, grouped by symbol.</p>
        </>
      )}
    </Card>
  );
}

function Risk({ data }: { data: Portfolio }) {
  const r = data.risk;
  return (
    <Card title={`Risk replay vs ${data.benchmark}`}>
      {r === null ? (
        <NoData message={data.risk_note || 'Not enough data.'} />
      ) : (
        <>
          <dl className="grid gap-3 sm:grid-cols-4">
            <Tile label="Volatility" value={pct(r.volatility)} sub="annualized" />
            <Tile label="Max drawdown" value={pct(r.max_drawdown)} />
            <Tile label="Beta" value={num(r.beta)} sub={`benchmark vol ${pct(r.benchmark_volatility)}`} />
            <Tile label="Correlation" value={num(r.correlation)} />
          </dl>
          <p className="mt-3 text-xs text-slate-300">
            {r.returns} daily returns, {r.start} to {r.end}. Covers {pct(data.coverage)} of long value.
          </p>
          {data.risk_note && <p className="mt-1 text-xs text-amber-300">{data.risk_note}</p>}
        </>
      )}
      <p className="mt-2 text-xs text-slate-400">Included: {data.risk_included.join(', ') || 'none'}</p>
      {data.risk_excluded.length > 0 && (
        <p className="text-xs text-slate-400">Excluded: {data.risk_excluded.join(', ')}</p>
      )}
      <p className="mt-2 text-[11px] text-slate-500">{data.price_basis}</p>
      <p className="mt-1 text-[11px] text-slate-500">{data.method}</p>
    </Card>
  );
}

function GreeksCard({ data }: { data: Portfolio }) {
  const g = data.greeks_total;
  const hasOptions = data.positions.some((p) => p.asset_class === 'option' && p.book === 'manual');
  if (!hasOptions) return null;
  return (
    <Card title="Option Greeks (totals)">
      {g === null ? (
        <p className="text-sm text-slate-400">No data: the imported quotes for your options carry no Greeks.</p>
      ) : (
        <dl className="grid gap-3 sm:grid-cols-3">
          <Tile label="Delta" value={g.delta.toFixed(1)} sub="share-equivalents" />
          <Tile label="Theta" value={money(g.theta)} sub="per day" />
          <Tile label="Vega" value={money(g.vega)} sub="per 1 IV point, as the vendor quotes it" />
        </dl>
      )}
      {data.greeks_missing.length > 0 && (
        <p className="mt-2 text-xs text-amber-300">No Greeks for: {data.greeks_missing.join(', ')}</p>
      )}
      <p className="mt-2 text-[11px] text-slate-500">
        Vendor Greeks from the newest imported quote, × 100 × contracts (negative contracts = short).
      </p>
    </Card>
  );
}

function PositionRow({ p, onChange }: { p: PortfolioPosition; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <tr className="border-t border-slate-800 align-top">
      <td className="py-1 pr-3">
        <span className="text-slate-100">{p.label}</span>
        <span className="block text-[11px] text-slate-500">
          {p.asset_class} · {p.account || 'no account label'}
          {p.book === 'paper' && ' · paper (simulated)'}
        </span>
      </td>
      <td className="py-1 pr-3 text-right">{p.quantity}</td>
      <td className="py-1 pr-3 text-right">{money(p.price)}</td>
      <td className="py-1 pr-3 text-[11px] text-slate-400">
        {p.price_source ?? 'No price source'}
        {p.price_as_of && ` · ${p.price_as_of}`}
        {p.stale && (
          <span className="ml-1" title={p.stale_reason ?? undefined}>
            <Badge tone="warn">stale</Badge>
          </span>
        )}
        {p.notes.map((n) => (
          <span key={n} className="block text-amber-300/90">
            {n}
          </span>
        ))}
      </td>
      <td className="py-1 pr-3 text-right">{p.value === null ? 'No data' : usd(p.value)}</td>
      <td className="py-1 pr-3 text-right">{pct(p.weight)}</td>
      <td className="py-1 text-right">
        {p.holding_id !== null && (
          <button
            type="button"
            className={quietButton}
            aria-label={`Delete holding ${p.label}`}
            onClick={() => {
              if (p.holding_id === null) return;
              deleteHolding(p.holding_id)
                .then(onChange)
                .catch((e: unknown) => {
                  setError(message(e));
                });
            }}
          >
            Delete
          </button>
        )}
        {error && <span role="alert" className="block text-rose-300">{error}</span>}
      </td>
    </tr>
  );
}

function Positions({ data, onChange }: { data: Portfolio; onChange: () => void }) {
  return (
    <Card title="Positions">
      {data.positions.length === 0 ? (
        <p className="text-sm text-slate-400">No positions in this view.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-3">Holding</th>
                <th className="py-1 pr-3 text-right">Qty</th>
                <th className="py-1 pr-3 text-right">Price</th>
                <th className="py-1 pr-3">Price source</th>
                <th className="py-1 pr-3 text-right">Value</th>
                <th className="py-1 pr-3 text-right">Weight</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p) => (
                <PositionRow key={p.key} p={p} onChange={onChange} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

const blankForm = {
  symbol: '',
  asset_class: 'etf',
  quantity: '',
  account: '',
  option_type: 'put',
  strike: '',
  expiration: '',
  manual_price: '',
  manual_price_as_of: '',
};

function HoldingsManager({ onChange }: { onChange: () => void }) {
  const [form, setForm] = useState(blankForm);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [replace, setReplace] = useState(false);
  const isOption = form.asset_class === 'option';
  const set = (key: keyof typeof blankForm) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setForm({ ...form, [key]: e.target.value });
  };
  const numOrNull = (s: string) => (s.trim() === '' ? null : Number(s));

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    addHolding({
      symbol: form.symbol,
      asset_class: form.asset_class,
      quantity: Number(form.quantity),
      account: form.account,
      option_type: isOption ? (form.option_type === 'call' ? 'call' : 'put') : null,
      strike: isOption ? numOrNull(form.strike) : null,
      expiration: isOption && form.expiration ? form.expiration : null,
      manual_price: numOrNull(form.manual_price),
      manual_price_as_of: form.manual_price_as_of || null,
    })
      .then((h) => {
        setStatus({ ok: true, text: `Added ${h.symbol}.` });
        setForm({ ...blankForm, asset_class: form.asset_class, account: form.account });
        onChange();
      })
      .catch((e: unknown) => {
        setStatus({ ok: false, text: message(e) });
      });
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    file
      .text()
      .then((text) => importHoldings(text, replace))
      .then((r) => {
        setStatus({
          ok: true,
          text: `Imported ${String(r.imported)} holding(s)${replace ? `, replaced ${String(r.removed)}` : ''}.`,
        });
        onChange();
      })
      .catch((e: unknown) => {
        setStatus({ ok: false, text: message(e) });
      });
  };

  const field = (label: string, key: keyof typeof blankForm, props: Record<string, string> = {}) => (
    <label className="text-slate-400">
      {label}
      <input className={`${inputClass} mt-1`} value={form[key]} onChange={set(key)} {...props} />
    </label>
  );

  return (
    <Card title="Manage holdings">
      <form onSubmit={submit} className="grid gap-2 text-xs sm:grid-cols-4 sm:items-end" aria-label="New holding">
        {field(isOption ? 'Underlying' : 'Symbol or name', 'symbol', { required: 'true' })}
        <label className="text-slate-400">
          Asset class
          <select className={`${inputClass} mt-1`} value={form.asset_class} onChange={set('asset_class')}>
            {ASSET_CLASSES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        {field(isOption ? 'Contracts (negative = short)' : form.asset_class === 'cash' ? 'Amount ($)' : 'Quantity', 'quantity', { inputMode: 'decimal', required: 'true' })}
        {field('Account label', 'account')}
        {isOption && (
          <>
            <label className="text-slate-400">
              Type
              <select className={`${inputClass} mt-1`} value={form.option_type} onChange={set('option_type')}>
                <option value="put">put</option>
                <option value="call">call</option>
              </select>
            </label>
            {field('Strike', 'strike', { inputMode: 'decimal' })}
            {field('Expiration', 'expiration', { type: 'date' })}
          </>
        )}
        {!isOption && form.asset_class !== 'cash' && (
          <>
            {field('Manual price (optional)', 'manual_price', { inputMode: 'decimal' })}
            {field('Manual price date', 'manual_price_as_of', { type: 'date' })}
          </>
        )}
        <button type="submit" className={buttonClass}>
          Add holding
        </button>
      </form>
      <p className="mt-2 text-[11px] text-slate-500">
        A manual price is used only when there are no imported bars for the symbol. Options are valued from imported
        quotes only.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3 text-xs">
        <label className={`${buttonClass} cursor-pointer`}>
          Import CSV
          <input type="file" accept=".csv,text/csv" className="sr-only" onChange={onFile} />
        </label>
        <label className="flex items-center gap-1 text-slate-300">
          <input
            type="checkbox"
            checked={replace}
            onChange={(e) => {
              setReplace(e.target.checked);
            }}
          />
          Replace all my holdings with the file
        </label>
        <a className={quietButton} href={holdingsExportUrl} download>
          Export CSV
        </a>
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Header: symbol,asset_class,quantity,account,option_type,strike,expiration,manual_price,manual_price_as_of. A bad
        row rejects the whole file.
      </p>
      {status && (
        <pre
          role={status.ok ? 'status' : 'alert'}
          className={`mt-2 whitespace-pre-wrap text-xs ${status.ok ? 'text-emerald-300' : 'text-rose-300'}`}
        >
          {status.text}
        </pre>
      )}
    </Card>
  );
}

export function PortfolioPage() {
  const [book, setBook] = useState<PortfolioBook>('manual');
  const [benchmark, setBenchmark] = useState('');
  const [sessions, setSessions] = useState(252);
  const load = useCallback(
    (signal: AbortSignal) => fetchPortfolio({ book, benchmark, window: sessions }, signal),
    [book, benchmark, sessions],
  );
  const [state, reload] = useApi(load);
  const [universe] = useApi(fetchUniverse);
  const symbols = universe.kind === 'ready' ? [...new Set(universe.data.entries.map((e) => e.symbol))] : [];

  return (
    <div className="space-y-6">
      <div className="panel flex flex-wrap items-end gap-4 p-4 text-xs">
        <fieldset className="flex flex-wrap gap-2">
          <legend className="mb-1 text-slate-400">View</legend>
          {BOOKS.map((b) => (
            <button
              key={b.id}
              type="button"
              aria-pressed={book === b.id}
              className={`border px-3 py-1.5 ${book === b.id ? 'border-sky-400 text-sky-300' : 'border-slate-600 text-slate-300'}`}
              onClick={() => {
                setBook(b.id);
              }}
            >
              {b.label}
            </button>
          ))}
        </fieldset>
        <label className="text-slate-400">
          Benchmark
          <select
            className={`${inputClass} mt-1`}
            value={benchmark}
            onChange={(e) => {
              setBenchmark(e.target.value);
            }}
          >
            <option value="">Default (from settings)</option>
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="text-slate-400">
          Window (sessions)
          <select
            className={`${inputClass} mt-1`}
            value={sessions}
            onChange={(e) => {
              setSessions(Number(e.target.value));
            }}
          >
            {[63, 126, 252, 504, 756].map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.kind === 'loading' && <p className="text-sm text-slate-400">Loading…</p>}
      {state.kind === 'error' && (
        <Card title="Portfolio">
          <NoData message={`Backend unreachable: ${state.message}`} />
        </Card>
      )}
      {state.kind === 'ready' && (
        <>
          <Card title="Portfolio">
            {state.data.paper.status !== 'not_requested' && (
              <p className={`mb-3 text-xs ${state.data.paper.status === 'included' ? 'text-slate-300' : 'text-amber-300'}`}>
                Paper account: {state.data.paper.detail}
              </p>
            )}
            <dl className="grid gap-3 sm:grid-cols-3">
              <Tile label="Net value" value={usd(state.data.total_value)} sub="priced positions only" />
              <Tile label="Long value" value={usd(state.data.long_value)} />
              <Tile
                label="Positions"
                value={String(state.data.positions.length)}
                sub={`${String(state.data.positions.filter((p) => p.value === null).length)} unpriced`}
              />
            </dl>
            <div className="mt-3">
              <Provenance source={state.data.source} asOf={state.data.as_of} asOfLabel="computed" />
            </div>
          </Card>
          <Tips data={state.data} />
          <div className="grid gap-6 lg:grid-cols-2">
            <Allocation data={state.data} />
            <Concentration data={state.data} />
          </div>
          <Risk data={state.data} />
          <GreeksCard data={state.data} />
          <Positions data={state.data} onChange={reload} />
        </>
      )}
      <HoldingsManager onChange={reload} />
    </div>
  );
}
