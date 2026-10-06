/** Net-worth history (one series) and the projection range (low/high band). Hand-rolled SVG. */
import { useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react';

import type { NetWorthPoint, Projection } from '../api/schemas';
import { usd } from '../format';

// Slots 1-2 of the validated reference palette (dark steps, checked on #0f172a), as in EquityChart.
const BLUE = '#3987e5';
const ORANGE = '#d95926';
const GRID = '#1e293b';
const INK = '#94a3b8';

const W = 720;
const H = 240;
const PAD = { top: 12, right: 24, bottom: 26, left: 72 };

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) ticks.push(t);
  return ticks;
}

const compactUsd = (x: number) =>
  x.toLocaleString(undefined, { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 });

function yScale(values: number[]) {
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const ticks = niceTicks(min, max);
  const lo = Math.min(min, ticks[0] ?? min);
  const hi = Math.max(max, ticks[ticks.length - 1] ?? max);
  const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo || 1)) * (H - PAD.top - PAD.bottom);
  return { y, ticks };
}

/** Pointer + arrow-key hover over n points, shared by both charts. */
function useHover(n: number, xs: number[]) {
  const [hover, setHover] = useState<number | null>(null);
  const onMove = (event: MouseEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const svgX = ((event.clientX - box.left) / box.width) * (W - PAD.left - PAD.right) + PAD.left;
    let best = 0;
    xs.forEach((x, i) => {
      if (Math.abs(x - svgX) < Math.abs((xs[best] ?? 0) - svgX)) best = i;
    });
    setHover(best);
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const delta = event.key === 'ArrowLeft' ? -1 : 1;
    setHover((h) => Math.max(0, Math.min(n - 1, (h ?? n - 1) + delta)));
  };
  return { hover, setHover, onMove, onKey };
}

function Grid({ ticks, y }: { ticks: number[]; y: (v: number) => number }) {
  return (
    <>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? '#475569' : GRID} strokeWidth={1} />
          <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill={INK}>
            {compactUsd(t)}
          </text>
        </g>
      ))}
    </>
  );
}

function Tooltip({ x, children }: { x: number; children: React.ReactNode }) {
  const left = `${String((x / W) * 100)}%`;
  const flip = x > W * 0.6;
  return (
    <div
      className="pointer-events-none absolute top-2 z-10 min-w-44 border border-slate-600 bg-slate-950/95 px-3 py-2 text-xs text-slate-200 shadow-lg"
      style={flip ? { right: `calc(100% - ${left} + 12px)` } : { left: `calc(${left} + 12px)` }}
    >
      {children}
    </div>
  );
}

