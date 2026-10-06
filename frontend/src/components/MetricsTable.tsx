import type { Estimate, Performance, ResultCore } from '../api/schemas';
import { num, pct, range, usd } from '../format';

interface Row {
  label: string;
  value: (p: Performance) => string;
  ci?: (p: Performance) => string;
}

const withCi = (e: Estimate, fmt: (x: number) => string) => range(e.ci95, fmt);

const ROWS: Row[] = [
  { label: 'Final equity', value: (p) => usd(p.final_equity) },
  { label: 'Total return', value: (p) => pct(p.total_return) },
  {
    label: 'Annualized return',
    value: (p) => pct(p.annualized_return.value),
    ci: (p) => withCi(p.annualized_return, (x) => pct(x)),
  },
  { label: 'Annual volatility', value: (p) => pct(p.annual_volatility) },
  {
    label: 'Sharpe ratio',
    value: (p) => num(p.sharpe.value),
    ci: (p) => withCi(p.sharpe, (x) => num(x)),
  },
  {
    label: 'Max drawdown',
    value: (p) => `${pct(p.max_drawdown)}${p.max_drawdown_date ? ` (${p.max_drawdown_date})` : ''}`,
  },
  { label: 'Time in market', value: (p) => pct(p.exposure, 0) },
  { label: 'Trades', value: (p) => String(p.trade_count) },
  {
    label: 'Win rate',
    value: (p) => pct(p.win_rate.value, 0),
    ci: (p) => withCi(p.win_rate, (x) => pct(x, 0)),
  },
  {
    label: 'Expectancy (avg P&L per trade, historical)',
    value: (p) => usd(p.expectancy.value),
    ci: (p) => withCi(p.expectancy, usd),
  },
  { label: 'Costs paid', value: (p) => usd(p.total_costs) },
];

export function MetricsTable({ report }: { report: ResultCore }) {
  const columns: [string, Performance][] = [
    [`${report.strategy.name} · ${report.symbol}`, report.strategy_metrics],
    [`Buy & hold · ${report.benchmark_symbol}`, report.benchmark_metrics],
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[34rem] text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-left text-xs text-slate-400">
            <th className="py-2 pr-4 font-normal">Metric (after costs)</th>
            {columns.map(([name]) => (
              <th key={name} className="py-2 pr-4 font-normal">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.label} className="border-b border-slate-800/60 align-top">
              <td className="py-2 pr-4 text-slate-400">{row.label}</td>
              {columns.map(([name, perf]) => (
                <td key={name} className="py-2 pr-4">
                  <span className="font-mono text-slate-100">{row.value(perf)}</span>
                  {row.ci && (
                    <span className="block text-xs text-slate-500">95% CI {row.ci(perf)}</span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
