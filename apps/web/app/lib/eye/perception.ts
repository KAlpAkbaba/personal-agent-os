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
 * bound for the outside world through `onObservation`/`onStatusChange` is
 * `EyeObservation` — the seven-field type — or a `PerceptionStatus` that
 * carries the same. There is no method anywhere in `FrameSource`,
 * `PerceptionSession`, `EyeStore` (`store.ts`) or `useActivePerception` that
 * returns pixel DATA — a canvas, an `ImageData`, a `MediaStream`'s frames —
 * to a caller. That is the mechanism, not a comment on top of one: grep this
 * module for `ImageData`, `getImageData`, `Uint8ClampedArray`, `toDataURL` or
 * `captureStream` and every hit is inside `BrowserFrameSource`, and none of
 * them is returned by anything the class exposes publicly.
 *
 * ADR-0198 (el hareketi kumandası, Stage 1) narrows this one specific way, deliberately:
 * `FrameSource.videoElement()` / `PerceptionSession.videoElement()` /
 * `PerceptionSession.attachVideoConsumer()` DO hand out the live `<video>` ELEMENT itself —
 * not a frame, not a pixel, not a copy — to a same-tab, in-process consumer (the gesture
 * tracker, `lib/gesture/tracker.ts`), because MediaPipe's `HandLandmarker.detectForVideo`
 * needs the element itself to read frames for local inference, the same way `sample()`'s own
 * `drawImage`/`getImageData` do. This is still "never leaves the tab": no new sink is added
 * that could serialise, upload or persist a frame — a consumer can only feed the element to
 * more on-device inference, exactly what `BrowserFrameSource.sample()` already does
 * internally. What remains categorically true, unchanged by this addition, is the ORIGINAL
 * claim above: no PIXEL BUFFER (a copyable, serialisable `ImageData`/`Uint8ClampedArray`)
 * is ever returned by anything in this module.
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
import type { ActivityLevel, EyeObservation, MediaTrackReadyState } from "./types";

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
/**
 * Where an open got to, one short stage at a time, for the action trace the
 * store relays (`observed_after.local.action_trace`). Text only: a stage
 * names a step (`permission:granted`), a device by its label, or a track
 * state — never a frame, a pixel count, or any identifier.
 */
export type StageReport = (stage: string) => void;

/**
 * Thrown by `BrowserFrameSource.start()` when `getUserMedia` resolved but the
 * stream's video track was not `"live"` — the browser handed over a stream
 * that had already ended (a device unplugged mid-prompt, a track revoked by
 * the OS). The source has already stopped the track when this is thrown; the
 * store maps it onto `stream_created_but_track_ended`.
 */
export class CameraTrackEndedError extends Error {
  override readonly name = "CameraTrackEndedError";

  constructor(readonly readyState: MediaTrackReadyState | null) {
    super(`camera stream created but its video track is ${readyState ?? "missing"}`);
  }
}

/**
 * Thrown by `PerceptionSession.start()` when the camera opened but the loop
 * could not be started afterwards (a status listener threw, the timer could
 * not be scheduled). The session has released the camera by then; the store
 * maps it onto `perception_start_failed` — distinct from a `getUserMedia`
 * failure, because the stream DID open.
 */
export class PerceptionStartError extends Error {
  override readonly name = "PerceptionStartError";

  constructor(readonly cause: unknown) {
    super(`perception loop failed to start after the camera opened: ${describeCause(cause)}`);
  }
}

function describeCause(cause: unknown): string {
  if (cause instanceof Error) return cause.name;
  return typeof cause === "string" ? cause : "unknown";
}

export interface FrameSource {
  /**
   * Opens the camera. Resolves once frames are capturable. `report`, when
   * given, receives each stage of the open as it happens (see `StageReport`).
   */
  start(deviceId?: string, report?: StageReport): Promise<void>;
  /**
   * Grab one frame, reduce it, and return only the reduction — or `null` if the
   * camera is not ready. The pixel buffer never crosses this boundary.
   */
  sample(reduce: FrameReducer): Float32Array | null;
  /** Releases the camera. Idempotent; safe to call when never started. */
  stop(): void;
  /** The active camera's label, once permission has revealed one. */
  label(): string | null;
  /**
   * The `readyState` of the most recent video track this source opened —
   * `"live"` while it is streaming, `"ended"` once stopped (proof the camera
   * light is off, not merely that the track was forgotten) — or `null` when
   * no track was ever opened. Optional: a synthetic source has no track.
   */
  trackReadyState?(): MediaTrackReadyState | null;
  /**
   * A short handle for that same track (`MediaStreamTrack.id`'s first
   * `TRACK_ID_CHARS` characters), so an action trace can show that a
   * re-enable opened a NEW stream rather than reused the stopped one. A
   * track id is a per-stream random token the browser mints, never imagery
   * and never a device identifier. Optional: a synthetic source has none.
   */
  trackShortId?(): string | null;
  /**
   * ADR-0198 (el hareketi kumandası, Stage 1): the live `<video>` element this source is
   * currently drawing from, or `null` when none is open — a SECOND consumer (the gesture
   * tracker) reading frames from the SAME element `sample()` already reads, never a second
   * `getUserMedia`. Optional: a synthetic test source has none, and returning `undefined`
   * here is the same as `null` to every caller (`PerceptionSession.videoElement()` below
   * folds it with `?? null`). This is read-only: nothing about the video element's own
   * pixels crosses this boundary any more than `sample()`'s pixel buffer does — a consumer
   * gets the element itself (as `detectForVideo` needs) and must not mutate it.
   */
  videoElement?(): HTMLVideoElement | null;
}

