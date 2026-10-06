import { useEffect, useState } from 'react';

import { HealthCard } from './components/HealthCard';
import { DataHealthPage } from './pages/DataHealthPage';

const PAGES = [
  { id: 'home', label: 'Home' },
  { id: 'data-health', label: 'Data Health' },
] as const;
type PageId = (typeof PAGES)[number]['id'];

const pageFromHash = (): PageId => (window.location.hash === '#data-health' ? 'data-health' : 'home');

export function App() {
  const [page, setPage] = useState<PageId>(pageFromHash);

  useEffect(() => {
    const onHash = () => {
      setPage(pageFromHash());
    };
    window.addEventListener('hashchange', onHash);
    return () => {
      window.removeEventListener('hashchange', onHash);
    };
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Paper Trading Lab</h1>
        <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-emerald-400">
          Paper only · no real money
        </span>
      </header>
      <nav className="mb-6 flex gap-1 border-b border-slate-800" aria-label="Main">
        {PAGES.map((p) => (
          <a
            key={p.id}
            href={`#${p.id}`}
            aria-current={page === p.id ? 'page' : undefined}
            className={`-mb-px border-b-2 px-4 py-2 text-sm ${
              page === p.id
                ? 'border-sky-400 text-slate-100'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {p.label}
          </a>
        ))}
      </nav>
      <main>{page === 'home' ? <HealthCard /> : <DataHealthPage />}</main>
    </div>
  );
}
