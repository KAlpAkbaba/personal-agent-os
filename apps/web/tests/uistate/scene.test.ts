/**
 * The frame reducer (M18.1, ADR-0065): the 3D scene's per-frame arithmetic,
 * run in Node with no three.js, no WebGL and no browser.
 *
 * What is pinned: a zero intent stays still forever; motion is a function of
 * elapsed seconds, not of how many frames were drawn; the state is mutated in
 * place and never reallocated; and — structurally, by reading the source — the
 * reducer and the scene's frame body contain nothing that allocates.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  CONSTELLATION_RATE,
  RING_RATE,
  type SceneState,
  approach,
  chordEndpoints,
  createSceneState,
  fibonacciSphere,
  particleSpeed,
  sceneIsStill,
  stepScene,
} from "../../app/lib/uistate/scene";
import { visualFor } from "../../app/lib/uistate/visual";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import {
  AGENT_IDLE,
  RESEARCH_RANKING,
  SELFMODEL_THINKING,
  T0,
  VOICE_LISTENING,
  VOICE_SPEAKING,
  resetSequence,
  response,
} from "./fixtures";

function intentOf(make: () => ReturnType<typeof AGENT_IDLE>) {
  resetSequence();
  return visualFor(applyResponse(emptyTruth(), response([make()]), T0), T0);
}

const UNTOLD = visualFor(applyResponse(emptyTruth(), response([]), T0), T0);

function run(state: SceneState, intent: Parameters<typeof stepScene>[1], seconds: number, fps: number) {
  const dt = 1 / fps;
  const frames = Math.round(seconds * fps);
  for (let i = 0; i < frames; i += 1) stepScene(state, intent, dt);
  return state;
}

describe("a zero intent stays still", () => {
  it("an untold core never moves, however many frames pass", () => {
    const state = createSceneState(64);
    const before = Array.from(state.phases);
    run(state, UNTOLD, 30, 60);
    expect(sceneIsStill(state, UNTOLD)).toBe(true);
    expect(state.ringAngle).toBe(0);
    expect(state.latticeAngle).toBe(0);
    expect(state.constellationAngle).toBe(0);
    expect(state.breath).toBe(0);
    expect(state.carrier).toBe(0);
    expect(state.clock).toBe(0);
    expect(Array.from(state.phases)).toEqual(before);
  });

  it("settles on the intent's targets and holds them", () => {
    const thinking = intentOf(SELFMODEL_THINKING);
    const state = run(createSceneState(16), thinking, 5, 60);
    expect(state.scale).toBeCloseTo(thinking.scale, 3);
    expect(state.shellSpread).toBeCloseTo(thinking.shellSpread, 3);
    expect(state.ringSpin).toBeCloseTo(thinking.ringSpin, 3);
    expect(state.topology).toBeCloseTo(thinking.topology, 3);
    expect(state.glow).toBeCloseTo(thinking.glow, 3);
    // Approach never overshoots: from 1 towards 1.12 the scale stays within.
    expect(state.scale).toBeLessThanOrEqual(thinking.scale + 1e-6);
  });

  it("returning to silence stops the rotation where it is", () => {
    const state = run(createSceneState(16), intentOf(SELFMODEL_THINKING), 3, 60);
    expect(state.ringAngle).not.toBe(0);
    run(state, UNTOLD, 5, 60);
    const held = state.ringAngle;
    run(state, UNTOLD, 5, 60);
    expect(state.ringAngle).toBeCloseTo(held, 3);
    expect(sceneIsStill(state, UNTOLD)).toBe(true);
  });

  it("a state that reported no motion has no breath and no carrier", () => {
    const state = run(createSceneState(0), UNTOLD, 2, 60);
    expect(state.breath).toBe(0);
    expect(state.carrier).toBe(0);
    expect(state.agitationOffset).toBe(0);
  });
});

describe("motion depends on elapsed time, not on frame count", () => {
  it("sixty small steps and twenty large ones reach the same picture", () => {
    const thinking = intentOf(SELFMODEL_THINKING);
    const fast = run(createSceneState(32), thinking, 4, 60);
    const slow = run(createSceneState(32), thinking, 4, 20);
    expect(fast.scale).toBeCloseTo(slow.scale, 2);
    expect(fast.shellSpread).toBeCloseTo(slow.shellSpread, 2);
    expect(fast.ringSpin).toBeCloseTo(slow.ringSpin, 2);
    expect(fast.ringAngle).toBeCloseTo(slow.ringAngle, 1);
    expect(fast.latticeAngle).toBeCloseTo(slow.latticeAngle, 1);
    for (let i = 0; i < 32; i += 1) {
      expect(fast.phases[i]).toBeCloseTo(slow.phases[i], 1);
    }
  });

  it("`approach` is exact in dt: two half steps equal one full step", () => {
    const one = approach(0, 1, 0.2);
    const two = approach(approach(0, 1, 0.1), 1, 0.1);
    expect(two).toBeCloseTo(one, 10);
  });

  it("the rings turn at the declared rate once settled", () => {
    const idle = intentOf(AGENT_IDLE);
    const state = run(createSceneState(0), idle, 10, 60);
    const before = state.ringAngle;
    stepScene(state, idle, 1);
    expect(state.ringAngle - before).toBeCloseTo(RING_RATE * idle.ringSpin, 2);
  });

  it("the constellation drifts only at its published-or-rest figure", () => {
    const research = intentOf(() => RESEARCH_RANKING(12, 5));
    const state = run(createSceneState(0), research, 10, 60);
    const before = state.constellationAngle;
    stepScene(state, research, 1);
    expect(state.constellationAngle - before).toBeCloseTo(CONSTELLATION_RATE * research.constellationDrift, 2);
  });
});

describe("the two flows move only their own population", () => {
  it("listening moves the inward population and leaves the path travellers", () => {
    const state = createSceneState(20);
    const before = Array.from(state.phases);
    run(state, intentOf(VOICE_LISTENING), 2, 60);
    for (let i = 0; i < state.inwardCount; i += 1) expect(state.phases[i]).not.toBe(before[i]);
    for (let i = state.inwardCount; i < 20; i += 1) expect(state.phases[i]).toBe(before[i]);
  });

  it("thinking moves the path travellers and leaves the inward population", () => {
    const state = createSceneState(20);
    const before = Array.from(state.phases);
    run(state, intentOf(SELFMODEL_THINKING), 2, 60);
    for (let i = 0; i < state.inwardCount; i += 1) expect(state.phases[i]).toBe(before[i]);
    for (let i = state.inwardCount; i < 20; i += 1) expect(state.phases[i]).not.toBe(before[i]);
  });

  it("the speaking pulse is the measured envelope shaped by a carrier, and zero without it", () => {
    const loud = run(createSceneState(0), intentOf(() => VOICE_SPEAKING(0.8)), 2, 60);
    expect(loud.pulse).toBeCloseTo(0.8, 2);
    expect(loud.carrier).toBeGreaterThanOrEqual(0);
    expect(loud.carrier).toBeLessThanOrEqual(1);
    const silent = run(createSceneState(0), intentOf(() => VOICE_SPEAKING(null)), 2, 60);
    expect(silent.pulse).toBe(0);
    expect(silent.carrier).toBe(0);
  });

  it("particle speeds are fixed by index and bounded", () => {
    for (let i = 0; i < 200; i += 1) {
      const speed = particleSpeed(i);
      expect(speed).toBeGreaterThanOrEqual(0.6);
      expect(speed).toBeLessThanOrEqual(1);
      expect(particleSpeed(i)).toBe(speed);
    }
  });
});

describe("the state is mutated in place, never reallocated", () => {
  it("returns the same object and keeps the same buffer", () => {
    const state = createSceneState(48);
    const phases = state.phases;
    const thinking = intentOf(SELFMODEL_THINKING);
    for (let i = 0; i < 500; i += 1) {
      expect(stepScene(state, thinking, 1 / 60)).toBe(state);
    }
    expect(state.phases).toBe(phases);
    expect(state.phases.length).toBe(48);
  });

  it("geometry helpers write into the caller's buffer, deterministically", () => {
    const a = fibonacciSphere(12, new Float32Array(36));
    const b = fibonacciSphere(12, new Float32Array(36));
    expect(Array.from(a)).toEqual(Array.from(b));
    for (let i = 0; i < 12; i += 1) {
      const x = a[i * 3];
      const y = a[i * 3 + 1];
      const z = a[i * 3 + 2];
      expect(Math.hypot(x, y, z)).toBeCloseTo(1, 5);
    }
    const chords = chordEndpoints(9, new Float32Array(54));
    expect(Array.from(chords)).toEqual(Array.from(chordEndpoints(9, new Float32Array(54))));
  });
});

/** The source between two markers, asserting both exist. */
function body(source: string, start: string, end: string): string {
  const from = source.indexOf(start);
  expect(from, `${start} not found`).toBeGreaterThanOrEqual(0);
  const to = source.indexOf(end, from);
  expect(to, `${end} not found`).toBeGreaterThan(from);
  return source.slice(from, to);
}

