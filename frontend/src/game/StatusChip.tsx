import type { AgentStatus } from '../api/schemas';
import { STATUS } from './status';

export function StatusChip({ status }: { status: AgentStatus }) {
  const s = STATUS[status];
  return (
    <span className="inline-flex items-center gap-1.5 font-pixel text-[8px] uppercase text-slate-300">
      <span className={`h-2 w-2 ${s.led} ${s.pulse ? 'led-pulse' : ''}`} aria-hidden="true" />
      {s.label}
    </span>
  );
}
