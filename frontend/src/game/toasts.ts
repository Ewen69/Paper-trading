/**
 * Toast policy. High-frequency activity (every optimizer trial, routine log lines, zero-XP
 * backtests) scrolls silently through the terminal and the mission log. Only major state
 * changes raise a toast, and repeats of the same kind within a window merge into one toast
 * with a counter instead of stacking.
 */
import type { GameEvent, LogLine } from '../api/schemas';

export type ToastTone = 'good' | 'info' | 'warn' | 'bad';

export interface ToastItem {
  id: string;
  kind: string;
  title: string;
  text: string;
  tone: ToastTone;
  count: number;
  at: number; // ms since epoch, of the latest merged event
}

export const MERGE_WINDOW_MS = 15_000;
export const MAX_TOASTS = 3;
export const TOAST_LIFETIME_MS = 6_000;
export const BULK_GAME_EVENTS = 3;

const MAJOR: Record<string, { title: string; tone: ToastTone }> = {
  active_best: { title: 'New Active Best', tone: 'good' },
  revalidation: { title: 'Active Best re-checked', tone: 'info' },
  budget: { title: 'Trial budget exhausted', tone: 'warn' },
  idle: { title: 'Optimizer idle', tone: 'warn' },
  risk: { title: 'Risk limit blocked an order', tone: 'bad' },
  kill_switch: { title: 'Kill switch tripped', tone: 'bad' },
  data_sync: { title: 'Daily data sync', tone: 'info' },
  order: { title: 'Paper order', tone: 'info' },
};

/** A toast for a daemon log line, or null when the line should only scroll by. */
export function toastForLog(line: LogLine, now: number): ToastItem | null {
  const major = MAJOR[line.kind];
  if (!major) return null;
  return {
    id: `log-${String(line.id)}`,
    kind: line.kind,
    title: major.title,
    text: line.message,
    tone: line.level === 'error' ? 'bad' : line.level === 'warn' && major.tone === 'info' ? 'warn' : major.tone,
    count: 1,
    at: now,
  };
}

const worthAToast = (e: GameEvent) => e.xp > 0 || /holdout/i.test(e.xp_note ?? '');

/** Toasts for newly seen game events: XP earned or a holdout warning; bursts collapse. */
export function toastsForGameEvents(fresh: GameEvent[], now: number): ToastItem[] {
  const notable = fresh.filter(worthAToast);
  if (notable.length > BULK_GAME_EVENTS) {
    const xp = notable.reduce((sum, e) => sum + e.xp, 0);
    return [
      {
        id: `game-bulk-${String(now)}`,
        kind: 'game',
        title: `+${String(xp)} XP`,
        text: `${String(notable.length)} research events logged. Details are in the HQ mission log.`,
        tone: 'info',
        count: 1,
        at: now,
      },
    ];
  }
  return notable.map((e) => ({
    id: `game-${e.at.toISOString()}-${e.kind}-${e.text}`,
    kind: 'game',
    title: `+${String(e.xp)} XP`,
    text: e.xp_note ? `${e.text} ${e.xp_note}` : e.text,
    tone: e.xp > 0 ? 'good' : 'warn',
    count: 1,
    at: now,
  }));
}

/** Add a toast: merge into a same-kind toast from the last window, else push; keep the newest. */
export function addToast(list: ToastItem[], item: ToastItem): ToastItem[] {
  const index = list.findIndex((t) => t.kind === item.kind && item.at - t.at < MERGE_WINDOW_MS);
  if (index >= 0) {
    const merged = list[index];
    if (!merged) return list;
    const updated: ToastItem = {
      ...merged,
      text: item.text,
      tone: item.tone === 'bad' ? 'bad' : merged.tone,
      count: merged.count + item.count,
      at: item.at,
    };
    return [updated, ...list.filter((_, i) => i !== index)].slice(0, MAX_TOASTS);
  }
  return [item, ...list].slice(0, MAX_TOASTS);
}

export function expire(list: ToastItem[], now: number): ToastItem[] {
  return list.filter((t) => now - t.at < TOAST_LIFETIME_MS);
}
