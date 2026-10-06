/**
 * City: the live agent architecture as an isometric diorama plus DOM panels.
 *
 * Everything shown comes from the backend's WebSocket stream (validated with zod). The 3D view
 * is decoration over the same data; the panels below carry the full, accessible detail, so the
 * page works without WebGL.
 */
import { lazy, Suspense, useMemo, useState, type ReactNode } from 'react';

import { setAutopilot } from '../api/client';
import type { Finding, LiveAgent, Sector } from '../api/schemas';
import { useActivity, type ActivityState } from '../api/useActivity';
import {
  DataCheckControl,
  EquitySweepControl,
  OptionsSweepControl,
  PaperCycleControl,
  RiskControl,
} from './controls';

const CityScene = lazy(() => import('./CityScene').then((m) => ({ default: m.CityScene })));

/** Agents with no jobs of their own (they react to other agents' orders). */
const NO_AUTOPILOT = new Set(['risk-officer']);

const STATUS_TEXT: Record<LiveAgent['status'], string> = {
  running: 'text-sky-300',
  queued: 'text-amber-300',
  error: 'text-rose-300',
  idle: 'text-slate-300',
  watching: 'text-indigo-300',
};

function webglAvailable(): boolean {
  if (typeof WebGLRenderingContext === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    return canvas.getContext('webgl2') !== null || canvas.getContext('webgl') !== null;
  } catch {
    return false;
  }
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

const time = (d: Date | null) => (d ? d.toLocaleTimeString() : '—');
const pct = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null) => (v === null ? '—' : v.toFixed(2));
const paramText = (p: Record<string, number> | null) =>
  p ? Object.entries(p).map(([k, v]) => `${k}=${String(v)}`).join(', ') : '';

function ConnectionBar({ state }: { state: ActivityState }) {
  const tone =
    state.connection === 'live'
      ? 'border-emerald-400 text-emerald-300'
      : state.connection === 'connecting'
        ? 'border-amber-400 text-amber-300'
        : 'border-rose-400 text-rose-300';
  return (
    <div className="panel mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-xs text-slate-400">
      <span className={`border-2 px-2 py-1 font-pixel text-[8px] uppercase ${tone}`} role="status">
        Stream {state.connection}
      </span>
      <span>Source: {state.source ?? 'no data'}</span>
      <span>Snapshot: {time(state.snapshotAt)}</span>
      <span>Last event: {time(state.lastEventAt)}</span>
      {state.invalidMessages > 0 && (
        <span className="text-amber-300">{state.invalidMessages} invalid message(s) ignored</span>
      )}
      {state.connection === 'offline' && (
        <span className="text-rose-300">Backend unreachable: showing nothing rather than stale state. Retrying…</span>
      )}
    </div>
  );
}

function KillSwitchBanner({ killSwitch }: { killSwitch: ActivityState['killSwitch'] }) {
  if (!killSwitch?.engaged) return null;
  return (
    <div role="alert" className="mb-4 border-2 border-rose-500 bg-rose-950/60 px-4 py-3 text-sm text-rose-200">
      <strong className="font-pixel text-[10px] uppercase">Kill switch engaged</strong> — no new risk will be
      opened. Reason: {killSwitch.reason || 'none given'}
    </div>
  );
}

