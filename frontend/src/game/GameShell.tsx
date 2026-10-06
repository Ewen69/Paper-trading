import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchGameState } from '../api/client';
import type { Agent, GameEvent, GameState } from '../api/schemas';
import { useApi, type ApiState } from '../api/useApi';
import { NetWorthPage } from '../networth/NetWorthPage';
import { OpsCenter } from '../ops/OpsCenter';
import { PortfolioPage } from '../portfolio/PortfolioPage';
import { DataHealthPage } from '../pages/DataHealthPage';
import { StrategyLabPage } from '../pages/StrategyLabPage';
import { ACTIVITY_EVENT, announceActivity } from './activity';
import { AgentSprite } from './AgentSprite';
import { AuditPage } from './AuditPage';
import { HQPage } from './HQPage';
import { timeOf } from './status';
import { StatusChip } from './StatusChip';

const STATIONS = [
  { id: 'ops', key: '1', label: 'Ops Center', agent: null, icon: '📡' },
  { id: 'hq', key: '2', label: 'HQ', agent: null, icon: '🏠' },
  { id: 'data-health', key: '3', label: 'Data Scout', agent: 'scout', icon: null },
  { id: 'strategy-lab', key: '4', label: 'Quant', agent: 'quant', icon: null },
  { id: 'audit', key: '5', label: 'Auditor', agent: 'auditor', icon: null },
  { id: 'net-worth', key: '6', label: 'Net Worth', agent: 'accountant', icon: null },
  { id: 'portfolio', key: '7', label: 'Portfolio', agent: 'analyst', icon: null },
] as const;
type StationId = (typeof STATIONS)[number]['id'];

const stationFromHash = (): StationId =>
  STATIONS.find((s) => `#${s.id}` === window.location.hash)?.id ?? 'ops';

const eventKey = (e: GameEvent) => `${e.at.toISOString()}|${e.kind}|${e.text}`;

function Hud({ game }: { game: ApiState<GameState> }) {
  const ready = game.kind === 'ready' ? game.data : null;
  const p = ready?.player;
  const progress = p ? (p.xp - p.level_start_xp) / (p.next_level_xp - p.level_start_xp) : 0;
  return (
    <header className="panel sticky top-0 z-40 mb-4 flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
      <div className="flex items-center gap-3">
        <span
          className="border-2 border-emerald-400 bg-emerald-400/10 px-2 py-1 font-pixel text-[8px] uppercase text-emerald-300"
          title="Paper trading only. This app cannot place real-money orders."
        >
          🛡 Paper only
        </span>
        <h1 className="font-pixel text-[11px] uppercase leading-relaxed text-slate-100 sm:text-xs">
          Paper Trading Lab
        </h1>
      </div>
      <div className="flex min-w-48 flex-1 items-center gap-3">
        {p ? (
          <>
            <span className="font-pixel text-[10px] text-amber-300">LV {p.level}</span>
            <div className="min-w-0 flex-1">
              <div
                className="h-3 border-2 border-slate-700 bg-slate-950"
                role="progressbar"
                aria-label="Process XP toward next level"
                aria-valuemin={p.level_start_xp}
                aria-valuemax={p.next_level_xp}
                aria-valuenow={p.xp}
              >
                <div className="xp-bar h-full" style={{ width: `${String(progress * 100)}%` }} />
              </div>
              <p className="mt-1 text-[10px] text-slate-500">
                {p.xp} / {p.next_level_xp} process XP · local records · {timeOf(ready.as_of)}
              </p>
            </div>
          </>
        ) : (
          <span className="text-xs text-slate-500">XP: no data</span>
        )}
      </div>
      {ready && (
        <p className="font-pixel text-[8px] uppercase leading-relaxed text-slate-400">
          NYSE {ready.market_open ? 'open' : 'closed'}
          <span className="block normal-case text-slate-500">
            last session {ready.last_completed_session}
          </span>
        </p>
      )}
    </header>
  );
}

function Hotbar({ current, agents }: { current: StationId; agents: Agent[] }) {
  const locked = agents.filter((a) => a.status === 'locked');
  return (
    <nav className="mb-6 flex flex-wrap gap-2" aria-label="Stations">
      {STATIONS.map((s) => {
        const active = s.id === current;
        return (
          <a
            key={s.id}
            href={`#${s.id}`}
            aria-current={active ? 'page' : undefined}
            className={`panel flex shrink-0 items-center gap-2 px-3 py-2 ${active ? 'border-sky-400' : 'hover:border-slate-500'}`}
          >
            <span className="font-pixel text-[8px] text-slate-500" aria-hidden="true">
              {s.key}
            </span>
            {s.agent ? (
              <AgentSprite agentId={s.agent} size={20} animate={false} />
            ) : (
              <span aria-hidden="true">{s.icon}</span>
            )}
            <span
              className={`font-pixel text-[9px] uppercase ${active ? 'text-sky-300' : 'text-slate-300'}`}
            >
              {s.label}
            </span>
          </a>
        );
      })}
      {locked.length > 0 && (
        <span
          className="panel flex shrink-0 items-center gap-1 px-3 py-2 opacity-60"
          title={locked.map((a) => `${a.name}: unlocks in ${a.unlocks_in ?? 'a later phase'}`).join('\n')}
        >
          {locked.map((a) => (
            <AgentSprite key={a.id} agentId={a.id} locked size={14} animate={false} />
          ))}
          <span className="ml-1 font-pixel text-[8px] uppercase text-slate-500">
            🔒 {locked.length} locked
          </span>
          <span className="sr-only">
            : {locked.map((a) => `${a.name} (${a.unlocks_in ?? 'later'})`).join(', ')}
          </span>
        </span>
      )}
    </nav>
  );
}

