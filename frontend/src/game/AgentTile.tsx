import type { Agent } from '../api/schemas';
import { AgentAvatar } from './AgentAvatar';
import { timeOf } from './status';
import { StatusChip } from './StatusChip';

/** One squad member's card: portrait, status, and a speech bubble of real report lines. */
export function AgentTile({ agent }: { agent: Agent }) {
  const locked = agent.status === 'locked';
  return (
    <article
      className={`panel flex flex-col p-4 ${locked ? 'opacity-60' : ''}`}
      aria-label={`${agent.name}${locked ? ' (locked)' : ''}`}
    >
      <div className="flex items-end gap-4">
        <div className="shrink-0">
          <AgentAvatar agentId={agent.id} locked={locked} size={64} />
        </div>
        <div className="min-w-0 flex-1 space-y-1.5">
          <h3 className="font-display font-bold tracking-wide text-[14px] uppercase leading-relaxed text-slate-100">
            {agent.name}
          </h3>
          <StatusChip status={agent.status} />
          {!locked && (
            <p className="font-display font-bold tracking-wide text-[11px] uppercase text-amber-300">
              LV {agent.level} · {agent.xp} XP
            </p>
          )}
        </div>
      </div>
      <p className="mt-3 text-xs text-slate-400">{agent.role}</p>

      {locked ? (
        <p className="mt-3 font-display font-bold tracking-wide text-[11px] uppercase leading-relaxed text-slate-500">
          🔒 Unlocks in {agent.unlocks_in}
        </p>
      ) : (
        <>
          <div className="relative mt-4 flex-1 border-2 border-slate-700 bg-slate-950/80 p-3">
            <span
              className="absolute -top-[9px] left-6 h-0 w-0 border-x-8 border-b-8 border-x-transparent border-b-slate-700"
              aria-hidden="true"
            />
            <ul className="space-y-2.5">
              {agent.report.map((line) => (
                <li key={line.text}>
                  <p className="text-sm text-slate-200">{line.text}</p>
                  <p className="text-[11px] text-slate-500">
                    {line.source} · {timeOf(line.as_of)}
                  </p>
                </li>
              ))}
            </ul>
          </div>
          {agent.station && (
            <a
              href={`#${agent.station}`}
              className="btn-game mt-4 inline-block self-start bg-sky-600 px-3 py-2 text-white hover:bg-sky-500"
            >
              Enter station &gt;
            </a>
          )}
        </>
      )}
    </article>
  );
}
