import { useMemo, useState, type KeyboardEvent, type MouseEvent } from 'react';

import type { Report } from '../api/schemas';
import { usd } from '../format';

type Curve = Report['curve'];

// Categorical slots 1-2 of the validated reference palette, dark steps (validated on #0f172a).
const SERIES = [
  { key: 'strategy', color: '#3987e5' },
  { key: 'benchmark', color: '#d95926' },
] as const;

const W = 720;
const H = 260;
const PAD = { top: 12, right: 92, bottom: 26, left: 64 };

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
  x.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  });

interface Props {
  curve: Curve;
  labels: { strategy: string; benchmark: string };
}

export function EquityChart({ curve, labels }: Props) {
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const values = curve.flatMap((p) => [p.strategy, p.benchmark]).filter((v) => v !== null);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const ticks = niceTicks(min, max);
    const lo = Math.min(min, ticks[0] ?? min);
    const hi = Math.max(max, ticks[ticks.length - 1] ?? max);
    const x = (i: number) =>
      PAD.left + (curve.length > 1 ? (i / (curve.length - 1)) * (W - PAD.left - PAD.right) : 0);
    const y = (v: number) => PAD.top + (1 - (v - lo) / (hi - lo || 1)) * (H - PAD.top - PAD.bottom);
    const path = (key: 'strategy' | 'benchmark') => {
      let d = '';
      let pen = false;
      curve.forEach((p, i) => {
        const v = p[key];
        if (v === null) {
          pen = false;
          return;
        }
        d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    };
    const years: { i: number; label: string }[] = [];
    curve.forEach((p, i) => {
      const year = p.day.slice(0, 4);
      if (i === 0 || curve[i - 1]?.day.slice(0, 4) !== year) years.push({ i, label: year });
    });
    const step = Math.ceil(years.length / 8);
    return {
      x,
      y,
      ticks: ticks.filter((t) => t >= lo && t <= hi),
      paths: { strategy: path('strategy'), benchmark: path('benchmark') },
      years: years.filter((_, k) => k % step === 0),
    };
  }, [curve]);

  if (curve.length === 0) return <p className="text-sm text-slate-400">No data</p>;

  const last = curve[curve.length - 1];
  const point = hover === null ? null : curve[hover];

  const onMove = (event: MouseEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const fraction = (event.clientX - box.left) / box.width;
    setHover(Math.max(0, Math.min(curve.length - 1, Math.round(fraction * (curve.length - 1)))));
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const delta = event.key === 'ArrowLeft' ? -1 : 1;
    setHover((h) => Math.max(0, Math.min(curve.length - 1, (h ?? curve.length - 1) + delta)));
  };

  return (
    <figure className="space-y-2">
      <figcaption className="flex flex-wrap items-center gap-4 text-xs text-slate-300">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ backgroundColor: s.color }} />
            {labels[s.key]}
          </span>
        ))}
        <span className="text-slate-500">Equity after costs · hover or use ←/→</span>
      </figcaption>
      <div
        className="relative"
        tabIndex={0}
        role="group"
        aria-label="Equity curve, strategy versus benchmark"
        onKeyDown={onKey}
        onBlur={() => {
          setHover(null);
        }}
      >
        <svg viewBox={`0 0 ${String(W)} ${String(H)}`} className="w-full" role="img">
          {geometry.ticks.map((t) => (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={geometry.y(t)}
                y2={geometry.y(t)}
                stroke="#1e293b"
                strokeWidth={1}
              />
              <text
                x={PAD.left - 8}
                y={geometry.y(t)}
                dy="0.32em"
                textAnchor="end"
                fontSize={11}
                fill="#94a3b8"
              >
                {compactUsd(t)}
              </text>
            </g>
          ))}
          {geometry.years.map(({ i, label }) => (
            <text
              key={label}
              x={geometry.x(i)}
              y={H - 8}
              textAnchor="middle"
              fontSize={11}
              fill="#94a3b8"
            >
              {label}
            </text>
          ))}
          {SERIES.map((s) => (
            <path
              key={s.key}
              d={geometry.paths[s.key]}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
          {last &&
            SERIES.map((s) => {
              const v = last[s.key];
              if (v === null) return null;
              const other = last[s.key === 'strategy' ? 'benchmark' : 'strategy'];
              // Keep the two end labels from overlapping: push them apart when close.
              let y = geometry.y(v);
              if (other !== null && Math.abs(y - geometry.y(other)) < 14) y += v >= other ? -7 : 7;
              return (
                <g key={s.key}>
                  <circle cx={geometry.x(curve.length - 1)} cy={geometry.y(v)} r={4} fill={s.color} stroke="#0f172a" strokeWidth={2} />
                  <text x={W - PAD.right + 8} y={y} dy="0.32em" fontSize={11} fill="#cbd5e1">
                    {labels[s.key]}
                  </text>
                </g>
              );
            })}
          {point && hover !== null && (
            <g>
              <line
                x1={geometry.x(hover)}
                x2={geometry.x(hover)}
                y1={PAD.top}
                y2={H - PAD.bottom}
                stroke="#64748b"
                strokeWidth={1}
              />
              {SERIES.map((s) => {
                const v = point[s.key];
                return v === null ? null : (
                  <circle
                    key={s.key}
                    cx={geometry.x(hover)}
                    cy={geometry.y(v)}
                    r={4}
                    fill={s.color}
                    stroke="#0f172a"
                    strokeWidth={2}
                  />
                );
              })}
            </g>
          )}
          <rect
            x={PAD.left}
            y={0}
            width={W - PAD.left - PAD.right}
            height={H}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => {
              setHover(null);
            }}
          />
        </svg>
        {point && hover !== null && (
          <div
            role="status"
            className="pointer-events-none absolute top-2 rounded-lg border border-slate-700 bg-slate-950/95 px-3 py-2 text-xs shadow-lg"
            style={
              geometry.x(hover) / W > 0.55
                ? { right: `${String(((W - geometry.x(hover)) / W) * 100 + 2)}%` }
                : { left: `${String((geometry.x(hover) / W) * 100 + 2)}%` }
            }
          >
            <p className="mb-1 font-medium text-slate-200">{point.day}</p>
            {SERIES.map((s) => (
              <p key={s.key} className="flex items-center gap-2 text-slate-300">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: s.color }} />
                {labels[s.key]}: <span className="font-mono text-slate-100">{usd(point[s.key])}</span>
              </p>
            ))}
          </div>
        )}
      </div>
      <details className="text-xs text-slate-400">
        <summary className="cursor-pointer">Table view (month-end equity)</summary>
        <table className="mt-2 w-full max-w-md">
          <thead>
            <tr className="text-left text-slate-500">
              <th className="py-1 font-normal">Month end</th>
              <th className="py-1 font-normal">{labels.strategy}</th>
              <th className="py-1 font-normal">{labels.benchmark}</th>
            </tr>
          </thead>
          <tbody className="font-mono text-slate-300">
            {curve
              .filter((p, i) => curve[i + 1]?.day.slice(0, 7) !== p.day.slice(0, 7))
              .map((p) => (
                <tr key={p.day}>
                  <td className="py-0.5">{p.day}</td>
                  <td className="py-0.5">{usd(p.strategy)}</td>
                  <td className="py-0.5">{usd(p.benchmark)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
