import type { Issue } from '../api/schemas';
import { Badge } from './Badge';
import { severityTone } from './tones';

export function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) {
    return <p className="text-sm text-slate-400">No quality issues found.</p>;
  }
  return (
    <ul className="space-y-2">
      {issues.map((issue) => (
        <li
          key={`${issue.check}-${issue.symbol}`}
          className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-sm"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={severityTone(issue.severity)}>{issue.severity}</Badge>
            <span className="font-mono text-slate-200">{issue.check}</span>
            <span className="text-slate-400">
              {issue.symbol} · ×{issue.count.toLocaleString()} ·{' '}
              {issue.first_date === issue.last_date
                ? issue.first_date
                : `${issue.first_date} → ${issue.last_date}`}
            </span>
          </div>
          <p className="mt-1 text-slate-400">{issue.detail}</p>
        </li>
      ))}
    </ul>
  );
}
