"use client";

/**
 * The local-perception runtime: opens a camera, samples it at a low rate, and
 * posts only the seven structured fields `signal.ts` derives
 * (M18_HOLOGRAPHIC_CORE_SPEC.md §2).
 *
 * ## The frame-never-escapes guarantee, and how it is a property of THIS code
 *
 * `BrowserFrameSource.sample()` is the only function in this entire module
 * that ever produces something frame-shaped (a `Uint8ClampedArray` of raw
 * RGBA pixels from `getImageData`). Its return value is consumed exactly
 * once, synchronously, by the `FrameReducer` it is handed —
 * `computeGridLuminance` — which reduces it to a ~108-number luminance grid.
 * The buffer is a local inside `sample()` and is never returned: the reducer
 * goes in and only the grid comes out, so `tick()` never holds a pixel buffer
 * at all. That is the structure, not a convention the caller has to keep.
 * From there on, `tick()` only ever
 * touches: that small luminance grid (overwritten into `previousGrid` each
 * tick, never grown, never serialised, never exposed by any getter), and
 * plain numbers (motion energy, a rolling window of booleans, a still-time
 * counter). `tick()` returns `void`; the ONLY thing that leaves this module
 * bound for the outside world is whatever `onObservation`/`onStatusChange`
 * receive, and both receive `EyeObservation` — the seven-field type — or a
 * `PerceptionStatus` that carries the same. There is no method anywhere in
 * `FrameSource`, `PerceptionSession`, or `useActivePerception` that returns
 * pixel data, a canvas, a video element, or a `MediaStream`'s frames to a
 * caller. That is the mechanism, not a comment on top of one: grep this
 * module for `ImageData`, `getImageData`, `Uint8ClampedArray`, `toDataURL` or
 * `captureStream` and every hit is inside `BrowserFrameSource`, and none of
 * them is returned by anything the class exposes publicly.
 *
 * ## Disable is immediate and total
 *
 * `PerceptionSession#stop()`:
 * 1. bumps an internal generation counter, so any tick already in flight
 *    recognises itself as stale the next time it checks (see below);
 * 2. clears the sampling timer synchronously — no further tick is scheduled;
 * 3. calls `frameSource.stop()` synchronously, which stops every
 *    `MediaStreamTrack` — this is what turns the browser's own camera light
 *    off, and it happens before `stop()` returns, not on a future tick;
 * 4. aborts the in-flight observation POST, if any, via `AbortController`.
 *
 * `tick()` re-checks `stopped`/the generation token at the one point that
 * matters: immediately before it would call `postObservation`, and again
 * before it would schedule the next tick. Because everything between
 * capturing a frame and that check is synchronous (no `await`), `stop()`
 * cannot interleave with it — JavaScript has nowhere to run `stop()` until
 * the current tick either finishes or hits an `await`. The only window where
 * a sample can be "in flight" when `stop()` runs is the network request
 * itself, which is exactly what the `AbortSignal` targets.
 */

import { postObservation } from "./client";
import { activityLevelFor, computeGridLuminance, deriveObservation, measureMotion } from "./signal";
import type { ActivityLevel } from "./types";
import type { EyeObservation } from "./types";

// ------------------------------------------------------------ frame source

/**
 * Reduces one frame's pixels to the small numeric summary the rest of the client
 * works from. Called with the pixel buffer as an argument; it is the only thing
 * that ever sees one.
 */
export type FrameReducer = (
  pixels: ArrayLike<number>,
  width: number,
  height: number,
) => Float32Array;

/**
 * The seam between this module and the real camera. `BrowserFrameSource`
 * (below) is the only production implementation; tests supply a plain object
 * built from synthetic pixel arrays, per the M18 task brief's "fake
 * getUserMedia/canvas with plain stubs" — no real browser involved.
 *
 * Note the shape of `sample`: the reducer is passed IN and only the reduced
 * grid comes OUT. An earlier version returned `{data, width, height}` and
 * relied on the caller to discard the buffer immediately — which it did, but
 * that made the guarantee a convention rather than a structure, and the module
 * docstring claimed more than the code enforced. Now there is no signature
 * anywhere in the client through which a frame can leave its frame source, so
 * the invariant holds against the next edit as well as this one.
 */
