"use client";

/**
 * The 3D Core.
 *
 * Every animated quantity in this file traces to a field of `VisualIntent`,
 * which traces to an event a subsystem published. There is exactly one use of
 * elapsed time — the breathing/pulse phase — and it is multiplied by an
 * amplitude that is zero unless a state reported one. Set every intent channel
 * to zero and this scene renders a still sphere, which is the correct picture
 * of a system that has told us nothing.
 *
 * Performance rules, because the renderer must never be on the critical path of
 * cognition (CLAUDE.md):
 *
 * - geometry is allocated once per tier and reused; nothing is created in the
 *   frame loop;
 * - the frame loop is throttled to the tier's fps and skipped entirely when the
 *   tab is hidden or motion is reduced;
 * - counts from the API are capped by the tier before they reach the GPU, while
 *   the readout keeps reporting the true number.
 */

import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

import { type QualityTier, TIER_BUDGETS, drawableCount } from "../lib/uistate/quality";
import type { PaletteToken, VisualIntent } from "../lib/uistate/visual";

/** Same values as the CSS palettes, so 2D and 3D agree on what a state looks like. */
const PALETTE: Record<PaletteToken, string> = {
  calm: "#6d7f96",
  inward: "#6fc3d6",
  active: "#5aa7e8",
  voice: "#8f7fe8",
  discovery: "#56c2a4",
  recall: "#7fb3d5",
  work: "#e8b339",
  held: "#b08a4f",
  achieved: "#34c98e",
  fault: "#e5655a",
  lab: "#9a86c9",
  ready: "#34c98e",
  unknown: "#58607a",
};

const BASE_RADIUS = 1;

export type CoreSceneProps = {
  intent: VisualIntent;
  tier: QualityTier;
  /** Freeze the frame loop entirely (hidden tab, reduced motion). */
  still: boolean;
};

/**
 * Critically-damped approach, frame-rate independent.
 *
 * Smoothing is legitimate — a state change should not teleport — but it must
 * only ever move *towards* the reported value and settle on it. It can never
 * overshoot into motion nobody reported.
 */
function approach(current: number, target: number, dt: number, rate = 6): number {
  return current + (target - current) * (1 - Math.exp(-rate * dt));
}

