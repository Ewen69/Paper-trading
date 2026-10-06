import type { AgentStatus } from '../api/schemas';

/** Status is always shown as a label plus a light, never as color alone. */
export const STATUS: Record<AgentStatus, { label: string; led: string; pulse: boolean }> = {
  ok: { label: 'Ready', led: 'bg-emerald-400', pulse: false },
  warning: { label: 'Caution', led: 'bg-amber-400', pulse: true },
  error: { label: 'Alert', led: 'bg-rose-500', pulse: true },
  idle: { label: 'Idle', led: 'bg-slate-500', pulse: false },
  locked: { label: 'Locked', led: 'bg-slate-700', pulse: false },
};

export const timeOf = (d: Date) =>
  d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