export interface FrameSource {
  /** Opens the camera. Resolves once frames are capturable. */
  start(deviceId?: string): Promise<void>;
  /**
   * Grab one frame, reduce it, and return only the reduction — or `null` if the
   * camera is not ready. The pixel buffer never crosses this boundary.
   */
  sample(reduce: FrameReducer): Float32Array | null;
  /** Releases the camera. Idempotent; safe to call when never started. */
  stop(): void;
  /** The active camera's label, once permission has revealed one. */
  label(): string | null;
}

/**
 * The real camera, via `getUserMedia` + an offscreen `<video>` + `<canvas>`.
 *
 * This class is the ONLY place `getImageData` is called anywhere in the
 * client — see the module docstring for why that makes the frame-never-
 * escapes guarantee a property of the code.
 */
export class BrowserFrameSource implements FrameSource {
  #stream: MediaStream | null = null;
  #video: HTMLVideoElement | null = null;
  #canvas: HTMLCanvasElement | null = null;
  #ctx: CanvasRenderingContext2D | null = null;

  async start(deviceId?: string): Promise<void> {
    this.stop();
    const constraints: MediaStreamConstraints = {
      video: deviceId ? { deviceId: { exact: deviceId } } : true,
      audio: false,
    };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play().catch(() => {
      /* autoplay quirks vary; loadedmetadata below is what we actually wait on */
    });
    await new Promise<void>((resolve) => {
      if (video.readyState >= 1 && video.videoWidth > 0) {
        resolve();
        return;
      }
      video.addEventListener("loadedmetadata", () => resolve(), { once: true });
    });
    this.#stream = stream;
    this.#video = video;
    this.#canvas = document.createElement("canvas");
    this.#canvas.width = video.videoWidth || 320;
    this.#canvas.height = video.videoHeight || 240;
    this.#ctx = this.#canvas.getContext("2d", { willReadFrequently: true });
  }

  sample(reduce: FrameReducer): Float32Array | null {
    if (!this.#video || !this.#canvas || !this.#ctx) return null;
    const { width, height } = this.#canvas;
    if (width === 0 || height === 0) return null;
    this.#ctx.drawImage(this.#video, 0, 0, width, height);
    // The pixel buffer exists only as a local in this one synchronous block. It
    // is passed to the reducer and then unreachable: it is not stored on the
    // instance, not returned, and not captured by any closure that outlives this
    // call. There is no way to ask this class for a frame.
    const pixels = this.#ctx.getImageData(0, 0, width, height).data;
    return reduce(pixels, width, height);
  }

  stop(): void {
    this.#stream?.getTracks().forEach((track) => track.stop());
    this.#stream = null;
    if (this.#video) {
      this.#video.srcObject = null;
      this.#video = null;
    }
    this.#canvas = null;
    this.#ctx = null;
  }

  label(): string | null {
    const track = this.#stream?.getVideoTracks()[0];
    return track?.label || null;
  }
}

// -------------------------------------------------------------- controller

/**
 * The measured numbers behind the last observation — shown on the Core so the
 * owner can see WHY the eye says what it says. Numbers only, never a frame.
 */
export type MotionStatus = {
  /** Fraction of grid cells that changed since the previous sample. */
  changedCellRatio: number;
  /** The strongest single-cell change, 0..1. */
  maxCellDelta: number;
  /** Milliseconds since the last meaningful movement, or `null` if none yet. */
  msSinceLastMotion: number | null;
  /** How strong that last movement was — an exit is `high`. */
  lastMotionLevel: ActivityLevel;
};

export type PerceptionStatus = {
  running: boolean;
  cameraLabel: string | null;
  lastObservation: EyeObservation | null;
  /** The measurements the last observation was derived from; `null` before the first sample. */
  motion: MotionStatus | null;
  /** Owner-facing text for the last thing that went wrong, or `null`. */
  lastError: string | null;
  startedAt: number | null;
};

export type PerceptionOptions = {
  /** How often to sample. Configurable per the task brief; a low default. */
  sampleIntervalMs?: number;
  /** Samples needed before `presence_confidence`'s sample term is "full". */
  samplesForFullConfidence?: number;
  deviceId?: string;
  onObservation?: (observation: EyeObservation) => void;
  onStatusChange?: (status: PerceptionStatus) => void;
  /** Injectable for tests; defaults to the real API client. */
  postObservation?: typeof postObservation;
  /** Injectable for tests; defaults to `BrowserFrameSource`. */
  frameSource?: FrameSource;
  now?: () => number;
};

const DEFAULT_SAMPLE_INTERVAL_MS = 5_000;
const DEFAULT_SAMPLES_FOR_FULL_CONFIDENCE = 5;

/**
 * Owns one perception run: the camera, the sampling loop, the rolling
 * evidence window, and the disable-immediacy guarantee.
 *
 * A fresh instance per `start()`/`stop()` cycle is not required — the same
 * session can be started again after stopping — but every field the loop
 * depends on is reset in `start()` so a restarted session carries no memory
 * of the room from before it was told to stop watching it.
 */
export class PerceptionSession {
  readonly #opts: Required<
    Pick<PerceptionOptions, "sampleIntervalMs" | "samplesForFullConfidence" | "now">
  > &
    PerceptionOptions;
  readonly #frameSource: FrameSource;
  readonly #post: typeof postObservation;

