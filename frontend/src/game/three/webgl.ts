import * as THREE from 'three';

/** Create a renderer, or null when WebGL isn't available (old GPU, policy, or tests). */
export function tryRenderer(options: THREE.WebGLRendererParameters): THREE.WebGLRenderer | null {
  try {
    const probe = document.createElement('canvas');
    if (!probe.getContext('webgl2') && !probe.getContext('webgl')) return null;
    return new THREE.WebGLRenderer(options);
  } catch {
    return null;
  }
}

/** Cheap check before mounting a 3D view (no renderer is created). */
export function webglAvailable(): boolean {
  try {
    const probe = document.createElement('canvas');
    return Boolean(probe.getContext('webgl2') ?? probe.getContext('webgl'));
  } catch {
    return false;
  }
}

export const prefersReducedMotion = (): boolean =>
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;
