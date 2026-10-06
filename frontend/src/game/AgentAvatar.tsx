import { useEffect, useState } from 'react';

interface Props {
  agentId: string;
  locked?: boolean;
  size?: number;
}

const INITIALS: Record<string, string> = {
  scout: 'DS',
  quant: 'Q',
  auditor: 'A',
  risk: 'RO',
  trader: 'PT',
  accountant: 'AC',
  analyst: 'AN',
  lead: 'LR',
};

/**
 * Decorative 3D portrait of an agent; the agent's name is always rendered as text nearby.
 * The 3D code loads on demand, so an initials badge shows first (and stays without WebGL).
 */
export function AgentAvatar({ agentId, locked = false, size = 48 }: Props) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void import('./three/portraits').then((m) => {
      if (alive) setUrl(m.portrait(agentId, locked));
    });
    return () => {
      alive = false;
    };
  }, [agentId, locked]);

  if (url) {
    return (
      <img
        src={url}
        alt=""
        aria-hidden="true"
        width={size}
        height={size}
        className={`avatar-3d ${locked ? 'opacity-50 grayscale' : ''}`}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      className="avatar-fallback"
      style={{ width: size, height: size, fontSize: size * 0.32 }}
    >
      {locked ? '?' : (INITIALS[agentId] ?? agentId.slice(0, 2).toUpperCase())}
    </span>
  );
}
