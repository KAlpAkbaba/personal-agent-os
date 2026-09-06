/**
 * The one-eye property (M18_ACTION_CONTRACT.md §7.1).
 *
 * Every view that shows the eye reads `EyeStore`. What must be true, and is
 * asserted here with `eyeInstances` rather than assumed: however many views
 * subscribe, however often one mounts and unmounts, and however many times
 * "enable" is asked for at once, there is exactly ONE `PerceptionSession`,
 * ONE camera open and ONE sampling loop. And the state machine is honest:
 * ENABLING is observable while the camera is opening, every camera failure
 * lands on one of the four receipt error classes, and a failure is an answer,
 * never an exception.
 *
 * The camera is a fake `FrameSource` whose `start()` the test settles by
 * hand (so ENABLING can be seen), the way `presence-memory.test.ts` scripts
 * its frames. No browser, no camera, no imagery: a 4×4 flat frame.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOCAL_EYE_ERROR_TEXT } from "../../app/lib/eye/labels";
import {
  CameraTrackEndedError,
  type FrameReducer,
  type FrameSource,
  PerceptionStartError,
} from "../../app/lib/eye/perception";
import {
  EyeStore,
  LOCAL_EYE_TIMEOUT_MS,
  MAX_ACTION_TRACE,
  classifyCameraError,
  eyeInstances,
  getEyeStore,
  installEyeStore,
} from "../../app/lib/eye/store";
import { EYE_ERROR_CLASSES, type EyeObservation } from "../../app/lib/eye/types";
import { useActivePerception } from "../../app/lib/eye/useActivePerception";
import { flatFrame } from "./fixtures";

/** A camera whose `start()` the test settles; pixels never leave the fake. */
class Camera implements FrameSource {
  starts = 0;
  stops = 0;
  /** True between a settled `start()` and the next `stop()`. */
  open = false;
  /** `start()` calls made while a previous open was still live: must stay 0. */
  overlaps = 0;
  /** Resolve `start()` immediately (true) or wait for `opened()` / `failed()`. */
  auto = true;
  private pending: { resolve: () => void; reject: (err: unknown) => void } | null = null;
  private readonly pixels = flatFrame(4, 4, 100);

  start(): Promise<void> {
    this.starts += 1;
    if (this.open) this.overlaps += 1;
    if (this.auto) {
      this.open = true;
      return Promise.resolve();
    }
    return new Promise<void>((resolve, reject) => {
      this.pending = {
        resolve: () => {
          this.open = true;
          resolve();
        },
        reject,
      };
    });
  }

  /** The owner granted / the device came up. */
  opened(): void {
    const pending = this.pending;
    this.pending = null;
    pending?.resolve();
  }

  /** getUserMedia rejected. */
  failed(err: unknown): void {
    const pending = this.pending;
    this.pending = null;
    pending?.reject(err);
  }

  sample(reduce: FrameReducer): Float32Array | null {
    return this.open ? reduce(this.pixels, 4, 4) : null;
  }

  stop(): void {
    this.stops += 1;
    this.open = false;
  }

  label(): string | null {
    return this.open ? "fake-cam" : null;
  }
}

type Durable = { kind: "enable" | "disable"; reason: string };

function harness(options: { auto?: boolean; cameraAvailable?: boolean } = {}) {
  let now = 1_700_000_000_000;
  const camera = new Camera();
  camera.auto = options.auto ?? true;
  const durable: Durable[] = [];
  const posted: EyeObservation[] = [];
  let postStatus: "posted" | "eye_disabled" = "posted";
  /** Durable calls resolve at once unless held; held ones are released by `releaseDurable()`. */
  let hold = false;
  const held: Array<() => void> = [];
  const durableCall = (kind: Durable["kind"]) => (reason: string) => {
    durable.push({ kind, reason });
    if (!hold) return Promise.resolve();
    return new Promise<void>((resolve) => held.push(resolve));
  };
  let builds = 0;
  const store = new EyeStore({
    build: () => {
      builds += 1;
      return {
        frameSource: camera,
        postObservation: async (observation: EyeObservation) => {
          posted.push(observation);
          return { status: postStatus };
        },
        sampleIntervalMs: 1000,
        now: () => now,
        cameraAvailable: () => options.cameraAvailable ?? true,
      };
    },
    durable: { enable: durableCall("enable"), disable: durableCall("disable") },
  });
  return {
    store,
    camera,
    durable,
    posted,
    get builds() {
      return builds;
    },
    holdDurable: () => {
      hold = true;
    },
    releaseDurable: () => {
      hold = false;
      for (const release of held.splice(0)) release();
    },
    disableServerSide: () => {
      postStatus = "eye_disabled";
    },
    advance: async (ms: number) => {
      now += ms;
      await vi.advanceTimersByTimeAsync(ms);
    },
    flush: () => vi.advanceTimersByTimeAsync(0),
  };
}

