import type { Report } from '../api/schemas';
import { pct, range, usd } from '../format';
import { Card } from './Card';

/** Shown next to every backtest result: what the numbers rest on and why to doubt them. */
export function RealityCheckPanel({ report }: { report: Report }) {
  const rc = report.reality_check;
  const excess = report.excess_annualized_return;
  const rows: [string, string][] = [
    ['Period', `${rc.period} · ${rc.window_start} → ${rc.window_end}`],
    ['Sample size', `${rc.sessions.toLocaleString()} sessions · ${String(rc.trade_count)} trades`],
    [
      'Parameter combinations tried',
      `${String(rc.parameter_combinations_tried)} for this strategy on ${report.symbol} (${String(rc.parameter_combinations_tried_all_strategies)} across all strategies, ${String(rc.in_sample_runs)} in-sample runs)`,
    ],
    [
      'Out-of-sample',
      `locked from ${rc.oos_start} · evaluated ${String(rc.oos_evaluations)} time(s)`,
    ],
    [
      `Excess annual return vs ${report.benchmark_symbol}`,
      `${pct(excess.value)} · 95% CI ${range(excess.ci95, (x) => pct(x))}`,
    ],
    [
      'Costs assumed',
      `${String(rc.costs.slippage_bps)} bps slippage per side · ${usd(rc.costs.commission_per_order)} per order · ${String(rc.costs.commission_bps)} bps commission`,
    ],
    ['Starting capital', usd(rc.initial_capital)],
    ['Prices', `${rc.price_basis} (benchmark: ${rc.benchmark_price_basis})`],
    ['Cash yield / Sharpe risk-free', `${rc.cash_yield} · ${rc.sharpe_risk_free}`],
    [
      'Confidence intervals',
      `${String(rc.bootstrap.confidence * 100)}% · ${rc.bootstrap.method} · ${String(rc.bootstrap.resamples)} resamples · block ${String(rc.bootstrap.block_length)} · seed ${String(rc.bootstrap.seed)}`,
    ],
  ];

  return (
    <Card title="Reality check">
      <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[max-content_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-slate-400">{label}</dt>
            <dd className="text-slate-200">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-slate-400">{rc.fill_model}</p>
      <p className="mt-1 text-xs text-slate-500">OOS lock: {rc.oos_lock_basis}</p>
      {rc.warnings.length > 0 && (
        <ul className="mt-4 space-y-1.5" aria-label="Warnings">
          {rc.warnings.map((w) => (
            <li
              key={w}
              className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm text-amber-200"
            >
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
