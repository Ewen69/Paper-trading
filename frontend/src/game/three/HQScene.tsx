/**
 * HQ as a live 3D room in a PS2-era style: low-poly faceted agents on glowing pads, fog, a soft
 * low-resolution render scaled up, and a slow orbiting camera you can drag.
 *
 * All text stays HTML: name tags are positioned over the canvas each frame, so they're crisp,
 * selectable and keyboard-focusable. Pad colors repeat the status that the tags spell out.
 */
import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

import type { Agent, AgentStatus } from '../../api/schemas';
import { STATUS } from '../status';
import { buildAgent, disposeTree } from './models';
import { prefersReducedMotion, tryRenderer, webglAvailable } from './webgl';

const RENDER_SCALE = 0.55; // fraction of display resolution: the soft PS2 look
const RING: Record<AgentStatus, number> = {
  ok: 0x34d399,
  warning: 0xfbbf24,
  error: 0xf43f5e,
  idle: 0x64748b,
  locked: 0x1e293b,
};

/** Deterministic pseudo-random numbers for decoration (no Math.random anywhere in the UI). */
function seeded(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
}

function gridTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d');
  if (g) {
    g.fillStyle = '#070b24';
    g.fillRect(0, 0, 64, 64);
    g.strokeStyle = '#1d4ed8';
    g.lineWidth = 2;
    g.strokeRect(0, 0, 64, 64);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(24, 24);
  return tex;
}

function signTexture(text: string): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 256;
  c.height = 64;
  const g = c.getContext('2d');
  if (g) {
    const grad = g.createLinearGradient(0, 0, 0, 64);
    grad.addColorStop(0, '#0c4a6e');
    grad.addColorStop(1, '#082f49');
    g.fillStyle = grad;
    g.fillRect(0, 0, 256, 64);
    g.fillStyle = '#e0f2fe';
    g.font = 'bold 28px "Exo 2", sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText(text.toUpperCase(), 128, 34);
  }
  return new THREE.CanvasTexture(c);
}

interface Placed {
  agent: Agent;
  group: THREE.Group;
  ring: THREE.Mesh;
  anchor: THREE.Vector3;
}

