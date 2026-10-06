import { useEffect, useState } from 'react';

import { fetchHealth, type Health } from '../api/health';
import { Provenance } from './Provenance';

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; health: Health };

export function HealthCard() {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((health) => {
        setState({ kind: 'ready', health });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof Error ? error.message : 'unknown' });
      });
    return () => {
      controller.abort();
    };
  }, []);

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-5">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Backend health
      </h2>
      {state.kind === 'loading' && <p className="text-slate-400">Checking…</p>}
      {state.kind === 'error' && (
        <div role="alert">
          <p className="font-medium text-amber-400">No data</p>
          <p className="text-sm text-slate-400">Backend unreachable or invalid: {state.message}</p>
        </div>
      )}
      {state.kind === 'ready' && (
        <div className="space-y-3">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
            <dt className="text-slate-400">Status</dt>
            <dd className="text-emerald-400">{state.health.status}</dd>
            <dt className="text-slate-400">Trading mode</dt>
            <dd className="font-semibold text-emerald-400">{state.health.tradingMode}</dd>
            <dt className="text-slate-400">Broker endpoint</dt>
            <dd className="font-mono">{state.health.brokerBaseUrl}</dd>
            <dt className="text-slate-400">Paper API keys</dt>
            <dd>
              {state.health.brokerCredentialsConfigured
                ? 'present (not yet verified)'
                : 'not configured'}
            </dd>
            <dt className="text-slate-400">Version</dt>
            <dd>{state.health.version}</dd>
          </dl>
          <Provenance source={state.health.source} asOf={state.health.asOf} />
        </div>
      )}
    </section>
  );
}