/** How much of a `MediaStreamTrack.id` the trace shows. */
export const TRACK_ID_CHARS = 8;

/** `track.id`'s first `TRACK_ID_CHARS` characters, or `null` for no track / no id. */
export function shortTrackId(track: { id?: unknown } | null): string | null {
  if (!track || typeof track.id !== "string" || track.id.length === 0) return null;
  return track.id.slice(0, TRACK_ID_CHARS);
}

/**
 * The real camera, via `getUserMedia` + an offscreen `<video>` + `<canvas>`.
 *
 * This class is the ONLY place `getImageData` is called anywhere in the
 * client — see the module docstring for why that makes the frame-never-
 * escapes guarantee a property of the code.
 *
 * A stopped track is never reused: every `start()` is a fresh `getUserMedia`
 * (the previous stream, if any, is released first), and a stream whose track
 * is not live on arrival is stopped on the spot and reported as such rather
 * than kept around as a camera that "opened".
 *
 * ## An open that is superseded releases what IT opened, and nothing else
 *
 * `getUserMedia` cannot be cancelled: a prompt the owner answers late resolves
 * long after a `stop()` — or after a NEWER `start()` — has run. Every open
 * takes a sequence number; `stop()` and a later `start()` advance it. When a
 * stale open resolves it stops the tracks of the stream it was handed and
 * returns without touching any field, so the stream a newer open installed
 * (and the track it answers `trackReadyState()` for) is never overwritten
 * or stopped by an older one. Before this guard, an enable superseded during
 * its prompt could, on resolving, replace the live stream of the NEXT enable
 * with its own and then have it stopped — one live orphan track, one loop
 * sampling nothing.
 */
export class BrowserFrameSource implements FrameSource {
  #stream: MediaStream | null = null;
  #video: HTMLVideoElement | null = null;
  #canvas: HTMLCanvasElement | null = null;
  #ctx: CanvasRenderingContext2D | null = null;
  /** The last video track opened, kept only to answer `trackReadyState()` / `trackShortId()` after a stop. */
  #lastTrack: MediaStreamTrack | null = null;
  /** Advanced by every `start()` and `stop()`; an open whose number is no longer current is stale. */
  #openSeq = 0;

