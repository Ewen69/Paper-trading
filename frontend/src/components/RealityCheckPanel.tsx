import type { Report } from '../api/schemas';
import { pct, range, usd } from '../format';
import { AgentSprite } from '../game/AgentSprite';
import { Provenance } from './Provenance';

interface Tile {
  label: string;
  value: string;
  sub: string;
  codes: string[]; // the tile is flagged when the backend raised one of these warnings
}

/** Read this first: what the numbers rest on and why to doubt them. Shown above every result. */
export function RealityCheckPanel({ report }: { report: Report }) {
  const rc = report.reality_check;
  const excess = report.excess_annualized_return;
  const raised = new Set(rc.warnings.map((w) => w.code));
  const tiles: Tile[] = [
    {
      label: 'Sample',
      value: `${rc.sessions.toLocaleString()} sessions`,
      sub: `${rc.window_start} → ${rc.window_end}`,
      codes: ['short_sample'],
    },
    {
      label: 'Trades',
      value: String(rc.trade_count),
      sub: 'round trips after costs',
      codes: ['low_trades'],
    },
    {
      label: `Excess vs ${report.benchmark_symbol}`,
      value: pct(excess.value),
      sub: `95% CI ${range(excess.ci95, (x) => pct(x))}`,
      codes: ['ci_includes_zero', 'no_ci'],
    },
    {
      label: 'Combinations tried',
      value: String(rc.parameter_combinations_tried),
      sub: `${String(rc.parameter_combinations_tried_all_strategies)} across all strategies`,
      codes: ['many_trials'],
    },
    {
      label: 'Out-of-sample looks',
      value: String(rc.oos_evaluations),
      sub: `holdout from ${rc.oos_start}`,
      codes: ['oos_repeat'],
    },
    {
      label: 'Costs',
      value: `${String(rc.costs.slippage_bps)} bps`,
      sub: `slippage/side · ${usd(rc.costs.commission_per_order)}/order · ${String(rc.costs.commission_bps)} bps comm.`,
      codes: [],
    },
  ];
  const details: [string, string][] = [
    ['Period', rc.period],
    ['Starting capital', usd(rc.initial_capital)],
    ['Prices', `${rc.price_basis} (benchmark: ${rc.benchmark_price_basis})`],
    ['Cash yield / Sharpe risk-free', `${rc.cash_yield} · ${rc.sharpe_risk_free}`],
    [
      'Confidence intervals',
      `${String(rc.bootstrap.confidence * 100)}% · ${rc.bootstrap.method} · ${String(rc.bootstrap.resamples)} resamples · block ${String(rc.bootstrap.block_length)} · seed ${String(rc.bootstrap.seed)}`,
    ],
    ['Fill model', rc.fill_model],
    ['Out-of-sample lock', rc.oos_lock_basis],
  ];

  return (
    <section
      className="panel border-amber-500/60 p-5"
      aria-labelledby={`reality-${String(report.run_id)}`}
    >
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2
          id={`reality-${String(report.run_id)}`}
          className="font-pixel text-[10px] uppercase leading-relaxed tracking-wider text-amber-300"
        >
          Reality check: read this first
        </h2>
        <Provenance
          source={`Run #${String(report.run_id)} · ${report.provenance.source}`}
          asOf={report.provenance.as_of}
          asOfLabel="computed"
        />
      </div>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {tiles.map((t) => {
          const flagged = t.codes.some((c) => raised.has(c));
          return (
            <div
              key={t.label}
              className={`border-2 p-3 ${flagged ? 'border-amber-500/70 bg-amber-500/10' : 'border-slate-800 bg-slate-950/60'}`}
            >
              <dt className="flex items-center justify-between gap-1 text-[11px] text-slate-400">
                {t.label}
                {flagged && (
                  <span className="font-pixel text-[7px] uppercase text-amber-300">⚠ flagged</span>
                )}
              </dt>
              <dd className="font-mono text-lg text-slate-100">{t.value}</dd>
              <dd className="text-[11px] text-slate-500">{t.sub}</dd>
            </div>
          );
        })}
      </dl>

      {rc.warnings.length > 0 && (
        <div className="mt-4 flex gap-3">
          <div className="shrink-0">
            <AgentSprite agentId="auditor" size={40} animate={false} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-pixel text-[8px] uppercase text-slate-300">Auditor says</p>
            <ul className="mt-2 space-y-1.5" aria-label="Warnings">
              {rc.warnings.map((w) => (
                <li
                  key={`${w.code}-${w.text}`}
                  className="border-l-4 border-amber-500/60 bg-amber-500/5 px-3 py-2 text-sm text-amber-100"
                >
                  {w.text}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-slate-400">All assumptions behind these numbers</summary>
        <dl className="mt-2 grid gap-x-6 gap-y-1.5 sm:grid-cols-[max-content_1fr]">
          {details.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-slate-400">{label}</dt>
              <dd className="text-slate-200">{value}</dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  );
}
