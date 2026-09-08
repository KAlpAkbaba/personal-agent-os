"use client";

/**
 * The Living Core (M18.3, ADR-0070): a dense gold/amber cognitive machine.
 *
 * Nine layers, outermost first — outer field, containment shell, topology
 * shell, orbital layers, data/circuit layer, particle transport, processor
 * structures, energy chamber, central nucleus — plus the things that exist
 * only when a subsystem said so: the evidence constellation, the parked
 * capability nodes, the eye's aperture, the lab's construction layers, the
 * release orbit and the wake surge.
 *
 * The rule that makes it original rather than a copy of a film interface is
 * unchanged from M18.1 (ADR-0065) and is the reason the structure is allowed
 * to be this rich: **every animated quantity traces to a field of
 * `VisualIntent`, which traces to an event a subsystem published or a level
 * the audio path measured.** Set every channel to zero and the reducer settles
 * to a still structure — which is the correct picture of a system that has
 * told us nothing. Depth here is real geometry (nine radii, tilted planes,
 * counter-rotation, parallax), never a fog that suggests activity.
 *
 * The frame arithmetic is not here: it is `stepScene` in `lib/uistate/scene.ts`,
 * a pure reducer tested in Node. This file only copies its numbers onto
 * three.js objects.
 *
 * Performance rules, because the renderer must never be on the critical path
 * of cognition (CLAUDE.md):
 *
 * - geometry is allocated once per tier and reused; the frame body allocates
 *   nothing (a structural test reads this file to make sure);
 * - the frame loop is throttled to the tier's fps, skipped under reduced
 *   motion, and does no work at all in a hidden tab;
 * - particles, fragments and the outer field are instanced; the paths and the
 *   circuitry are merged line geometry; the "bloom" is two additive shells and
 *   no post-processing library;
 * - counts from the API are capped by the tier before they reach the GPU, and
 *   the readout keeps reporting the true number.
 */

import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

import {
  MAX_CAPABILITY_NODES,
  type QualityTier,
  TIER_BUDGETS,
  drawableCount,
} from "../lib/uistate/quality";
import {
  CAPABILITY_RADIUS,
  CHAMBER_RADIUS,
  CIRCUIT_RADIUS,
  CONSTELLATION_RADIUS,
  FIELD_RADIUS,
  INWARD_END,
  INWARD_START,
  ORBITAL_RADII,
  OUTER_FIELD_RADIUS,
  WAKE_RADIUS,
  chordEndpoints,
  circuitTraces,
  constellationPoint,
  createSceneState,
  fibonacciSphere,
  fragmentStation,
  stepScene,
} from "../lib/uistate/scene";
import { type PaletteToken, type VisualIntent, releasePalette } from "../lib/uistate/visual";

/**
 * The Living Core's palette: warm white through gold to amber, one ember for
 * failure, and exactly one cool colour in the whole scene — the eye's
 * aperture, which stays cool precisely so that "the camera is on" can never be
 * mistaken for the Core's own light.
 *
 * Same values as the CSS palettes, so 2D and 3D agree on what a state looks
 * like. `unknown` is deliberately a desaturated warm grey rather than a gold:
 * silence must not glow.
 */
const PALETTE: Record<PaletteToken, string> = {
  calm: "#c9a05a",
  inward: "#ffcf7a",
  active: "#ffb347",
  voice: "#ffd98a",
  discovery: "#f2c14e",
  recall: "#e0b070",
  work: "#f0a53a",
  held: "#b98a4a",
  achieved: "#ffe6a8",
  fault: "#e0623c",
  lab: "#d9a15c",
  ready: "#ffd27a",
  // v5: a reading Core is parchment — pale, desaturated, calmer than any
  // working amber, and still inside the gold family.
  reading: "#e3cfa0",
  // v6: a planning Core is straw — the parchment's neighbour, a shade
  // greener and duller, so laying out a day is told apart from reading a
  // page without leaving the gold family.
  planning: "#d4c48e",
  unknown: "#6b6250",
};

/** The nucleus itself: warm white at the centre, gold at its surface. */
const NUCLEUS_CORE_COLOR = "#fff1d0";
const NUCLEUS_SKIN_COLOR = "#ffc86a";
/** The one cool colour in the scene (privacy, ADR-0056 §2a). */
const EYE_COLOR = "#5aa7e8";

const NUCLEUS_RADIUS = 0.62;
/** The internal rings, innermost first. */
const RING_RADII = [0.84, 1.0, 1.16];
/** Each ring sits on its own tilted plane so the set reads as a volume. */
const RING_TILTS: Array<[number, number, number]> = [
  [Math.PI / 2, 0, 0],
  [Math.PI / 2 - 0.55, 0.35, 0],
  [Math.PI / 2 + 0.4, -0.6, 0.2],
];
/** The structural shells: [0] the topology shell, [1] the containment shell. */
const SHELL_RADII = [1.3, 1.52];
/** The orbital layers' planes: each one is genuinely a different plane. */
const ORBITAL_TILTS: Array<[number, number, number]> = [
  [Math.PI / 2 - 0.22, 0.18, 0],
  [Math.PI / 2 + 0.62, -0.34, 0.15],
  [Math.PI / 2 - 0.78, 0.52, -0.2],
];
const LATTICE_RADIUS = 1.22;
const EYE_RADIUS = 1.64;
const HELD_RADIUS = 1.28;
const ERROR_RADIUS = 1.12;

// Scratch objects for the frame. Allocated once at module load, never inside
// the frame: the structural test in tests/uistate/scene.test.ts forbids `new`
// between the frame body's start and the render.
const scratchMatrix = new THREE.Matrix4();
const scratchPosition = new THREE.Vector3();
const scratchQuaternion = new THREE.Quaternion();
const scratchScale = new THREE.Vector3(1, 1, 1);
const scratchPoint = { x: 0, y: 0, z: 0 };
const scratchStation = { x: 0, y: 0, z: 0, size: 0, tilt: 0 };
const scratchEuler = new THREE.Euler();