export function HQScene({ agents }: { agents: Agent[] }) {
  const mount = useRef<HTMLDivElement>(null);
  const labels = useRef(new Map<string, HTMLElement>());
  const [supported] = useState(webglAvailable);

  useEffect(() => {
    const el = mount.current;
    if (!el || !supported) return;
    const renderer = tryRenderer({ antialias: false });
    if (!renderer) return;
    const reduced = prefersReducedMotion();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5) * RENDER_SCALE);
    renderer.domElement.className = 'hq-canvas';
    renderer.domElement.setAttribute('aria-hidden', 'true');
    el.prepend(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x050822);
    scene.fog = new THREE.Fog(0x0a1036, 9, 24);
    scene.add(new THREE.HemisphereLight(0x93c5fd, 0x1e1b4b, 1.2));
    const sun = new THREE.DirectionalLight(0xfff1d6, 1.7);
    sun.position.set(4, 8, 6);
    scene.add(sun);
    const glow = new THREE.PointLight(0x22d3ee, 6, 12);
    glow.position.set(0, 0.6, 2);
    scene.add(glow);

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(60, 60),
      new THREE.MeshLambertMaterial({ map: gridTexture() }),
    );
    floor.rotation.x = -Math.PI / 2;
    scene.add(floor);

    // Back wall of glowing panels; station panels carry their station's name.
    const stations = agents.filter((a) => a.station !== null);
    for (let i = 0; i < 9; i++) {
      const angle = Math.PI * (0.18 + (0.64 * i) / 8);
      const station = stations[i - 3];
      const mat = station
        ? new THREE.MeshBasicMaterial({ map: signTexture(station.name) })
        : new THREE.MeshLambertMaterial({ color: 0x0f172a, emissive: 0x0b1e4a });
      const panel = new THREE.Mesh(new THREE.BoxGeometry(2.3, station ? 0.6 : 2.4, 0.12), mat);
      panel.position.set(Math.cos(angle) * 10, station ? 3.2 : 1.6, -Math.sin(angle) * 10 + 2);
      panel.lookAt(0, panel.position.y, 2);
      scene.add(panel);
    }

    // Floating dust, PS2-BIOS style. Seeded, so it's identical on every load.
    const rand = seeded(7);
    const dust = new Float32Array(240 * 3);
    for (let i = 0; i < dust.length; i += 3) {
      dust[i] = (rand() - 0.5) * 24;
      dust[i + 1] = rand() * 6;
      dust[i + 2] = (rand() - 0.5) * 18;
    }
    const dustGeo = new THREE.BufferGeometry();
    dustGeo.setAttribute('position', new THREE.BufferAttribute(dust, 3));
    const particles = new THREE.Points(
      dustGeo,
      new THREE.PointsMaterial({ color: 0x7dd3fc, size: 0.06, transparent: true, opacity: 0.7 }),
    );
    scene.add(particles);

    // The squad: active agents in a gentle front arc; locked silhouettes wait in the wings,
    // out from behind the active agents so their name tags never overlap.
    const active = agents.filter((a) => a.status !== 'locked');
    const locked = agents.filter((a) => a.status === 'locked');
    const spots: [Agent, number, number][] = [
      ...active.map((a, i): [Agent, number, number] => {
        const x = (i - (active.length - 1) / 2) * 2.4;
        return [a, x, 0.6 - Math.abs(x) * 0.2];
      }),
      ...locked.map((a, i): [Agent, number, number] => {
        const side = i % 2 === 0 ? -1 : 1;
        const rank = Math.floor(i / 2);
        return [a, side * (5.2 + rank * 1.7), -0.9 - rank * 1.1];
      }),
    ];
    const placed: Placed[] = [];
    for (const [agent, x, z] of spots) {
      const pad = new THREE.Mesh(
        new THREE.CylinderGeometry(0.75, 0.85, 0.16, 12),
        new THREE.MeshLambertMaterial({ color: 0x1e293b, flatShading: true }),
      );
      pad.position.set(x, 0.08, z);
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(0.8, 0.05, 4, 24),
        new THREE.MeshBasicMaterial({ color: RING[agent.status] }),
      );
      ring.rotation.x = Math.PI / 2;
      ring.position.set(x, 0.18, z);
      const group = buildAgent(agent.id, agent.status === 'locked');
      group.position.set(x, 0.16, z);
      group.lookAt(0, 0.16, 10);
      group.userData = { agentId: agent.id };
      scene.add(pad, ring, group);
      placed.push({ agent, group, ring, anchor: new THREE.Vector3(x, 2.55, z) });
    }

    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 60);
    camera.position.set(0, 4.6, 12);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 1.1, -0.2);
    controls.enableZoom = false;
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.minPolarAngle = 1.05;
    controls.maxPolarAngle = 1.32;
    controls.minAzimuthAngle = -0.55;
    controls.maxAzimuthAngle = 0.55;
    controls.autoRotate = !reduced;
    controls.autoRotateSpeed = 0.35;

    // Hover and click on agents (pointer); keyboard users use the HTML name tags.
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hovered: Placed | null = null;
    const pick = (event: PointerEvent): Placed | null => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(placed.map((p) => p.group), true)[0];
      let obj: THREE.Object3D | null = hit ? hit.object : null;
      while (obj && !('agentId' in obj.userData)) obj = obj.parent;
      return obj ? (placed.find((p) => p.group === obj) ?? null) : null;
    };
    const onMove = (event: PointerEvent) => {
      hovered = pick(event);
      renderer.domElement.style.cursor = hovered?.agent.station
        ? 'pointer'
        : hovered
          ? 'help'
          : 'grab';
    };
    const onClick = (event: PointerEvent) => {
      const target = pick(event);
      if (target?.agent.station) window.location.hash = target.agent.station;
    };
    renderer.domElement.addEventListener('pointermove', onMove);
    renderer.domElement.addEventListener('click', onClick as EventListener);

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = el;
      if (w === 0 || h === 0) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
    observer?.observe(el);

    const started = performance.now();
    const projected = new THREE.Vector3();
    let frame = 0;
    const tick = () => {
      const t = (performance.now() - started) / 1000;
      placed.forEach((p, i) => {
        const bob = reduced || p.agent.status === 'locked' ? 0 : Math.sin(t * 1.6 + i) * 0.05;
        p.group.position.y = 0.16 + bob;
        const scale = hovered === p ? 1.08 : 1;
        p.group.scale.setScalar(scale);
        if (!reduced) p.ring.rotation.z = t * 0.6;
      });
      if (!reduced) particles.rotation.y = t * 0.02;
      controls.update();
      renderer.render(scene, camera);
      const { clientWidth: w, clientHeight: h } = el;
      for (const p of placed) {
        const label = labels.current.get(p.agent.id);
        if (!label) continue;
        projected.copy(p.anchor).project(camera);
        // Locked agents' tags appear on hover only; active agents are always labeled.
        const shown = p.agent.status !== 'locked' || hovered === p;
        const visible = projected.z < 1 && shown;
        label.style.opacity = visible ? '1' : '0';
        label.style.transform = `translate(-50%, -100%) translate(${String(((projected.x + 1) / 2) * w)}px, ${String(((1 - projected.y) / 2) * h)}px)`;
      }
      frame = requestAnimationFrame(tick);
    };
    tick();

    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      renderer.domElement.removeEventListener('pointermove', onMove);
      renderer.domElement.removeEventListener('click', onClick as EventListener);
      controls.dispose();
      disposeTree(scene);
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [agents, supported]);

  if (!supported) {
    return (
      <p className="panel p-4 text-sm text-slate-400">
        3D view unavailable: this browser has WebGL turned off. The squad is listed below.
      </p>
    );
  }

  return (
    <div
      ref={mount}
      className="hq-stage relative h-[340px] overflow-hidden sm:h-[420px]"
      aria-label="HQ 3D view: drag to look around"
      role="group"
    >
      {agents.map((a) => {
        const s = STATUS[a.status];
        const content = (
          <>
            <span className="block font-display text-[12px] uppercase tracking-wider text-white">
              {a.name}
            </span>
            <span className="flex items-center justify-center gap-1 text-[10px] text-slate-300">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${s.led}`} aria-hidden="true" />
              {a.status === 'locked' ? `Locked · ${a.unlocks_in ?? 'later'}` : `${s.label} · LV ${String(a.level)}`}
            </span>
          </>
        );
        const ref = (node: HTMLElement | null) => {
          if (node) labels.current.set(a.id, node);
          else labels.current.delete(a.id);
        };
        return a.station ? (
          <a key={a.id} ref={ref} href={`#${a.station}`} className="scene-tag scene-tag-active">
            {content}
          </a>
        ) : (
          <span key={a.id} ref={ref} className="scene-tag">
            {content}
          </span>
        );
      })}
    </div>
  );
}
