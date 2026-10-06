import type { Graduation } from '../api/schemas';
import { Card } from '../components/Card';
import { timeOf } from './status';

const STATUS = {
  met: { icon: '✓', label: 'Met', tone: 'border-emerald-500/60 text-emerald-300' },
  not_met: { icon: '✗', label: 'Not met', tone: 'border-rose-500/50 text-rose-300' },
  not_started: { icon: '○', label: 'Not started', tone: 'border-slate-700 text-slate-400' },
} as const;

/** Real-money readiness checklist. It only tracks; nothing reads it to enable live trading. */
export function GraduationGate({ gate }: { gate: Graduation }) {
  return (
    <Card title={`Graduation gate ${String(gate.met)}/${String(gate.total)}`}>
      <p className="mb-4 border-2 border-sky-500/40 bg-sky-500/5 px-3 py-2 text-xs text-sky-200">
        🛡 {gate.note}
      </p>
      <ol className="space-y-3">
        {gate.items.map((item) => {
          const s = STATUS[item.status];
          return (
            <li key={item.id} className={`border-l-4 bg-slate-950/50 py-2 pl-3 pr-2 ${s.tone}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-display font-bold tracking-wide text-[12px] uppercase leading-relaxed text-slate-100">
                  {item.title}
                </span>
                <span className={`font-display font-bold tracking-wide text-[11px] uppercase ${s.tone}`}>
                  <span aria-hidden="true">{s.icon} </span>
                  {s.label}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-400">{item.requirement}</p>
              <p className="mt-1 text-sm text-slate-200">{item.progress}</p>
              {item.evidence && <p className="mt-1 text-xs text-emerald-200">{item.evidence}</p>}
              <p className="mt-1 text-[11px] text-slate-500">
                {item.source} · {timeOf(item.as_of)}
              </p>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