/**
 * By construction: the reducer body and the scene's frame body contain no
 * allocating construct. This reads the source, so a later edit that adds a
 * `new Vector3()` to the frame fails here rather than in a profiler.
 */
describe("no allocation in the frame, structurally", () => {
  const ALLOCATING = [
    "new ",
    "Array.from",
    ".map(",
    ".filter(",
    ".slice(",
    ".concat(",
    ".forEach(",
    "Object.keys",
    "Object.entries",
    "JSON.",
    "=> ({",
    "= {",
    "= [",
    "push(",
  ];

  it("stepScene allocates nothing", () => {
    const source = readFileSync(resolve(__dirname, "../../app/lib/uistate/scene.ts"), "utf8");
    const reducer = body(source, "export function stepScene(", "export function sceneIsStill(");
    for (const construct of ALLOCATING) {
      expect(reducer, construct).not.toContain(construct);
    }
  });

  it("the 3D frame body allocates nothing", () => {
    const source = readFileSync(resolve(__dirname, "../../app/core/CoreScene.tsx"), "utf8");
    // From the frame-application function through the frame loop and the
    // reduced-motion settle, up to the render: everything that runs per frame.
    const frame = body(source, "const applyFrame = (", "// ------------------------------------------------------------ render");
    expect(frame).toContain("useFrame((");
    for (const construct of ALLOCATING) {
      expect(frame, construct).not.toContain(construct);
    }
    // And it draws from the reducer rather than doing its own arithmetic.
    expect(frame).toContain("stepScene(");
    // A hidden tab does no work: the loop returns before any arithmetic.
    expect(frame).toContain("if (hidden || still) return;");
  });
});
