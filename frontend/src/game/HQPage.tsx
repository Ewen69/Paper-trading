import type { ApiState } from '../api/useApi';
import type { GameState } from '../api/schemas';
import { Card, NoData } from '../components/Card';
import { HealthCard } from '../components/HealthCard';
import { Provenance } from '../components/Provenance';
import { AgentSprite } from './AgentSprite';
import { AgentTile } from './AgentTile';
import { GraduationGate } from './GraduationGate';
import { timeOf } from './status';

const MAX_EVENTS = 15;

function MissionLog({ game }: { game: GameState }) {
  const names = Object.fromEntries(game.agents.map((a) => [a.id, a.name]));
  const shown = game.events.slice(0, MAX_EVENTS);
  return (
    <Card title={`Mission log (${String(game.events.length)})`}>
      {shown.length === 0 ? (
        <p className="text-sm text-slate-400">
          No missions yet. Import a dataset to give the Data Scout its first job.
        </p>
      ) : (
        <ol className="space-y-3">
          {shown.map((e) => (
            <li key={`${e.at.toISOString()}-${e.kind}-${e.text}`} className="flex gap-3">
              <div className="shrink-0 pt-0.5">
                <AgentSprite agentId={e.agent} size={24} animate={false} />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm text-slate-200">
                  <span className="text-slate-400">{names[e.agent] ?? e.agent}:</span> {e.text}
                </p>
                <p className="text-[11px] text-slate-500">
                  {e.source} · {timeOf(e.at)}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <span
                  className={`font-pixel text-[9px] ${e.xp > 0 ? 'text-amber-300' : 'text-slate-500'}`}
                >
                  +{e.xp} XP
                </span>
                {e.xp_note && <p className="max-w-36 text-[11px] text-slate-500">{e.xp_note}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
      {game.events.length > MAX_EVENTS && (
        <p className="mt-3 text-xs text-slate-500">
          Showing the latest {MAX_EVENTS} of {game.events.length}.
        </p>
      )}
    </Card>
  );
}

function Badges({ game }: { game: GameState }) {
  const earned = game.badges.filter((b) => b.earned).length;
  return (
    <Card title={`Badges ${String(earned)}/${String(game.badges.length)}`}>
      <ul className="grid gap-3 sm:grid-cols-2">
        {game.badges.map((b) => (
          <li
            key={b.id}
            className={`border-2 p-3 ${b.earned ? 'border-amber-400/70 bg-amber-400/5' : 'border-slate-800 opacity-70'}`}
          >
            <p className="flex items-center justify-between gap-2">
              <span className="font-pixel text-[9px] uppercase leading-relaxed text-slate-100">
                {b.earned ? '★' : '☆'} {b.name}
              </span>
              <span
                className={`font-pixel text-[7px] uppercase ${b.earned ? 'text-amber-300' : 'text-slate-500'}`}
              >
                {b.earned ? 'Earned' : 'Not yet'}
              </span>
            </p>
            <p className="mt-1.5 text-xs text-slate-400">{b.description}</p>
            {b.earned && b.earned_at && (
              <p className="mt-1 text-[11px] text-slate-500">
                {b.detail} · {timeOf(b.earned_at)}
              </p>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function XpRules({ game }: { game: GameState }) {
  return (
    <Card title="How XP works">
      <ul className="space-y-1.5 text-sm">
        {game.xp_rules.map((r) => (
          <li key={r.id} className="flex justify-between gap-3">
            <span className="text-slate-300">{r.description}</span>
            <span className="shrink-0 font-pixel text-[9px] text-amber-300">+{r.xp}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-slate-400">{game.xp_policy}</p>
    </Card>
  );
}

export function HQPage({ game }: { game: ApiState<GameState> }) {
  return (
    <div className="space-y-6">
      {game.kind === 'loading' && <p className="text-slate-400">Assembling the squad…</p>}
      {game.kind === 'error' && (
        <Card title="Squad">
          <NoData message={`Game state unavailable: ${game.message}`} />
        </Card>
      )}
      {game.kind === 'ready' && (
        <>
          <section aria-label="Squad">
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="font-pixel text-xs uppercase text-slate-100">The squad</h2>
              <Provenance source={game.data.source} asOf={game.data.as_of} asOfLabel="as of" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {game.data.agents.map((agent, i) => (
                <AgentTile key={agent.id} agent={agent} index={i} />
              ))}
            </div>
          </section>
          <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
            <div className="space-y-6">
              <GraduationGate gate={game.data.graduation} />
              <MissionLog game={game.data} />
            </div>
            <div className="space-y-6">
              <Badges game={game.data} />
              <XpRules game={game.data} />
            </div>
          </div>
        </>
      )}
      <HealthCard />
    </div>
  );
}
