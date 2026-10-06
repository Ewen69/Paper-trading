/** Number formatting shared by results screens. Missing values render as an em dash, never 0. */

export const pct = (x: number | null | undefined, digits = 1): string =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(digits)}%`;

export const num = (x: number | null | undefined, digits = 2): string =>
  x === null || x === undefined ? '—' : x.toFixed(digits);

export const usd = (x: number | null | undefined): string =>
  x === null || x === undefined
    ? '—'
    : x.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

export const range = (
  ci: { low: number; high: number } | null,
  fmt: (x: number) => string,
): string => (ci ? `${fmt(ci.low)} to ${fmt(ci.high)}` : 'n/a (too little data)');