  #generation = 0;
  #stopped = true;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #abort: AbortController | null = null;

  #previousGrid: Float32Array | null = null;
  #sampleCount = 0;
  #stillSinceMs: number | null = null;
  // The motion memory (signal.ts): when the last meaningful movement happened and how
  // strong it was. These two numbers are what "present" is derived from - not this tick's
  // motion alone, which is what let a seated owner read as absent (2026-09-06 owner run).
  #lastMotionAtMs: number | null = null;
  #lastMotionLevel: ActivityLevel = "none";

  #status: PerceptionStatus = {
    running: false,
    cameraLabel: null,
    lastObservation: null,
    motion: null,
    lastError: null,
    startedAt: null,
  };

  constructor(options: PerceptionOptions = {}) {
    this.#opts = {
      sampleIntervalMs: options.sampleIntervalMs ?? DEFAULT_SAMPLE_INTERVAL_MS,
      samplesForFullConfidence: options.samplesForFullConfidence ?? DEFAULT_SAMPLES_FOR_FULL_CONFIDENCE,
      now: options.now ?? Date.now,
      ...options,
    };
    this.#frameSource = options.frameSource ?? new BrowserFrameSource();
    this.#post = options.postObservation ?? postObservation;
  }

  getStatus(): PerceptionStatus {
    return this.#status;
  }

  async start(): Promise<void> {
    this.#stopped = false;
    this.#generation += 1;
    const generation = this.#generation;
    this.#previousGrid = null;
    this.#sampleCount = 0;
    this.#stillSinceMs = null;
    this.#lastMotionAtMs = null;
    this.#lastMotionLevel = "none";

    await this.#frameSource.start(this.#opts.deviceId);
    if (this.#stopped || generation !== this.#generation) {
      // stop() ran while the camera was opening: close what we just opened
      // and report nothing — never leave a track running behind a "stopped" status.
      this.#frameSource.stop();
      return;
    }
    this.#setStatus({
      running: true,
      cameraLabel: this.#frameSource.label(),
      startedAt: this.#opts.now(),
      lastError: null,
    });
    this.#scheduleNext(generation, 0);
  }

  /**
   * Stops perception immediately: clears the timer, releases the camera
   * (the browser's own indicator light goes out synchronously, here), and
   * aborts any observation POST still in flight. See the module docstring
   * for why an in-flight tick cannot race this.
   */
  stop(): void {
    this.#stopped = true;
    this.#generation += 1;
    if (this.#timer !== null) {
      clearTimeout(this.#timer);
      this.#timer = null;
    }
    this.#abort?.abort();
    this.#abort = null;
    this.#frameSource.stop();
    this.#setStatus({ running: false, cameraLabel: null });
  }

  #setStatus(patch: Partial<PerceptionStatus>): void {
    this.#status = { ...this.#status, ...patch };
    this.#opts.onStatusChange?.(this.#status);
  }

  #scheduleNext(generation: number, delayMs: number): void {
    if (this.#stopped || generation !== this.#generation) return;
    this.#timer = setTimeout(() => {
      void this.#tick(generation);
    }, delayMs);
  }

  async #tick(generation: number): Promise<void> {
    if (this.#stopped || generation !== this.#generation) return;

    // The reducer goes in; only the luminance grid comes back. `tick` never
    // holds a pixel buffer at all, so there is nothing here to leak, log or
    // accidentally keep.
    const grid = this.#frameSource.sample(computeGridLuminance);
    if (!grid) {
      this.#scheduleNext(generation, this.#opts.sampleIntervalMs);
      return;
    }

    // Everything from here to the pre-post check is synchronous: `stop()`
    // cannot run in the middle of it (see module docstring).
    const motion = measureMotion(grid, this.#previousGrid);
    this.#previousGrid = grid;
    this.#sampleCount += 1;

    const observedAtMs = this.#opts.now();
    const currentActivity = activityLevelFor(motion.changedCellRatio);
    if (currentActivity === "none") {
      this.#stillSinceMs ??= observedAtMs;
    } else {
      this.#stillSinceMs = null;
      this.#lastMotionAtMs = observedAtMs;
      this.#lastMotionLevel = currentActivity;
    }
    const stillDurationMs = this.#stillSinceMs !== null ? observedAtMs - this.#stillSinceMs : 0;
    const msSinceLastMotion = this.#lastMotionAtMs === null ? null : observedAtMs - this.#lastMotionAtMs;

    const observation = deriveObservation(
      {
        currentActivity,
        msSinceLastMotion,
        lastMotionLevel: this.#lastMotionLevel,
        sampleCount: this.#sampleCount,
        samplesForFullConfidence: this.#opts.samplesForFullConfidence,
        stillDurationMs,
      },
      new Date(observedAtMs),
    );

    this.#setStatus({
      lastObservation: observation,
      cameraLabel: this.#frameSource.label(),
      motion: {
        changedCellRatio: motion.changedCellRatio,
        maxCellDelta: motion.maxCellDelta,
        msSinceLastMotion,
        lastMotionLevel: this.#lastMotionLevel,
      },
    });
    this.#opts.onObservation?.(observation);

    if (this.#stopped || generation !== this.#generation) return; // disabled between capture and post

    const controller = new AbortController();
    this.#abort = controller;
    try {
      const result = await this.#post(observation, { signal: controller.signal });
      if (this.#stopped || generation !== this.#generation) return; // disabled while the POST was in flight
      if (result.status === "eye_disabled") {
        // The server-side flag is off (possibly from another device, or from
        // the voice path). Stop locally too: there is no honest reason to
        // keep sampling a camera the Cloud Core has been told to ignore.
        this.stop();
        return;
      }
      if (result.status === "rejected") {
        this.#setStatus({ lastError: result.detail });
      }
    } catch (err) {
      if (this.#stopped || generation !== this.#generation) return; // an abort from our own stop() is not an error to report
      this.#setStatus({ lastError: err instanceof Error ? err.message : String(err) });
    } finally {
      if (this.#abort === controller) this.#abort = null;
    }

    this.#scheduleNext(generation, this.#opts.sampleIntervalMs);
  }
}