function StationHeader({ agent }: { agent: Agent | undefined }) {
  if (!agent) return null;
  const line = agent.report[0];
  return (
    <div className="panel mb-6 flex items-center gap-4 p-4">
      <AgentSprite agentId={agent.id} size={56} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-pixel text-[11px] uppercase text-slate-100">{agent.name}</h2>
          <StatusChip status={agent.status} />
        </div>
        {line && (
          <p className="mt-2 text-sm text-slate-300">
            “{line.text}”{' '}
            <span className="text-[11px] text-slate-500">
              {line.source} · {timeOf(line.as_of)}
            </span>
          </p>
        )}
      </div>
    </div>
  );
}

function Toasts({ events, onDismiss }: { events: GameEvent[]; onDismiss: () => void }) {
  useEffect(() => {
    if (events.length === 0) return;
    const timer = window.setTimeout(onDismiss, 6000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [events, onDismiss]);
  if (events.length === 0) return null;
  return (
    <div className="fixed right-4 top-24 z-50 w-72 space-y-2" role="status" aria-live="polite">
      {events.map((e) => (
        <div key={eventKey(e)} className="panel toast-in p-3">
          <p className={`font-pixel text-[10px] ${e.xp > 0 ? 'text-amber-300' : 'text-slate-400'}`}>
            +{e.xp} XP
          </p>
          <p className="mt-1 text-xs text-slate-200">{e.text}</p>
          {e.xp_note && <p className="mt-1 text-[11px] text-slate-500">{e.xp_note}</p>}
        </div>
      ))}
    </div>
  );
}

export function GameShell() {
  const [station, setStation] = useState<StationId>(stationFromHash);
  const [game, reload] = useApi(fetchGameState);
  const [toasts, setToasts] = useState<GameEvent[]>([]);
  const seen = useRef<Set<string> | null>(null);

  // Follow the URL hash; refresh game state on every station change.
  useEffect(() => {
    const onHash = () => {
      setStation(stationFromHash());
      reload();
    };
    window.addEventListener('hashchange', onHash);
    window.addEventListener(ACTIVITY_EVENT, reload);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener(ACTIVITY_EVENT, reload);
    };
  }, [reload]);

  // Hotkeys 1-7 switch stations, except while typing in a form field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target !== null &&
        (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable);
      if (typing || event.altKey || event.ctrlKey || event.metaKey) return;
      const match = STATIONS.find((s) => s.key === event.key);
      if (match) window.location.hash = match.id;
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  // New events since the last load become toasts (none on the very first load).
  useEffect(() => {
    if (game.kind !== 'ready') return;
    const keys = new Set(game.data.events.map(eventKey));
    if (seen.current) {
      const previous = seen.current;
      const fresh = game.data.events.filter((e) => !previous.has(eventKey(e))).slice(0, 3);
      if (fresh.length) setToasts(fresh);
    }
    seen.current = keys;
  }, [game]);

  const dismiss = useCallback(() => {
    setToasts([]);
  }, []);

  const agents = game.kind === 'ready' ? game.data.agents : [];
  const stationAgent = agents.find((a) => a.station === station);

  return (
    <div className={`mx-auto px-4 py-4 ${station === 'ops' ? 'max-w-[1700px]' : 'max-w-6xl'}`}>
      <Hud game={game} />
      <Hotbar current={station} agents={agents} />
      <main>
        {station === 'hq' && <HQPage game={game} />}
        {station === 'ops' && <OpsCenter />}
        {station !== 'hq' && station !== 'ops' && <StationHeader agent={stationAgent} />}
        {station === 'data-health' && <DataHealthPage />}
        {station === 'strategy-lab' && <StrategyLabPage onActivity={announceActivity} />}
        {station === 'audit' && <AuditPage game={game} />}
        {station === 'net-worth' && <NetWorthPage />}
        {station === 'portfolio' && <PortfolioPage />}
      </main>
      <Toasts events={toasts} onDismiss={dismiss} />
    </div>
  );
}
