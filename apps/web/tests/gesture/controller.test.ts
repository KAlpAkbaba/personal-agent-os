/**
 * `GestureController` (ADR-0198, Stage 1) — the three-way gate (eye active AND toggle on
 * AND a local session exists) and the toggle's persistence, driven entirely by fakes: a
 * scriptable video-consumer attacher, an in-memory toggle storage, and a fake tracker that
 * records start/stop calls instead of touching MediaPipe.
 */

import { describe, expect, it, vi } from "vitest";

import { GestureController, type GestureControllerDeps, type GestureToggleStorage } from "../../app/lib/gesture/controller";
import type { FrameMeasure } from "../../app/lib/gesture/recognizer";
import type { GestureEvent } from "../../app/lib/gesture/types";

function memoryStorage(initial = false): GestureToggleStorage {
  let value = initial;
  return {
    get: () => value,
    set: (v) => {
      value = v;
    },
  };
}

class FakeVideoBus {
  private cb: ((video: HTMLVideoElement | null) => void) | null = null;
  attaches = 0;
  detaches = 0;

  attach = (cb: (video: HTMLVideoElement | null) => void): (() => void) => {
    this.attaches += 1;
    this.cb = cb;
    cb(this.current);
    return () => {
      this.detaches += 1;
      if (this.cb === cb) this.cb = null;
    };
  };

  current: HTMLVideoElement | null = null;

  emit(video: HTMLVideoElement | null): void {
    this.current = video;
    this.cb?.(video);
  }
}

class FakeTracker {
  starts: HTMLVideoElement[] = [];
  stops = 0;
  constructor(private readonly handlers: { onGesture: (e: GestureEvent) => void; onStats: (s: { fps: number; measure: FrameMeasure | null }) => void; onError: (m: string) => void }) {}

  async start(video: HTMLVideoElement): Promise<void> {
    this.starts.push(video);
  }

  stop(): void {
    this.stops += 1;
  }

  fireGesture(event: GestureEvent): void {
    this.handlers.onGesture(event);
  }

  fireStats(fps: number): void {
    this.handlers.onStats({ fps, measure: null });
  }

  fireError(message: string): void {
    this.handlers.onError(message);
  }
}

function setup(overrides: Partial<{ eyeActive: boolean; hasSession: boolean; storageInitial: boolean }> = {}) {
  const bus = new FakeVideoBus();
  const storage = memoryStorage(overrides.storageInitial ?? false);
  let eyeActive = overrides.eyeActive ?? true;
  let hasSession = overrides.hasSession ?? true;
  const trackers: FakeTracker[] = [];
  const dispatched: string[] = [];
  const deps: GestureControllerDeps = {
    attachVideoConsumer: bus.attach,
    isEyeActive: () => eyeActive,
    hasLocalSession: () => hasSession,
    dispatchGesture: async (gesture) => {
      dispatched.push(gesture);
    },
    createTracker: (handlers) => {
      const t = new FakeTracker(handlers);
      trackers.push(t);
      return t;
    },
    storage,
    now: () => 1000,
  };
  const controller = new GestureController(deps);
  return {
    controller,
    bus,
    storage,
    trackers,
    dispatched,
    setEyeActive: (v: boolean) => {
      eyeActive = v;
    },
    setHasSession: (v: boolean) => {
      hasSession = v;
    },
  };
}

const video = {} as HTMLVideoElement;

describe("GestureController: the toggle", () => {
  it("defaults OFF and reads the storage's initial value", () => {
    const off = setup({ storageInitial: false });
    expect(off.controller.getSnapshot().enabled).toBe(false);
    const on = setup({ storageInitial: true });
    expect(on.controller.getSnapshot().enabled).toBe(true);
  });

  it("setEnabled persists to storage and notifies subscribers", () => {
    const { controller, storage } = setup();
    const listener = vi.fn();
    controller.subscribe(listener);
    controller.setEnabled(true);
    expect(storage.get()).toBe(true);
    expect(controller.getSnapshot().enabled).toBe(true);
    expect(listener).toHaveBeenCalled();
  });
});

