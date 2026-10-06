/**
 * Pixel-art agent sprites: a shared 12x14 body plus per-agent palette and accessory pixels.
 * Characters: K outline, H hat/hair, S skin, E eyes, C coat, D coat shade, W shirt,
 * A accessory, G glass/lens, '.' transparent.
 */

export const SPRITE_W = 12;
export const SPRITE_H = 14;

const BODY = [
  '....KKKK....',
  '...KHHHHK...',
  '..KHHHHHHK..',
  '..KSSSSSSK..',
  '..KSESSESK..',
  '..KSSSSSSK..',
  '...KSSSSK...',
  '..KCCCCCCK..',
  '.KCCCWWCCCK.',
  '.KCCCCCCCCK.',
  '.KSKCCCCKSK.',
  '..KKDDDDKK..',
  '...KDKKDK...',
  '...KK..KK...',
];

type Palette = Record<string, string>;

interface SpriteSpec {
  palette: Palette;
  overlay: [x: number, y: number, ch: string][];
}

const BASE: Palette = { K: '#0b1020', E: '#0b1020', W: '#e2e8f0', G: '#7dd3fc' };

export const SPRITES: Record<string, SpriteSpec> = {
  scout: {
    // Green field cap with a brim; binoculars held at the chest.
    palette: { ...BASE, H: '#22c55e', S: '#f1c27d', C: '#4d7c0f', D: '#365314', A: '#1f2937' },
    overlay: [
      [9, 2, 'H'],
      [10, 2, 'K'],
      [4, 8, 'A'],
      [5, 8, 'G'],
      [6, 8, 'G'],
      [7, 8, 'A'],
    ],
  },
  quant: {
    // Lab coat, glasses, a pen in the pocket.
    palette: { ...BASE, H: '#94a3b8', S: '#8d5524', C: '#e2e8f0', D: '#94a3b8', W: '#38bdf8', A: '#2563eb' },
    overlay: [
      [3, 4, 'G'],
      [5, 4, 'G'],
      [6, 4, 'G'],
      [8, 4, 'G'],
      [7, 9, 'A'],
    ],
  },
  auditor: {
    // Dark suit, white hair, a gold-rimmed magnifying glass in the right hand.
    palette: { ...BASE, H: '#e5e7eb', S: '#c68642', C: '#334155', D: '#1e293b', W: '#f8fafc', A: '#facc15' },
    overlay: [
      [10, 6, 'A'],
      [9, 7, 'A'],
      [10, 7, 'G'],
      [11, 7, 'A'],
      [10, 8, 'A'],
      [9, 9, 'A'],
    ],
  },
  risk: {
    palette: { ...BASE, H: '#1e3a8a', S: '#c68642', C: '#1e40af', D: '#172554', A: '#facc15' },
    overlay: [
      [9, 8, 'A'],
      [10, 8, 'A'],
      [9, 9, 'A'],
      [10, 9, 'A'],
      [10, 10, 'A'],
    ],
  },
  trader: {
    palette: { ...BASE, H: '#78350f', S: '#e0ac69', C: '#0369a1', D: '#0c4a6e', A: '#f97316' },
    overlay: [
      [2, 3, 'A'],
      [9, 3, 'A'],
      [2, 4, 'A'],
      [3, 5, 'A'],
    ],
  },
  accountant: {
    palette: { ...BASE, H: '#a16207', S: '#f1c27d', C: '#7e22ce', D: '#581c87', A: '#22d3ee' },
    overlay: [
      [3, 1, 'A'],
      [4, 1, 'A'],
      [5, 1, 'A'],
      [6, 1, 'A'],
      [7, 1, 'A'],
      [8, 1, 'A'],
    ],
  },
  analyst: {
    palette: { ...BASE, H: '#111827', S: '#8d5524', C: '#0f766e', D: '#134e4a', A: '#f43f5e' },
    overlay: [
      [5, 8, 'A'],
      [6, 8, 'A'],
      [5, 9, 'A'],
    ],
  },
  lead: {
    palette: { ...BASE, H: '#f59e0b', S: '#e0ac69', C: '#b45309', D: '#78350f', A: '#fde047' },
    overlay: [
      [4, 0, 'A'],
      [7, 0, 'A'],
      [3, 0, 'A'],
      [8, 0, 'A'],
    ],
  },
};

const SILHOUETTE: Palette = { K: '#1e293b' };
const SILHOUETTE_FILL = '#334155';

export interface Pixel {
  x: number;
  y: number;
  fill: string;
}

/** Resolve a sprite to colored pixels. Locked agents render as a silhouette. */
export function spritePixels(id: string, locked: boolean): Pixel[] {
  const spec = SPRITES[id] ?? SPRITES['scout'];
  if (!spec) return [];
  const grid = BODY.map((row) => row.split(''));
  for (const [x, y, ch] of spec.overlay) {
    const row = grid[y];
    if (row) row[x] = ch;
  }
  const pixels: Pixel[] = [];
  grid.forEach((row, y) => {
    row.forEach((ch, x) => {
      if (ch === '.') return;
      const fill = locked ? (SILHOUETTE[ch] ?? SILHOUETTE_FILL) : spec.palette[ch];
      if (fill) pixels.push({ x, y, fill });
    });
  });
  return pixels;
}
