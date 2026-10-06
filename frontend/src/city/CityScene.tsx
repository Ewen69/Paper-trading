/**
 * Isometric "city" view of the live agent architecture (react-three-fiber).
 *
 * Clean diorama look: orthographic isometric camera, high-resolution antialiased render, soft
 * daylight, no fog. Each sector is a district plate with a grid of slots; agents are geometric
 * nodes on towers. A running agent pulses, streams data particles, and shows a floating log
 * of what it's testing. Empty slots wait for sub-agents.
 *
 * Text is plain DOM in one overlay owned by this component's React tree. A projector inside the
 * canvas moves each label to its 3D anchor every frame (no nested React roots).
 */
import { MapControls, RoundedBox } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { useMemo, useRef, type RefObject } from 'react';
import type { InstancedMesh, Mesh, MeshStandardMaterial } from 'three';
import { Object3D, Vector3 } from 'three';

import type { LiveAgent, Sector } from '../api/schemas';

interface Label {
  key: string;
  anchor: [number, number, number];
  kind: 'agent' | 'sector';
  agent?: LiveAgent;
  text?: string;
}
type LabelNodes = RefObject<Map<string, HTMLDivElement>>;

const SECTOR_COLORS: Record<string, string> = {
  data: '#cfe3f7',
  research: '#d6efd9',
  risk: '#f7e4c6',
  execution: '#e6dcf6',
};
const STATUS_COLORS: Record<LiveAgent['status'], string> = {
  running: '#0ea5e9',
  queued: '#f59e0b',
  error: '#ef4444',
  idle: '#f8fafc',
  watching: '#6366f1',
};
const PLATE = 7.2; // sector plate size
const GAP = 1.6; // road width between sectors
const SLOT = 2.2; // slot spacing inside a sector

function sectorOrigin(index: number): [number, number] {
  const col = index % 2;
  const row = Math.floor(index / 2);
  const step = PLATE + GAP;
  return [(col - 0.5) * step, (row - 0.5) * step];
}

function slotPosition(index: number, slots: number): [number, number] {
  const cols = Math.ceil(Math.sqrt(slots));
  const rows = Math.ceil(slots / cols);
  const col = index % cols;
  const row = Math.floor(index / cols);
  return [(col - (cols - 1) / 2) * SLOT, (row - (rows - 1) / 2) * SLOT];
}

function ParticleStream({ active, color, reduced }: { active: boolean; color: string; reduced: boolean }) {
  const mesh = useRef<InstancedMesh>(null);
  const dummy = useMemo(() => new Object3D(), []);
  const count = 24;
  useFrame(({ clock }) => {
    const m = mesh.current;
    if (!m) return;
    const t = reduced ? 0 : clock.getElapsedTime();
    for (let i = 0; i < count; i++) {
      const phase = (i / count + t * 0.35) % 1;
      const angle = phase * Math.PI * 6 + i;
      dummy.position.set(Math.cos(angle) * 0.45, 1.1 + phase * 3.2, Math.sin(angle) * 0.45);
      dummy.scale.setScalar(active ? 0.07 * (1 - phase * 0.6) : 0);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
  });
  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, count]}>
      <sphereGeometry args={[1, 8, 8]} />
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.8} />
    </instancedMesh>
  );
}

function AgentNode({ agent, position, reduced }: { agent: LiveAgent; position: [number, number]; reduced: boolean }) {
  const node = useRef<Mesh>(null);
  const material = useRef<MeshStandardMaterial>(null);
  const running = agent.status === 'running';
  const color = STATUS_COLORS[agent.status];
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    if (node.current && !reduced) node.current.rotation.y = t * (running ? 1.4 : 0.25);
    if (material.current) {
      material.current.emissiveIntensity = running && !reduced ? 0.55 + 0.45 * Math.sin(t * 4) : 0.15;
    }
  });
  return (
    <group position={[position[0], 0, position[1]]}>
      <mesh position={[0, 0.55, 0]} castShadow receiveShadow>
        <boxGeometry args={[1.1, 1.1, 1.1]} />
        <meshStandardMaterial color="#ffffff" roughness={0.6} />
      </mesh>
      <mesh ref={node} position={[0, 1.65, 0]} castShadow>
        <octahedronGeometry args={[0.5, 0]} />
        <meshStandardMaterial ref={material} color={color} emissive={color} roughness={0.3} metalness={0.1} />
      </mesh>
      <ParticleStream active={running} color={color} reduced={reduced} />
    </group>
  );
}

function EmptySlot({ position }: { position: [number, number] }) {
  return (
    <mesh position={[position[0], 0.03, position[1]]} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[0.55, 0.62, 4, 1, Math.PI / 4]} />
      <meshBasicMaterial color="#94a3b8" transparent opacity={0.6} />
    </mesh>
  );
}