const state = (t: ReturnType<typeof harness>) => t.store.getSnapshot().state;

/** A consumer exactly like `EyeControl`'s: one hook call, one span. */
function Probe() {
  const { status, permission, busy, error, start, stop, stopLocalOnly } = useActivePerception();
  const shape = [typeof start, typeof stop, typeof stopLocalOnly].join(",");
  return createElement(
    "span",
    { "data-running": String(status.running), "data-permission": permission, "data-busy": String(busy), "data-error": String(error) },
    shape,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  eyeInstances.reset();
  installEyeStore(null);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the state machine", () => {
  it("DISABLED → ENABLING (camera opening) → ACTIVE, the camera before the durable call, then a verified result", async () => {
    const t = harness({ auto: false });
    expect(state(t)).toBe("DISABLED");
    const pending = t.store.enable("owner_start");
    await t.flush();
    expect(state(t)).toBe("ENABLING");
    expect(t.store.getSnapshot().busy).toBe(true);
    expect(t.camera.starts).toBe(1);
    expect(t.durable).toEqual([]); // never before the camera is open

    t.camera.opened();
    const result = await pending;
    expect(state(t)).toBe("ACTIVE");
    expect(result).toEqual({
      state: "ACTIVE",
      running: true,
      camera_label: "fake-cam",
      error_class: null,
      observed_at: new Date(1_700_000_000_000).toISOString(),
      changed: true,
      media_track_ready_state: null, // a synthetic source holds no MediaStreamTrack
      action_trace: ["request:enable", "getUserMedia:called", "loop:started", "durable:enable", "state:ENABLING->ACTIVE"],
    });
    expect(t.store.getSnapshot().lastActionTrace).toEqual(result.action_trace);
    expect(t.durable).toEqual([{ kind: "enable", reason: "owner_start" }]);
    expect(t.store.getSnapshot().permission).toBe("granted");
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 1 });

    // The loop is really running: it samples and posts on its interval.
    await t.flush();
    expect(t.posted).toHaveLength(1);
    await t.advance(1000);
    expect(t.posted).toHaveLength(2);
  });

  it("ACTIVE → DISABLING (durable in flight, camera still open) → DISABLED: durable first, local stop second", async () => {
    const t = harness();
    await t.store.enable("owner_start");
    t.holdDurable();
    const pending = t.store.disable("owner_stop");
    await t.flush();
    expect(state(t)).toBe("DISABLING");
    expect(t.durable).toEqual([{ kind: "enable", reason: "owner_start" }, { kind: "disable", reason: "owner_stop" }]);
    expect(t.camera.stops).toBe(0); // the flag flips first…
    t.releaseDurable();
    const result = await pending;
    expect(t.camera.stops).toBe(1); // …then the camera light goes out
    expect(state(t)).toBe("DISABLED");
    expect(result).toMatchObject({ state: "DISABLED", running: false, camera_label: null, error_class: null, changed: true });
    expect(result.action_trace).toEqual(["request:disable", "durable:disable", "loop:stopped", "state:DISABLING->DISABLED"]);
    // No further sample is taken.
    const before = t.posted.length;
    await t.advance(5000);
    expect(t.posted).toHaveLength(before);
  });

  it("a durable disable failure still stops the camera and lands on DISABLED with the error shown", async () => {
    const t = harness();
    await t.store.enable("owner_start");
    const failing = new EyeStore({
      build: () => ({ frameSource: t.camera, postObservation: async () => ({ status: "posted" as const }), sampleIntervalMs: 1000 }),
      durable: {
        enable: async () => {},
        disable: async () => {
          throw new Error("Cloud Core ulaşılamıyor");
        },
      },
    });
    await failing.enable("owner_start");
    const result = await failing.disable("owner_stop");
    expect(result.state).toBe("DISABLED");
    expect(failing.getSnapshot().error).toBe("Cloud Core ulaşılamıyor");
    expect(t.camera.open).toBe(false);
  });

  it("the loop stopping on its own (a 409: the flag went off elsewhere) takes the state to DISABLED without a durable call", async () => {
    const t = harness();
    await t.store.enable("owner_start");
    expect(state(t)).toBe("ACTIVE");
    t.disableServerSide();
    await t.flush(); // the first sample posts and is answered eye_disabled
    expect(state(t)).toBe("DISABLED");
    expect(t.camera.open).toBe(false);
    expect(t.durable.map((d) => d.kind)).toEqual(["enable"]);
    // The self-stop is appended to the enable's trace: how the eye came to be DISABLED.
    expect(t.store.getSnapshot().lastActionTrace.slice(-2)).toEqual(["loop:stopped", "state:ACTIVE->DISABLED"]);
  });

  it("stopLocalOnly (the bus said disabled) releases the camera and reads DISABLED, with no durable call", async () => {
    const t = harness();
    await t.store.enable("owner_start");
    t.store.stopLocalOnly();
    expect(state(t)).toBe("DISABLED");
    expect(t.camera.stops).toBe(1);
    expect(t.durable.map((d) => d.kind)).toEqual(["enable"]);
    // And it is idempotent.
    t.store.stopLocalOnly();
    expect(t.camera.stops).toBe(2);
    expect(state(t)).toBe("DISABLED");
  });
});

