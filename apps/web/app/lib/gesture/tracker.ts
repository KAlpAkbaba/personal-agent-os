"use client";

/**
 * El hareketi kumandası (ADR-0198, Stage 1): drives `@mediapipe/tasks-vision`'s
 * `HandLandmarker` over the eye's own `<video>` element (never a second `getUserMedia` —
 * see `lib/eye/perception.ts`'s `attachVideoConsumer`/`videoElement()`) and feeds every
 * detection into the pure `GestureRecognizer`.
 *
 * Not to be confused with `@mediapipe/tasks-vision`'s own `GestureRecognizer` task (a
 * different, canned gesture vocabulary this module does not use) — see `recognizer.ts`'s
 * module docstring.
 *
 * ## Fails CLOSED, never fetches from a CDN
 *
 * The WASM runtime and the `hand_landmarker.task` model are LOCAL files
 * (`public/mediapipe/`), fetched once by `pnpm run fetch:mediapipe`
 * (`scripts/fetch-mediapipe-assets.mjs`) and gitignored. `browserHandLandmarkerFactory()`
 * only ever asks `FilesetResolver`/`HandLandmarker` to load from THOSE local paths
 * (`MEDIAPIPE_WASM_PATH` / `MEDIAPIPE_MODEL_PATH`) — never a CDN URL — and when they are
 * missing (the fetch script was never run) it rejects with `GestureTrackerAssetsMissingError`
 * (`ASSETS_MISSING_TR`) rather than silently reaching out to the network. This is the same
 * "never fetch a frame's contents off this device" posture `perception.ts` documents for the
 * eye, extended to "never fetch the model that reads the frames from anywhere but this
 * device's own build output" — a network fetch here would not leak a frame itself, but it
 * would make hand tracking depend on a third party being reachable and trusted at runtime,
 * which the product's privacy invariant (module docstring, `perception.ts`) rules out.
 */

import { type FrameMeasure, GestureRecognizer } from "./recognizer";
import type { GestureEvent, HandednessLabel, TrackedFrame } from "./types";

export const MEDIAPIPE_WASM_PATH = "/mediapipe/wasm";
export const MEDIAPIPE_MODEL_PATH = "/mediapipe/hand_landmarker.task";

export const ASSETS_MISSING_TR = "El takibi dosyaları yok; pnpm run fetch:mediapipe";

export class GestureTrackerAssetsMissingError extends Error {
  override readonly name = "GestureTrackerAssetsMissingError";
  constructor() {
    super(ASSETS_MISSING_TR);
  }
}

/** The subset of `@mediapipe/tasks-vision`'s `HandLandmarker` this module drives. */
export interface HandLandmarkerLike {
  detectForVideo(
    video: unknown,
    timestampMs: number,
  ): {
    landmarks: Array<Array<{ x: number; y: number; z?: number }>>;
    handedness: Array<Array<{ categoryName?: string }>>;
  };
  close(): void;
}

export type HandLandmarkerFactory = () => Promise<HandLandmarkerLike>;

/**
 * The real factory: builds the vision WASM fileset + `HandLandmarker` from the LOCAL assets
 * only (see the module docstring). Any failure along the way — the optional
 * `@mediapipe/tasks-vision` import itself, `FilesetResolver.forVisionTasks`, or
 * `HandLandmarker.createFromOptions` — is a "the assets are not there" failure from this
 * caller's point of view (a 404 on either the WASM loader or the model asset resolves the
 * `fetch` inside those calls but the promise they return still rejects), so all three are
 * folded into the one closed, Turkish-worded error the HUD shows.
 */
export function browserHandLandmarkerFactory(): HandLandmarkerFactory {
  return async () => {
    let vision: typeof import("@mediapipe/tasks-vision");
    try {
      vision = await import("@mediapipe/tasks-vision");
    } catch {
      throw new GestureTrackerAssetsMissingError();
    }
    const { FilesetResolver, HandLandmarker } = vision;
    try {
      const fileset = await FilesetResolver.forVisionTasks(MEDIAPIPE_WASM_PATH);
      const landmarker = await HandLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MEDIAPIPE_MODEL_PATH },
        runningMode: "VIDEO",
        numHands: 2,
      });
      return landmarker as unknown as HandLandmarkerLike;
    } catch {
      throw new GestureTrackerAssetsMissingError();
    }
  };
}

export type TrackerStats = { fps: number; measure: FrameMeasure | null };

export type GestureTrackerOptions = {
  onGesture: (event: GestureEvent) => void;
  /** A rolling detection-rate readout for the HUD; called at most a few times a second. */
  onStats?: (stats: TrackerStats) => void;
  /** The Turkish message from a failure to start (asset-missing or otherwise). */
  onError?: (message: string) => void;
  /** Injectable for tests; `browserHandLandmarkerFactory()` in production. */
  createLandmarker?: HandLandmarkerFactory;
  recognizer?: GestureRecognizer;
  /** A monotonic millisecond clock; `performance.now` by default. */
  now?: () => number;
  /** `requestAnimationFrame`-shaped driver, injectable so tests step frames by hand. */
  scheduleFrame?: (cb: (t: number) => void) => number;
  cancelFrame?: (handle: number) => void;
};