export type CoreSceneProps = {
  intent: VisualIntent;
  tier: QualityTier;
  /** Freeze motion (reduced motion): the structure snaps to its targets and holds. */
  still: boolean;
  /** The tab is hidden: no frame runs and nothing is invalidated. */
  hidden: boolean;
};

type Opaque = { opacity: number };

function opacityOf(object: THREE.Object3D | null): Opaque | null {
  if (!object) return null;
  return (object as unknown as { material: Opaque }).material ?? null;
}

export default function CoreScene({ intent, tier, still, hidden }: CoreSceneProps) {
  const budget = TIER_BUDGETS[tier];
  const { invalidate } = useThree();

  const nucleusRef = useRef<THREE.Mesh>(null);
  const nucleusSkinRef = useRef<THREE.Mesh>(null);
  const wireRef = useRef<THREE.LineSegments>(null);
  const haloRef = useRef<THREE.Mesh>(null);
  const chamberRef = useRef<THREE.LineSegments>(null);
  const ringRefs = useRef<Array<THREE.Mesh | null>>([]);
  const shellRefs = useRef<Array<THREE.LineSegments | null>>([]);
  const orbitalRefs = useRef<Array<THREE.Mesh | null>>([]);
  const circuitRef = useRef<THREE.LineSegments>(null);
  const fragmentsRef = useRef<THREE.InstancedMesh>(null);
  const outerFieldRef = useRef<THREE.InstancedMesh>(null);
  const wakeRef = useRef<THREE.Mesh>(null);
  const latticeRef = useRef<THREE.LineSegments>(null);
  const inwardRef = useRef<THREE.InstancedMesh>(null);
  const travellersRef = useRef<THREE.InstancedMesh>(null);
  const pulseRef = useRef<THREE.Mesh>(null);
  const constellationGroupRef = useRef<THREE.Group>(null);
  const constellationRef = useRef<THREE.InstancedMesh>(null);
  const fieldGroupRef = useRef<THREE.Group>(null);
  const fieldRef = useRef<THREE.InstancedMesh>(null);
  const spokesRef = useRef<THREE.LineSegments>(null);
  const capabilityRef = useRef<THREE.InstancedMesh>(null);
  const satelliteHaloRef = useRef<THREE.Mesh>(null);
  const eyeRef = useRef<THREE.Mesh>(null);
  const heldRef = useRef<THREE.LineLoop>(null);
  const errorRef = useRef<THREE.Mesh>(null);
  const convergenceRef = useRef<THREE.Mesh>(null);
  const constructionRef = useRef<THREE.Group>(null);

  const lastFrame = useRef(0);
  const color = useMemo(() => new THREE.Color(PALETTE[intent.palette]), [intent.palette]);

  // --------------------------------------------------------- frame state

  /** The reducer's state: one allocation per particle budget, mutated in place. */
  const scene = useMemo(() => createSceneState(budget.maxParticles), [budget.maxParticles]);
  const inwardCount = scene.inwardCount;
  const travellerCount = budget.maxParticles - scene.inwardCount;

  // --------------------------------------------------------- geometry

  const nucleusGeometry = useMemo(
    () => new THREE.IcosahedronGeometry(NUCLEUS_RADIUS, budget.detail),
    [budget.detail],
  );
  const skinGeometry = useMemo(
    () => new THREE.IcosahedronGeometry(NUCLEUS_RADIUS * 1.14, Math.max(1, budget.detail - 1)),
    [budget.detail],
  );
  const wireGeometry = useMemo(
    () => new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(NUCLEUS_RADIUS * 1.02, 1)),
    [],
  );
  /** The energy chamber: the vessel the nucleus sits in. Present at every tier. */
  const chamberGeometry = useMemo(
    () => new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(CHAMBER_RADIUS, 2)),
    [],
  );
  const ringGeometries = useMemo(
    () => RING_RADII.slice(0, budget.rings).map((r) => new THREE.TorusGeometry(r, 0.008, 6, 128)),
    [budget.rings],
  );
  const shellGeometries = useMemo(
    () =>
      SHELL_RADII.slice(0, budget.shells).map(
        (r, i) => new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(r, i === 0 ? 2 : 1)),
      ),
    [budget.shells],
  );
  /**
   * The orbital layers. Each is a thin torus on its own plane, and each turns
   * at its own rate and in its own direction (`ORBITAL_RATES`) — which is what
   * stops the structure reading as one rotating object.
   */
  const orbitalGeometries = useMemo(
    () =>
      ORBITAL_RADII.slice(0, budget.orbitals).map(
        (r, i) => new THREE.TorusGeometry(r, i === 1 ? 0.005 : 0.007, 6, 160),
      ),
    [budget.orbitals],
  );

  /**
   * The connection paths: chords across the interior. Built once per tier,
   * never per frame. Positions are deterministic (a fixed golden-angle
   * spiral), so the same topology always draws the same figure — a lattice
   * that reshuffled every render would read as activity.
   */
  const chords = useMemo(
    () => chordEndpoints(budget.latticeSegments, new Float32Array(budget.latticeSegments * 6)),
    [budget.latticeSegments],
  );
  const latticeGeometry = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    if (budget.latticeSegments > 0) {
      geometry.setAttribute("position", new THREE.BufferAttribute(chords, 3));
    }
    return geometry;
  }, [chords, budget.latticeSegments]);

  /**
   * The data / circuit layer: procedural board traces, merged into ONE line
   * geometry so the whole layer is a single draw call. Each trace is two
   * segments (out, then along), which is why the buffer is four points per
   * trace rather than the three the generator writes.
   */
  const circuitGeometry = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    const segments = budget.circuitSegments;
    if (segments > 0) {
      const traces = circuitTraces(segments, new Float32Array(segments * 9));
      const merged = new Float32Array(segments * 12);
      for (let i = 0; i < segments; i += 1) {
        const from = i * 9;
        const to = i * 12;
        for (let k = 0; k < 3; k += 1) merged[to + k] = traces[from + k];
        for (let k = 0; k < 3; k += 1) merged[to + 3 + k] = traces[from + 3 + k];
        for (let k = 0; k < 3; k += 1) merged[to + 6 + k] = traces[from + 3 + k];
        for (let k = 0; k < 3; k += 1) merged[to + 9 + k] = traces[from + 6 + k];
      }
      geometry.setAttribute("position", new THREE.BufferAttribute(merged, 3));
    }
    return geometry;
  }, [budget.circuitSegments]);

  /** The inward population's fixed directions: a spiral over the sphere. */
  const inwardDirections = useMemo(
    () => fibonacciSphere(inwardCount, new Float32Array(inwardCount * 3)),
    [inwardCount],
  );
  /** The outer field's fixed directions: the same spiral, far out and faint. */
  const outerDirections = useMemo(
    () => fibonacciSphere(budget.outerFieldPoints, new Float32Array(budget.outerFieldPoints * 3)),
    [budget.outerFieldPoints],
  );

  const particleGeometry = useMemo(() => new THREE.SphereGeometry(0.028, 6, 5), []);
  const nodeGeometry = useMemo(() => new THREE.IcosahedronGeometry(0.05, 1), []);
  const fieldGeometry = useMemo(() => new THREE.SphereGeometry(0.02, 5, 4), []);
  const outerGeometry = useMemo(() => new THREE.SphereGeometry(0.016, 4, 3), []);
  const fragmentGeometry = useMemo(() => new THREE.OctahedronGeometry(1, 0), []);
  const capabilityGeometry = useMemo(() => new THREE.IcosahedronGeometry(0.11, 1), []);
  const unitTorus = useMemo(() => new THREE.TorusGeometry(1, 0.006, 6, 128), []);
  /** The wake surge's ring: thicker, so it reads through the whole structure. */
  const wakeGeometry = useMemo(() => new THREE.TorusGeometry(WAKE_RADIUS, 0.016, 8, 128), []);

  /** The held boundary: a dashed loop, its dash distances computed once. */
  const heldGeometry = useMemo(() => {
    const points = new Float32Array(96 * 3);
    for (let i = 0; i < 96; i += 1) {
      const a = (i / 96) * Math.PI * 2;
      points[i * 3] = Math.cos(a) * HELD_RADIUS;
      points[i * 3 + 1] = 0;
      points[i * 3 + 2] = Math.sin(a) * HELD_RADIUS;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(points, 3));
    return geometry;
  }, []);
  useEffect(() => {
    heldRef.current?.computeLineDistances();
  }, [heldGeometry]);

  /**
   * Spokes from each constellation node towards the outer shell. The buffer
   * is owned by the geometry and written through its attribute when the count
   * changes, so nothing outside three.js holds a reference to mutate.
   */
  const spokeGeometry = useMemo(() => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.BufferAttribute(new Float32Array(budget.maxSatellites * 6), 3),
    );
    return geometry;
  }, [budget.maxSatellites]);

  // Three.js objects are not garbage collected by React; dispose them by hand
  // when the tier changes or the scene unmounts, or a tier toggle leaks a
  // buffer per switch.
  useEffect(() => () => nucleusGeometry.dispose(), [nucleusGeometry]);
  useEffect(() => () => skinGeometry.dispose(), [skinGeometry]);
  useEffect(() => () => wireGeometry.dispose(), [wireGeometry]);
  useEffect(() => () => chamberGeometry.dispose(), [chamberGeometry]);
  useEffect(() => () => ringGeometries.forEach((g) => g.dispose()), [ringGeometries]);
  useEffect(() => () => shellGeometries.forEach((g) => g.dispose()), [shellGeometries]);
  useEffect(() => () => orbitalGeometries.forEach((g) => g.dispose()), [orbitalGeometries]);
  useEffect(() => () => latticeGeometry.dispose(), [latticeGeometry]);
  useEffect(() => () => circuitGeometry.dispose(), [circuitGeometry]);
  useEffect(() => () => particleGeometry.dispose(), [particleGeometry]);
  useEffect(() => () => nodeGeometry.dispose(), [nodeGeometry]);
  useEffect(() => () => fieldGeometry.dispose(), [fieldGeometry]);
  useEffect(() => () => outerGeometry.dispose(), [outerGeometry]);
  useEffect(() => () => fragmentGeometry.dispose(), [fragmentGeometry]);
  useEffect(() => () => capabilityGeometry.dispose(), [capabilityGeometry]);
  useEffect(() => () => unitTorus.dispose(), [unitTorus]);
  useEffect(() => () => wakeGeometry.dispose(), [wakeGeometry]);
  useEffect(() => () => heldGeometry.dispose(), [heldGeometry]);
  useEffect(() => () => spokeGeometry.dispose(), [spokeGeometry]);

  // ------------------------------------------------ counted things

  const drawnConstellation = drawableCount(intent.constellationNodes, tier);
  const drawnField = drawableCount(intent.fieldNodes, tier);
  const drawnCapabilities = Math.min(MAX_CAPABILITY_NODES, Math.max(0, Math.floor(intent.capabilityNodes)));

  /**
   * Place the constellation, the field and the capability nodes. Once per
   * count change, not per frame: the groups rotate as wholes, so the matrices
   * themselves hold still. `invalidate` only when the tab can see it.
   */
  useEffect(() => {
    const nodes = constellationRef.current;
    const spokes = spokesRef.current;
    const spokeAttribute = spokes
      ? (spokes.geometry.getAttribute("position") as THREE.BufferAttribute)
      : null;
    const spokeBuffer = spokeAttribute ? (spokeAttribute.array as Float32Array) : null;
    if (nodes) {
      for (let i = 0; i < drawnConstellation; i += 1) {
        constellationPoint(i, drawnConstellation, CONSTELLATION_RADIUS, 0, scratchPoint);
        scratchMatrix.setPosition(scratchPoint.x, scratchPoint.y, scratchPoint.z);
        nodes.setMatrixAt(i, scratchMatrix);
        if (spokeBuffer && i * 6 + 5 < spokeBuffer.length) {
          const o = i * 6;
          spokeBuffer[o] = scratchPoint.x;
          spokeBuffer[o + 1] = scratchPoint.y;
          spokeBuffer[o + 2] = scratchPoint.z;
          // The spoke reaches from the node a short way towards the shell.
          spokeBuffer[o + 3] = scratchPoint.x * 0.86;
          spokeBuffer[o + 4] = scratchPoint.y * 0.86;
          spokeBuffer[o + 5] = scratchPoint.z * 0.86;
        }
      }
      nodes.count = drawnConstellation;
      nodes.instanceMatrix.needsUpdate = true;
    }
    if (spokes && spokeAttribute) {
      spokes.geometry.setDrawRange(0, drawnConstellation * 2);
      spokeAttribute.needsUpdate = true;
    }
    const field = fieldRef.current;
    if (field) {
      for (let i = 0; i < drawnField; i += 1) {
        constellationPoint(i, drawnField, FIELD_RADIUS, 0.37, scratchPoint);
        scratchMatrix.setPosition(scratchPoint.x, scratchPoint.y * 1.6, scratchPoint.z);
        field.setMatrixAt(i, scratchMatrix);
      }
      field.count = drawnField;
      field.instanceMatrix.needsUpdate = true;
    }
    const capabilities = capabilityRef.current;
    if (capabilities) {
      for (let i = 0; i < drawnCapabilities; i += 1) {
        // Parked, evenly spaced, the first one at the satellite's station.
        const a = (i / MAX_CAPABILITY_NODES) * Math.PI * 2;
        scratchMatrix.setPosition(Math.cos(a) * CAPABILITY_RADIUS, 0, Math.sin(a) * CAPABILITY_RADIUS);
        capabilities.setMatrixAt(i, scratchMatrix);
      }
      capabilities.count = drawnCapabilities;
      capabilities.instanceMatrix.needsUpdate = true;
    }
    if (!hidden) invalidate();
  }, [drawnConstellation, drawnField, drawnCapabilities, hidden, invalidate]);

  /**
   * The structural layers that never move relative to themselves: the outer
   * field's points and the processor fragments' stations. Placed once per
   * tier, because a structure that re-placed itself would read as activity.
   */
  useEffect(() => {
    const outer = outerFieldRef.current;
    if (outer) {
      for (let i = 0; i < budget.outerFieldPoints; i += 1) {
        const x = outerDirections[i * 3] * OUTER_FIELD_RADIUS;
        const y = outerDirections[i * 3 + 1] * OUTER_FIELD_RADIUS;
        const z = outerDirections[i * 3 + 2] * OUTER_FIELD_RADIUS;
        scratchMatrix.setPosition(x, y, z);
        outer.setMatrixAt(i, scratchMatrix);
      }
      outer.count = budget.outerFieldPoints;
      outer.instanceMatrix.needsUpdate = true;
    }
    const fragments = fragmentsRef.current;
    if (fragments) {
      for (let i = 0; i < budget.fragments; i += 1) {
        fragmentStation(i, budget.fragments, scratchStation);
        scratchPosition.set(scratchStation.x, scratchStation.y, scratchStation.z);
        scratchEuler.set(scratchStation.tilt, scratchStation.tilt * 1.7, scratchStation.tilt * 0.6);
        scratchQuaternion.setFromEuler(scratchEuler);
        scratchScale.set(scratchStation.size, scratchStation.size * 1.6, scratchStation.size);
        scratchMatrix.compose(scratchPosition, scratchQuaternion, scratchScale);
        fragments.setMatrixAt(i, scratchMatrix);
      }
      fragments.count = budget.fragments;
      fragments.instanceMatrix.needsUpdate = true;
      // The scratch quaternion is shared with the frame body, which composes
      // unrotated matrices; hand it back the way it found it.
      scratchQuaternion.identity();
      scratchScale.set(1, 1, 1);
    }
    if (!hidden) invalidate();
  }, [budget.outerFieldPoints, budget.fragments, outerDirections, hidden, invalidate]);

  // -------------------------------------------------------- frame body

  /**
   * Apply one reducer step to the objects. Called by the frame loop while the
   * tab is visible and motion is allowed, and once — with a settling `dt` —
   * when motion is reduced, so the structure shows the intent without moving.
   */
  const applyFrame = (dt: number, pointerX: number, pointerY: number, camera: THREE.Camera) => {
    const s = stepScene(scene, intent, dt, pointerX, pointerY);
    const breathScale = s.scale * (1 + s.breath);
    // The wake surge is its own light. It brightens the nucleus and lights its
    // own ring; it deliberately does NOT drive `ringSpin`, because ring speed
    // is the thinking channel and an alarm is not the Core thinking.
    const surge = s.wake * (0.55 + 0.45 * s.wakeCarrier);

    const nucleus = nucleusRef.current;
    if (nucleus) {
      nucleus.scale.setScalar(breathScale);
      const material = opacityOf(nucleus);
      if (material) material.opacity = (0.4 + 0.45 * s.glow + 0.3 * surge) * s.opacity;
      nucleus.rotation.y = s.ringAngle * 0.5;
    }

    // The nucleus' gold skin, just off the warm-white body: the two together
    // are what make the centre read as hot rather than as a lit ball.
    const skin = nucleusSkinRef.current;
    if (skin) {
      skin.scale.setScalar(breathScale * (1 + 0.05 * s.pulse));
      const material = opacityOf(skin);
      if (material) material.opacity = (0.12 + 0.3 * s.glow + 0.25 * surge) * s.opacity;
      skin.rotation.y = -s.ringAngle * 0.32;
      skin.rotation.x = s.ringAngle * 0.14;
    }

    const wire = wireRef.current;
    if (wire) {
      wire.scale.setScalar(breathScale);
      const material = opacityOf(wire);
      if (material) material.opacity = (0.22 + 0.4 * s.glow) * s.opacity;
      wire.rotation.y = s.ringAngle * 0.5;
    }

    // The restrained bloom: one additive shell around the nucleus, scaled by
    // the breath and by whatever real energy there is. No post-processing.
    const halo = haloRef.current;
    if (halo) {
      halo.scale.setScalar(breathScale * (1.5 + 0.5 * s.glow + 0.4 * s.pulse + 0.5 * surge));
      const material = opacityOf(halo);
      if (material) material.opacity = (0.03 + 0.16 * s.glow + 0.18 * surge) * s.opacity;
    }

    // The energy chamber: the vessel around the nucleus. It breathes with it
    // and counter-rotates against the rings.
    const chamber = chamberRef.current;
    if (chamber) {
      chamber.scale.setScalar(s.scale * (1 + s.breath * 0.6));
      chamber.rotation.y = -s.ringAngle * 0.6;
      chamber.rotation.z = s.ringAngle * 0.22;
      const material = opacityOf(chamber);
      if (material) material.opacity = (0.06 + 0.22 * s.glow) * s.opacity;
    }

    // The rings: each turns about its own tilted axis at the reported spin,
    // and the set contracts while energy is drawn inward.
    const ringScale = s.scale * (1 - 0.22 * s.inward) * (1 + s.breath * 0.5);
    const rings = ringRefs.current;
    for (let i = 0; i < rings.length; i += 1) {
      const ring = rings[i];
      if (!ring) continue;
      ring.scale.setScalar(ringScale);
      ring.rotation.z = s.ringAngle * (i % 2 === 0 ? 1 : -0.7) + i * 0.4;
      const material = opacityOf(ring);
      if (material) material.opacity = (0.28 + 0.45 * s.glow) * s.opacity;
    }

    // The shells stand off by the reported spread and counter-rotate slowly.
    const shells = shellRefs.current;
    for (let i = 0; i < shells.length; i += 1) {
      const shell = shells[i];
      if (!shell) continue;
      shell.scale.setScalar(s.scale * (1 + s.shellSpread * (0.16 + i * 0.1)) * (1 + s.breath * 0.3));
      shell.rotation.y = s.shellAngle * (i === 0 ? 1 : -0.6);
      shell.rotation.x = s.shellAngle * 0.3;
      const material = opacityOf(shell);
      if (material) material.opacity = (0.05 + 0.15 * s.glow) * (1 - i * 0.3) * s.opacity;
    }

    // The orbital layers: independent rates, independent directions, one
    // shared driver. The surge lights them without speeding them up.
    const orbitals = orbitalRefs.current;
    for (let i = 0; i < orbitals.length; i += 1) {
      const orbital = orbitals[i];
      if (!orbital) continue;
      orbital.rotation.z = s.orbitAngles[i];
      orbital.rotation.x = ORBITAL_TILTS[i][0] + s.orbitAngles[i] * 0.12;
      orbital.scale.setScalar(s.scale * (1 + s.shellSpread * 0.06));
      const material = opacityOf(orbital);
      if (material) material.opacity = (0.1 + 0.34 * s.glow + 0.3 * surge) * s.opacity;
    }

    // The data / circuit layer. It is structure, so it is always mounted; it
    // brightens with the light and carries a pulse only while something flows.
    const circuit = circuitRef.current;
    if (circuit) {
      circuit.scale.setScalar(s.scale * CIRCUIT_RADIUS);
      circuit.rotation.y = s.latticeAngle * 0.5 + s.circuitPhase * 0.6;
      circuit.rotation.x = 0.42;
      const material = opacityOf(circuit);
      if (material) material.opacity = (0.05 + 0.22 * s.glow + 0.3 * s.flowRate) * s.opacity;
    }

    // The processor structures: parked fragments that drift with the whole
    // machine and hold their stations relative to one another.
    const fragments = fragmentsRef.current;
    if (fragments) {
      fragments.rotation.y = s.fragmentAngle;
      fragments.rotation.z = s.fragmentAngle * 0.25;
      fragments.scale.setScalar(s.scale);
      const material = opacityOf(fragments);
      if (material) material.opacity = (0.16 + 0.5 * s.glow) * s.opacity;
    }

    // The outer field: the faintest boundary. It drifts against the orbitals.
    const outer = outerFieldRef.current;
    if (outer) {
      outer.rotation.y = -s.shellAngle * 0.4;
      outer.rotation.x = s.shellAngle * 0.12;
      const material = opacityOf(outer);
      if (material) material.opacity = (0.04 + 0.2 * s.glow) * s.opacity;
    }

    // The wake surge's own ring: visible only while an alarm is sounding.
    const wake = wakeRef.current;
    if (wake) {
      wake.visible = s.wake > 0.01;
      wake.scale.setScalar(s.scale * (1 + 0.06 * s.wakeCarrier));
      wake.rotation.z = s.ringAngle * 0.2;
      const material = opacityOf(wake);
      if (material) material.opacity = 0.85 * surge * s.opacity;
    }

    const lattice = latticeRef.current;
    if (lattice) {
      lattice.visible = s.topology > 0.01;
      lattice.scale.setScalar(s.scale * LATTICE_RADIUS);
      lattice.rotation.y = s.latticeAngle;
      lattice.rotation.x = s.latticeAngle * 0.35;
      const material = opacityOf(lattice);
      if (material) material.opacity = 0.42 * s.topology * s.opacity;
    }

    // The inward population: each particle travels its fixed direction from
    // the shell to the nucleus, at the inward flow's rate.
    const inward = inwardRef.current;
    if (inward) {
      inward.visible = s.inward > 0.01;
      if (inward.visible) {
        const phases = s.phases;
        const directions = inwardDirections;
        for (let i = 0; i < inwardCount; i += 1) {
          const radius = (INWARD_START - phases[i] * (INWARD_START - INWARD_END)) * s.scale;
          scratchPosition.set(directions[i * 3] * radius, directions[i * 3 + 1] * radius, directions[i * 3 + 2] * radius);
          const size = 0.55 + 0.45 * (1 - phases[i]);
          scratchMatrix.compose(scratchPosition, scratchQuaternion, scratchScale.set(size, size, size));
          inward.setMatrixAt(i, scratchMatrix);
        }
        inward.instanceMatrix.needsUpdate = true;
        const material = opacityOf(inward);
        if (material) material.opacity = (0.5 + 0.5 * s.ownerVoice) * s.inward * s.opacity;
      }
    }

    // The path population: each particle travels a chord, at the flow rate.
    const travellers = travellersRef.current;
    if (travellers) {
      travellers.visible = s.flowRate > 0.01 && budget.latticeSegments > 0;
      if (travellers.visible) {
        const phases = s.phases;
        const segments = budget.latticeSegments;
        for (let i = 0; i < travellerCount; i += 1) {
          const t = phases[inwardCount + i];
          const c = (i % segments) * 6;
          scratchPosition.set(
            (chords[c] + (chords[c + 3] - chords[c]) * t) * LATTICE_RADIUS * s.scale,
            (chords[c + 1] + (chords[c + 4] - chords[c + 1]) * t) * LATTICE_RADIUS * s.scale,
            (chords[c + 2] + (chords[c + 5] - chords[c + 2]) * t) * LATTICE_RADIUS * s.scale,
          );
          scratchMatrix.compose(scratchPosition, scratchQuaternion, scratchScale.set(0.8, 0.8, 0.8));
          travellers.setMatrixAt(i, scratchMatrix);
        }
        travellers.instanceMatrix.needsUpdate = true;
        travellers.rotation.y = s.latticeAngle;
        travellers.rotation.x = s.latticeAngle * 0.35;
        const material = opacityOf(travellers);
        if (material) material.opacity = 0.85 * s.flowRate * s.opacity;
      }
    }

    const pulse = pulseRef.current;
    if (pulse) {
      pulse.visible = s.pulse > 0.01;
      // Amplitude is the measured energy; the carrier only shapes it. No
      // energy reported means no visible shell at all.
      pulse.scale.setScalar(s.scale * (1.28 + s.pulse * 0.45 * s.carrier));
      const material = opacityOf(pulse);
      if (material) material.opacity = 0.45 * s.pulse * s.opacity;
    }

    // The constellation and its field drift as wholes, at the published or
    // resting figure. Their nodes do not move relative to one another.
    const constellation = constellationGroupRef.current;
    if (constellation) {
      constellation.visible = drawnConstellation > 0;
      constellation.rotation.y = s.constellationAngle;
    }
    const nodes = constellationRef.current;
    if (nodes) {
      const material = opacityOf(nodes);
      if (material) material.opacity = 0.9 * s.opacity;
    }
    const spokes = spokesRef.current;
    if (spokes) {
      const material = opacityOf(spokes);
      if (material) material.opacity = 0.35 * s.opacity;
    }
    const field = fieldGroupRef.current;
    if (field) {
      field.visible = drawnField > 0;
      field.rotation.y = -s.constellationAngle * 0.5;
    }

    // Parked things hold station: nothing here rotates.
    const capabilities = capabilityRef.current;
    if (capabilities) capabilities.visible = drawnCapabilities > 0;
    const satellite = satelliteHaloRef.current;
    if (satellite) satellite.visible = intent.satelliteComplete;

    const eye = eyeRef.current;
    if (eye) {
      eye.visible = intent.eyeActive > 0;
      const material = opacityOf(eye);
      if (material) material.opacity = 0.55 * s.opacity;
    }

    const held = heldRef.current;
    if (held) {
      held.visible = s.restraint > 0.01;
      held.scale.setScalar(s.scale);
      const material = opacityOf(held);
      if (material) material.opacity = 0.6 * s.restraint * s.opacity;
    }

    // Error: one bounded, slow offset. The reducer capped it; this only draws it.
    const error = errorRef.current;
    if (error) {
      error.visible = s.agitation > 0.01;
      error.scale.setScalar(ERROR_RADIUS * s.scale);
      error.position.x = s.agitationOffset;
      const material = opacityOf(error);
      if (material) material.opacity = 0.7 * s.agitation * s.opacity;
    }

    // Memory: the convergence ring, drawn only against real progress, closes
    // in by exactly that progress.
    const convergence = convergenceRef.current;
    if (convergence) {
      convergence.visible = intent.convergenceKnown;
      convergence.scale.setScalar((1.45 - intent.convergence * 0.9) * s.scale);
      const material = opacityOf(convergence);
      if (material) material.opacity = 0.6 * s.opacity;
    }

    const construction = constructionRef.current;
    if (construction) {
      construction.rotation.z = s.ringAngle * 0.3;
    }

    // The camera leans towards the pointer; a way of seeing the layers, not a
    // claim about the system.
    if (budget.parallax) {
      camera.position.x = s.parallaxX;
      camera.position.y = s.parallaxY;
      camera.lookAt(0, 0, 0);
    }
  };

  useFrame((root) => {
    // A hidden tab does no work at all; reduced motion is handled by the
    // settling effect below. `useFrame` still fires under `frameloop="always"`,
    // so the guard lives here as well as on the Canvas.
    if (hidden || still) return;
    const now = performance.now();
    if (now - lastFrame.current < 1000 / budget.fps) return;
    const dt = Math.min(0.1, (now - lastFrame.current) / 1000);
    lastFrame.current = now;
    applyFrame(dt, budget.parallax ? root.pointer.x : 0, budget.parallax ? root.pointer.y : 0, root.camera);
  });

  // Reduced motion: when the intent changes, settle the structure on its new
  // targets in one long step and draw that frame. Nothing moves; the shape,
  // the counts and the glow are all still shown. `applyFrame` is rebuilt
  // every render, so the latest one is reached through a ref and the effect
  // keys on the intent it closes over.
  const camera = useThree((root) => root.camera);
  const settle = useRef(applyFrame);
  useEffect(() => {
    settle.current = applyFrame;
  });
  useEffect(() => {
    if (!still || hidden) return;
    settle.current(10, 0, 0, camera);
    invalidate();
  }, [still, hidden, intent, camera, invalidate]);

  // ------------------------------------------------------------ render

  return (
    <group>
      {/* The outer field: the widest, faintest layer. Instanced; culling off
          because the base geometry's bounds sit at the origin. */}
      {budget.outerFieldPoints > 0 && (
        <instancedMesh
          ref={outerFieldRef}
          args={[outerGeometry, undefined, budget.outerFieldPoints]}
          frustumCulled={false}
        >
          <meshBasicMaterial color={color} transparent opacity={0.08} depthWrite={false} />
        </instancedMesh>
      )}

      {/* The translucent structural shells: [0] topology, [1] containment. */}
      {shellGeometries.map((geometry, i) => (
        <lineSegments
          key={`shell-${i}`}
          ref={(el) => {
            shellRefs.current[i] = el;
          }}
          geometry={geometry}
        >
          <lineBasicMaterial color={color} transparent opacity={0.08} depthWrite={false} />
        </lineSegments>
      ))}

      {/* The independent orbital layers. */}
      {orbitalGeometries.map((geometry, i) => (
        <mesh
          key={`orbital-${i}`}
          ref={(el) => {
            orbitalRefs.current[i] = el;
          }}
          geometry={geometry}
          rotation={ORBITAL_TILTS[i]}
        >
          <meshBasicMaterial color={color} transparent opacity={0.25} depthWrite={false} />
        </mesh>
      ))}

      {/* The data / circuit layer: one merged line geometry, one draw call. */}
      {budget.circuitSegments > 0 && (
        <lineSegments ref={circuitRef} geometry={circuitGeometry}>
          <lineBasicMaterial color={color} transparent opacity={0.12} depthWrite={false} />
        </lineSegments>
      )}

      {/* The processor structures: floating fragments at fixed stations. */}
      {budget.fragments > 0 && (
        <instancedMesh
          ref={fragmentsRef}
          args={[fragmentGeometry, undefined, budget.fragments]}
          frustumCulled={false}
        >
          <meshBasicMaterial color={color} transparent opacity={0.3} depthWrite={false} />
        </instancedMesh>
      )}

      {/* The wake surge (M18.3 §7): an alarm's own ring, and nothing else's. */}
      <mesh ref={wakeRef} geometry={wakeGeometry} rotation={[Math.PI / 2 - 0.35, 0, 0]} visible={false}>
        <meshBasicMaterial color={PALETTE.work} transparent opacity={0} depthWrite={false} />
      </mesh>

      {/* The energy chamber: the vessel the nucleus sits in. */}
      <lineSegments ref={chamberRef} geometry={chamberGeometry}>
        <lineBasicMaterial color={NUCLEUS_SKIN_COLOR} transparent opacity={0.1} depthWrite={false} />
      </lineSegments>

      {/* The internal rings: the topology layers. */}
      {ringGeometries.map((geometry, i) => (
        <mesh
          key={`ring-${i}`}
          ref={(el) => {
            ringRefs.current[i] = el;
          }}
          geometry={geometry}
          rotation={RING_TILTS[i]}
        >
          <meshBasicMaterial color={color} transparent opacity={0.4} depthWrite={false} />
        </mesh>
      ))}

      {/* The nucleus: warm white body, gold skin, wire, and one additive halo
          that is the whole of the "bloom" — no post-processing anywhere. */}
      <mesh ref={nucleusRef} geometry={nucleusGeometry}>
        <meshBasicMaterial color={NUCLEUS_CORE_COLOR} transparent opacity={0.4} depthWrite={false} />
      </mesh>
      <mesh ref={nucleusSkinRef} geometry={skinGeometry}>
        <meshBasicMaterial
          color={NUCLEUS_SKIN_COLOR}
          transparent
          opacity={0.16}
          side={THREE.BackSide}
          depthWrite={false}
        />
      </mesh>
      <lineSegments ref={wireRef} geometry={wireGeometry}>
        <lineBasicMaterial color={NUCLEUS_SKIN_COLOR} transparent opacity={0.4} depthWrite={false} />
      </lineSegments>
      {budget.glow && (
        <mesh ref={haloRef}>
          <sphereGeometry args={[NUCLEUS_RADIUS, 20, 14]} />
          <meshBasicMaterial
            color={NUCLEUS_SKIN_COLOR}
            transparent
            opacity={0.08}
            blending={THREE.AdditiveBlending}
            side={THREE.BackSide}
            depthWrite={false}
          />
        </mesh>
      )}

      {/* The connection paths: drawn at the reported topology. */}
      {budget.latticeSegments > 0 && (
        <lineSegments ref={latticeRef} geometry={latticeGeometry} visible={false}>
          <lineBasicMaterial color={color} transparent opacity={0} depthWrite={false} />
        </lineSegments>
      )}

      {/* The two bounded particle populations. Instanced; culling is off
          because the base geometry's bounds sit at the origin. */}
      {inwardCount > 0 && (
        <instancedMesh
          ref={inwardRef}
          args={[particleGeometry, undefined, inwardCount]}
          frustumCulled={false}
          visible={false}
        >
          <meshBasicMaterial color={color} transparent opacity={0} depthWrite={false} />
        </instancedMesh>
      )}
      {travellerCount > 0 && budget.latticeSegments > 0 && (
        <instancedMesh
          ref={travellersRef}
          args={[particleGeometry, undefined, travellerCount]}
          frustumCulled={false}
          visible={false}
        >
          <meshBasicMaterial color={color} transparent opacity={0} depthWrite={false} />
        </instancedMesh>
      )}

      {budget.glow && (
        <mesh ref={pulseRef} visible={false}>
          <sphereGeometry args={[1, 24, 16]} />
          <meshBasicMaterial
            color={color}
            transparent
            opacity={0}
            side={THREE.BackSide}
            depthWrite={false}
          />
        </mesh>
      )}

      {/* Research: the evidence constellation — the count the publisher sent,
          capped by the tier, or the fixed motif — with a spoke per node; and
          the wider field it was kept from, when both were counted. */}
      <group ref={constellationGroupRef} visible={false}>
        <instancedMesh
          ref={constellationRef}
          args={[nodeGeometry, undefined, Math.max(1, budget.maxSatellites)]}
          frustumCulled={false}
        >
          <meshBasicMaterial color={color} transparent opacity={0.9} />
        </instancedMesh>
        <lineSegments ref={spokesRef} geometry={spokeGeometry}>
          <lineBasicMaterial color={color} transparent opacity={0.35} depthWrite={false} />
        </lineSegments>
      </group>
      <group ref={fieldGroupRef} visible={false}>
        <instancedMesh
          ref={fieldRef}
          args={[fieldGeometry, undefined, Math.max(1, budget.maxSatellites)]}
          frustumCulled={false}
        >
          <meshBasicMaterial color={color} transparent opacity={0.35} depthWrite={false} />
        </instancedMesh>
      </group>

      {/* SHADOW_READY: parked capability nodes, the first at the satellite's
          station under its halo. Nothing here moves. */}
      <instancedMesh
        ref={capabilityRef}
        args={[capabilityGeometry, undefined, MAX_CAPABILITY_NODES]}
        frustumCulled={false}
        visible={false}
      >
        <meshBasicMaterial color={PALETTE.ready} transparent opacity={0.85} />
      </instancedMesh>
      <mesh ref={satelliteHaloRef} position={[CAPABILITY_RADIUS, 0, 0]} visible={false}>
        <sphereGeometry args={[0.26, 16, 12]} />
        <meshBasicMaterial
          color={PALETTE.ready}
          transparent
          opacity={0.18}
          side={THREE.BackSide}
          depthWrite={false}
        />
      </mesh>

      {/* The eye's aperture: one thin tilted ring while eye.active is current,
          and the one cool colour in the scene. */}
      <mesh ref={eyeRef} geometry={unitTorus} scale={EYE_RADIUS} rotation={[Math.PI / 2 + 0.9, 0.3, 0]} visible={false}>
        <meshBasicMaterial color={EYE_COLOR} transparent opacity={0.55} depthWrite={false} />
      </mesh>

      {/* Waiting on the owner: a held, dashed boundary. */}
      <lineLoop ref={heldRef} geometry={heldGeometry} visible={false}>
        <lineDashedMaterial color={color} transparent opacity={0} dashSize={0.06} gapSize={0.14} />
      </lineLoop>

      {/* Error: one offset ring. Bounded, slow — never a strobe. */}
      <mesh ref={errorRef} geometry={unitTorus} rotation={[Math.PI / 2, 0, 0]} visible={false}>
        <meshBasicMaterial color={PALETTE.fault} transparent opacity={0} depthWrite={false} />
      </mesh>

      {/* Memory: the convergence ring, only against real progress. */}
      <mesh ref={convergenceRef} geometry={unitTorus} rotation={[Math.PI / 2 - 0.3, 0.2, 0]} visible={false}>
        <meshBasicMaterial color={color} transparent opacity={0} depthWrite={false} />
      </mesh>

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

      {/* The release orbit (M18 spec §15), on the satellite's own radius: a
          full faint ring says a stage is current; the bright arc is real
          progress and nothing else. No progress, no arc. */}
      {intent.releaseStage !== "none" && (
        <group rotation={[Math.PI / 2, 0, 0]}>
          <mesh>
            <torusGeometry args={[1.7, 0.008, 6, 96]} />
            <meshBasicMaterial
              color={PALETTE[releasePalette(intent.releaseStage)]}
              transparent
              opacity={intent.releaseProgress == null ? 0.35 : 0.2}
            />
          </mesh>
          {intent.releaseProgress != null && intent.releaseProgress > 0 && (
            <mesh rotation={[0, 0, Math.PI / 2]}>
              <torusGeometry args={[1.7, 0.02, 8, 96, Math.PI * 2 * intent.releaseProgress]} />
              <meshBasicMaterial
                color={PALETTE[releasePalette(intent.releaseStage)]}
                transparent
                opacity={0.9}
              />
            </mesh>
          )}
        </group>
      )}
    </group>
  );
}
