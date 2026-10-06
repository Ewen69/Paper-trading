import { HealthCard } from './components/HealthCard';

export function App() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Paper Trading Lab</h1>
        <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-emerald-400">
          Paper only · no real money
        </span>
      </header>
      <main>
        <HealthCard />
      </main>
    </div>
  );
}