describe("idempotency and join-in-flight", () => {
  it("enable while ACTIVE answers changed:false and opens nothing; disable while DISABLED likewise", async () => {
    const t = harness();
    const first = await t.store.enable("owner_start");
    expect(first.changed).toBe(true);
    const again = await t.store.enable("owner_start");
    expect(again).toMatchObject({ state: "ACTIVE", running: true, camera_label: "fake-cam", changed: false });
    expect(t.camera.starts).toBe(1);
    expect(t.durable).toHaveLength(1);

    const off = await t.store.disable("owner_stop");
    expect(off.changed).toBe(true);
    const offAgain = await t.store.disable("owner_stop");
    expect(offAgain).toMatchObject({ state: "DISABLED", running: false, changed: false });
    expect(t.durable).toHaveLength(2);
    expect(t.camera.stops).toBe(1);
  });

  it("a second enable while ENABLING joins the in-flight promise: one getUserMedia, one durable call, one result", async () => {
    const t = harness({ auto: false });
    const a = t.store.enable("owner_start");
    const b = t.store.enable("voice:Gözünü aç.");
    expect(b).toBe(a);
    await t.flush();
    expect(t.camera.starts).toBe(1);
    t.camera.opened();
    const [ra, rb] = await Promise.all([a, b]);
    expect(ra).toBe(rb);
    expect(ra.state).toBe("ACTIVE");
    expect(t.durable).toEqual([{ kind: "enable", reason: "owner_start" }]);
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 1 });
  });

  it("a disable while the camera is still opening does not wait for the prompt: the late stream is released, nothing durable says enabled", async () => {
    const t = harness({ auto: false });
    const enabling = t.store.enable("owner_start");
    await t.flush();
    expect(state(t)).toBe("ENABLING");
    const disabling = t.store.disable("voice:Gözünü kapat.");
    const off = await disabling; // settles without the camera ever answering
    expect(off.state).toBe("DISABLED");
    expect(state(t)).toBe("DISABLED");
    expect(t.durable).toEqual([{ kind: "disable", reason: "voice:Gözünü kapat." }]);
    const on = await enabling;
    expect(on).toMatchObject({ state: "DISABLED", running: false, changed: false });
    // The owner grants late: the session releases what it opened.
    const stops = t.camera.stops;
    t.camera.opened();
    await t.flush();
    expect(t.camera.stops).toBe(stops + 1);
    expect(t.camera.open).toBe(false);
    expect(state(t)).toBe("DISABLED");
    expect(eyeInstances.snapshot().loopsStarted).toBe(0);
  });
});

