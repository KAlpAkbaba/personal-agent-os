/**
 * The frame step of the Core, as a pure reducer (M18.1, ADR-0065).
 *
 * `CoreScene` owns three.js objects; this file owns the *numbers* those
 * objects are set from. Every frame the scene calls `stepScene(state, intent,
 * dt)` and then copies the resulting scalars onto materials and transforms.
 * Splitting it this way buys three properties the constitution asks for:
 *
 * 1. **The truth is the intent; the frame only draws it.** Every value here is
 *    an approach towards a `VisualIntent` channel, or an accumulation driven by
 *    one. There is no target that the intent did not set, so a zero intent
 *    settles to a still scene and stays there — asserted in
 *    `tests/uistate/scene.test.ts` in Node, with no browser and no WebGL.
 * 2. **No dependence on frame rate.** Approaches are exponential in `dt`,
 *    accumulations are linear in `dt`. Sixty small steps and thirty larger
 *    ones land on the same picture; the tests check that too.
 * 3. **No allocation in the frame.** The state is created once by
 *    `createSceneState` and mutated in place; `stepScene` contains no `new`,
 *    no literal, no array method that would allocate. A structural test reads
 *    this file and refuses any of those inside the reducer.
 *
 * Nothing here imports three.js: the reducer is plain arithmetic over a plain
 * object, which is what lets it run under vitest in Node.
 */

import type { VisualIntent } from "./visual";

/** Radians per second of ring rotation at `ringSpin = 1`. */
export const RING_RATE = 0.9;
/** The shells counter-rotate, slower. */
export const SHELL_RATE = 0.35;
/**
 * The independent orbital layers: one rate each, and the sign is the
 * direction (M18.3). They are deliberately incommensurate — no two of them
 * ever line up on a beat — because a structure whose layers pulse together
 * reads as one animation rather than as a machine with parts.
 *
 * Every one of them is still multiplied by `ringSpin`, so a Core that was told
 * nothing does not turn at all.
 */
export const ORBITAL_RATES = [0.52, -0.31, 0.19] as const;
/** The circuit layer's data pulse travels at this many circuits per second. */
export const CIRCUIT_RATE = 0.4;
/** The floating processor fragments drift at this fraction of the ring rate. */
export const FRAGMENT_RATE = 0.14;
/** The wake surge's own carrier: slow, and only ever scaled by the surge itself. */
export const WAKE_CARRIER_HZ = 0.6;
/** The connection paths turn against the rings, faster, at full topology. */
export const LATTICE_RATE = 0.6;
/** The evidence constellation drifts at most this fast, at `constellationDrift = 1`. */
export const CONSTELLATION_RATE = 0.25;
/** Particle circuits per second at a channel of 1. */
export const PARTICLE_RATE = 0.55;
/** Carrier frequency of the speaking pulse; its amplitude is the measured energy. */
export const PULSE_CARRIER_HZ = 2.2;
/** How quickly a smoothed value settles on its target. */
export const APPROACH_RATE = 6;
/** How far the camera leans towards the pointer, in world units. */
export const PARALLAX_REACH = 0.22;

const TWO_PI = Math.PI * 2;

/**
 * Critically-damped approach, frame-rate independent.
 *
 * Smoothing is legitimate — a state change should not teleport — but it must
 * only ever move *towards* the reported value and settle on it. It can never
 * overshoot into motion nobody reported.
 */
export function approach(current: number, target: number, dt: number, rate = APPROACH_RATE): number {
  return current + (target - current) * (1 - Math.exp(-rate * dt));
}

