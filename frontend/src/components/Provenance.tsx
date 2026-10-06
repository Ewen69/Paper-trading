interface ProvenanceProps {
  source: string;
  asOf: Date;
}

/** Source + timestamp line that accompanies every displayed value. */
export function Provenance({ source, asOf }: ProvenanceProps) {
  return (
    <p className="text-xs text-slate-400">
      Source: <span className="text-slate-300">{source}</span> · as of{' '}
      <time dateTime={asOf.toISOString()} className="text-slate-300">
        {asOf.toLocaleString()}
      </time>
    </p>
  );
}