describe("one session, one camera, one loop", () => {
  it("two consumers + a remount + a double enable: {sessions:1, cameraOpens:1, loopsStarted:1}", async () => {
    const t = harness();
    // First view mounts: render (getSnapshot), then effect (subscribe).
    const first = t.store.getSnapshot();
    const unsubA = t.store.subscribe(() => {});
    // Second view (the cockpit next to the core).
    t.store.getSnapshot();
    const unsubB = t.store.subscribe(() => {});
    // The first view unmounts and mounts again.
    unsubA();
    t.store.getSnapshot();
    const unsubC = t.store.subscribe(() => {});
    expect(t.builds).toBe(1);
    expect(first.ready).toBe(true);

    // Both views fire enable on the same gesture.
    await Promise.all([t.store.enable("owner_start"), t.store.enable("owner_start")]);
    expect(state(t)).toBe("ACTIVE");
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 1 });
    expect(t.camera.overlaps).toBe(0);

    // A view leaving does not touch the camera.
    unsubB();
    unsubC();
    expect(state(t)).toBe("ACTIVE");
    expect(t.camera.open).toBe(true);
    expect(t.camera.stops).toBe(0);
  });

  it("enable → ACTIVE → disable → DISABLED → enable → ACTIVE: exactly two camera opens, never two loops at once", async () => {
    const t = harness();
    expect((await t.store.enable("owner_start")).state).toBe("ACTIVE");
    await t.flush();
    expect((await t.store.disable("owner_stop")).state).toBe("DISABLED");
    expect((await t.store.enable("owner_start")).state).toBe("ACTIVE");
    await t.flush();
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 2, loopsStarted: 2 });
    expect(t.camera.overlaps).toBe(0);
    expect(t.camera.stops).toBe(1);
    // One loop: one sample per interval, not two.
    const before = t.posted.length;
    await t.advance(1000);
    expect(t.posted).toHaveLength(before + 1);
    expect(t.store.getSnapshot().status.cameraLabel).toBe("fake-cam");
  });

  it("getSnapshot is referentially stable until something changes (the useSyncExternalStore contract)", async () => {
    const t = harness();
    const a = t.store.getSnapshot();
    expect(t.store.getSnapshot()).toBe(a);
    await t.store.enable("owner_start");
    const b = t.store.getSnapshot();
    expect(b).not.toBe(a);
    expect(b.state).toBe("ACTIVE");
    expect(t.store.getSnapshot()).toBe(b);
  });
});

