import type { Flag, GameState } from '../api/schemas';
import type { ApiState } from '../api/useApi';
import { Card, NoData } from '../components/Card';
import { Provenance } from '../components/Provenance';
import { GraduationGate } from './GraduationGate';
import { timeOf } from './status';

const SEVERITY = {
  error: { label: 'Alert', icon: '⛔', tone: 'border-rose-500/60' },
  warning: { label: 'Caution', icon: '⚠', tone: 'border-amber-500/60' },
  info: { label: 'Note', icon: 'ℹ', tone: 'border-sky-500/40' },
} as const;

const STATION_NAMES: Record<string, string> = {
  'data-health': 'Data Scout',
  'strategy-lab': 'Quant',
};

function FlagItem({ flag }: { flag: Flag }) {
  const s = SEVERITY[flag.severity];
  return (
    <li className={`border-l-4 bg-slate-950/50 py-2 pl-3 pr-2 ${s.tone}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-slate-100">
          <span aria-hidden="true">{s.icon} </span>
          {flag.title}
        </span>
        <span className="font-display font-bold tracking-wide text-[11px] uppercase text-slate-400">{s.label}</span>
      </div>
      <p className="mt-1 text-sm text-slate-300">{flag.summary}</p>
      <p className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px] text-slate-500">
        <span>
          {flag.source} · {timeOf(flag.as_of)}
        </span>
        {flag.station && (
          <a href={`#${flag.station}`} className="text-sky-400 hover:text-sky-300">
            Open {STATION_NAMES[flag.station] ?? flag.station} &gt;
          </a>
        )}
      </p>
    </li>
  );
}

export function AuditPage({ game }: { game: ApiState<GameState> }) {
  if (game.kind === 'loading') return <p className="text-slate-400">Auditing…</p>;
  if (game.kind === 'error') {
    return (
      <Card title="Audit">
        <NoData message={`Audit unavailable: ${game.message}`} />
      </Card>
    );
  }
  const { flags } = game.data;
  const counts = (['error', 'warning', 'info'] as const).map(
    (sev) => `${String(flags.filter((f) => f.severity === sev).length)} ${SEVERITY[sev].label.toLowerCase()}`,
  );
  return (
    <div className="space-y-6">
      <Card title={`Open flags (${String(flags.length)})`}>
        <div className="mb-3">
          <Provenance source={game.data.source} asOf={game.data.as_of} asOfLabel="audited" />
          <p className="mt-1 text-xs text-slate-400">{counts.join(' · ')}</p>
        </div>
        {flags.length === 0 ? (
          <p className="text-sm text-slate-400">No open flags.</p>
        ) : (
          <ul className="space-y-3" aria-label="Open flags">
            {flags.map((f) => (
              <FlagItem key={`${f.code}-${f.title}`} flag={f} />
            ))}
          </ul>
        )}
      </Card>
      <GraduationGate gate={game.data.graduation} />
    </div>
  );
}
