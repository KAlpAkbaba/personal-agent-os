/**
 * The owner's 2026-09-06 run, replayed against a fake camera.
 *
 * What happened: the owner sat in front of the camera for eleven minutes and the pipeline
 * reported `away` the whole time, because presence was "moving right now, as a whole-frame
 * mean". These tests drive `PerceptionSession` with synthetic frames — a seated owner who
 * moves a few cells every so often, an owner who walks out, an empty room — and assert what
 * the seven-field observation says at each point. No browser, no imagery: a 24×18 flat
 * frame with one block that "moves".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type FrameReducer,
  type FrameSource,
  PerceptionSession,
} from "../../app/lib/eye/perception";
import { LINGER_MS, PRESENCE_MEMORY_MS } from "../../app/lib/eye/signal";
import type { EyeObservation } from "../../app/lib/eye/types";
import { blockFrame, flatFrame } from "./fixtures";

const W = 24;
const H = 18;

/** A camera whose next frame the test chooses; pixels never leave the fake. */
class ScriptedCamera implements FrameSource {
  private frame: Uint8ClampedArray = flatFrame(W, H, 100);
  stopped = 0;

  still(): void {
    this.frame = flatFrame(W, H, 100);
  }

  /** A small movement: a 4×4-pixel block (four grid cells of 108) changes strongly. */
  fidget(gray = 200): void {
    this.frame = blockFrame(W, H, 100, { x0: 0, y0: 0, x1: 4, y1: 4, gray });
  }

  /** An exit: half the frame changes — someone walking through and out. */
  exit(gray = 200): void {
    this.frame = blockFrame(W, H, 100, { x0: 0, y0: 0, x1: 16, y1: 12, gray });
  }

  async start(): Promise<void> {}
  sample(reduce: FrameReducer): Float32Array | null {
    return reduce(this.frame, W, H);
  }
  stop(): void {
    this.stopped += 1;
  }
  label(): string | null {
    return "scripted-cam";
  }
}

const INTERVAL = 5_000;

function harness() {
  let now = 1_000_000;
  const camera = new ScriptedCamera();
  const posted: EyeObservation[] = [];
  const session = new PerceptionSession({
    frameSource: camera,
    sampleIntervalMs: INTERVAL,
    now: () => now,
    postObservation: async (obs) => {
      posted.push(obs);
      return { status: "posted" as const };
    },
  });
  /** Advance one sample interval (both the fake clock and the timers). */
  const tick = async (n = 1) => {
    for (let i = 0; i < n; i++) {
      now += INTERVAL;
      await vi.advanceTimersByTimeAsync(INTERVAL);
    }
  };
  const last = () => posted[posted.length - 1];
  return { camera, session, posted, tick, last };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("a seated owner", () => {
  it("who fidgets every few samples is PRESENT throughout, at real confidence", async () => {
    const { camera, session, tick, last } = harness();
    await session.start();
    await vi.advanceTimersByTimeAsync(0); // first sample: nothing to compare against yet
    camera.fidget();
    await tick(); // the movement is seen against the still frame
    expect(last().person_present).toBe(true);
    expect(last().activity_level).not.toBe("none");

    camera.still();
    await tick(6); // thirty seconds of sitting still - well inside the memory window
    expect(last().person_present).toBe(true);
    expect(last().presence_confidence).toBeGreaterThan(0.4);
    expect(last().activity_level).toBe("none");

    camera.fidget(20); // a different small movement
    await tick();
    expect(last().person_present).toBe(true);
    session.stop();
  });

  it("who sits very still after a small movement stays present, weakly, then is let go", async () => {
    const { camera, session, tick, last } = harness();
    await session.start();
    await vi.advanceTimersByTimeAsync(0);
    camera.fidget();
    await tick();
    camera.still();

    await tick(Math.ceil(PRESENCE_MEMORY_MS / INTERVAL) + 2); // just past the memory window
    expect(last().person_present).toBe(true);
    expect(last().presence_confidence).toBeLessThan(0.65);

    await tick(Math.ceil(LINGER_MS / INTERVAL) + 2); // and past the linger window
    expect(last().person_present).toBe(false);
    expect(last().presence_confidence).toBeLessThanOrEqual(0.75);
    session.stop();
  });
});

describe("an owner who leaves", () => {
  it("is absent once the exit burst is followed by stillness past the memory window", async () => {
    const { camera, session, tick, last } = harness();
    await session.start();
    await vi.advanceTimersByTimeAsync(0);
    camera.fidget();
    await tick();
    camera.exit();
    await tick(); // the large change
    expect(last().activity_level).toBe("high");
    camera.still();
    await tick(2);
    expect(last().person_present).toBe(true); // still inside the memory window

    await tick(Math.ceil(PRESENCE_MEMORY_MS / INTERVAL) + 1);
    expect(last().person_present).toBe(false);
    expect(last().presence_confidence).toBeGreaterThanOrEqual(0.5);
    session.stop();
  });
});

describe("an empty room", () => {
  it("is absent at low confidence, and never claims certainty", async () => {
    const { session, tick, last } = harness();
    await session.start();
    await vi.advanceTimersByTimeAsync(0);
    await tick(20);
    expect(last().person_present).toBe(false);
    expect(last().presence_confidence).toBeLessThanOrEqual(0.2);
    session.stop();
  });
});

describe("the diagnostics behind the verdict", () => {
  it("are exposed as numbers on the status, never a frame", async () => {
    const { camera, session, tick } = harness();
    await session.start();
    await vi.advanceTimersByTimeAsync(0);
    camera.fidget();
    await tick();
    const status = session.getStatus();
    expect(status.motion).not.toBeNull();
    expect(status.motion!.changedCellRatio).toBeGreaterThan(0);
    expect(status.motion!.msSinceLastMotion).toBe(0);
    expect(status.motion!.lastMotionLevel).not.toBe("none");
    expect(Object.keys(status.motion!).toSorted()).toEqual(
      ["changedCellRatio", "lastMotionLevel", "maxCellDelta", "msSinceLastMotion"].toSorted(),
    );
    session.stop();
  });
});