describe("failures are answers on the receipt's closed vocabulary", () => {
  it("NotAllowedError → ERROR permission_denied, camera released, permission denied; the next disable() lands on DISABLED", async () => {
    const t = harness({ auto: false });
    const pending = t.store.enable("owner_start");
    await t.flush();
    t.camera.failed(new DOMException("Permission denied", "NotAllowedError"));
    const result = await pending;
    expect(result).toMatchObject({ state: "ERROR", running: false, camera_label: null, error_class: "permission_denied", changed: true });
    expect(result.action_trace).toEqual(["request:enable", "getUserMedia:called", "getUserMedia:NotAllowedError", "state:ENABLING->ERROR"]);
    expect(state(t)).toBe("ERROR");
    expect(t.store.getSnapshot()).toMatchObject({ permission: "denied", errorClass: "permission_denied", busy: false });
    expect(t.store.getSnapshot().error).toBe("Tarayıcı kamera izni vermedi.");
    expect(t.durable).toEqual([]); // the Cloud Core was never told the eye is on
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 0 });

    const off = await t.store.disable("voice:Gözünü kapat.");
    expect(off).toMatchObject({ state: "DISABLED", error_class: null, changed: true });
    expect(state(t)).toBe("DISABLED");
    expect(t.store.getSnapshot().errorClass).toBeNull();
    expect(t.durable).toEqual([{ kind: "disable", reason: "voice:Gözünü kapat." }]);
  });

  it("every getUserMedia failure lands on a structured class by its error name — never a generic one", () => {
    expect(classifyCameraError(new DOMException("x", "NotAllowedError"))).toBe("permission_denied");
    expect(classifyCameraError(new DOMException("x", "SecurityError"))).toBe("permission_denied");
    expect(classifyCameraError(new DOMException("x", "NotFoundError"))).toBe("device_not_found");
    expect(classifyCameraError(new DOMException("x", "OverconstrainedError"))).toBe("device_not_found");
    expect(classifyCameraError(new DOMException("x", "NotReadableError"))).toBe("device_busy");
    expect(classifyCameraError(new DOMException("x", "AbortError"))).toBe("device_busy");
    expect(classifyCameraError(new CameraTrackEndedError("ended"))).toBe("stream_created_but_track_ended");
    expect(classifyCameraError(new PerceptionStartError(new Error("listener threw")))).toBe("perception_start_failed");
    // Anything else getUserMedia rejects with is still named, not "unavailable".
    expect(classifyCameraError(new TypeError("bad constraints"))).toBe("get_user_media_failed");
    expect(classifyCameraError(new Error("?"))).toBe("get_user_media_failed");
    expect(classifyCameraError("not even an error")).toBe("get_user_media_failed");
    for (const cls of EYE_ERROR_CLASSES) expect(typeof LOCAL_EYE_ERROR_TEXT[cls]).toBe("string"); // every class has on-screen text
  });

  it("NotReadableError → device_busy with the browser's error name in the trace, and an ERROR can be retried", async () => {
    const t = harness({ auto: false });
    const pending = t.store.enable("owner_start");
    await t.flush();
    t.camera.failed(new DOMException("Could not start video source", "NotReadableError"));
    const result = await pending;
    expect(result.error_class).toBe("device_busy");
    expect(result.action_trace).toEqual(["request:enable", "getUserMedia:called", "getUserMedia:NotReadableError", "state:ENABLING->ERROR"]);
    expect(t.store.getSnapshot().error).toBe(LOCAL_EYE_ERROR_TEXT.device_busy);
    expect(t.store.getSnapshot().permission).toBe("unknown"); // a busy device says nothing about permission

    // The device frees up; the owner tries again.
    const retry = t.store.enable("owner_start");
    await t.flush();
    expect(state(t)).toBe("ENABLING");
    t.camera.opened();
    expect((await retry).state).toBe("ACTIVE");
    expect(t.store.getSnapshot().errorClass).toBeNull();
  });

  it(`a camera that does not answer within ${LOCAL_EYE_TIMEOUT_MS} ms → ERROR timeout; when it finally opens, the stream is released`, async () => {
    const t = harness({ auto: false });
    const pending = t.store.enable("voice:Gözünü aç.");
    await t.advance(LOCAL_EYE_TIMEOUT_MS - 1);
    expect(state(t)).toBe("ENABLING");
    await t.advance(1);
    const result = await pending;
    expect(result).toMatchObject({ state: "ERROR", running: false, error_class: "timeout" });
    expect(t.store.getSnapshot().error).toBe("Kamera zamanında açılmadı.");
    expect(t.durable).toEqual([]);

    const stops = t.camera.stops;
    t.camera.opened(); // the prompt is answered much later
    await t.flush();
    expect(t.camera.stops).toBe(stops + 1);
    expect(t.camera.open).toBe(false);
    expect(state(t)).toBe("ERROR");
    expect(eyeInstances.snapshot().loopsStarted).toBe(0);
  });

  it("no mediaDevices at all → ERROR capability_missing without touching the frame source", async () => {
    const t = harness({ cameraAvailable: false });
    const result = await t.store.enable("owner_start");
    expect(result).toMatchObject({ state: "ERROR", error_class: "capability_missing", running: false });
    expect(result.action_trace).toEqual(["request:enable", "capability:missing", "state:ENABLING->ERROR"]);
    expect(t.camera.starts).toBe(0);
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 0, loopsStarted: 0 });
  });

  it("the stream opened but the loop could not start (a status listener throws) → perception_start_failed, camera released", async () => {
    const t = harness();
    t.store.subscribe(() => {
      if (t.store.getSnapshot().status.running) throw new Error("listener broke");
    });
    const result = await t.store.enable("owner_start");
    expect(result).toMatchObject({ state: "ERROR", running: false, error_class: "perception_start_failed", changed: true });
    expect(result.action_trace).toEqual(["request:enable", "getUserMedia:called", "loop:start failed", "state:ENABLING->ERROR"]);
    expect(t.camera.open).toBe(false); // the stream that DID open is not left running
    expect(t.durable).toEqual([]); // and the Cloud Core was never told the eye is on
    expect(eyeInstances.snapshot()).toEqual({ sessions: 1, cameraOpens: 1, loopsStarted: 0 });
  });

  it("an unexpected throw inside a transition (the camera's stop() breaks) → state_transition_failed, never a rejection", async () => {
    const t = harness();
    await t.store.enable("owner_start");
    t.camera.stop = () => {
      throw new RangeError("stop broke");
    };
    const result = await t.store.disable("owner_stop"); // resolves: a failure is an observation
    expect(result).toMatchObject({ state: "ERROR", error_class: "state_transition_failed", changed: true });
    expect(result.action_trace).toEqual(["request:disable", "durable:disable", "unexpected:RangeError", "state:DISABLING->ERROR"]);
    expect(t.store.getSnapshot()).toMatchObject({ state: "ERROR", errorClass: "state_transition_failed", busy: false });
    expect(t.store.getSnapshot().error).toBe(LOCAL_EYE_ERROR_TEXT.state_transition_failed);
  });
});