export type SceneState = {
  /** Seconds of motion so far. Advances only while a rhythm channel is live. */
  clock: number;

  // smoothed channels (each approaches the intent's value of the same name)
  scale: number;
  opacity: number;
  glow: number;
  shellSpread: number;
  ringSpin: number;
  flowRate: number;
  inward: number;
  topology: number;
  pulse: number;
  ownerVoice: number;
  constellationDrift: number;
  restraint: number;
  agitation: number;
  /** The wake alarm's surge, smoothed. Its own channel; never the core body's. */
  wake: number;

  // accumulations, driven by the smoothed channels
  ringAngle: number;
  shellAngle: number;
  latticeAngle: number;
  constellationAngle: number;
  /** One angle per orbital layer, each at its own rate and direction. */
  orbitAngles: Float32Array;
  /** The circuit layer's travelling data pulse, 0..1. */
  circuitPhase: number;
  /** The processor fragments' slow drift about the vertical. */
  fragmentAngle: number;
  /** The wake surge's carrier, 0..1; zero whenever the surge is. */
  wakeCarrier: number;

  // rhythms: breath and the pulse carrier, both zero unless their amplitude is
  breath: number;
  carrier: number;
  /** The error state's bounded, slow offset. */
  agitationOffset: number;

  // the two bounded particle populations, phase 0..1 along a fixed path
  phases: Float32Array;
  /** The first `inwardCount` phases are the inward pull; the rest travel the paths. */
  inwardCount: number;

  // the camera's lean towards the pointer
  parallaxX: number;
  parallaxY: number;
};

/** Allocate the whole frame state once. Everything starts still. */
export function createSceneState(maxParticles: number): SceneState {
  const count = Math.max(0, Math.floor(maxParticles));
  const phases = new Float32Array(count);
  // Deterministic starting phases, spread along each path so a flow reads as a
  // stream rather than a single wave. Fixed per index: the same particle is at
  // the same place every mount.
  for (let i = 0; i < count; i += 1) phases[i] = ((i * 0.6180339887) % 1 + 1) % 1;
  return {
    clock: 0,
    scale: 1,
    opacity: 1,
    glow: 0,
    shellSpread: 0,
    ringSpin: 0,
    flowRate: 0,
    inward: 0,
    topology: 0,
    pulse: 0,
    ownerVoice: 0,
    constellationDrift: 0,
    restraint: 0,
    agitation: 0,
    wake: 0,
    ringAngle: 0,
    shellAngle: 0,
    latticeAngle: 0,
    constellationAngle: 0,
    orbitAngles: new Float32Array(ORBITAL_RATES.length),
    circuitPhase: 0,
    fragmentAngle: 0,
    wakeCarrier: 0,
    breath: 0,
    carrier: 0,
    agitationOffset: 0,
    phases,
    inwardCount: Math.floor(count / 2),
    parallaxX: 0,
    parallaxY: 0,
  };
}

/** Per-particle speed factor in 0.6..1: fixed by index, so no two frames disagree. */
export function particleSpeed(index: number): number {
  return 0.6 + 0.4 * ((index * 7919) % 13) / 13;
}

/**
 * Advance the frame state by `dt` seconds towards `intent`.
 *
 * Mutates `state` in place and returns it. `pointerX`/`pointerY` are the
 * pointer in -1..1 for the parallax lean, or 0 when the tier has none.
 */
