/**
 * Net Worth station: manual entries and CSV imports only, stored in local SQLite.
 * Every number comes from the backend; missing values show as "no data", never a guess.
 */
import { useCallback, useState, type ChangeEvent, type SyntheticEvent } from 'react';

import {
  addBalance,
  createAccount,
  deleteAccount,
  deleteBalance,
  fetchBalances,
  fetchNetWorth,
  fetchProjection,
  importNetWorth,
  netWorthExportUrl,
} from '../api/client';
import type { NetWorth, NetWorthAccount, Projection } from '../api/schemas';
import { useApi } from '../api/useApi';
import { Badge } from '../components/Badge';
import { Card, NoData } from '../components/Card';
import { inputClass, NumberField } from '../components/fields';
import { Provenance } from '../components/Provenance';
import { usd } from '../format';
import { HistoryChart, ProjectionChart } from './charts';

const buttonClass = 'btn-pixel bg-sky-600 px-3 py-2 text-white hover:bg-sky-500 disabled:opacity-50';
const quietButton = 'border border-slate-600 px-2 py-1 text-xs text-slate-300 hover:border-slate-400 disabled:opacity-50';

const label = (category: string) => category.replace(/_/g, ' ');

function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${String(d.getFullYear())}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const message = (error: unknown) => (error instanceof Error ? error.message : 'unknown error');

