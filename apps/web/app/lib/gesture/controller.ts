"use client";

/**
 * El hareketi kumandası (ADR-0198, Stage 1): owns the owner's "El kumandası" toggle (a
 * per-browser preference, default OFF, remembered in `localStorage`) and the gate that
 * decides whether the tracker should actually be running right now — ALL THREE of:
 *   1. the eye is ACTIVE (the owner already turned the camera on for its own reason);
 *   2. the toggle is on;
 *   3. a local-mode voice session exists (a gesture has no session of its own to post
 *      events through — `LocalVoiceMode.dispatchGesture` needs one).
 * Turning the eye off (any of the three ways `store.ts` documents — the owner's button,
 * their voice, or the Cloud Core) stops tracking immediately: this controller re-evaluates
 * the gate every time `refresh()` is called, and the eye going off is exactly the kind of
 * external change a caller is expected to call `refresh()` after (see `useGestureController`
 * in the core UI, which calls it from a `useEffect` on the eye/voice snapshots it already
 * subscribes to — no separate subscription machinery duplicated here).
 *
 * Deliberately NOT itself a React hook or component: `GestureController` is plain,
 * synchronous-except-for-`start()` state, testable the same way `EyeStore`/`LocalVoiceMode`
 * are (`useSyncExternalStore` over `subscribe`/`getSnapshot` in the real UI).
 */

import type { FrameMeasure } from "./recognizer";
import type { GestureEvent, GestureName } from "./types";
import type { GestureTracker } from "./tracker";

const STORAGE_KEY = "pagentos.gesture.enabled";

export type GestureToggleStorage = {
  get(): boolean;
  set(value: boolean): void;
};

/** The real `localStorage`-backed toggle; default OFF (per the task brief), and tolerant of
 * a browser that refuses storage access (private mode, blocked site data) — a read/write
 * failure there is a per-viewer convenience lost, never a reason to throw. */
export function browserGestureToggleStorage(): GestureToggleStorage {
  return {
    get(): boolean {
      try {
        return typeof window !== "undefined" && window.localStorage.getItem(STORAGE_KEY) === "1";
      } catch {
        return false;
      }
    },
    set(value: boolean): void {
      try {
        if (typeof window === "undefined") return;
        if (value) window.localStorage.setItem(STORAGE_KEY, "1");
        else window.localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* per-viewer convenience only; nothing to recover */
      }
    },
  };
}

export type GestureControllerDeps = {
  /** `PerceptionSession#attachVideoConsumer` — the ONLY source of a video element; this
   * controller never opens a camera of its own. */
  attachVideoConsumer: (cb: (video: HTMLVideoElement | null) => void) => () => void;
  /** True while the eye is ACTIVE right now. */
  isEyeActive: () => boolean;
  /** True while a local-mode session exists (has a `sessionId`) right now. */
  hasLocalSession: () => boolean;
  /** Issues the gesture's tool the same way a spoken command would, silently. */
  dispatchGesture: (gesture: string) => Promise<void>;
  /** Builds a tracker bound to `onGesture`/`onStats`/`onError`; injectable for tests. */
  createTracker: (handlers: {
    onGesture: (event: GestureEvent) => void;
    onStats: (stats: { fps: number; measure: FrameMeasure | null }) => void;
    onError: (message: string) => void;
  }) => Pick<GestureTracker, "start" | "stop">;
  storage?: GestureToggleStorage;
  now?: () => number;
};

export type GestureControllerSnapshot = {
  /** The owner's own toggle. */
  enabled: boolean;
  /** True only while all three gates hold AND the tracker has actually been started. */
  running: boolean;
  lastGesture: GestureName | null;
  lastGestureAtMs: number | null;
  trackingFps: number | null;
  /** The live calibration readout (ADR-0198): what the last frame measured as. */
  measure: FrameMeasure | null;
  lastError: string | null;
};

const INITIAL: GestureControllerSnapshot = {
  enabled: false,
  running: false,
  lastGesture: null,
  lastGestureAtMs: null,
  trackingFps: null,
  measure: null,
  lastError: null,
};