export function stepScene(
  state: SceneState,
  intent: VisualIntent,
  dt: number,
  pointerX = 0,
  pointerY = 0,
): SceneState {
  const s = state;

  s.scale = approach(s.scale, intent.scale, dt);
  s.opacity = approach(s.opacity, 1 - intent.dim * 0.75, dt);
  s.glow = approach(s.glow, intent.glow, dt);
  s.shellSpread = approach(s.shellSpread, intent.shellSpread, dt);
  s.ringSpin = approach(s.ringSpin, intent.ringSpin, dt);
  s.flowRate = approach(s.flowRate, intent.flowRate, dt);
  s.inward = approach(s.inward, intent.inwardFlow, dt);
  s.topology = approach(s.topology, intent.topology, dt);
  // The pulse follows the measurement quickly: a smoothed envelope is still the
  // envelope, but a laggy one would be drawn as speech after the audio ended.
  s.pulse = approach(s.pulse, intent.pulse, dt, APPROACH_RATE * 3);
  s.ownerVoice = approach(s.ownerVoice, intent.ownerVoice, dt, APPROACH_RATE * 3);
  s.constellationDrift = approach(s.constellationDrift, intent.constellationDrift, dt);
  s.restraint = approach(s.restraint, intent.restraint, dt);
  s.agitation = approach(s.agitation, intent.agitation, dt);
  // The wake surge rises fast and falls with the rest: an alarm starting is an
  // event, and a Core that eased into it over a second would be describing the
  // ramp rather than the moment it began.
  s.wake = approach(s.wake, intent.wakeSurge, dt, APPROACH_RATE * 1.5);

  // Rotation is tied to the reported spin, not to the clock: a core with
  // nothing to do does not turn. The layers turn at different rates and in
  // different senses, which is what gives the structure its depth.
  s.ringAngle = (s.ringAngle + dt * RING_RATE * s.ringSpin) % TWO_PI;
  s.shellAngle = (s.shellAngle - dt * SHELL_RATE * s.ringSpin) % TWO_PI;
  s.latticeAngle = (s.latticeAngle - dt * LATTICE_RATE * s.topology) % TWO_PI;
  s.constellationAngle = (s.constellationAngle + dt * CONSTELLATION_RATE * s.constellationDrift) % TWO_PI;

  // The orbital layers: one rate and one direction each, all of them scaled by
  // the same reported spin, so they stop together when the reporting stops.
  const orbits = s.orbitAngles;
  for (let i = 0; i < orbits.length; i += 1) {
    orbits[i] = (orbits[i] + dt * ORBITAL_RATES[i] * s.ringSpin) % TWO_PI;
  }
  // The circuit layer carries a data pulse only while something is flowing,
  // and the fragments hold station unless the structure itself is turning.
  s.circuitPhase = (s.circuitPhase + dt * CIRCUIT_RATE * s.flowRate) % 1;
  s.fragmentAngle = (s.fragmentAngle + dt * FRAGMENT_RATE * s.ringSpin) % TWO_PI;

  // The rhythms. The clock advances only while something rhythmic was reported,
  // so a still core does not accumulate a phase it will later jump to.
  const rhythmic =
    intent.breathAmplitude > 0 || s.pulse > 0.001 || s.agitation > 0.001 || s.wake > 0.001;
  if (rhythmic) s.clock += dt;
  // The surge's carrier shapes it; its amplitude is the surge, which is the
  // published stage (and the published ramp level). Zero surge, no carrier.
  s.wakeCarrier = s.wake > 0.001 ? 0.5 + 0.5 * Math.sin(s.clock * WAKE_CARRIER_HZ * TWO_PI) : 0;
  s.breath =
    intent.breathAmplitude > 0
      ? Math.sin(s.clock * intent.breathHz * TWO_PI) * intent.breathAmplitude
      : 0;
  // The carrier only shapes the pulse; its amplitude is the measured energy,
  // which is zero for every state that reported none.
  s.carrier = s.pulse > 0.001 ? 0.5 + 0.5 * Math.sin(s.clock * PULSE_CARRIER_HZ * TWO_PI) : 0;
  // Error: one bounded, slow offset. Its rate is the error breath rate the
  // intent already capped; there is no faster term to abuse.
  s.agitationOffset =
    s.agitation > 0.001 ? Math.sin(s.clock * intent.breathHz * TWO_PI) * s.agitation * 0.06 : 0;

  // Particles. The inward population moves at the inward flow, quickened by
  // the owner's measured voice; the path population at the flow rate. A zero
  // channel leaves its population exactly where it was.
  const phases = s.phases;
  const inwardRate = dt * PARTICLE_RATE * s.inward * (0.6 + 0.8 * s.ownerVoice);
  const flowRateStep = dt * PARTICLE_RATE * s.flowRate;
  const n = phases.length;
  const split = s.inwardCount;
  for (let i = 0; i < split; i += 1) {
    phases[i] = (phases[i] + inwardRate * particleSpeed(i)) % 1;
  }
  for (let i = split; i < n; i += 1) {
    phases[i] = (phases[i] + flowRateStep * particleSpeed(i)) % 1;
  }

  // The camera leans a little towards the pointer. Not a claim about the
  // system — a way of seeing the layers — and it settles when the pointer does.
  s.parallaxX = approach(s.parallaxX, pointerX * PARALLAX_REACH, dt, 3);
  s.parallaxY = approach(s.parallaxY, pointerY * PARALLAX_REACH, dt, 3);

  return s;
}

