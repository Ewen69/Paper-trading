/**
 * Low-poly, PS2-era agent models built from a few faceted primitives.
 * Flat-shaded Lambert materials give the chunky late-90s/early-2000s console look.
 */
import * as THREE from 'three';

interface Look {
  coat: number;
  skin: number;
  hair: number;
  accent: number;
}

const LOOKS: Record<string, Look> = {
  scout: { coat: 0x4d7c0f, skin: 0xf1c27d, hair: 0x22c55e, accent: 0x1f2937 },
  quant: { coat: 0xe2e8f0, skin: 0x8d5524, hair: 0x94a3b8, accent: 0x2563eb },
  auditor: { coat: 0x334155, skin: 0xc68642, hair: 0xe5e7eb, accent: 0xfacc15 },
  risk: { coat: 0x1e40af, skin: 0xc68642, hair: 0x1e3a8a, accent: 0xfacc15 },
  trader: { coat: 0x0369a1, skin: 0xe0ac69, hair: 0x78350f, accent: 0xf97316 },
  accountant: { coat: 0x7e22ce, skin: 0xf1c27d, hair: 0xa16207, accent: 0x22d3ee },
  analyst: { coat: 0x0f766e, skin: 0x8d5524, hair: 0x111827, accent: 0xf43f5e },
  lead: { coat: 0xb45309, skin: 0xe0ac69, hair: 0xf59e0b, accent: 0xfde047 },
};

const DEFAULT_LOOK: Look = { coat: 0x475569, skin: 0xd6a77a, hair: 0x334155, accent: 0x38bdf8 };

function material(color: number, locked: boolean): THREE.Material {
  if (locked) {
    return new THREE.MeshLambertMaterial({
      color: 0x1e293b,
      transparent: true,
      opacity: 0.55,
      flatShading: true,
    });
  }
  return new THREE.MeshLambertMaterial({ color, flatShading: true });
}

function part(
  geometry: THREE.BufferGeometry,
  color: number,
  locked: boolean,
  position: [number, number, number],
  rotation: [number, number, number] = [0, 0, 0],
): THREE.Mesh {
  const mesh = new THREE.Mesh(geometry, material(color, locked));
  mesh.position.set(...position);
  mesh.rotation.set(...rotation);
  return mesh;
}

function accessories(id: string, look: Look, locked: boolean): THREE.Object3D[] {
  const a = look.accent;
  switch (id) {
    case 'scout': // cap brim + binoculars
      return [
        part(new THREE.BoxGeometry(0.42, 0.05, 0.28), look.hair, locked, [0, 1.92, 0.22]),
        part(new THREE.CylinderGeometry(0.07, 0.07, 0.2, 6), a, locked, [-0.09, 1.2, 0.42], [Math.PI / 2, 0, 0]),
        part(new THREE.CylinderGeometry(0.07, 0.07, 0.2, 6), a, locked, [0.09, 1.2, 0.42], [Math.PI / 2, 0, 0]),
      ];
    case 'quant': // glasses + clipboard
      return [
        part(new THREE.TorusGeometry(0.07, 0.015, 4, 8), a, locked, [-0.1, 1.72, 0.28]),
        part(new THREE.TorusGeometry(0.07, 0.015, 4, 8), a, locked, [0.1, 1.72, 0.28]),
        part(new THREE.BoxGeometry(0.3, 0.38, 0.03), 0xf8fafc, locked, [0.42, 1.05, 0.25], [0, -0.4, 0.1]),
      ];
    case 'auditor': // magnifying glass
      return [
        part(new THREE.TorusGeometry(0.14, 0.03, 4, 10), a, locked, [0.55, 1.45, 0.25]),
        part(new THREE.CircleGeometry(0.12, 10), 0x7dd3fc, locked, [0.55, 1.45, 0.25]),
        part(new THREE.CylinderGeometry(0.025, 0.025, 0.3, 5), 0x78350f, locked, [0.55, 1.18, 0.25]),
      ];
    case 'risk': // shield
      return [part(new THREE.BoxGeometry(0.42, 0.55, 0.06), a, locked, [-0.55, 1.1, 0.2], [0, 0.4, 0])];
    case 'trader': // headset
      return [part(new THREE.TorusGeometry(0.33, 0.035, 4, 10, Math.PI), a, locked, [0, 1.75, 0])];
    case 'accountant': // visor
      return [part(new THREE.BoxGeometry(0.5, 0.07, 0.32), a, locked, [0, 1.93, 0.15])];
    case 'analyst': // tie
      return [part(new THREE.BoxGeometry(0.09, 0.4, 0.04), a, locked, [0, 1.15, 0.4])];
    case 'lead': // crown
      return [part(new THREE.CylinderGeometry(0.24, 0.27, 0.16, 6, 1, true), a, locked, [0, 2.0, 0])];
    default:
      return [];
  }
}

/** A complete agent, about 2.1 units tall, standing at the origin and facing +z. */
export function buildAgent(id: string, locked: boolean): THREE.Group {
  const look = LOOKS[id] ?? DEFAULT_LOOK;
  const group = new THREE.Group();
  const body = [
    // legs
    part(new THREE.BoxGeometry(0.16, 0.6, 0.18), look.coat, locked, [-0.13, 0.3, 0]),
    part(new THREE.BoxGeometry(0.16, 0.6, 0.18), look.coat, locked, [0.13, 0.3, 0]),
    // torso: a hexagonal prism, wider at the shoulders
    part(new THREE.CylinderGeometry(0.42, 0.34, 0.85, 6), look.coat, locked, [0, 1.02, 0]),
    // arms
    part(new THREE.BoxGeometry(0.14, 0.62, 0.16), look.coat, locked, [-0.5, 1.05, 0], [0, 0, 0.12]),
    part(new THREE.BoxGeometry(0.14, 0.62, 0.16), look.coat, locked, [0.5, 1.05, 0], [0, 0, -0.12]),
    // hands
    part(new THREE.BoxGeometry(0.13, 0.13, 0.13), look.skin, locked, [-0.54, 0.7, 0]),
    part(new THREE.BoxGeometry(0.13, 0.13, 0.13), look.skin, locked, [0.54, 0.7, 0]),
    // head + hair cap
    part(new THREE.SphereGeometry(0.3, 8, 6), look.skin, locked, [0, 1.72, 0]),
    part(new THREE.SphereGeometry(0.315, 8, 4, 0, Math.PI * 2, 0, Math.PI / 2.2), look.hair, locked, [0, 1.76, 0]),
    // eyes
    part(new THREE.BoxGeometry(0.06, 0.07, 0.02), 0x0b1020, locked, [-0.1, 1.73, 0.29]),
    part(new THREE.BoxGeometry(0.06, 0.07, 0.02), 0x0b1020, locked, [0.1, 1.73, 0.29]),
  ];
  group.add(...body, ...accessories(id, look, locked));
  return group;
}

/** Free GPU memory for everything under `root`. */
export function disposeTree(root: THREE.Object3D): void {
  root.traverse((obj) => {
    if (obj instanceof THREE.Mesh || obj instanceof THREE.Points || obj instanceof THREE.Line) {
      (obj.geometry as THREE.BufferGeometry).dispose();
      const mats = ([] as THREE.Material[]).concat(obj.material as THREE.Material | THREE.Material[]);
      for (const m of mats) {
        const map = (m as THREE.MeshBasicMaterial).map;
        map?.dispose();
        m.dispose();
      }
    }
  });
}