describe("the action trace", () => {
  it("an idempotent call is a two-stage trace with no transition, and the snapshot keeps the last trace", async () => {
    const t = harness();
    expect(t.store.getSnapshot().lastActionTrace).toEqual([]);
    await t.store.enable("owner_start");
    const again = await t.store.enable("owner_start");
    expect(again.action_trace).toEqual(["request:enable", "already:ACTIVE"]);
    expect(t.store.getSnapshot().lastActionTrace).toEqual(["request:enable", "already:ACTIVE"]);
    const off = await t.store.disable("owner_stop");
    expect(off.action_trace).toEqual(["request:disable", "durable:disable", "loop:stopped", "state:DISABLING->DISABLED"]);
    const offAgain = await t.store.disable("owner_stop");
    expect(offAgain.action_trace).toEqual(["request:disable", "already:DISABLED"]);
  });

  it("a timeout, a superseded enable and a bus stop each say what happened to THIS action", async () => {
    const t = harness({ auto: false });
    const timedOut = t.store.enable("voice:Gözünü aç.");
    await t.advance(LOCAL_EYE_TIMEOUT_MS);
    expect((await timedOut).action_trace).toEqual([
      "request:enable",
      "getUserMedia:called",
      `timeout:${LOCAL_EYE_TIMEOUT_MS}ms`,
      "state:ENABLING->ERROR",
    ]);

    const enabling = t.store.enable("owner_start");
    await t.flush();
    const disabling = t.store.disable("voice:Gözünü kapat.");
    expect((await enabling).action_trace).toEqual(["request:enable", "getUserMedia:called", "superseded:disable"]);
    expect((await disabling).action_trace).toEqual(["request:disable", "durable:disable", "loop:stopped", "state:DISABLING->DISABLED"]);

    t.camera.auto = true;
    await t.store.enable("owner_start");
    t.store.stopLocalOnly();
    expect(t.store.getSnapshot().lastActionTrace).toEqual(["request:stop_local", "loop:stopped", "state:ACTIVE->DISABLED"]);
  });

  it("is bounded: a durable call that fails still fits, and a trace never exceeds MAX_ACTION_TRACE entries", async () => {
    const camera = new Camera();
    const store = new EyeStore({
      build: () => ({ frameSource: camera, postObservation: async () => ({ status: "posted" as const }), sampleIntervalMs: 1000 }),
      durable: {
        enable: async () => {
          throw new Error("Cloud Core ulaşılamıyor");
        },
        disable: async () => {},
      },
    });
    const result = await store.enable("owner_start");
    expect(result.action_trace).toEqual([
      "request:enable",
      "getUserMedia:called",
      "loop:started",
      "durable:enable",
      "durable:failed",
      "state:ENABLING->ACTIVE",
    ]);
    expect(result.action_trace.length).toBeLessThanOrEqual(MAX_ACTION_TRACE);
    expect(MAX_ACTION_TRACE).toBe(12);
    // The trace carries no frame, no pixel count, no identifier: only stage words and the camera's label.
    for (const stage of result.action_trace) expect(stage).toMatch(/^[a-zA-Z_]+:[ ->A-Za-z0-9_:.]+$/);
    store.dispose();
  });

  it("a durable enable failure after the camera opened is not a local failure: ACTIVE, error shown, the loop keeps its own 409 self-correction", async () => {
    const camera = new Camera();
    const posted: EyeObservation[] = [];
    const store = new EyeStore({
      build: () => ({
        frameSource: camera,
        postObservation: async (observation: EyeObservation) => {
          posted.push(observation);
          return { status: "eye_disabled" as const };
        },
        sampleIntervalMs: 1000,
      }),
      durable: {
        enable: async () => {
          throw new Error("Cloud Core ulaşılamıyor");
        },
        disable: async () => {},
      },
    });
    const result = await store.enable("owner_start");
    expect(result).toMatchObject({ state: "ACTIVE", running: true, camera_label: "fake-cam", error_class: null });
    expect(store.getSnapshot().error).toBe("Cloud Core ulaşılamıyor");
    // The Cloud Core's flag is off: the first sample is refused and the loop stops itself.
    await vi.advanceTimersByTimeAsync(0);
    expect(posted).toHaveLength(1);
    expect(store.getSnapshot().state).toBe("DISABLED");
    expect(camera.open).toBe(false);
  });
});

