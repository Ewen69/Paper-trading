import { useEffect, useReducer } from 'react';

import { activityUrl } from './client';
import {
  activityEventSchema,
  type ActivityEvent,
  type Finding,
  type LiveAgent,
  type Sector,
} from './schemas';

export interface ActivityState {
  connection: 'connecting' | 'live' | 'offline';
  sectors: Sector[];
  agents: Record<string, LiveAgent>;
  findings: Finding[];
  killSwitch: { engaged: boolean; reason: string } | null;
  source: string | null;
  snapshotAt: Date | null;
  lastEventAt: Date | null;
  invalidMessages: number;
}

export const initialActivity: ActivityState = {
  connection: 'connecting',
  sectors: [],
  agents: {},
  findings: [],
  killSwitch: null,
  source: null,
  snapshotAt: null,
  lastEventAt: null,
  invalidMessages: 0,
};

const MAX_FINDINGS = 100;

type Action =
  | { kind: 'connection'; value: ActivityState['connection'] }
  | { kind: 'event'; event: ActivityEvent; at: Date }
  | { kind: 'invalid' };

export function activityReducer(state: ActivityState, action: Action): ActivityState {
  if (action.kind === 'connection') return { ...state, connection: action.value };
  if (action.kind === 'invalid') return { ...state, invalidMessages: state.invalidMessages + 1 };
  const { event, at } = action;
  switch (event.type) {
    case 'snapshot':
      return {
        ...state,
        connection: 'live',
        sectors: event.sectors,
        agents: Object.fromEntries(event.agents.map((a) => [a.id, a])),
        findings: event.findings,
        killSwitch: { engaged: event.kill_switch.engaged, reason: event.kill_switch.reason },
        source: event.source,
        snapshotAt: event.as_of,
        lastEventAt: at,
      };
    case 'agent':
      return { ...state, agents: { ...state.agents, [event.agent.id]: event.agent }, lastEventAt: at };
    case 'finding':
      return {
        ...state,
        findings: [event.finding, ...state.findings].slice(0, MAX_FINDINGS),
        lastEventAt: at,
      };
    case 'kill_switch':
      return { ...state, killSwitch: { engaged: event.engaged, reason: event.reason }, lastEventAt: at };
  }
}

/** Live agent activity over a WebSocket, reconnecting with backoff. Messages are validated. */
export function useActivity(url: string = activityUrl()): ActivityState {
  const [state, dispatch] = useReducer(activityReducer, initialActivity);

  useEffect(() => {
    if (typeof WebSocket === 'undefined') {
      dispatch({ kind: 'connection', value: 'offline' });
      return;
    }
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      dispatch({ kind: 'connection', value: 'connecting' });
      socket = new WebSocket(url);
      socket.onmessage = (message: MessageEvent<string>) => {
        let raw: unknown;
        try {
          raw = JSON.parse(message.data);
        } catch {
          dispatch({ kind: 'invalid' });
          return;
        }
        const parsed = activityEventSchema.safeParse(raw);
        if (parsed.success) {
          retry = 0;
          dispatch({ kind: 'event', event: parsed.data, at: new Date() });
        } else {
          dispatch({ kind: 'invalid' });
        }
      };
      socket.onclose = () => {
        if (closed) return;
        dispatch({ kind: 'connection', value: 'offline' });
        retry += 1;
        timer = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** Math.min(retry, 5)));
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, [url]);

  return state;
}
