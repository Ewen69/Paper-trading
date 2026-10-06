import { useMemo } from 'react';

import { SPRITE_H, SPRITE_W, spritePixels } from './sprites';

interface Props {
  agentId: string;
  locked?: boolean;
  size?: number; // rendered width in px
  animate?: boolean;
  delay?: number; // seconds, so a squad doesn't bob in lockstep
}

/** Decorative pixel character. The agent's name is always rendered as text next to it. */
export function AgentSprite({ agentId, locked = false, size = 72, animate = true, delay = 0 }: Props) {
  const pixels = useMemo(() => spritePixels(agentId, locked), [agentId, locked]);
  return (
    <svg
      viewBox={`0 0 ${String(SPRITE_W)} ${String(SPRITE_H)}`}
      width={size}
      height={(size * SPRITE_H) / SPRITE_W}
      shapeRendering="crispEdges"
      aria-hidden="true"
      className={animate && !locked ? 'sprite-idle' : undefined}
      style={{ animationDelay: `${String(delay)}s` }}
    >
      {pixels.map((p) => (
        <rect key={`${String(p.x)}-${String(p.y)}`} x={p.x} y={p.y} width={1} height={1} fill={p.fill} />
      ))}
    </svg>
  );
}
