/**
 * `PerceptionSession`'s lifecycle: sampling on a schedule, and — the property
 * the M18 task brief asks to be proven in a test — that disable is immediate
 * and total, including when a sample is already in flight.
 *
 * Everything here is a plain-object fake per the task brief's "fake
 * getUserMedia/canvas with plain stubs": no `getUserMedia`, no `<video>`, no
 * `<canvas>`, no browser at all. `FakeFrameSource` stands in for
 * `BrowserFrameSource` behind the same `FrameSource` interface. The first
 * tick is scheduled (not synchronous), like every later one, so every test
 * flushes it with one `vi.advanceTimersByTimeAsync(0)` — there is only one
 * scheduling mechanism in `PerceptionSession`, not a special-cased first tick.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PerceptionSession,
  type FrameReducer,
  type FrameSource,
} from "../../app/lib/eye/perception";
import type { EyeObservation } from "../../app/lib/eye/types";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

class FakeFrameSource implements FrameSource {
  startCalls = 0;
  stopCalls = 0;
  labelValue: string | null = "fake-cam";
  // A tiny, uniform, synthetic "frame" — never real imagery. It lives here, not
  // in the session: the fake is the only thing on either side that holds pixels,
  // which is the same shape as production.
  private pixels: Uint8Array | null = new Uint8Array(4 * 4 * 4);
  onCapture: (() => void) | null = null;
  startImpl: (() => Promise<void>) | null = null;

  async start(): Promise<void> {
    this.startCalls += 1;
    if (this.startImpl) await this.startImpl();
  }

  sample(reduce: FrameReducer): Float32Array | null {
    this.onCapture?.();
    if (!this.pixels) return null;
    return reduce(this.pixels, 4, 4);
  }

  stop(): void {
    this.stopCalls += 1;
  }

  label(): string | null {
    return this.labelValue;
  }
}

type PostResult =
  | { status: "posted" }
  | { status: "rejected"; detail: string }
  | { status: "eye_disabled" };

function buildSession(overrides: {
  frameSource?: FakeFrameSource;
  postObservation?: (obs: EyeObservation, opts?: { signal?: AbortSignal }) => Promise<PostResult>;
  sampleIntervalMs?: number;
} = {}) {
  const frameSource = overrides.frameSource ?? new FakeFrameSource();
  const postObservation =
    overrides.postObservation ?? vi.fn(async () => ({ status: "posted" as const }));
  const onObservation = vi.fn();
  const onStatusChange = vi.fn();
  const session = new PerceptionSession({
    frameSource,
    postObservation,
    sampleIntervalMs: overrides.sampleIntervalMs ?? 1000,
    onObservation,
    onStatusChange,
    now: () => Date.now(),
  });
  return { session, frameSource, postObservation, onObservation, onStatusChange };
}

/** Starts the session and flushes the first (scheduled, delay-0) tick. */
async function startAndFlush(session: PerceptionSession): Promise<void> {
  await session.start();
  await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the sampling loop", () => {
  it("captures and posts on the configured interval, not before", async () => {
    const { session, postObservation } = buildSession({ sampleIntervalMs: 1000 });
    await startAndFlush(session);
    expect(postObservation).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(999);
    expect(postObservation).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(postObservation).toHaveBeenCalledTimes(2);

    session.stop();
  });

  it("an option passed as an explicit undefined still gets the default: the loop samples every 5 s, not every 0 ms", async () => {
    // What `useActivePerception()` used to hand the session when called with
    // no options: the key present, the value undefined. With the defaults
    // spread BEFORE the options, `setTimeout(fn, undefined)` ran the loop as
    // fast as the POST round trip allowed on the owner's real camera.
    const postObservation = vi.fn(async () => ({ status: "posted" as const }));
    const session = new PerceptionSession({
      frameSource: new FakeFrameSource(),
      postObservation,
      sampleIntervalMs: undefined,
      now: undefined,
    });
    await startAndFlush(session);
    expect(postObservation).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(postObservation).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(postObservation).toHaveBeenCalledTimes(2);
    session.stop();
  });

  it("posts exactly the seven-field EyeObservation shape derived from the frame", async () => {
    const { session, postObservation } = buildSession();
    await startAndFlush(session);
    const [observation] = (postObservation as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(Object.keys(observation).toSorted()).toEqual(
      [
        "activity_level",
        "awake_state",
        "observed_at",
        "person_present",
        "posture",
        "presence_confidence",
        "source",
      ].toSorted(),
    );
    expect(observation.source).toBe("camera");
    session.stop();
  });

  it("surfaces the camera label and the last observation via onStatusChange", async () => {
    const { session, onStatusChange } = buildSession();
    await startAndFlush(session);
    const last = onStatusChange.mock.calls.at(-1)![0];
    expect(last.running).toBe(true);
    expect(last.cameraLabel).toBe("fake-cam");
    expect(last.lastObservation).not.toBeNull();
    session.stop();
  });
});