function District({ sector, index, agents, reduced }: { sector: Sector; index: number; agents: LiveAgent[]; reduced: boolean }) {
  const [x, z] = sectorOrigin(index);
  const members = sector.agents
    .map((id) => agents.find((a) => a.id === id))
    .filter((a): a is LiveAgent => a !== undefined);
  return (
    <group position={[x, 0, z]}>
      <RoundedBox args={[PLATE, 0.25, PLATE]} radius={0.12} position={[0, -0.12, 0]} receiveShadow>
        <meshStandardMaterial color={SECTOR_COLORS[sector.id] ?? '#e2e8f0'} roughness={0.9} />
      </RoundedBox>
      {Array.from({ length: sector.slots }, (_, slot) => {
        const pos = slotPosition(slot, sector.slots);
        const agent = members[slot];
        return agent ? (
          <AgentNode key={agent.id} agent={agent} position={pos} reduced={reduced} />
        ) : (
          <EmptySlot key={`slot-${String(slot)}`} position={pos} />
        );
      })}
    </group>
  );
}

function labelsFor(sectors: Sector[], agents: LiveAgent[]): Label[] {
  const labels: Label[] = [];
  sectors.forEach((sector, index) => {
    const [x, z] = sectorOrigin(index);
    labels.push({
      key: `sector-${sector.id}`,
      anchor: [x - PLATE / 2 + 0.3, 0.05, z + PLATE / 2 - 0.3],
      kind: 'sector',
      text: sector.name,
    });
    sector.agents.forEach((id, slot) => {
      const agent = agents.find((a) => a.id === id);
      if (!agent || slot >= sector.slots) return;
      const [sx, sz] = slotPosition(slot, sector.slots);
      labels.push({ key: `agent-${id}`, anchor: [x + sx, 2.4, z + sz], kind: 'agent', agent });
    });
  });
  return labels;
}

/** Projects every label anchor to screen space each frame and moves its DOM node there. */
function LabelProjector({ labels, nodes }: { labels: Label[]; nodes: LabelNodes }) {
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const v = useMemo(() => new Vector3(), []);
  useFrame(() => {
    for (const label of labels) {
      const el = nodes.current.get(label.key);
      if (!el) continue;
      v.set(...label.anchor).project(camera);
      const x = ((v.x + 1) / 2) * size.width;
      const y = ((1 - v.y) / 2) * size.height;
      el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
      el.style.visibility = 'visible';
    }
  });
  return null;
}

function AgentLabel({ agent }: { agent: LiveAgent }) {
  const running = agent.status === 'running';
  const progress =
    agent.progress_total !== null && agent.progress_done !== null
      ? ` · ${String(agent.progress_done)}/${String(agent.progress_total)}`
      : '';
  return (
    <div className={`city-log ${running ? 'city-log-active' : ''}`}>
      <p className="city-log-name">{agent.name}</p>
      <p className="city-log-status">
        {agent.status}
        {progress}
      </p>
      {running && agent.message && <p className="city-log-message">{agent.message}</p>}
    </div>
  );
}

export function CityScene({ sectors, agents, reduced }: { sectors: Sector[]; agents: LiveAgent[]; reduced: boolean }) {
  const labels = useMemo(() => labelsFor(sectors, agents), [sectors, agents]);
  const nodes = useRef(new Map<string, HTMLDivElement>());
  return (
    <div className="city-stage">
      <Canvas
        shadows
        flat
        orthographic
        dpr={[1, 2]}
        gl={{ antialias: true }}
        camera={{ position: [22, 22, 22], zoom: 31, near: -100, far: 200 }}
        aria-hidden="true"
      >
        <color attach="background" args={['#eef3f8']} />
        <MapControls enableRotate={false} minZoom={16} maxZoom={100} screenSpacePanning />
        <hemisphereLight args={['#ffffff', '#dfe6ee', 1.25]} />
        <directionalLight position={[12, 20, 8]} intensity={1.8} castShadow shadow-mapSize={[2048, 2048]} />
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.26, 0]} receiveShadow>
          <planeGeometry args={[60, 60]} />
          <meshStandardMaterial color="#f4f7fa" roughness={1} />
        </mesh>
        {/* roads between districts */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.245, 0]}>
          <planeGeometry args={[GAP * 0.8, PLATE * 2 + GAP * 2]} />
          <meshStandardMaterial color="#9aa6b2" />
        </mesh>
        <mesh rotation={[-Math.PI / 2, 0, Math.PI / 2]} position={[0, -0.245, 0]}>
          <planeGeometry args={[GAP * 0.8, PLATE * 2 + GAP * 2]} />
          <meshStandardMaterial color="#9aa6b2" />
        </mesh>
        {sectors.map((sector, i) => (
          <District key={sector.id} sector={sector} index={i} agents={agents} reduced={reduced} />
        ))}
        <LabelProjector labels={labels} nodes={nodes} />
      </Canvas>
      <div className="city-labels" aria-hidden="true">
        {labels.map((label) => (
          <div
            key={label.key}
            className={`city-label ${label.kind === 'agent' ? 'city-label-agent' : 'city-label-sector'}`}
            ref={(el) => {
              if (el) nodes.current.set(label.key, el);
              else nodes.current.delete(label.key);
            }}
          >
            {label.agent ? <AgentLabel agent={label.agent} /> : <p className="city-sector-label">{label.text}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