describe("GestureController: the three-way gate", () => {
  it("all three true (toggle on, eye active, session exists) starts the tracker once the video arrives", () => {
    const { controller, bus, trackers } = setup();
    controller.setEnabled(true);
    expect(controller.getSnapshot().running).toBe(false); // no video yet
    bus.emit(video);
    expect(trackers).toHaveLength(1);
    expect(trackers[0].starts).toEqual([video]);
    expect(controller.getSnapshot().running).toBe(true);
  });

  it("toggle off never starts a tracker even with the eye active and a session", () => {
    const { controller, bus, trackers } = setup();
    bus.emit(video); // no-op: nothing attached yet since the gate never opened
    controller.refresh();
    expect(trackers).toHaveLength(0);
    expect(controller.getSnapshot().running).toBe(false);
  });

  it("eye inactive prevents tracking; becoming active (with the toggle already on) starts it", () => {
    const { controller, bus, trackers, setEyeActive } = setup({ eyeActive: false });
    controller.setEnabled(true);
    expect(trackers).toHaveLength(0);
    setEyeActive(true);
    controller.refresh();
    bus.emit(video);
    expect(trackers).toHaveLength(1);
    expect(controller.getSnapshot().running).toBe(true);
  });

  it("no local session prevents tracking even with the eye active and the toggle on", () => {
    const { controller, trackers, setHasSession } = setup({ hasSession: false });
    controller.setEnabled(true);
    expect(trackers).toHaveLength(0);
    expect(controller.getSnapshot().running).toBe(false);
    // Still no session -> still no tracking, even calling refresh() again with nothing changed.
    controller.refresh();
    expect(controller.getSnapshot().running).toBe(false);
    // Once a session exists the gate opens; no video has arrived in this test yet, so the
    // tracker (if constructed at all) has not actually been started.
    setHasSession(true);
    controller.refresh();
    expect(controller.getSnapshot().running).toBe(false);
    expect(trackers.every((t) => t.starts.length === 0)).toBe(true);
  });

  it("turning the eye off (video goes null) stops tracking immediately", () => {
    const { controller, bus, trackers, setEyeActive } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    expect(controller.getSnapshot().running).toBe(true);
    setEyeActive(false);
    bus.emit(null); // the eye's own store stops the camera -> video consumer sees null
    expect(trackers[0].stops).toBe(1);
    expect(controller.getSnapshot().running).toBe(false);
  });

  it("turning the toggle off while running stops the tracker and detaches the video consumer", () => {
    const { controller, bus, trackers } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    expect(controller.getSnapshot().running).toBe(true);
    controller.setEnabled(false);
    expect(trackers[0].stops).toBe(1);
    expect(bus.detaches).toBe(1);
    expect(controller.getSnapshot().running).toBe(false);
  });

  it("losing the local session (refresh() after it closes) stops tracking", () => {
    const { controller, bus, trackers, setHasSession } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    expect(controller.getSnapshot().running).toBe(true);
    setHasSession(false);
    controller.refresh();
    expect(trackers[0].stops).toBe(1);
    expect(controller.getSnapshot().running).toBe(false);
  });
});

describe("GestureController: gesture events reach dispatchGesture and the HUD", () => {
  it("a gesture from the tracker updates lastGesture/lastGestureAtMs and calls dispatchGesture, without blocking", () => {
    const { controller, bus, trackers, dispatched } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    trackers[0].fireGesture({ name: "swipe_right", t_ms: 42 });
    expect(controller.getSnapshot().lastGesture).toBe("swipe_right");
    expect(controller.getSnapshot().lastGestureAtMs).toBe(1000);
    expect(dispatched).toEqual(["swipe_right"]);
  });

  it("tracking fps flows into the HUD snapshot, and resets to null when tracking stops", () => {
    const { controller, bus, trackers } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    trackers[0].fireStats(24.5);
    expect(controller.getSnapshot().trackingFps).toBeCloseTo(24.5);
    controller.setEnabled(false);
    expect(controller.getSnapshot().trackingFps).toBeNull();
  });

  it("a tracker error (assets missing) surfaces on lastError and running goes false", () => {
    const { controller, bus, trackers } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    trackers[0].fireError("El takibi dosyaları yok; pnpm run fetch:mediapipe");
    expect(controller.getSnapshot().lastError).toBe("El takibi dosyaları yok; pnpm run fetch:mediapipe");
    expect(controller.getSnapshot().running).toBe(false);
  });
});

describe("GestureController: dispose", () => {
  it("stops the tracker and clears listeners", () => {
    const { controller, bus, trackers } = setup();
    controller.setEnabled(true);
    bus.emit(video);
    const listener = vi.fn();
    controller.subscribe(listener);
    controller.dispose(); // its own stop() may notify subscribers still attached at that point
    expect(trackers[0].stops).toBe(1);
    const callsAtDispose = listener.mock.calls.length;
    controller.setEnabled(false); // would notify again if the listener were still attached
    expect(listener.mock.calls.length).toBe(callsAtDispose); // no further notifications post-dispose
  });
});