describe("stop() is immediate and total", () => {
  it("releases the frame source synchronously, before any pending network work resolves", async () => {
    const post = deferred<PostResult>();
    const { session, frameSource } = buildSession({ postObservation: () => post.promise });
    await startAndFlush(session); // tick 0 is now awaiting `post.promise`

    expect(frameSource.stopCalls).toBe(0);
    session.stop();
    // Synchronous: no await needed before this is true. This is the
    // "browser's own camera light must go out" guarantee.
    expect(frameSource.stopCalls).toBe(1);

    post.resolve({ status: "posted" });
    await vi.advanceTimersByTimeAsync(0);
    // The camera was not silently reopened or re-stopped by the tick finishing late.
    expect(frameSource.stopCalls).toBe(1);
  });

  it("aborts an in-flight observation POST rather than leaving it uncancelled", async () => {
    let capturedSignal: AbortSignal | undefined;
    const post = deferred<PostResult>();
    const { session } = buildSession({
      postObservation: (_obs, opts) => {
        capturedSignal = opts?.signal;
        return post.promise;
      },
    });
    await startAndFlush(session);
    expect(capturedSignal?.aborted).toBe(false);
    session.stop();
    expect(capturedSignal?.aborted).toBe(true);
    post.resolve({ status: "posted" });
    await vi.advanceTimersByTimeAsync(0);
  });

  it("never schedules another tick once stopped, even after the in-flight post resolves", async () => {
    const post = deferred<PostResult>();
    const postObservation = vi.fn(() => post.promise);
    const { session } = buildSession({ postObservation, sampleIntervalMs: 1000 });
    await startAndFlush(session);
    expect(postObservation).toHaveBeenCalledTimes(1);

    session.stop();
    post.resolve({ status: "posted" });
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(10_000); // plenty of time for a next tick, if one were scheduled

    expect(postObservation).toHaveBeenCalledTimes(1); // still just the one in-flight sample
  });

  it("a sample cannot be posted at all once stop() has run before the pre-post check — even reentrantly, mid-tick", async () => {
    // sample() runs synchronously inside tick(), before the pre-post guard. A
    // frame source that calls stop() from inside sample() proves the guard
    // works even in the tightest possible race: everything up to and
    // including the check happens in one synchronous stretch of code, so
    // there is no interleaving JavaScript could ever produce between them —
    // this test exercises that same guard the only way it CAN be exercised.
    const frameSource = new FakeFrameSource();
    const postObservation = vi.fn(async () => ({ status: "posted" as const }));
    const { session } = buildSession({ frameSource, postObservation, sampleIntervalMs: 1000 });
    frameSource.onCapture = () => session.stop();

    await startAndFlush(session);
    expect(postObservation).not.toHaveBeenCalled();
    expect(frameSource.stopCalls).toBeGreaterThanOrEqual(1);
  });

  it("start() racing a stop() during camera setup never leaves a track running", async () => {
    const opening = deferred<void>();
    const frameSource = new FakeFrameSource();
    frameSource.startImpl = () => opening.promise;
    const { session, onStatusChange } = buildSession({ frameSource });

    const startPromise = session.start(); // awaiting frameSource.start()
    session.stop(); // disabled before the camera even finished opening
    opening.resolve();
    await startPromise;

    expect(frameSource.stopCalls).toBeGreaterThanOrEqual(1);
    expect(onStatusChange.mock.calls.some(([s]) => s.running === true)).toBe(false);
  });
});

describe("server-side disable reconciliation", () => {
  it("a 409 from the server stops the local loop too — no reason to keep sampling", async () => {
    const postObservation = vi.fn(async () => ({ status: "eye_disabled" as const }));
    const { session, frameSource, onStatusChange } = buildSession({ postObservation, sampleIntervalMs: 1000 });
    await startAndFlush(session);

    expect(frameSource.stopCalls).toBeGreaterThanOrEqual(1);
    expect(onStatusChange.mock.calls.at(-1)![0].running).toBe(false);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(postObservation).toHaveBeenCalledTimes(1); // did not keep sampling after the server said no
  });

  it("a 422 rejection is surfaced as an error but does not stop sampling", async () => {
    const postObservation = vi.fn(async () => ({ status: "rejected" as const, detail: "kötü alan" }));
    const { session, onStatusChange } = buildSession({ postObservation, sampleIntervalMs: 1000 });
    await startAndFlush(session);
    expect(onStatusChange.mock.calls.at(-1)![0].lastError).toBe("kötü alan");

    await vi.advanceTimersByTimeAsync(1000);
    expect(postObservation).toHaveBeenCalledTimes(2); // still running

    session.stop();
  });
});
