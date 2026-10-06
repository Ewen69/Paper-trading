import type { DataType } from '../api/schemas';
import { Badge } from './Badge';

interface ProvenanceProps {
  source: string;
  asOf: Date;
  dataType?: DataType;
  stale?: boolean;
  staleReason?: string | null;
  asOfLabel?: string;
}

/** Source, timestamp, data type and staleness. Every displayed number sits next to one of these. */
export function Provenance({
  source,
  asOf,
  dataType,
  stale,
  staleReason,
  asOfLabel = 'as of',
}: ProvenanceProps) {
  return (
    <div className="space-y-1 text-xs text-slate-400">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span>
          Source: <span className="text-slate-300">{source}</span>
        </span>
        <span>·</span>
        <span>
          {asOfLabel}{' '}
          <time dateTime={asOf.toISOString()} className="text-slate-300">
            {asOf.toLocaleString()}
          </time>
        </span>
        {dataType && <Badge tone="muted">{dataType}</Badge>}
        {stale === true && <Badge tone="warn">stale</Badge>}
      </p>
      {stale === true && staleReason && <p className="text-amber-300/90">{staleReason}</p>}
    </div>
  );
}