describe("the module singleton and the React binding", () => {
  it("getEyeStore returns the installed store, and the same one every time", () => {
    const t = harness();
    installEyeStore(t.store);
    expect(getEyeStore()).toBe(t.store);
    expect(getEyeStore()).toBe(getEyeStore());
  });

  it("the server render path builds nothing: useActivePerception on the server reads the server snapshot", () => {
    const t = harness();
    installEyeStore(t.store);
    const html = renderToStaticMarkup(createElement(Probe));
    expect(html).toContain('data-running="false"');
    expect(html).toContain('data-permission="unknown"');
    expect(html).toContain('data-busy="false"');
    expect(html).toContain("function,function,function");
    expect(t.builds).toBe(0);
    expect(eyeInstances.snapshot()).toEqual({ sessions: 0, cameraOpens: 0, loopsStarted: 0 });
  });

  it("the permission watch runs once, on first subscribe, and its answer reaches the snapshot", () => {
    let watches = 0;
    let report: ((p: "granted" | "prompt" | "denied") => void) | null = null;
    const store = new EyeStore({
      build: () => ({
        frameSource: new Camera(),
        postObservation: async () => ({ status: "posted" as const }),
        watchPermission: (r) => {
          watches += 1;
          report = r;
          return () => {};
        },
      }),
      durable: { enable: async () => {}, disable: async () => {} },
    });
    expect(store.getSnapshot().permission).toBe("unknown");
    store.subscribe(() => {});
    store.subscribe(() => {});
    expect(watches).toBe(1);
    report!("prompt");
    expect(store.getSnapshot().permission).toBe("prompt");
  });
});
