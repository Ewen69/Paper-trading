import type { ReactNode } from 'react';

/** A game window. Titles use the pixel font; content stays in a readable face. */
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
    <section className="panel p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display font-bold tracking-wide text-[13px] uppercase leading-relaxed tracking-wider text-sky-300">
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function NoData({ message }: { message: string }) {
  return (
    <div role="alert">
      <p className="font-display font-bold tracking-wide text-[13px] uppercase text-amber-400">No data</p>
      <p className="mt-2 text-sm text-slate-400">{message}</p>
    </div>
  );
}