export default function CoreScene({ intent, tier, still }: CoreSceneProps) {
  const budget = TIER_BUDGETS[tier];
  const { invalidate } = useThree();

  const coreRef = useRef<THREE.Mesh>(null);
  const wireRef = useRef<THREE.LineSegments>(null);
  const pulseRef = useRef<THREE.Mesh>(null);
  const latticeRef = useRef<THREE.LineSegments>(null);
  const inwardRef = useRef<THREE.Points>(null);
  const sourcesRef = useRef<THREE.InstancedMesh>(null);
  const satelliteRef = useRef<THREE.Group>(null);
  const constructionRef = useRef<THREE.Group>(null);

  // Smoothed values, kept out of React state so they never cause a re-render.
  const smooth = useRef({ scale: 1, opacity: 1, topology: 0, pulse: 0, inward: 0 });
  const lastFrame = useRef(0);
  const clock = useRef(0);

  const color = useMemo(() => new THREE.Color(PALETTE[intent.palette]), [intent.palette]);

  // --------------------------------------------------------- geometry

  const coreGeometry = useMemo(
    () => new THREE.IcosahedronGeometry(BASE_RADIUS, budget.detail),
    [budget.detail],
  );
  const wireGeometry = useMemo(
    () => new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(BASE_RADIUS * 1.01, 1)),
    [],
  );

  /**
   * The thinking lattice: chords across the interior. Built once per tier and
   * per topology bucket, never per frame. Positions are deterministic (a fixed
   * golden-angle spiral), so the same topology always draws the same figure —
   * a lattice that reshuffled every render would read as activity.
   */
  const latticeGeometry = useMemo(() => {
    const segments = budget.latticeSegments;
    const geometry = new THREE.BufferGeometry();
    if (segments === 0) return geometry;
    const points: number[] = [];
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < segments; i += 1) {
      const y = 1 - (i / Math.max(1, segments - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      points.push(Math.cos(theta) * r, y, Math.sin(theta) * r);
      const j = (i + Math.floor(segments / 3)) % segments;
      const y2 = 1 - (j / Math.max(1, segments - 1)) * 2;
      const r2 = Math.sqrt(Math.max(0, 1 - y2 * y2));
      const theta2 = golden * j;
      points.push(Math.cos(theta2) * r2, y2, Math.sin(theta2) * r2);
    }
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    return geometry;
  }, [budget.latticeSegments]);

  /** Listening: points on a shell that travel inward. */
  const inwardGeometry = useMemo(() => {
    const count = Math.min(96, budget.maxSatellites * 2);
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < count; i += 1) {
      const y = 1 - (i / Math.max(1, count - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      positions[i * 3] = Math.cos(theta) * r;
      positions[i * 3 + 1] = y;
      positions[i * 3 + 2] = Math.sin(theta) * r;
    }
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geometry;
  }, [budget.maxSatellites]);

  const sourceGeometry = useMemo(() => new THREE.SphereGeometry(0.045, 8, 8), []);

  // Three.js objects are not garbage collected by React; dispose them by hand
  // when the tier changes or the scene unmounts, or a tier toggle leaks a
  // buffer per switch.
  useEffect(() => () => coreGeometry.dispose(), [coreGeometry]);
  useEffect(() => () => wireGeometry.dispose(), [wireGeometry]);
  useEffect(() => () => latticeGeometry.dispose(), [latticeGeometry]);
  useEffect(() => () => inwardGeometry.dispose(), [inwardGeometry]);
  useEffect(() => () => sourceGeometry.dispose(), [sourceGeometry]);

  // ------------------------------------------------------ source nodes

  const drawnSources = intent.sourceNodesKnown
    ? drawableCount(intent.sourceNodes, tier)
    : 0;

  /** Fixed positions for the evidence nodes: one ring, evenly spaced. */
  const sourcePositions = useMemo(() => {
    const out: THREE.Vector3[] = [];
    for (let i = 0; i < drawnSources; i += 1) {
      const angle = (i / Math.max(1, drawnSources)) * Math.PI * 2;
      const tilt = Math.sin(angle * 3) * 0.18;
      out.push(
        new THREE.Vector3(Math.cos(angle) * 1.6, tilt, Math.sin(angle) * 1.6),
      );
    }
    return out;
  }, [drawnSources]);

  useEffect(() => {
    const mesh = sourcesRef.current;
    if (!mesh) return;
    const matrix = new THREE.Matrix4();
    sourcePositions.forEach((p, i) => {
      matrix.setPosition(p);
      mesh.setMatrixAt(i, matrix);
    });
    mesh.count = sourcePositions.length;
    mesh.instanceMatrix.needsUpdate = true;
    invalidate();
  }, [sourcePositions, invalidate]);

  // -------------------------------------------------------- frame loop

  useFrame(() => {
    // A still scene does no work at all. `useFrame` still fires under
    // `frameloop="always"`, so the guard lives here as well as on the Canvas.
    if (still) return;

    const now = performance.now();
    if (now - lastFrame.current < 1000 / budget.fps) return;
    const dt = Math.min(0.1, (now - lastFrame.current) / 1000);
    lastFrame.current = now;
    clock.current += dt;

    const s = smooth.current;
    s.scale = approach(s.scale, intent.scale, dt);
    s.opacity = approach(s.opacity, 1 - intent.dim * 0.75, dt);
    s.topology = approach(s.topology, intent.topology, dt);
    s.pulse = approach(s.pulse, intent.pulse, dt);
    s.inward = approach(s.inward, intent.inwardFlow, dt);

    // The one use of elapsed time. `breathAmplitude` is 0 for every state that
    // did not report motion, so this term vanishes rather than idling.
    const breath =
      intent.breathAmplitude > 0
        ? Math.sin(clock.current * intent.breathHz * Math.PI * 2) * intent.breathAmplitude
        : 0;

    const core = coreRef.current;
    if (core) {
      const scale = s.scale * (1 + breath);
      core.scale.setScalar(scale);
      const material = core.material as THREE.MeshBasicMaterial;
      material.opacity = 0.16 * s.opacity;
      material.color.copy(color);
      // Rotation is tied to reported topology, not to the clock: a core with
      // nothing to think about does not spin.
      core.rotation.y += dt * 0.25 * s.topology;
    }

    const wire = wireRef.current;
    if (wire) {
      wire.scale.setScalar(s.scale * (1 + breath));
      const material = wire.material as THREE.LineBasicMaterial;
      material.opacity = 0.5 * s.opacity;
      material.color.copy(color);
      wire.rotation.y += dt * 0.25 * s.topology;
    }

    const lattice = latticeRef.current;
    if (lattice) {
      lattice.visible = s.topology > 0.01;
      lattice.scale.setScalar(s.scale * 0.82);
      (lattice.material as THREE.LineBasicMaterial).opacity = 0.45 * s.topology * s.opacity;
      (lattice.material as THREE.LineBasicMaterial).color.copy(color);
      lattice.rotation.y -= dt * 0.4 * s.topology;
      lattice.rotation.x += dt * 0.15 * s.topology;
    }

    const inward = inwardRef.current;
    if (inward) {
      inward.visible = s.inward > 0.01;
      // Energy travelling in: the shell contracts towards the core, its depth
      // set by the reported inward flow.
      const travel = 1.9 - s.inward * 0.7 - breath * 0.5;
      inward.scale.setScalar(travel * s.scale);
      (inward.material as THREE.PointsMaterial).opacity = 0.8 * s.inward * s.opacity;
      (inward.material as THREE.PointsMaterial).color.copy(color);
      inward.rotation.y += dt * 0.2 * s.inward;
    }

    const pulse = pulseRef.current;
    if (pulse) {
      pulse.visible = s.pulse > 0.01;
      // Amplitude is the reported energy; the carrier only shapes it. No
      // energy reported means no visible ring at all.
      const carrier = 0.5 + 0.5 * Math.sin(clock.current * 2.2 * Math.PI);
      pulse.scale.setScalar(s.scale * (1.25 + s.pulse * 0.5 * carrier));
      (pulse.material as THREE.MeshBasicMaterial).opacity = 0.5 * s.pulse * s.opacity;
      (pulse.material as THREE.MeshBasicMaterial).color.copy(color);
    }

    const construction = constructionRef.current;
    if (construction) {
      construction.rotation.z += dt * 0.12 * (intent.constructionLayer > 0 ? 1 : 0);
    }

    const satellite = satelliteRef.current;
    if (satellite) {
      // A finished candidate holds station. It does not orbit: nothing about it
      // is in motion, and drawing movement would suggest work still happening.
      satellite.visible = intent.satelliteComplete;
    }
  });

  // ------------------------------------------------------------ render

  return (
    <group>
      <mesh ref={coreRef} geometry={coreGeometry}>
        <meshBasicMaterial color={color} transparent opacity={0.16} depthWrite={false} />
      </mesh>

      <lineSegments ref={wireRef} geometry={wireGeometry}>
        <lineBasicMaterial color={color} transparent opacity={0.5} />
      </lineSegments>

      {budget.latticeSegments > 0 && (
        <lineSegments ref={latticeRef} geometry={latticeGeometry} visible={false}>
          <lineBasicMaterial color={color} transparent opacity={0} />
        </lineSegments>
      )}

      <points ref={inwardRef} geometry={inwardGeometry} visible={false}>
        <pointsMaterial color={color} size={0.035} transparent opacity={0} sizeAttenuation />
      </points>

      {budget.glow && (
        <mesh ref={pulseRef} visible={false}>
          <sphereGeometry args={[BASE_RADIUS, 24, 16]} />
          <meshBasicMaterial
            color={color}
            transparent
            opacity={0}
            side={THREE.BackSide}
            depthWrite={false}
          />
        </mesh>
      )}

      {/* Research evidence: exactly the nodes the publisher counted, capped. */}
      {drawnSources > 0 && (
        <instancedMesh
          ref={sourcesRef}
          args={[sourceGeometry, undefined, Math.max(1, drawnSources)]}
        >
          <meshBasicMaterial color={color} transparent opacity={0.9} />
        </instancedMesh>
      )}

      {/* Evolution: one ring per construction layer actually reached. */}
      {intent.constructionLayer > 0 && (
        <group ref={constructionRef}>
          {Array.from({ length: intent.constructionLayer }, (_, i) => (
            <mesh key={`layer-${i}`} rotation={[Math.PI / 2 + i * 0.22, 0, 0]}>
              <torusGeometry args={[1.35 + i * 0.16, 0.006, 6, 64]} />
              <meshBasicMaterial color={color} transparent opacity={0.65} />
            </mesh>
          ))}
        </group>
      )}

      {/* SHADOW_READY: a completed, parked satellite. */}
      <group ref={satelliteRef} position={[1.7, 0, 0]} visible={intent.satelliteComplete}>
        <mesh>
          <icosahedronGeometry args={[0.16, 1]} />
          <meshBasicMaterial color={PALETTE.ready} transparent opacity={0.85} />
        </mesh>
        <mesh>
          <sphereGeometry args={[0.26, 16, 12]} />
          <meshBasicMaterial
            color={PALETTE.ready}
            transparent
            opacity={0.18}
            side={THREE.BackSide}
            depthWrite={false}
          />
        </mesh>
      </group>
    </group>
  );
}