export class GestureController {
  private readonly storage: GestureToggleStorage;
  private readonly now: () => number;
  private snapshot: GestureControllerSnapshot;
  private readonly listeners = new Set<() => void>();
  private tracker: Pick<GestureTracker, "start" | "stop"> | null = null;
  private detachVideo: (() => void) | null = null;
  private currentVideo: HTMLVideoElement | null = null;
  /** True once a tracker has been told to `start()` for the CURRENT gate-passing episode
   * (guards against starting twice while waiting on the async `start()` of a slow/first
   * load). */
  private starting = false;

  constructor(private readonly deps: GestureControllerDeps) {
    this.storage = deps.storage ?? browserGestureToggleStorage();
    this.now = deps.now ?? Date.now;
    this.snapshot = { ...INITIAL, enabled: this.storage.get() };
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): GestureControllerSnapshot => this.snapshot;
  getServerSnapshot = (): GestureControllerSnapshot => INITIAL;

  private patch(partial: Partial<GestureControllerSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...partial };
    for (const listener of this.listeners) listener();
  }

  /** The owner's own action: flips the toggle, persists it, and re-evaluates the gate. */
  setEnabled(on: boolean): void {
    if (on === this.snapshot.enabled) return;
    this.storage.set(on);
    this.patch({ enabled: on, lastError: on ? this.snapshot.lastError : null });
    this.refresh();
  }

  /**
   * Re-evaluate the three-way gate. Call after anything that might have changed one of its
   * inputs — the eye's status, the local-mode session, or `setEnabled` (which calls this
   * itself). Idempotent: calling it again with nothing changed does nothing observable.
   */
  refresh(): void {
    const shouldRun = this.snapshot.enabled && this.deps.isEyeActive() && this.deps.hasLocalSession();
    if (shouldRun && !this.snapshot.running && !this.starting) {
      this.beginTracking();
    } else if (!shouldRun && (this.snapshot.running || this.starting)) {
      this.stopTracking();
    }
  }

  private beginTracking(): void {
    this.starting = true;
    this.tracker = this.deps.createTracker({
      onGesture: (event) => this.onGesture(event),
      onStats: (stats) => this.patch({ trackingFps: stats.fps, measure: stats.measure }),
      onError: (message) => this.patch({ lastError: message, running: false }),
    });
    this.detachVideo = this.deps.attachVideoConsumer((video) => this.onVideo(video));
  }

  private onVideo(video: HTMLVideoElement | null): void {
    this.currentVideo = video;
    if (!this.starting && !this.snapshot.running) return; // gate closed meanwhile
    if (video) {
      if (this.tracker) {
        void this.tracker.start(video);
        this.starting = false;
        this.patch({ running: true });
      }
      return;
    }
    // video is null. `attachVideoConsumer` calls back immediately with the CURRENT value at
    // attach time (perception.ts's own contract) — while still `starting` and never yet
    // `running`, a null here just means "no camera open yet, still waiting", not "the camera
    // that was tracking just stopped". Only the latter (a live camera going away) is a real
    // reason to stop: `this.snapshot.running` is true only once a real video was handed to
    // the tracker, so it is the right thing to gate on here.
    if (this.snapshot.running) this.stopTracking();
  }

  private stopTracking(): void {
    this.starting = false;
    this.tracker?.stop();
    this.tracker = null;
    this.detachVideo?.();
    this.detachVideo = null;
    this.currentVideo = null;
    if (this.snapshot.running) this.patch({ running: false, trackingFps: null, measure: null });
  }

  private onGesture(event: GestureEvent): void {
    this.patch({ lastGesture: event.name, lastGestureAtMs: this.now() });
    void this.deps.dispatchGesture(event.name);
  }

  /** Tests / page teardown only: releases the tracker and the video subscription. */
  dispose(): void {
    this.stopTracking();
    this.listeners.clear();
  }
}