/**
 * True when nothing in the state is moving: every accumulator's driver is at
 * zero and no rhythm is live. Used by the tests to prove a zero intent stays
 * still, and by the scene to stop invalidating in demand mode.
 */
export function sceneIsStill(state: SceneState, intent: VisualIntent, epsilon = 0.002): boolean {
  return (
    state.ringSpin < epsilon &&
    state.topology < epsilon &&
    state.flowRate < epsilon &&
    state.inward < epsilon &&
    state.constellationDrift < epsilon &&
    state.pulse < epsilon &&
    state.agitation < epsilon &&
    state.wake < epsilon &&
    intent.breathAmplitude === 0
  );
}

// ------------------------------------------------------------ geometry

/**
 * `count` points on the unit sphere along a golden-angle spiral, written into
 * `out` (xyz per point). Deterministic, so the same index is the same point in
 * every frame, every mount and every tier — a shell whose points reshuffled
 * would read as activity.
 */
export function fibonacciSphere(count: number, out: Float32Array): Float32Array {
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i += 1) {
    const y = count > 1 ? 1 - (i / (count - 1)) * 2 : 0;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    out[i * 3] = Math.cos(theta) * r;
    out[i * 3 + 1] = y;
    out[i * 3 + 2] = Math.sin(theta) * r;
  }
  return out;
}

/**
 * The connection paths: `segments` chords across the interior, each from a
 * spiral point to the point a third of the way round. Two endpoints (xyz each)
 * per segment, written into `out`.
 */
export function chordEndpoints(segments: number, out: Float32Array): Float32Array {
  if (segments <= 0) return out;
  const golden = Math.PI * (3 - Math.sqrt(5));
  const denom = Math.max(1, segments - 1);
  const stride = Math.floor(segments / 3);
  for (let i = 0; i < segments; i += 1) {
    const y = 1 - (i / denom) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    const j = (i + stride) % segments;
    const y2 = 1 - (j / denom) * 2;
    const r2 = Math.sqrt(Math.max(0, 1 - y2 * y2));
    const theta2 = golden * j;
    const o = i * 6;
    out[o] = Math.cos(theta) * r;
    out[o + 1] = y;
    out[o + 2] = Math.sin(theta) * r;
    out[o + 3] = Math.cos(theta2) * r2;
    out[o + 4] = y2;
    out[o + 5] = Math.sin(theta2) * r2;
  }
  return out;
}

/** The outer radius of the inward flow's start, and the radius it arrives at. */
export const INWARD_START = 1.55;
export const INWARD_END = 0.32;

/** The radius of the evidence constellation and of the capability nodes. */
export const CONSTELLATION_RADIUS = 1.78;
export const FIELD_RADIUS = 2.0;
export const CAPABILITY_RADIUS = 1.7;

// ------------------------------------------ M18.3: the Living Core's layers

/**
 * The layers, outermost first, as world radii (M18.3 §9). The 2D fallback
 * draws the same proportions from these same numbers, so the two views cannot
 * drift apart.
 *
 * Nothing here is a channel: these are where things ARE, not whether they
 * move. Every one of them is still until a channel says otherwise.
 */
/** The outer field: the faintest boundary, a sparse deterministic point cloud. */
export const OUTER_FIELD_RADIUS = 2.18;
/** The independent orbital layers, innermost first. */
export const ORBITAL_RADII = [1.3, 1.62, 1.98] as const;
/** The wake surge's ring: outside the shells, inside the outer field. */
export const WAKE_RADIUS = 1.86;
/** The processor structures: floating fragments, parked at fixed stations. */
export const FRAGMENT_RADIUS = 1.42;
/** The data / circuit layer: procedural traces on a shallow band. */
export const CIRCUIT_RADIUS = 1.2;
/** The energy chamber: the translucent vessel the nucleus sits in. */
export const CHAMBER_RADIUS = 0.92;