  async start(deviceId?: string, report: StageReport = () => {}): Promise<void> {
    this.stop();
    this.#openSeq += 1;
    const seq = this.#openSeq;
    const constraints: MediaStreamConstraints = {
      video: deviceId ? { deviceId: { exact: deviceId } } : true,
      audio: false,
    };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    if (this.#release(seq, stream, report)) return;
    // Resolved: the permission question is answered. What came back is checked
    // before it is trusted — a stream is only a camera if its track is live.
    report("permission:granted");
    const track = stream.getVideoTracks()[0] ?? null;
    this.#lastTrack = track;
    if (track?.label) report(`device:${track.label}`);
    const shortId = shortTrackId(track);
    if (shortId) report(`track:${shortId}`);
    this.#assertLive(stream, track, report);
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
    if (this.#release(seq, stream, report)) {
      video.srcObject = null;
      // Only this open's own track is forgotten; a newer open's stays answerable.
      if (this.#lastTrack === track) this.#lastTrack = null;
      return;
    }
    // The track can end while the element loads (device yanked); check again
    // before this counts as an open camera.
    this.#assertLive(stream, track, null);
    report(`stream:${stream.getVideoTracks().length} track live`);
    this.#stream = stream;
    this.#video = video;
    this.#canvas = document.createElement("canvas");
    this.#canvas.width = video.videoWidth || 320;
    this.#canvas.height = video.videoHeight || 240;
    this.#ctx = this.#canvas.getContext("2d", { willReadFrequently: true });
  }

  /**
   * True — with the stream's tracks stopped — when the open numbered `seq` was
   * overtaken by a `stop()` or a later `start()` while it waited. The stale
   * open then owns nothing: its caller (`PerceptionSession.start`) sees the
   * generation mismatch and reports nothing further.
   */
  #release(seq: number, stream: MediaStream, report: StageReport): boolean {
    if (seq === this.#openSeq) return false;
    stream.getTracks().forEach((t) => t.stop());
    report("stream:stale release");
    return true;
  }

  /** Stop and throw unless the stream's video track is `"live"`; never leaves a track behind. */
  #assertLive(stream: MediaStream, track: MediaStreamTrack | null, report: StageReport | null): void {
    const readyState = track ? track.readyState : null;
    if (readyState === "live") return;
    stream.getTracks().forEach((t) => t.stop());
    report?.(`stream:track ${readyState ?? "missing"}`);
    throw new CameraTrackEndedError(readyState);
  }

  trackReadyState(): MediaTrackReadyState | null {
    return this.#lastTrack ? this.#lastTrack.readyState : null;
  }

  trackShortId(): string | null {
    return shortTrackId(this.#lastTrack);
  }

  /** ADR-0198: the live element (see the module docstring), or `null` when no camera is open. */
  videoElement(): HTMLVideoElement | null {
    return this.#video;
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
    this.#openSeq += 1; // any open still waiting on its prompt is now stale
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
  /** ADR-0198: same-tab consumers of the live video element (see the module docstring). */
  #videoConsumers = new Set<(video: HTMLVideoElement | null) => void>();

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
    // The defaults come AFTER the spread: an option passed as an explicit
    // `undefined` (which is what `{ sampleIntervalMs: opts.sampleIntervalMs }`
    // produces when the caller had none) must still get the default. With
    // the spread last, `sampleIntervalMs: undefined` reached `setTimeout` as a
    // 0 ms delay and the camera loop sampled as fast as the POST round trip
    // allowed instead of every five seconds.
    this.#opts = {
      ...options,
      sampleIntervalMs: options.sampleIntervalMs ?? DEFAULT_SAMPLE_INTERVAL_MS,
      samplesForFullConfidence: options.samplesForFullConfidence ?? DEFAULT_SAMPLES_FOR_FULL_CONFIDENCE,
      now: options.now ?? Date.now,
    };
    this.#frameSource = options.frameSource ?? new BrowserFrameSource();
    this.#post = options.postObservation ?? postObservation;
  }

  getStatus(): PerceptionStatus {
    return this.#status;
  }

  /**
   * Opens the camera and starts the sampling loop. `report` receives the
   * open's stages (see `StageReport`); `loop:started` is reported here, once
   * the loop is really scheduled, so a caller can tell "the stream opened"
   * from "the loop runs" when something throws in between.
   */
  async start(report: StageReport = () => {}): Promise<void> {
    this.#stopped = false;
    this.#generation += 1;
    const generation = this.#generation;
    this.#previousGrid = null;
    this.#sampleCount = 0;
    this.#stillSinceMs = null;
    this.#lastMotionAtMs = null;
    this.#lastMotionLevel = "none";

    await this.#frameSource.start(this.#opts.deviceId, report);
    if (this.#stopped || generation !== this.#generation) {
      // This open was overtaken while the camera was opening, and reports
      // nothing. If the session is STOPPED, close what was just opened —
      // never leave a track running behind a "stopped" status. If instead a
      // NEWER start() owns the source now (stop, then start again, before
      // this prompt was answered), the source is that start's to keep: the
      // frame source releases its own stale stream (`BrowserFrameSource`),
      // and stopping it here would turn off the newer camera.
      if (this.#stopped) this.#frameSource.stop();
      return;
    }
    try {
      this.#setStatus({
        running: true,
        cameraLabel: this.#frameSource.label(),
        startedAt: this.#opts.now(),
        lastError: null,
      });
      this.#notifyVideoConsumers();
      this.#scheduleNext(generation, 0);
    } catch (cause) {
      // The stream is open but the loop is not: release the camera rather
      // than leave a track running behind a session that never started.
      try {
        this.stop();
      } catch {
        this.#frameSource.stop();
      }
      throw new PerceptionStartError(cause);
    }
    report("loop:started");
  }

  /** The frame source's track state, for the store's read-back; `null` for a source without tracks. */
  trackReadyState(): MediaTrackReadyState | null {
    return this.#frameSource.trackReadyState?.() ?? null;
  }

  /** The frame source's short track handle, for the store's trace; `null` for a source without tracks. */
  trackShortId(): string | null {
    return this.#frameSource.trackShortId?.() ?? null;
  }

  /** ADR-0198: the live video element the frame source is currently drawing from, or `null`
   * when no camera is open. See the module docstring — this hands out the ELEMENT, never a
   * pixel buffer. */
  videoElement(): HTMLVideoElement | null {
    return this.#frameSource.videoElement?.() ?? null;
  }

  /**
   * ADR-0198: subscribe to the live video element, called immediately with the CURRENT value
   * and again whenever it might have changed (the loop starting, or stopping). Returns an
   * unsubscribe. This is the gesture tracker's only way to reach a video element — it never
   * opens its own camera (`GestureTracker` takes a `<video>` element as an argument, never a
   * `deviceId`).
   */
  attachVideoConsumer(cb: (video: HTMLVideoElement | null) => void): () => void {
    this.#videoConsumers.add(cb);
    cb(this.videoElement());
    return () => {
      this.#videoConsumers.delete(cb);
    };
  }

  #notifyVideoConsumers(): void {
    const video = this.videoElement();
    for (const cb of this.#videoConsumers) cb(video);
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
    this.#notifyVideoConsumers();
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
