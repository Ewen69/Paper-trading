/**
 * Agent portraits: each agent's 3D model rendered once, offscreen, at a low PS2-style
 * resolution, then cached as an image URL. Returns null when WebGL is unavailable.
 */
import * as THREE from 'three';

import { buildAgent, disposeTree } from './models';
import { tryRenderer } from './webgl';

const SIZE = 96; // render size; shown smaller or larger with soft scaling, like a PS2 menu
const cache = new Map<string, string | null>();
let renderer: THREE.WebGLRenderer | null | undefined;

function sharedRenderer(): THREE.WebGLRenderer | null {
  if (renderer === undefined) {
    renderer = tryRenderer({ antialias: false, alpha: true, preserveDrawingBuffer: true });
    renderer?.setSize(SIZE, SIZE, false);
    renderer?.setPixelRatio(1);
  }
  return renderer;
}

export function portrait(agentId: string, locked: boolean): string | null {
  const key = `${agentId}:${String(locked)}`;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  const r = sharedRenderer();
  if (!r) {
    cache.set(key, null);
    return null;
  }
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xbfdbfe, 0x1e1b4b, 1.6));
  const key1 = new THREE.DirectionalLight(0xffffff, 1.8);
  key1.position.set(2, 3, 4);
  scene.add(key1);
  const agent = buildAgent(agentId, locked);
  agent.rotation.y = -0.35;
  scene.add(agent);
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
  camera.position.set(0.4, 1.75, 3.2);
  camera.lookAt(0, 1.45, 0);
  r.render(scene, camera);
  const url = r.domElement.toDataURL('image/png');
  disposeTree(scene);
  cache.set(key, url);
  return url;
}