/**
 * The camera, stated here rather than in the canvas, because the coverage
 * arithmetic in `layout.ts` depends on it: how much of the viewport the Core
 * fills is a function of the frustum and of how far the structure reaches.
 */
export const CAMERA_DISTANCE = 6.0;
export const CAMERA_FOV_DEG = 42;

/** Half the world extent visible at the camera's distance. */
export const VIEW_HALF_EXTENT =
  Math.tan((CAMERA_FOV_DEG / 2) * (Math.PI / 180)) * CAMERA_DISTANCE;

/** The radius of the Core's principal structure: its outermost orbital layer. */
export const CORE_SPAN_RADIUS = ORBITAL_RADII[ORBITAL_RADII.length - 1];

/**
 * The fraction of the (square) stage the Core's principal structure spans.
 *
 * Derived from the camera rather than typed, so a change to the framing moves
 * the layout with it instead of quietly making the coverage claim false.
 */
export const CORE_FILL = CORE_SPAN_RADIUS / VIEW_HALF_EXTENT;

/**
 * The procedural circuit layer: `segments` traces, each a three-point path
 * that steps outward, runs along an arc and steps back — the geometry of a
 * board trace rather than of a spider's web. Three points (xyz each) per
 * trace, written into `out`.
 *
 * Deterministic in the index, like everything else here: a circuit that
 * redrew itself would read as data moving through it.
 */
export function circuitTraces(segments: number, out: Float32Array): Float32Array {
  if (segments <= 0) return out;
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < segments; i += 1) {
    const a = golden * i;
    // A shallow band rather than a sphere: the circuitry reads as a plane seen
    // at an angle, which is what makes the depth of the shells visible.
    const y = Math.sin(a * 1.7) * 0.34;
    const inner = 0.62 + 0.1 * ((i * 7) % 5) / 5;
    const outer = 0.9 + 0.1 * ((i * 11) % 7) / 7;
    const sweep = 0.18 + 0.22 * ((i * 13) % 4) / 4;
    const o = i * 9;
    out[o] = Math.cos(a) * inner;
    out[o + 1] = y * inner;
    out[o + 2] = Math.sin(a) * inner;
    out[o + 3] = Math.cos(a) * outer;
    out[o + 4] = y * outer;
    out[o + 5] = Math.sin(a) * outer;
    out[o + 6] = Math.cos(a + sweep) * outer;
    out[o + 7] = y * outer;
    out[o + 8] = Math.sin(a + sweep) * outer;
  }
  return out;
}

/**
 * One processor fragment's station: a fixed point on a tilted band, with a
 * fixed size and tilt per index. They are parked structures, not orbits.
 */
export function fragmentStation(
  index: number,
  count: number,
  out: { x: number; y: number; z: number; size: number; tilt: number },
): void {
  const angle = (index / Math.max(1, count)) * TWO_PI;
  const lift = Math.sin(angle * 2 + index) * 0.42;
  const radius = FRAGMENT_RADIUS * (0.86 + 0.14 * ((index * 5) % 3) / 3);
  out.x = Math.cos(angle) * radius;
  out.y = lift;
  out.z = Math.sin(angle) * radius;
  out.size = 0.055 + 0.055 * ((index * 3) % 4) / 4;
  out.tilt = angle * 0.5 + index * 0.31;
}

/**
 * A constellation node's position on its ring: evenly spaced, with a fixed
 * vertical undulation so the ring reads as a band rather than a line.
 * Deterministic in `index` and `count`.
 */
export function constellationPoint(
  index: number,
  count: number,
  radius: number,
  angleOffset: number,
  out: { x: number; y: number; z: number },
): void {
  const angle = angleOffset + (index / Math.max(1, count)) * TWO_PI;
  out.x = Math.cos(angle) * radius;
  out.y = Math.sin(angle * 3) * 0.18;
  out.z = Math.sin(angle) * radius;
}
