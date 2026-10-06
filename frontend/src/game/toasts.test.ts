import { describe, expect, it } from 'vitest';

import type { GameEvent, LogLine } from '../api/schemas';
import { addToast, expire, MAX_TOASTS, MERGE_WINDOW_MS, TOAST_LIFETIME_MS, toastForLog, toastsForGameEvents } from './toasts';

const line = (id: number, kind: string, message = `line ${String(id)}`, level: LogLine['level'] = 'info'): LogLine => ({
  id,
  created_at: '2026-10-06T15:00:00+00:00',
  daemon: 'optimizer',
  level,
  message,
  kind,
});

const event = (xp: number, note: string | null, text = 'run'): GameEvent => ({
  at: new Date('2026-10-06T15:00:00Z'),
  agent: 'quant',
  kind: 'run_in-sample',
  text,
  xp,
  xp_note: note,
  source: 'Backtest run log',
});

describe('toast policy', () => {
  it('only major kinds toast', () => {
    expect(toastForLog(line(1, 'info'), 0)).toBeNull();
    expect(toastForLog(line(2, 'active_best'), 0)?.title).toBe('New Active Best');
    expect(toastForLog(line(3, 'risk'), 0)?.tone).toBe('bad');
    expect(toastForLog(line(4, 'data_sync', 'x', 'warn'), 0)?.tone).toBe('warn');
  });

  it('merges repeats of a kind inside the window and keeps at most three', () => {
    let list = [toastForLog(line(1, 'risk'), 0)].flatMap((t) => t ?? []);
    for (let i = 2; i <= 5; i++) {
      const t = toastForLog(line(i, 'risk', `blocked ${String(i)}`), i * 1000);
      if (t) list = addToast(list, t);
    }
    expect(list).toHaveLength(1);
    expect(list[0]?.count).toBe(5);
    expect(list[0]?.text).toBe('blocked 5');
    const late = toastForLog(line(9, 'risk'), 5000 + MERGE_WINDOW_MS + 1);
    if (late) list = addToast(list, late);
    expect(list).toHaveLength(2); // outside the window: a new toast
    for (const kind of ['active_best', 'budget', 'order']) {
      const t = toastForLog(line(20, kind), 30_000);
      if (t) list = addToast(list, t);
    }
    expect(list).toHaveLength(MAX_TOASTS);
    expect(expire(list, 30_000 + TOAST_LIFETIME_MS)).toEqual([]);
  });

  it('skips zero-XP game events, keeps holdout warnings, and collapses bursts', () => {
    expect(toastsForGameEvents([event(0, 'XP only for the first in-sample run per symbol')], 0)).toEqual([]);
    const [warning] = toastsForGameEvents([event(0, 'repeat look at the holdout: no XP')], 0);
    expect(warning?.text).toBe('run repeat look at the holdout: no XP');
    const burst = toastsForGameEvents([1, 2, 3, 4, 5].map((i) => event(10, null, `run ${String(i)}`)), 0);
    expect(burst).toHaveLength(1);
    expect(burst[0]?.title).toBe('+50 XP');
    expect(burst[0]?.text).toBe('5 research events logged. Details are in the HQ mission log.');
  });
});