function AgentCard({ agent }: { agent: LiveAgent }) {
  const [error, setError] = useState<string | null>(null);
  const progress =
    agent.progress_total !== null && agent.progress_done !== null && agent.progress_total > 0
      ? agent.progress_done / agent.progress_total
      : null;
  return (
    <article className="border border-slate-700 bg-slate-950/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-100">{agent.name}</h4>
        <span className={`font-pixel text-[8px] uppercase ${STATUS_TEXT[agent.status]}`}>
          {agent.status}
          {agent.queued > 0 ? ` · ${String(agent.queued)} queued` : ''}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-slate-500">{agent.role}</p>
      {agent.message && <p className="mt-2 font-mono text-xs text-slate-300">{agent.message}</p>}
      {agent.params && <p className="mt-1 font-mono text-[11px] text-sky-300">Testing {paramText(agent.params)}</p>}
      {progress !== null && (
        <div
          className="mt-2 h-2 border border-slate-700 bg-slate-950"
          role="progressbar"
          aria-label={`${agent.name} progress`}
          aria-valuemin={0}
          aria-valuemax={agent.progress_total ?? 0}
          aria-valuenow={agent.progress_done ?? 0}
        >
          <div className="h-full bg-sky-500" style={{ width: `${String(progress * 100)}%` }} />
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
        <span>updated {agent.updated_at.toLocaleTimeString()}</span>
        {!NO_AUTOPILOT.has(agent.id) && (
          <label className="flex items-center gap-1 text-slate-300" title="Autopilot only queues untried in-sample work; it never runs the out-of-sample test.">
            <input
              type="checkbox"
              checked={agent.autopilot}
              onChange={(e) => {
                setError(null);
                setAutopilot(agent.id, e.target.checked).catch((err: unknown) => {
                  setError(err instanceof Error ? err.message : 'unknown');
                });
              }}
            />
            Autopilot
          </label>
        )}
      </div>
      {error && <p role="alert" className="mt-1 text-xs text-rose-300">{error}</p>}
    </article>
  );
}

function controlsFor(agentId: string, engaged: boolean | null): ReactNode {
  switch (agentId) {
    case 'scout':
      return <DataCheckControl />;
    case 'quant-equity':
      return <EquitySweepControl />;
    case 'quant-options':
      return <OptionsSweepControl />;
    case 'risk-officer':
      return <RiskControl engaged={engaged} />;
    case 'paper-trader':
      return <PaperCycleControl />;
    default:
      return null;
  }
}

function SectorPanel({ sector, agents, engaged }: { sector: Sector; agents: LiveAgent[]; engaged: boolean | null }) {
  const members = sector.agents.map((id) => agents.find((a) => a.id === id)).filter((a): a is LiveAgent => !!a);
  const free = Math.max(0, sector.slots - members.length);
  return (
    <section className="panel p-4" aria-labelledby={`sector-${sector.id}`}>
      <h3 id={`sector-${sector.id}`} className="font-pixel text-[10px] uppercase text-slate-100">
        {sector.name}
      </h3>
      <p className="mt-1 text-xs text-slate-400">{sector.purpose}</p>
      <div className="mt-3 space-y-3">
        {members.map((agent) => (
          <div key={agent.id}>
            <AgentCard agent={agent} />
            <div className="border border-t-0 border-slate-700 p-3">{controlsFor(agent.id, engaged)}</div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] text-slate-500">
        {free} free slot{free === 1 ? '' : 's'} reserved for sub-agents
      </p>
    </section>
  );
}

function FindingsFeed({ findings }: { findings: Finding[] }) {
  return (
    <section className="panel mt-6 p-4" aria-labelledby="findings-title">
      <h3 id="findings-title" className="font-pixel text-[10px] uppercase text-slate-100">
        Findings log
      </h3>
      <p className="mt-1 text-xs text-slate-400">
        Every combination tried is recorded and counted. In-sample results are optimistic by
        construction; only the single out-of-sample test per sweep is an honest estimate, and it is
        still one noisy sample. Not advice.
      </p>
      {findings.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">No findings yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-3">Time</th>
                <th className="py-1 pr-3">Agent</th>
                <th className="py-1 pr-3">Test</th>
                <th className="py-1 pr-3">Period</th>
                <th className="py-1 pr-3">Run</th>
                <th className="py-1 pr-3 text-right">Trades</th>
                <th className="py-1 pr-3 text-right">Win rate</th>
                <th className="py-1 pr-3 text-right">Sharpe</th>
                <th className="py-1">Note</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((f) => (
                <tr key={f.id} className="border-t border-slate-800 align-top">
                  <td className="py-1 pr-3 whitespace-nowrap">{f.created_at.toLocaleString()}</td>
                  <td className="py-1 pr-3">{f.agent_id}</td>
                  <td className="py-1 pr-3 font-mono">
                    {f.strategy} {f.symbol} ({paramText(f.params)})
                  </td>
                  <td className={`py-1 pr-3 ${f.period === 'out-of-sample' ? 'text-amber-300' : ''}`}>{f.period}</td>
                  <td className="py-1 pr-3">{f.run_id === null ? '—' : `#${String(f.run_id)}`}</td>
                  <td className="py-1 pr-3 text-right">{f.trades ?? '—'}</td>
                  <td className="py-1 pr-3 text-right">{pct(f.win_rate)}</td>
                  <td className="py-1 pr-3 text-right">{num(f.sharpe)}</td>
                  <td className="py-1 text-slate-400">{f.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function CityPage({ url }: { url?: string }) {
  const state = useActivity(url);
  const agents = useMemo(() => Object.values(state.agents), [state.agents]);
  const [webgl] = useState(webglAvailable);
  const [reduced] = useState(prefersReducedMotion);
  const engaged = state.killSwitch?.engaged ?? null;
  const live = state.sectors.length > 0;

  return (
    <div>
      <ConnectionBar state={state} />
      <KillSwitchBanner killSwitch={state.killSwitch} />
      {live && webgl && (
        <div className="city-frame mb-6" aria-label="Isometric city view of the agents (decorative; details below)">
          <Suspense fallback={<p className="p-4 text-sm text-slate-600">Loading 3D view…</p>}>
            <CityScene sectors={state.sectors} agents={agents} reduced={reduced} />
          </Suspense>
          <div className="city-tilt city-tilt-top" />
          <div className="city-tilt city-tilt-bottom" />
          <p className="absolute bottom-2 right-3 z-[3] text-[11px] text-slate-600">Drag to pan · scroll to zoom</p>
        </div>
      )}
      {live && !webgl && (
        <p className="panel mb-6 p-4 text-sm text-slate-400">3D view unavailable (no WebGL). All agent detail is below.</p>
      )}
      {!live && (
        <p className="panel mb-6 p-4 text-sm text-slate-400">
          {state.connection === 'offline' ? 'No data: the agent stream is offline.' : 'Connecting to the agent stream…'}
        </p>
      )}
      {live && (
        <div className="grid gap-6 lg:grid-cols-2">
          {state.sectors.map((sector) => (
            <SectorPanel key={sector.id} sector={sector} agents={agents} engaged={engaged} />
          ))}
        </div>
      )}
      {live && <FindingsFeed findings={state.findings} />}
    </div>
  );
}