export function HistoryChart({ points }: { points: NetWorthPoint[] }) {
  const geometry = useMemo(() => {
    const times = points.map((p) => Date.parse(p.as_of));
    const t0 = times[0] ?? 0;
    const t1 = times[times.length - 1] ?? 0;
    const xs = times.map((t) =>
      points.length > 1 ? PAD.left + ((t - t0) / (t1 - t0 || 1)) * (W - PAD.left - PAD.right) : (W + PAD.left - PAD.right) / 2,
    );
    const { y, ticks } = yScale(points.map((p) => p.net_worth));
    const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${(xs[i] ?? 0).toFixed(1)},${y(p.net_worth).toFixed(1)}`).join('');
    return { xs, y, ticks, d };
  }, [points]);
  const { hover, setHover, onMove, onKey } = useHover(points.length, geometry.xs);

  if (points.length === 0) return <p className="text-sm text-slate-400">No data: enter a balance to start the history.</p>;
  const point = hover === null ? null : points[hover];
  const first = points[0];
  const last = points[points.length - 1];

  return (
    <figure className="space-y-2">
      <figcaption className="text-xs text-slate-500">
        Net worth on each date you entered a balance · hover or use ←/→
      </figcaption>
      <div
        className="relative"
        tabIndex={0}
        role="group"
        aria-label="Net worth history"
        onKeyDown={onKey}
        onBlur={() => {
          setHover(null);
        }}
      >
        <svg viewBox={`0 0 ${String(W)} ${String(H)}`} className="w-full" role="img" aria-label="Net worth over time">
          <Grid ticks={geometry.ticks} y={geometry.y} />
          {first && last && (
            <>
              <text x={PAD.left} y={H - 8} fontSize={11} fill={INK}>{first.as_of}</text>
              {points.length > 1 && (
                <text x={W - PAD.right} y={H - 8} textAnchor="end" fontSize={11} fill={INK}>{last.as_of}</text>
              )}
            </>
          )}
          <path d={geometry.d} fill="none" stroke={BLUE} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {points.map((p, i) => (
            <circle key={p.as_of} cx={geometry.xs[i]} cy={geometry.y(p.net_worth)} r={hover === i ? 5 : 4} fill={BLUE} stroke="#0f172a" strokeWidth={2} />
          ))}
          {hover !== null && (
            <line x1={geometry.xs[hover]} x2={geometry.xs[hover]} y1={PAD.top} y2={H - PAD.bottom} stroke="#64748b" strokeWidth={1} />
          )}
          <rect
            x={PAD.left - 8}
            y={PAD.top}
            width={W - PAD.left - PAD.right + 16}
            height={H - PAD.top - PAD.bottom}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => {
              setHover(null);
            }}
          />
        </svg>
        {point && hover !== null && (
          <Tooltip x={geometry.xs[hover] ?? 0}>
            <p className="font-semibold">{point.as_of}</p>
            <p>Net worth {usd(point.net_worth)}</p>
            <p className="text-slate-400">Assets {usd(point.assets)} · Liabilities {usd(point.liabilities)}</p>
            {point.carried_forward > 0 && (
              <p className="text-slate-400">{point.carried_forward} value(s) carried from earlier dates</p>
            )}
            {point.missing.length > 0 && <p className="text-amber-300">Not entered yet (counted as 0): {point.missing.join(', ')}</p>}
          </Tooltip>
        )}
      </div>
    </figure>
  );
}

export function ProjectionChart({ projection }: { projection: Projection }) {
  const pts = projection.points;
  const geometry = useMemo(() => {
    const xs = pts.map((p) => PAD.left + (p.year / (projection.years || 1)) * (W - PAD.left - PAD.right));
    const { y, ticks } = yScale(pts.flatMap((p) => [p.low, p.high]));
    const line = (key: 'low' | 'high') =>
      pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${(xs[i] ?? 0).toFixed(1)},${y(p[key]).toFixed(1)}`).join('');
    const band =
      line('high') +
      [...pts]
        .reverse()
        .map((p, k) => `L${(xs[pts.length - 1 - k] ?? 0).toFixed(1)},${y(p.low).toFixed(1)}`)
        .join('') +
      'Z';
    return { xs, y, ticks, low: line('low'), high: line('high'), band };
  }, [pts, projection.years]);
  const { hover, setHover, onMove, onKey } = useHover(pts.length, geometry.xs);
  const point = hover === null ? null : pts[hover];
  const rate = (r: number) => `${(r * 100).toFixed(1)}%`;
  const series = [
    { key: 'high' as const, color: BLUE, label: `${rate(projection.high_rate)} a year` },
    { key: 'low' as const, color: ORANGE, label: `${rate(projection.low_rate)} a year` },
  ];

  return (
    <figure className="space-y-2">
      <figcaption className="flex flex-wrap items-center gap-4 text-xs text-slate-300">
        {series.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ backgroundColor: s.color }} />
            {s.label}
          </span>
        ))}
        <span className="text-slate-500">Assumption range, not a forecast · hover or use ←/→</span>
      </figcaption>
      <div
        className="relative"
        tabIndex={0}
        role="group"
        aria-label="Projection range"
        onKeyDown={onKey}
        onBlur={() => {
          setHover(null);
        }}
      >
        <svg viewBox={`0 0 ${String(W)} ${String(H)}`} className="w-full" role="img" aria-label="Projected net worth range by year">
          <Grid ticks={geometry.ticks} y={geometry.y} />
          {pts.map((p, i) =>
            p.year % Math.max(1, Math.ceil(projection.years / 10)) === 0 ? (
              <text key={p.year} x={geometry.xs[i]} y={H - 8} textAnchor="middle" fontSize={11} fill={INK}>
                {p.year === 0 ? 'now' : `+${String(p.year)}y`}
              </text>
            ) : null,
          )}
          <path d={geometry.band} fill={BLUE} fillOpacity={0.12} stroke="none" />
          {series.map((s) => (
            <path key={s.key} d={geometry[s.key]} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
          ))}
          {hover !== null && point && (
            <>
              <line x1={geometry.xs[hover]} x2={geometry.xs[hover]} y1={PAD.top} y2={H - PAD.bottom} stroke="#64748b" strokeWidth={1} />
              {series.map((s) => (
                <circle key={s.key} cx={geometry.xs[hover]} cy={geometry.y(point[s.key])} r={4} fill={s.color} stroke="#0f172a" strokeWidth={2} />
              ))}
            </>
          )}
          <rect
            x={PAD.left - 8}
            y={PAD.top}
            width={W - PAD.left - PAD.right + 16}
            height={H - PAD.top - PAD.bottom}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => {
              setHover(null);
            }}
          />
        </svg>
        {point && hover !== null && (
          <Tooltip x={geometry.xs[hover] ?? 0}>
            <p className="font-semibold">{point.year === 0 ? 'Today' : `Year ${String(point.year)}`}</p>
            <p>At {rate(projection.high_rate)}: {usd(point.high)}</p>
            <p>At {rate(projection.low_rate)}: {usd(point.low)}</p>
          </Tooltip>
        )}
      </div>
    </figure>
  );
}