function defaultSchedule(): { schedule: (cb: (t: number) => void) => number; cancel: (h: number) => void } {
  if (typeof requestAnimationFrame === "function") {
    return { schedule: (cb) => requestAnimationFrame(cb), cancel: (h) => cancelAnimationFrame(h) };
  }
  // jsdom/node fallback (never used in production; kept so importing this module never
  // throws outside a browser, matching perception.ts's "server can import this" posture).
  return {
    schedule: (cb) => setTimeout(() => cb(Date.now()), 16) as unknown as number,
    cancel: (h) => clearTimeout(h as unknown as ReturnType<typeof setTimeout>),
  };
}

/**
 * Owns one `HandLandmarker` and one `requestAnimationFrame` loop over a given `<video>`
 * element, for the life of one `start()`/`stop()` cycle. `controller.ts` owns deciding WHEN
 * to start/stop (the eye-enabled AND toggle-on AND local-session-exists gate); this class
 * only knows how to track hands in a video element once told to.
 */
export class GestureTracker {
  private readonly recognizer: GestureRecognizer;
  private readonly now: () => number;
  private readonly scheduleFrame: (cb: (t: number) => void) => number;
  private readonly cancelFrame: (handle: number) => void;
  private readonly createLandmarker: HandLandmarkerFactory;
  private landmarker: HandLandmarkerLike | null = null;
  private frameHandle: number | null = null;
  private stopped = true;
  private video: HTMLVideoElement | null = null;
  private detectionsInWindow = 0;
  private lastFps = 0;
  private lastStatsAtMs = 0;
  private windowStartMs = 0;

  constructor(private readonly options: GestureTrackerOptions) {
    this.recognizer = options.recognizer ?? new GestureRecognizer();
    this.now = options.now ?? (() => (typeof performance !== "undefined" ? performance.now() : Date.now()));
    const fallback = defaultSchedule();
    this.scheduleFrame = options.scheduleFrame ?? fallback.schedule;
    this.cancelFrame = options.cancelFrame ?? fallback.cancel;
    this.createLandmarker = options.createLandmarker ?? browserHandLandmarkerFactory();
  }

  /** Loads the landmarker (once) and starts the detection loop over `video`. Never throws:
   * a failure is reported through `onError` (Turkish, closed set) so the HUD can show it. */
  async start(video: HTMLVideoElement): Promise<void> {
    this.stopped = false;
    this.video = video;
    if (!this.landmarker) {
      try {
        this.landmarker = await this.createLandmarker();
      } catch (err) {
        this.options.onError?.(err instanceof Error ? err.message : ASSETS_MISSING_TR);
        this.stopped = true;
        return;
      }
    }
    if (this.stopped) return; // stop() raced the (possibly slow, first-time) load
    this.windowStartMs = this.now();
    this.detectionsInWindow = 0;
    this.loop();
  }

  stop(): void {
    this.stopped = true;
    if (this.frameHandle !== null) {
      this.cancelFrame(this.frameHandle);
      this.frameHandle = null;
    }
    this.landmarker?.close();
    this.landmarker = null;
    this.video = null;
  }

  private loop = (): void => {
    if (this.stopped) return;
    this.frameHandle = this.scheduleFrame(() => this.tick());
  };

  private tick(): void {
    if (this.stopped || !this.landmarker || !this.video) return;
    const t_ms = this.now();
    let hands: TrackedFrame["hands"] = [];
    try {
      const result = this.landmarker.detectForVideo(this.video, t_ms);
      hands = result.landmarks.map((landmarks, i) => ({
        handedness: (result.handedness[i]?.[0]?.categoryName === "Left" ? "Left" : "Right") as HandednessLabel,
        landmarks,
      }));
    } catch {
      // A single frame's detection failing (e.g. the video paused mid-frame) is not fatal;
      // the loop just tries again next frame.
    }
    const frame: TrackedFrame = { t_ms, hands };
    for (const event of this.recognizer.ingest(frame)) this.options.onGesture(event);

    this.detectionsInWindow += 1;
    const elapsed = t_ms - this.windowStartMs;
    // The calibration readout wants the live numbers a few times a second, the fps once a
    // second: one stats callback every ~200 ms carries both (fps as last measured).
    if (elapsed >= 1_000) {
      this.lastFps = (this.detectionsInWindow * 1000) / elapsed;
      this.detectionsInWindow = 0;
      this.windowStartMs = t_ms;
    }
    if (t_ms - this.lastStatsAtMs >= 200) {
      this.lastStatsAtMs = t_ms;
      this.options.onStats?.({ fps: this.lastFps, measure: this.recognizer.measureNow(frame) });
    }

    this.loop();
  }
}
