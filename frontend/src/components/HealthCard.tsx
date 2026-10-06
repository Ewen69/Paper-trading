import { fetchHealth } from '../api/client';
import { useApi } from '../api/useApi';
import { Card, NoData } from './Card';
import { Provenance } from './Provenance';

export function HealthCard() {
  const [state] = useApi(fetchHealth);

  return (
    <Card title="Backend health">
      {state.kind === 'loading' && <p className="text-slate-400">Checking…</p>}
      {state.kind === 'error' && (
        <NoData message={`Backend unreachable or invalid: ${state.message}`} />
      )}
      {state.kind === 'ready' && (
        <div className="space-y-3">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
            <dt className="text-slate-400">Status</dt>
            <dd className="text-emerald-400">{state.data.status}</dd>
            <dt className="text-slate-400">Trading mode</dt>
            <dd className="font-semibold text-emerald-400">{state.data.trading_mode}</dd>
            <dt className="text-slate-400">Broker endpoint</dt>
            <dd className="break-all font-mono">{state.data.broker_base_url}</dd>
            <dt className="text-slate-400">Paper API keys</dt>
            <dd>
              {state.data.broker_credentials_configured
                ? 'present (verified on Data Health)'
                : 'not configured'}
            </dd>
            <dt className="text-slate-400">Version</dt>
            <dd>{state.data.version}</dd>
          </dl>
          <Provenance source={state.data.source} asOf={state.data.as_of} />
        </div>
      )}
    </Card>
  );
}