function Summary({ data }: { data: NetWorth }) {
  const { totals } = data;
  const stale = data.accounts.filter((a) => a.stale && a.latest_as_of !== null);
  return (
    <Card title="Net worth">
      {totals.as_of === null ? (
        <NoData message="No balances yet. Add an account and its balance below, or import a CSV." />
      ) : (
        <>
          <dl className="grid gap-4 sm:grid-cols-3">
            {[
              ['Net worth', totals.net_worth],
              ['Assets', totals.assets],
              ['Liabilities', totals.liabilities],
            ].map(([name, value]) => (
              <div key={name} className="border border-slate-700 bg-slate-950/50 p-3">
                <dt className="font-pixel text-[8px] uppercase text-slate-400">{name}</dt>
                <dd className={`mt-2 text-2xl font-semibold ${name === 'Net worth' ? 'text-slate-50' : 'text-slate-200'}`}>
                  {usd(value as number)}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-slate-400">
            Uses each account&apos;s latest balance: newest {totals.as_of}, oldest {totals.oldest_included}.
          </p>
          {totals.missing.length > 0 && (
            <p className="mt-1 text-xs text-amber-300">No balance yet (counted as 0): {totals.missing.join(', ')}</p>
          )}
          {stale.length > 0 && (
            <p className="mt-1 text-xs text-amber-300">
              Stale (older than {data.stale_after_days} days): {stale.map((a) => a.name).join(', ')}
            </p>
          )}
        </>
      )}
      <div className="mt-4">
        <Provenance source={data.source} asOf={data.as_of} dataType="manual" asOfLabel="computed" />
        <p className="mt-1 text-xs text-slate-500">Stays on this computer. No bank logins; manual entry and CSV only.</p>
      </div>
    </Card>
  );
}

function BalanceHistory({ account, onChange }: { account: NetWorthAccount; onChange: () => void }) {
  const load = useCallback((signal: AbortSignal) => fetchBalances(account.id, signal), [account.id]);
  const [balances, reload] = useApi(load);
  const [error, setError] = useState<string | null>(null);
  if (balances.kind === 'loading') return <p className="text-xs text-slate-400">Loading…</p>;
  if (balances.kind === 'error') return <p role="alert" className="text-xs text-rose-300">{balances.message}</p>;
  if (balances.data.length === 0) return <p className="text-xs text-slate-400">No balances entered.</p>;
  return (
    <div>
      <table className="w-full text-left text-xs text-slate-300">
        <thead className="text-slate-500">
          <tr>
            <th className="py-1 pr-3">Date</th>
            <th className="py-1 pr-3 text-right">Balance</th>
            <th className="py-1 pr-3">Source</th>
            <th className="py-1" />
          </tr>
        </thead>
        <tbody>
          {balances.data.map((b) => (
            <tr key={b.id} className="border-t border-slate-800">
              <td className="py-1 pr-3">{b.as_of}</td>
              <td className="py-1 pr-3 text-right">{usd(b.amount)}</td>
              <td className="py-1 pr-3 text-slate-400">
                {b.source} · entered {b.entered_at.toLocaleDateString()}
              </td>
              <td className="py-1 text-right">
                <button
                  type="button"
                  className={quietButton}
                  aria-label={`Delete ${account.name} balance on ${b.as_of}`}
                  onClick={() => {
                    deleteBalance(b.id)
                      .then(() => {
                        reload();
                        onChange();
                      })
                      .catch((e: unknown) => {
                        setError(message(e));
                      });
                  }}
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {error && <p role="alert" className="mt-1 text-xs text-rose-300">{error}</p>}
    </div>
  );
}

function AccountRow({ account, onChange }: { account: NetWorthAccount; onChange: () => void }) {
  const [date, setDate] = useState(todayIso);
  const [amount, setAmount] = useState('');
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);

  const save = (event: SyntheticEvent) => {
    event.preventDefault();
    const value = Number(amount);
    if (amount.trim() === '' || !Number.isFinite(value) || value < 0) {
      setStatus({ ok: false, text: 'Enter a positive amount; the account type sets the sign.' });
      return;
    }
    addBalance({ account_id: account.id, as_of: date, amount: value })
      .then((b) => {
        setStatus({ ok: true, text: `${b.replaced ? 'Replaced' : 'Saved'} ${usd(b.amount)} for ${b.as_of}.` });
        setAmount('');
        onChange();
      })
      .catch((e: unknown) => {
        setStatus({ ok: false, text: message(e) });
      });
  };

  return (
    <li className="border border-slate-700 bg-slate-950/50 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-100">{account.name}</h4>
          <p className="text-[11px] text-slate-500">
            {account.kind} · {label(account.category)}
          </p>
        </div>
        <div className="text-right">
          <p className="text-sm text-slate-100">{account.latest_amount === null ? 'No data' : usd(account.latest_amount)}</p>
          <p className="text-[11px] text-slate-500">
            {account.latest_as_of === null
              ? 'never entered'
              : `${account.latest_as_of} · ${String(account.age_days)} day(s) old · ${account.latest_source ?? ''}`}
            {account.stale && account.latest_as_of !== null && (
              <span className="ml-1">
                <Badge tone="warn">stale</Badge>
              </span>
            )}
          </p>
        </div>
      </div>
      <form onSubmit={save} className="mt-3 flex flex-wrap items-end gap-2 text-xs" aria-label={`New balance for ${account.name}`}>
        <label className="text-slate-400">
          Date
          <input type="date" className={`${inputClass} mt-1`} value={date} max={todayIso()} onChange={(e) => { setDate(e.target.value); }} />
        </label>
        <label className="text-slate-400">
          Balance ($)
          <input
            inputMode="decimal"
            className={`${inputClass} mt-1`}
            value={amount}
            placeholder="0.00"
            onChange={(e) => {
              setAmount(e.target.value);
            }}
          />
        </label>
        <button type="submit" className={buttonClass}>
          Save balance
        </button>
        <button
          type="button"
          className={quietButton}
          aria-expanded={open}
          onClick={() => {
            setOpen((o) => !o);
          }}
        >
          {open ? 'Hide history' : 'History'}
        </button>
        <button
          type="button"
          className={`${quietButton} ${confirming ? 'border-rose-500 text-rose-300' : ''}`}
          onClick={() => {
            if (!confirming) {
              setConfirming(true);
              return;
            }
            deleteAccount(account.id)
              .then(onChange)
              .catch((e: unknown) => {
                setStatus({ ok: false, text: message(e) });
              });
          }}
          onBlur={() => {
            setConfirming(false);
          }}
        >
          {confirming ? 'Confirm: delete account and all its balances' : 'Delete account'}
        </button>
      </form>
      {status && (
        <p role={status.ok ? 'status' : 'alert'} className={`mt-1 text-xs ${status.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
          {status.text}
        </p>
      )}
      {open && (
        <div className="mt-3">
          <BalanceHistory account={account} onChange={onChange} />
        </div>
      )}
    </li>
  );
}

function NewAccount({ categories, onChange }: { categories: NetWorth['categories']; onChange: () => void }) {
  const [name, setName] = useState('');
  const [kind, setKind] = useState<'asset' | 'liability'>('asset');
  const [category, setCategory] = useState(categories.asset[0] ?? 'other');
  const [note, setNote] = useState('');
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    createAccount({ name, kind, category, note })
      .then((a) => {
        setStatus({ ok: true, text: `Added ${a.name}. Enter its balance below.` });
        setName('');
        setNote('');
        onChange();
      })
      .catch((e: unknown) => {
        setStatus({ ok: false, text: message(e) });
      });
  };
  return (
    <form onSubmit={submit} className="grid gap-2 text-xs sm:grid-cols-5 sm:items-end" aria-label="New account">
      <label className="text-slate-400 sm:col-span-2">
        Account name
        <input className={`${inputClass} mt-1`} value={name} required maxLength={80} onChange={(e) => { setName(e.target.value); }} />
      </label>
      <label className="text-slate-400">
        Type
        <select
          className={`${inputClass} mt-1`}
          value={kind}
          onChange={(e) => {
            const next = e.target.value === 'liability' ? 'liability' : 'asset';
            setKind(next);
            setCategory(categories[next][0] ?? 'other');
          }}
        >
          <option value="asset">Asset</option>
          <option value="liability">Liability</option>
        </select>
      </label>
      <label className="text-slate-400">
        Category
        <select className={`${inputClass} mt-1`} value={category} onChange={(e) => { setCategory(e.target.value); }}>
          {categories[kind].map((c) => (
            <option key={c} value={c}>
              {label(c)}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" className={buttonClass}>
        Add account
      </button>
      <label className="text-slate-400 sm:col-span-5">
        Note (optional)
        <input className={`${inputClass} mt-1`} value={note} maxLength={200} onChange={(e) => { setNote(e.target.value); }} />
      </label>
      {status && (
        <p role={status.ok ? 'status' : 'alert'} className={`text-xs sm:col-span-5 ${status.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
          {status.text}
        </p>
      )}
    </form>
  );
}

function ImportExport({ onChange }: { onChange: () => void }) {
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setBusy(true);
    file
      .text()
      .then((text) => importNetWorth(file.name, text))
      .then((r) => {
        setStatus({
          ok: true,
          text: `Imported ${String(r.rows)} balance(s): ${String(r.inserted)} new, ${String(r.updated)} replaced, ${String(r.accounts_created)} account(s) created.`,
        });
        onChange();
      })
      .catch((e: unknown) => {
        setStatus({ ok: false, text: message(e) });
      })
      .finally(() => {
        setBusy(false);
      });
  };
  return (
    <Card title="Import / export">
      <p className="text-xs text-slate-400">
        CSV with the header <code className="text-slate-200">account,kind,category,as_of,amount</code>. Amounts are positive;
        the kind (asset or liability) sets the sign. A bad row rejects the whole file, so nothing half-imports. Exports use
        the same format.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
        <label className={`${buttonClass} cursor-pointer`}>
          {busy ? 'Importing…' : 'Import CSV'}
          <input type="file" accept=".csv,text/csv" className="sr-only" disabled={busy} onChange={onFile} />
        </label>
        <a className={quietButton} href={netWorthExportUrl} download>
          Export CSV
        </a>
        <span className="text-slate-500">The export holds your financial data; keep it private.</span>
      </div>
      {status && (
        <pre
          role={status.ok ? 'status' : 'alert'}
          className={`mt-3 whitespace-pre-wrap text-xs ${status.ok ? 'text-emerald-300' : 'text-rose-300'}`}
        >
          {status.text}
        </pre>
      )}
    </Card>
  );
}

function ProjectionPanel({ netWorth }: { netWorth: number | null }) {
  const [years, setYears] = useState(10);
  const [low, setLow] = useState(4);
  const [high, setHigh] = useState(6);
  const [contribution, setContribution] = useState(0);
  const [result, setResult] = useState<{ ok: true; data: Projection } | { ok: false; text: string } | null>(null);
  const run = (event: SyntheticEvent) => {
    event.preventDefault();
    fetchProjection({ years, low: low / 100, high: high / 100, contribution })
      .then((data) => {
        setResult({ ok: true, data });
      })
      .catch((e: unknown) => {
        setResult({ ok: false, text: message(e) });
      });
  };
  return (
    <Card title="Projection (assumptions)">
      <p className="text-xs text-slate-400">
        A what-if range, not a forecast. It compounds today&apos;s net worth at two fixed yearly rates you choose. Real
        returns vary year to year and can be negative.
      </p>
      {netWorth === null ? (
        <p className="mt-3 text-sm text-slate-400">No data: enter balances first.</p>
      ) : (
        <form onSubmit={run} className="mt-3 grid gap-3 sm:grid-cols-5 sm:items-end" aria-label="Projection inputs">
          <NumberField label="Years" value={years} min={1} max={50} onChange={setYears} />
          <NumberField label="Low rate (% a year)" value={low} min={-50} max={50} step="any" onChange={setLow} />
          <NumberField label="High rate (% a year)" value={high} min={-50} max={50} step="any" onChange={setHigh} />
          <NumberField label="Added each year ($)" value={contribution} min={0} step="any" onChange={setContribution} />
          <button type="submit" className={buttonClass}>
            Show range
          </button>
        </form>
      )}
      {result?.ok === false && (
        <p role="alert" className="mt-3 text-sm text-rose-300">
          {result.text}
        </p>
      )}
      {result?.ok === true && (
        <div className="mt-4 space-y-3">
          <ProjectionChart projection={result.data} />
          <p className="text-xs text-slate-300">
            Start: {usd(result.data.start)} (balances up to {result.data.start_as_of}). After {result.data.years} years:{' '}
            {usd(result.data.points.at(-1)?.low)} to {usd(result.data.points.at(-1)?.high)}.
          </p>
          <p className="text-xs text-amber-300/90">{result.data.note}</p>
          <Provenance source={result.data.source} asOf={result.data.as_of} asOfLabel="computed" />
        </div>
      )}
    </Card>
  );
}

export function NetWorthPage() {
  const [state, reload] = useApi(fetchNetWorth);
  if (state.kind === 'loading') return <p className="text-sm text-slate-400">Loading…</p>;
  if (state.kind === 'error') {
    return (
      <Card title="Net worth">
        <NoData message={`Backend unreachable: ${state.message}`} />
      </Card>
    );
  }
  const data = state.data;
  return (
    <div className="space-y-6">
      <Summary data={data} />
      <Card title="History">
        <HistoryChart points={data.history} />
        <p className="mt-2 text-xs text-slate-500">{data.method}</p>
      </Card>
      <Card title="Accounts">
        <NewAccount categories={data.categories} onChange={reload} />
        {data.accounts.length === 0 ? (
          <p className="mt-4 text-sm text-slate-400">No accounts yet.</p>
        ) : (
          <ul className="mt-4 space-y-3">
            {data.accounts.map((a) => (
              <AccountRow key={a.id} account={a} onChange={reload} />
            ))}
          </ul>
        )}
      </Card>
      <ImportExport onChange={reload} />
      <ProjectionPanel netWorth={data.totals.as_of === null ? null : data.totals.net_worth} />
    </div>
  );
}
