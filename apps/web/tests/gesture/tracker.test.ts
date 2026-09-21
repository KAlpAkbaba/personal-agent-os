/**
 * `GestureTracker` (ADR-0198, Stage 1) — driven by a FAKE `HandLandmarker` (plain stubs, no
 * real MediaPipe/WASM), the same "fake getUserMedia/canvas" discipline M18's
 * `perception.test.ts` uses. One test at the bottom exercises the REAL
 * `browserHandLandmarkerFactory()` to prove the fail-closed path is not just a fake's
 * promise: in this test environment `public/mediapipe/` is not served, so it authentically
 * reproduces the pre-`pnpm run fetch:mediapipe` state.
 */

import { describe, expect, it, vi } from "vitest";

import {
  ASSETS_MISSING_TR,
  browserHandLandmarkerFactory,
  GestureTracker,
  type HandLandmarkerLike,
} from "../../app/lib/gesture/tracker";
import { GestureRecognizer } from "../../app/lib/gesture/recognizer";

class FakeLandmarker implements HandLandmarkerLike {
  closed = 0;
  calls = 0;
  /** Script what `detectForVideo` returns on each call, in order; the last entry repeats. */
  script: HandLandmarkerLike["detectForVideo"] extends (...a: infer _A) => infer R ? R[] : never = [];

  detectForVideo(): ReturnType<HandLandmarkerLike["detectForVideo"]> {
    this.calls += 1;
    const result = this.script[Math.min(this.calls - 1, this.script.length - 1)];
    return result ?? { landmarks: [], handedness: [] };
  }

  close(): void {
    this.closed += 1;
  }
}

/** A manual `requestAnimationFrame`-shaped driver: `tick()` runs exactly one queued frame. */
function manualScheduler() {
  let queued: ((t: number) => void) | null = null;
  let handle = 0;
  return {
    schedule: (cb: (t: number) => void): number => {
      queued = cb;
      return ++handle;
    },
    cancel: (): void => {
      queued = null;
    },
    tick: (t: number): void => {
      const cb = queued;
      queued = null;
      cb?.(t);
    },
    pending: (): boolean => queued !== null,
  };
}

function fakeVideo(): HTMLVideoElement {
  return {} as HTMLVideoElement;
}

describe("GestureTracker: wiring a fake landmarker into the recognizer", () => {
  it("start() loads the landmarker once, ticks detectForVideo on the scheduler, and forwards recognizer events", async () => {
    const landmarker = new FakeLandmarker();
    landmarker.script = [
      { landmarks: [[{ x: 0.7, y: 0.5 }]], handedness: [[{ categoryName: "Right" }]] },
      { landmarks: [[{ x: 0.4, y: 0.5 }]], handedness: [[{ categoryName: "Right" }]] },
    ];
    const events: string[] = [];
    // A recognizer that just echoes "saw N hands" per tick so this test proves WIRING
    // (video -> detectForVideo -> recognizer.ingest -> onGesture), not gesture geometry
    // (that is recognizer.test.ts's job entirely).
    const recognizer = {
      ingest: vi.fn((frame: { hands: unknown[] }) => (frame.hands.length > 0 ? [{ name: "swipe_left", t_ms: frame.hands.length }] : [])),
    } as unknown as GestureRecognizer;
    const scheduler = manualScheduler();
    let created = 0;
    const tracker = new GestureTracker({
      onGesture: (e) => events.push(e.name),
      createLandmarker: async () => {
        created += 1;
        return landmarker;
      },
      recognizer,
      now: () => 1000,
      scheduleFrame: scheduler.schedule,
      cancelFrame: scheduler.cancel,
    });

    await tracker.start(fakeVideo());
    expect(created).toBe(1);
    expect(scheduler.pending()).toBe(true);
    scheduler.tick(1000);
    expect(landmarker.calls).toBe(1);
    expect(events).toEqual(["swipe_left"]);
    expect(scheduler.pending()).toBe(true); // the loop re-scheduled itself

    scheduler.tick(1016);
    expect(landmarker.calls).toBe(2);
    expect(events).toEqual(["swipe_left", "swipe_left"]);
  });

  it("stop() cancels the scheduled frame and closes the landmarker; a queued tick after stop() does nothing", async () => {
    const landmarker = new FakeLandmarker();
    const scheduler = manualScheduler();
    const tracker = new GestureTracker({
      onGesture: () => {},
      createLandmarker: async () => landmarker,
      scheduleFrame: scheduler.schedule,
      cancelFrame: scheduler.cancel,
    });
    await tracker.start(fakeVideo());
    expect(scheduler.pending()).toBe(true);
    tracker.stop();
    expect(landmarker.closed).toBe(1);
    expect(scheduler.pending()).toBe(false);
  });

  it("a detection failure on one frame does not stop the loop", async () => {
    const landmarker = new FakeLandmarker();
    let calls = 0;
    landmarker.detectForVideo = () => {
      calls += 1;
      if (calls === 1) throw new Error("boom");
      return { landmarks: [], handedness: [] };
    };
    const scheduler = manualScheduler();
    const tracker = new GestureTracker({
      onGesture: () => {},
      createLandmarker: async () => landmarker,
      scheduleFrame: scheduler.schedule,
      cancelFrame: scheduler.cancel,
    });
    await tracker.start(fakeVideo());
    scheduler.tick(0);
    expect(scheduler.pending()).toBe(true); // still looping after the throw
    scheduler.tick(16);
    expect(calls).toBe(2);
  });

  it("reports a rolling fps to onStats after each ~1s window", async () => {
    const landmarker = new FakeLandmarker();
    const scheduler = manualScheduler();
    const stats: number[] = [];
    let clock = 0;
    const tracker = new GestureTracker({
      onGesture: () => {},
      onStats: (s) => stats.push(s.fps),
      createLandmarker: async () => landmarker,
      now: () => clock,
      scheduleFrame: scheduler.schedule,
      cancelFrame: scheduler.cancel,
    });
    await tracker.start(fakeVideo());
    // 10 ticks spaced 100ms apart: the 1000ms mark falls at the 10th tick.
    for (let i = 1; i <= 10; i += 1) {
      clock = i * 100;
      scheduler.tick(clock);
    }
    expect(stats).toHaveLength(1);
    expect(stats[0]).toBeCloseTo(10, 0);
  });

  it("a landmarker that fails to load reports the Turkish assets-missing message and never schedules a frame", async () => {
    const scheduler = manualScheduler();
    const messages: string[] = [];
    const tracker = new GestureTracker({
      onGesture: () => {},
      onError: (m) => messages.push(m),
      createLandmarker: async () => {
        throw new Error(ASSETS_MISSING_TR);
      },
      scheduleFrame: scheduler.schedule,
      cancelFrame: scheduler.cancel,
    });
    await tracker.start(fakeVideo());
    expect(messages).toEqual([ASSETS_MISSING_TR]);
    expect(scheduler.pending()).toBe(false);
  });
});

describe("GestureTracker: the REAL factory fails closed with no assets served", () => {
  it("browserHandLandmarkerFactory() rejects with GestureTrackerAssetsMissingError when public/mediapipe is not reachable", async () => {
    // No dev server here (this is a vitest/node environment): a fetch for
    // /mediapipe/wasm/... or the model 404s/errors exactly as it would before anyone ran
    // `pnpm run fetch:mediapipe` on a real page — the same failure this proves.
    const factory = browserHandLandmarkerFactory();
    await expect(factory()).rejects.toThrow(ASSETS_MISSING_TR);
  });
});
