import { useEffect, useReducer } from 'react';

import { telemetryUrl } from './client';
import {
  telemetryEventSchema,
  type Daemon,
  type LogLine,
  type TelemetryEvent,
  type TelemetryState,
  type TelemetryTrial,
} from './schemas';

/** One line in the terminal feed: a daemon log line or a backtest trial. */
export type FeedItem =
  | { kind: 'log'; key: string; at: string; line: LogLine }
  | { kind: 'trial'; key: string; at: string; trial: TelemetryTrial };

export interface TelemetryView {
  connection: 'connecting' | 'live' | 'offline';
  state: TelemetryState | null;
  daemons: Daemon[];
  feed: FeedItem[];
  latestTrial: TelemetryTrial | null;
  lastEventAt: Date | null;
  invalidMessages: number;
  streamError: string | null;
}

export const initialTelemetry: TelemetryView = {
  connection: 'connecting',
  state: null,
  daemons: [],
  feed: [],
  latestTrial: null,
  lastEventAt: null,
  invalidMessages: 0,
  streamError: null,
};

export const MAX_FEED = 400;

const logItem = (line: LogLine): FeedItem => ({ kind: 'log', key: `l${String(line.id)}`, at: line.created_at, line });
const trialItem = (trial: TelemetryTrial): FeedItem => ({
  kind: 'trial',
  key: `t${String(trial.id)}`,
  at: trial.created_at,
  trial,
});

type Action =
  | { kind: 'connection'; value: TelemetryView['connection'] }
  | { kind: 'event'; event: TelemetryEvent; at: Date }
  | { kind: 'invalid' };

export function telemetryReducer(view: TelemetryView, action: Action): TelemetryView {
  if (action.kind === 'connection') return { ...view, connection: action.value };
  if (action.kind === 'invalid') return { ...view, invalidMessages: view.invalidMessages + 1 };
  const { event, at } = action;
  const append = (items: FeedItem[]) => [...view.feed, ...items].slice(-MAX_FEED);
  switch (event.type) {
    case 'hello': {
      const feed = [...event.log.map(logItem), ...event.trials.map(trialItem)].sort((a, b) =>
        a.at < b.at ? -1 : a.at > b.at ? 1 : 0,
      );
      return {
        ...view,
        connection: 'live',
        state: event.state,
        daemons: event.state.daemons,
        feed: feed.slice(-MAX_FEED),
        latestTrial: event.trials.at(-1) ?? null,
        lastEventAt: at,
        streamError: null,
      };
    }
    case 'trial':
      return { ...view, feed: append([trialItem(event.trial)]), latestTrial: event.trial, lastEventAt: at };
    case 'log':
      return { ...view, feed: append([logItem(event.line)]), lastEventAt: at };
    case 'daemons':
      return { ...view, daemons: event.daemons, lastEventAt: at };
    case 'state':
      return { ...view, state: event.state, daemons: event.state.daemons, lastEventAt: at };
    case 'error':
      return { ...view, streamError: event.message, lastEventAt: at };
  }
}

/** Live telemetry over a WebSocket, reconnecting with backoff. Every message is validated. */
export function useTelemetry(url?: string): TelemetryView {
  const [view, dispatch] = useReducer(telemetryReducer, initialTelemetry);

  useEffect(() => {
    if (typeof WebSocket === 'undefined') {
      dispatch({ kind: 'connection', value: 'offline' });
      return;
    }
    const target = url ?? telemetryUrl();
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      dispatch({ kind: 'connection', value: 'connecting' });
      socket = new WebSocket(target);
      socket.onmessage = (message: MessageEvent<string>) => {
        let raw: unknown;
        try {
          raw = JSON.parse(message.data);
        } catch {
          dispatch({ kind: 'invalid' });
          return;
        }
        const parsed = telemetryEventSchema.safeParse(raw);
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

  return view;
}
