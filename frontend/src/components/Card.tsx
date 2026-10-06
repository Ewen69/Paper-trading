import type { ReactNode } from 'react';

export function Card({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function NoData({ message }: { message: string }) {
  return (
    <div role="alert">
      <p className="font-medium text-amber-400">No data</p>
      <p className="text-sm text-slate-400">{message}</p>
    </div>
  );
}
