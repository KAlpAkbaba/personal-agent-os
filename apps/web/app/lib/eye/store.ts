/**
 * The one Active Eye of this tab (M18_ACTION_CONTRACT.md §7.1; the pattern is
 * `lib/voice/store.ts`, ADR-0061 §1).
 *
 * `getEyeStore()` is a module-level singleton that lazily builds ONE
 * `PerceptionSession` over ONE frame source the first time anything needs it,
 * and never builds them again for the life of the tab. Every view that shows
 * the eye reads this store; mounting a second view, or unmounting and
 * remounting one, adds and removes a listener and does nothing else — in
 * particular it never opens a second camera stream and never stops the one
 * that is open. The eye is on until it is told otherwise, by the owner's
 * button, by the owner's voice (`eye.disable` through the voice rig's
 * `LocalActionPort`), or by the Cloud Core (`eye.disabled` on the bus, a 409
 * on the next observation).
 *
 * ## The state machine
 *
 * ```
 * DISABLED  -enable()->  ENABLING  -camera open, loop started, POST eye/enable ok->  ACTIVE
 * ACTIVE    -disable()-> DISABLING -POST eye/disable, loop stopped->                DISABLED
 * ENABLING  -camera failure / 8 s bound->  ERROR (error_class kept)  -disable()->   DISABLED
 * ACTIVE    -loop stopped by the Cloud Core (409) or a NEWER bus eye.disabled->   DISABLED
 * ```
 *
 * `enable()`/`disable()` answer a `LocalEyeResult`: what this device can
 * honestly say about its own camera the moment the call settled. Already in
 * the requested state → `changed: false`, no second stream, no fake
 * transition. A second `enable()` while one is in flight joins it (one
 * `getUserMedia`, one prompt). The two orders are deliberate and different:
 *
 * - disable: the DURABLE call first, the local stop second (as the owner
 *   button always did) — a sample already captured when the owner spoke is
 *   refused server-side the instant the flag flips, before the loop notices;
 * - enable: the LOCAL camera first, the durable call only once the sampling
 *   loop has actually started — never tell the Cloud Core perception is on
 *   before it is.
 *
 * ## Generation-owned transitions
 *
 * Every transition (an enable, a disable, a bus-driven local stop) takes a
 * monotonically increasing generation, `gen:N`, the first stage of its
 * trace. A state commit or a trace stage belongs to the generation that
 * started the work, and is applied only while that generation is the
 * current one. Whatever settles late — the camera prompt of a superseded
 * enable, its 8 s timer, a durable POST, a session status callback — is
 * recognised by its generation and recorded on the CURRENT trace as
 * `ignored:gen<N>`; it never moves the state. A `disable()` returns its
 * terminal result only once nothing of its own or of the enable it
 * interrupted can still mutate state: the interrupted enable's timer is
 * cleared synchronously, its durable POST (if it got that far) and the
 * disable's own are awaited, and a camera prompt that cannot be cancelled
 * is defused by three independent guards (this store's generation, the
 * session's, and `BrowserFrameSource`'s open sequence).
 *
 * ## The bus-driven stop is a dated request, not a command
 *
 * The owner's run of 2026-09-06 (session 9df439af): after a verified
 * disable, EVERY following "Gözünü aç" came back `state_mismatch` with the
 * trace `request:stop_local > superseded:stop > state:ENABLING->DISABLED`.
 * `EyeControl`'s effect re-ran when the loop started (`status.running`
 * flipped, BEFORE the durable enable, by design) while the page's last
 * polled bus state was still the previous `eye.disabled`, and the old
 * `stopLocalOnly()` obeyed it: a STALE external event cancelled a NEWER
 * transition, and `beginTrace("stop_local")` erased the enable's own trace so
 * the receipt could not even show what the enable had done. The first
 * enable had escaped only because the bus claim had `expired` by then.
 * `stopLocalIfStale()` replaces it: the bus view is dated (`ageMs`), and a
 * stop happens only from ACTIVE, only for an event newer than the moment
 * ACTIVE was committed and newer than this generation's durable enable —
 * the pure rule is `shouldStopLocalPerception` (`reconcile.ts`).
 *
 * Every camera failure is mapped onto the closed `EyeErrorClass` set the
 * server's receipt speaks (§5.2); nothing else is ever relayed. `enable()`
 * and `disable()` never reject: a failure is an observation.
 *
 * Nothing here is React; `useActivePerception.ts` is the binding.
 */

import type { EyeView } from "../uistate/ambient";
import { type EyeActionIdentity, disableEye, enableEye, explainEyeError } from "./client";
import { LOCAL_EYE_ERROR_TEXT } from "./labels";
import {
  BrowserFrameSource,
  type FrameSource,
  PerceptionSession,
  type PerceptionOptions,
  type PerceptionStatus,
  type StageReport,
} from "./perception";
import { type LocalEyeFacts, shouldStopLocalPerception } from "./reconcile";
import type { CameraPermission, EyeErrorClass, MediaTrackReadyState } from "./types";

export type EyeState = "DISABLED" | "ENABLING" | "ACTIVE" | "DISABLING" | "ERROR";

/** What `observed_after.local.state` may say (§5.1): only terminal states are observations. */
export type LocalEyeState = "ACTIVE" | "DISABLED" | "ERROR";

/** The dated bus view of the eye, as `EyeControl` receives it from the page. */
export type BusEyeEvent = Pick<EyeView, "status" | "ageMs" | "expired">;

export type LocalEyeResult = {
  state: LocalEyeState;
  running: boolean;
  camera_label: string | null;
  error_class: EyeErrorClass | null;
  /** ISO-8601, the moment this result was read. */
  observed_at: string;
  /** False when the store was already in the requested state and did nothing. */
  changed: boolean;
  /**
   * The current video track's `readyState` right after the action: `"live"`
   * for an open camera, `"ended"` for one that was really stopped (the light
   * is off), `null` when this device holds no track at all.
   */
  media_track_ready_state: MediaTrackReadyState | null;
  /**
   * The stages the store went through for THIS action, in order, bounded to
   * `MAX_ACTION_TRACE` entries — e.g. `["gen:3", "request:enable",
   * "action:call_7f2", "getUserMedia:called", "permission:granted",
   * "device:Integrated Webcam", "track:1a2b3c4d", "stream:1 track live",
   * "loop:started", "durable:enable", "state:ENABLING->ACTIVE"]`. Text only:
   * step names, the generation, the action's call id, the camera's label, a
   * track's short handle and state, an error's NAME. Never a frame or a
   * pixel count.
   */
  action_trace: string[];
};

/** §5.1: the local capability is bounded; past this an enable is `timeout`. */
export const LOCAL_EYE_TIMEOUT_MS = 8_000;

/** The action trace never grows past this; the newest stage always survives. */
export const MAX_ACTION_TRACE = 12;

// ---------------------------------------------------------------- registry

/**
 * How many of each singleton-shaped thing this tab has built — the cheapest
 * thing that can DISPROVE "one session, one camera, one loop" (the same
 * device `voiceInstances` is in `lib/voice/rig.ts`). Every construction or
 * open site below bumps one; the tests read them back after mounting the
 * hook twice and enabling twice.
 */
export type EyeInstanceCounts = {
  /** `PerceptionSession` constructions. */
  sessions: number;
  /** `FrameSource.start()` calls that reached the source (each is one `getUserMedia`). */
  cameraOpens: number;
  /** Sampling loops that actually started (a camera opened and the session reported `running`). */
  loopsStarted: number;
};

function emptyCounts(): EyeInstanceCounts {
  return { sessions: 0, cameraOpens: 0, loopsStarted: 0 };
}

class InstanceRegistry {
  private counts = emptyCounts();

  snapshot(): EyeInstanceCounts {
    return { ...this.counts };
  }

  bump(key: keyof EyeInstanceCounts): void {
    this.counts[key] += 1;
  }

  /** Tests only: a fresh tab. */
  reset(): void {
    this.counts = emptyCounts();
  }
}

export const eyeInstances = new InstanceRegistry();

// ------------------------------------------------------------------- parts

/**
 * What the store builds its one session from. `browserEyeParts` is the only
 * builder that touches the browser; tests pass a fake frame source and a
 * recording `postObservation`, exactly as `tests/eye/presence-memory.test.ts`
 * drives `PerceptionSession` directly.
 */
export type EyeParts = {
  frameSource: FrameSource;
  postObservation?: PerceptionOptions["postObservation"];
  sampleIntervalMs?: number;
  /** Wall clock in ms (the perception clock and `observed_at`); `Date.now` by default. */
  now?: () => number;
  /**
   * Whether a camera can be asked for at all. False answers `capability_missing`
   * without touching the frame source; absent means yes.
   */
  cameraAvailable?: () => boolean;
  /**
   * The browser's own permission state, when it can give one; called once on
   * first subscribe, returns the unsubscribe. Absent: the store only learns
   * the permission from an enable's outcome.
   */
  watchPermission?: (report: (permission: CameraPermission) => void) => () => void;
};

export type EyeBuilder = () => EyeParts;

/**
 * The browser Permissions API for `camera`, where it exists. Support is
 * inconsistent (notably absent in some WebKit builds), so "unsupported" is a
 * real, distinct answer rather than a fallback to "unknown".
 */
function watchBrowserCameraPermission(report: (permission: CameraPermission) => void): () => void {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) {
    report("unsupported");
    return () => {};
  }
  let cancelled = false;
  let handle: PermissionStatus | null = null;
  const onChange = () => {
    if (handle) report(handle.state as CameraPermission);
  };
  navigator.permissions
    .query({ name: "camera" as PermissionName })
    .then((result) => {
      if (cancelled) return;
      handle = result;
      report(result.state as CameraPermission);
      result.addEventListener("change", onChange);
    })
    .catch(() => {
      if (!cancelled) report("unsupported");
    });
  return () => {
    cancelled = true;
    handle?.removeEventListener("change", onChange);
  };
}

/** The real camera. This is the only place a `BrowserFrameSource` is built for the Core. */
export function browserEyeParts(): EyeBuilder {
  return () => ({
    frameSource: new BrowserFrameSource(),
    cameraAvailable: () =>
      typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function",
    watchPermission: watchBrowserCameraPermission,
  });
}

// ----------------------------------------------------------------- errors

/** An error's `name` (a DOMException's is its kind), or `""` for a non-error. */
export function errorName(err: unknown): string {
  return typeof err === "object" && err !== null && "name" in err ? String((err as { name: unknown }).name) : "";
}

/**
 * A camera open's failure, onto the receipt's closed set (`types.ts`) by the
 * error's NAME — structured, never generic. The `getUserMedia` names are the
 * WebRTC spec's; the two class names are this module's own (`perception.ts`)
 * and mark failures AFTER `getUserMedia` resolved, which is why "any other
 * rejection" is `get_user_media_failed` and not one of those. A TypeError
 * (no `mediaDevices` at all) never reaches here: `cameraAvailable()` answers
 * `capability_missing` before the source is touched.
 */
export function classifyCameraError(err: unknown): EyeErrorClass {
  const name = errorName(err);
  if (name === "NotAllowedError" || name === "SecurityError") return "permission_denied";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "device_not_found";
  if (name === "NotReadableError" || name === "AbortError") return "device_busy";
  if (name === "CameraTrackEndedError") return "stream_created_but_track_ended";
  if (name === "PerceptionStartError") return "perception_start_failed";
  return "get_user_media_failed";
}

// ------------------------------------------------------------------ store

export type EyeStoreSnapshot = {
  /** False on the server and before the session exists; every other field is then a placeholder. */
  ready: boolean;
  state: EyeState;
  /** The session's own status (what the loop measured; numbers, never a frame). */
  status: PerceptionStatus;
  permission: CameraPermission;
  /** ENABLING or DISABLING: a round trip is in progress. */
  busy: boolean;
  /** Owner-facing text for the last thing that went wrong, or `null`. */
  error: string | null;
  /** The closed-vocabulary reason behind `state === "ERROR"`, else `null`. */
  errorClass: EyeErrorClass | null;
  /** The stages of the most recent (or in-flight) action, for the Core's eye cell; see `LocalEyeResult.action_trace`. */
  lastActionTrace: string[];
  /** The generation that owns the state right now (the `gen:N` of the last transition). */
  generation: number;
  instances: EyeInstanceCounts;
};

const INITIAL_STATUS: PerceptionStatus = {
  running: false,
  cameraLabel: null,
  lastObservation: null,
  motion: null,
  lastError: null,
  startedAt: null,
};

const SERVER_SNAPSHOT: EyeStoreSnapshot = Object.freeze({
  ready: false,
  state: "DISABLED" as const,
  status: INITIAL_STATUS,
  permission: "unknown" as const,
  busy: false,
  error: null,
  errorClass: null,
  lastActionTrace: [],
  generation: 0,
  instances: emptyCounts(),
});

export type EyeDurable = {
  enable: (reason: string, identity?: EyeActionIdentity) => Promise<void>;
  disable: (reason: string, identity?: EyeActionIdentity) => Promise<void>;
};

export type EyeStoreOptions = {
  /** Builds the session's parts; the browser builder by default (see `getEyeStore`). */
  build: EyeBuilder;
  /** The Cloud Core's durable flag; `client.ts`'s `enableEye`/`disableEye` by default. */
  durable?: EyeDurable;
  /** The bound on the local capability; `LOCAL_EYE_TIMEOUT_MS` by default. */
  timeoutMs?: number;
};

type Operation = { kind: "enable" | "disable"; promise: Promise<LocalEyeResult> };

/** The parts of a running enable a disable must defuse before it may proceed. */
type PendingEnable = {
  gen: number;
  timer: ReturnType<typeof setTimeout> | null;
  supersede: () => void;
};

const TIMED_OUT = Symbol("timed_out");
const SUPERSEDED = Symbol("superseded");

/** The stage a stale bus `eye.disabled` leaves behind; at most once per trace. */
const BUS_STALE_STAGE = "bus:stale_disable_ignored";

export class EyeStore {
  private parts: EyeParts | null = null;
  private session: PerceptionSession | null = null;
  private readonly listeners = new Set<() => void>();
  private snapshot: EyeStoreSnapshot = SERVER_SNAPSHOT;

  private state: EyeState = "DISABLED";
  private status: PerceptionStatus = INITIAL_STATUS;
  private permission: CameraPermission = "unknown";
  private error: string | null = null;
  private errorClass: EyeErrorClass | null = null;

  /** The generation that owns the state; every transition takes the next one. */
  private gen = 0;
  /** The generation on whose behalf the session was last started/stopped: what its status callbacks belong to. */
  private sessionGen = 0;
  /** When the current ACTIVE was committed (this store's clock); `null` outside ACTIVE. */
  private activeSince: number | null = null;
  /** When the current generation's durable enable was acknowledged; `null` if it was not (yet). */
  private durableEnabledAt: number | null = null;

  /** Operations run one at a time, in order; `last` is what a same-kind call joins. */
  private tail: Promise<unknown> = Promise.resolve();
  private last: Operation | null = null;
  /** The enable whose camera prompt is still open, if any (a disable or dispose defuses it). */
  private pendingEnable: PendingEnable | null = null;
  /** The running/most recent enable's durable POST, so a disable posts after it, never racing it. */
  private durableEnable: Promise<void> | null = null;
  private unwatch: (() => void) | null = null;

  constructor(private readonly options: EyeStoreOptions) {}

  // -------------------------------------------------------------- lifecycle

  /**
   * Build the session if it does not exist. Idempotent by construction: the
   * only assignment to `this.session` is guarded by its own null check, and
   * there is one store per tab (`getEyeStore`). `eyeInstances` proves it.
   */
  private ensureSession(): PerceptionSession {
    if (this.session) return this.session;
    const parts = this.options.build();
    this.parts = parts;
    const source = parts.frameSource;
    // Count what the loop actually opens, at the source itself.
    const counted: FrameSource = {
      start: (deviceId, report) => {
        eyeInstances.bump("cameraOpens");
        return source.start(deviceId, report);
      },
      sample: (reduce) => source.sample(reduce),
      stop: () => source.stop(),
      label: () => source.label(),
      trackReadyState: () => source.trackReadyState?.() ?? null,
      trackShortId: () => source.trackShortId?.() ?? null,
      // ADR-0198: the gesture tracker reaches the live <video> only through the session,
      // and the session only through THIS wrapper. Found live on 2026-09-21: without this
      // line `videoElement?.()` was undefined here, every consumer got null, and "El
      // kumandası" waited for ever behind a gate that had in fact opened.
      videoElement: () => source.videoElement?.() ?? null,
    };
    eyeInstances.bump("sessions");
    const session = new PerceptionSession({
      frameSource: counted,
      postObservation: parts.postObservation,
      sampleIntervalMs: parts.sampleIntervalMs,
      now: this.now,
      onStatusChange: (status) => this.onStatus(status),
    });
    this.session = session;
    this.publish();
    return session;
  }

  private now = (): number => this.parts?.now?.() ?? Date.now();

  private watchOnce(): void {
    if (this.unwatch || !this.parts?.watchPermission) return;
    this.unwatch = this.parts.watchPermission((permission) => {
      this.permission = permission;
      this.publish();
    });
  }

  /**
   * The session's status, always kept (it is the loop's own measurement,
   * never a state commit). The one TRANSITION it can cause — the loop
   * stopping on its own from ACTIVE (a 409: the durable flag went off
   * elsewhere) — belongs to the generation that last drove the session, and
   * is applied only while that generation is current; otherwise it is
   * `ignored:gen<N>` on the current trace, and the state stays where its
   * owner put it. The self-stop is appended to the current trace: that is
   * the story of how the eye came to be DISABLED.
   */
  private onStatus(status: PerceptionStatus): void {
    this.status = status;
    if (this.state === "ACTIVE" && !status.running) {
      if (this.sessionGen !== this.gen) {
        this.ignored(this.sessionGen);
        return;
      }
      this.stage("loop:stopped");
      this.traceTrack();
      this.setState("DISABLED");
      return;
    }
    this.publish();
  }

  private publish(): void {
    this.snapshot = this.session
      ? {
          ready: true,
          state: this.state,
          status: this.status,
          permission: this.permission,
          busy: this.state === "ENABLING" || this.state === "DISABLING",
          error: this.error,
          errorClass: this.errorClass,
          lastActionTrace: [...this.trace],
          generation: this.gen,
          instances: eyeInstances.snapshot(),
        }
      : SERVER_SNAPSHOT;
    for (const listener of this.listeners) listener();
  }

  /**
   * Move the machine. A TERMINAL destination (ACTIVE / DISABLED / ERROR) is a
   * stage of the current action (`state:<from>-><to>`); the transient ones
   * (ENABLING / DISABLING) are not, so the trace reads as the spec's example.
   * ACTIVE is dated on entry; every other destination forgets both dates —
   * the commit time and the durable acknowledgement belong to the ACTIVE
   * they describe, and a store that is not ACTIVE has nothing a bus event
   * could be compared against.
   */
  private setState(state: EyeState, patch: { error?: string | null; errorClass?: EyeErrorClass | null } = {}): void {
    const from = this.state;
    this.state = state;
    if ("error" in patch) this.error = patch.error ?? null;
    if ("errorClass" in patch) this.errorClass = patch.errorClass ?? null;
    if (state === "ACTIVE") {
      this.activeSince = this.now();
    } else {
      this.activeSince = null;
      this.durableEnabledAt = null;
    }
    if (state !== "ENABLING" && state !== "DISABLING") this.stage(`state:${from}->${state}`);
    this.publish();
  }

  // ----------------------------------------------------------- generations

  /** The next generation; the caller now owns the state. */
  private newGeneration(): number {
    this.gen += 1;
    return this.gen;
  }

  private isCurrent(gen: number): boolean {
    return gen === this.gen;
  }

  /** Something of generation `gen` reached the store after generation `gen` had ended: recorded, never applied. */
  private ignored(gen: number): void {
    this.stageOnce(`ignored:gen${gen}`);
    this.publish();
  }

  /**
   * A reporter bound to one generation: a stage from the generation that
   * owns the trace is appended; one from an older generation (the camera of
   * a superseded enable answering late) is `ignored:gen<N>` instead.
   */
  private stageFor(gen: number): StageReport {
    return (stage) => {
      if (this.isCurrent(gen)) this.stage(stage);
      else this.ignored(gen);
    };
  }

  // ---------------------------------------------------------------- trace

  /** The stages of the action in flight (or the last one); see `LocalEyeResult.action_trace`. */
  private trace: string[] = [];

  /** A new action: the trace starts over with its generation, its request, and the action's identity when it has one. */
  private beginTrace(request: string, gen: number, identity?: EyeActionIdentity): void {
    this.trace = [`gen:${gen}`, `request:${request}`];
    if (identity?.action_id) this.stage(`action:${identity.action_id}`);
  }

  /** Append a stage, bounded: past `MAX_ACTION_TRACE` the newest replaces the last slot. */
  private stage = (stage: string): void => {
    if (this.trace.length >= MAX_ACTION_TRACE) this.trace[MAX_ACTION_TRACE - 1] = stage;
    else this.trace.push(stage);
  };

  /** Append a stage unless this trace already carries it (a repeating poll tells the story once). */
  private stageOnce(stage: string): void {
    if (!this.trace.includes(stage)) this.stage(stage);
  }

  // ------------------------------------------------------------ observers

  /** `useSyncExternalStore` contract: stable until something changes. */
  getSnapshot = (): EyeStoreSnapshot => {
    if (!this.session) this.ensureSession();
    return this.snapshot;
  };

  /** The server has no camera and never builds a session. */
  getServerSnapshot = (): EyeStoreSnapshot => SERVER_SNAPSHOT;

  subscribe = (listener: () => void): (() => void) => {
    this.ensureSession();
    this.watchOnce();
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** The session, if one has been built; never builds one. */
  peekSession(): PerceptionSession | null {
    return this.session;
  }

  /** What `shouldStopLocalPerception` is decided from, as of now. */
  localFacts(): LocalEyeFacts {
    return { state: this.state, activeSince: this.activeSince, durableEnabledAt: this.durableEnabledAt };
  }

  // ------------------------------------------------------------- results

  private result(changed: boolean): LocalEyeResult {
    const state: LocalEyeState = this.state === "ACTIVE" ? "ACTIVE" : this.state === "ERROR" ? "ERROR" : "DISABLED";
    return {
      state,
      running: this.status.running,
      camera_label: this.status.cameraLabel,
      error_class: this.state === "ERROR" ? this.errorClass : null,
      observed_at: new Date(this.now()).toISOString(),
      changed,
      media_track_ready_state: this.session?.trackReadyState() ?? null,
      action_trace: [...this.trace],
    };
  }

  /** The store was already where it was asked to be: no transition, so no new generation — the trace names the one that owns the state. */
  private already(request: "enable" | "disable", identity?: EyeActionIdentity): LocalEyeResult {
    this.beginTrace(request, this.gen, identity);
    this.stage(`already:${this.state}`);
    this.publish();
    return this.result(false);
  }

  // ------------------------------------------------------------- commands

  /**
   * Open the camera on this device, start the sampling loop, then tell the
   * Cloud Core. Idempotent: ACTIVE answers `changed: false` at once; a call
   * while one is in flight joins it. `identity` (the voice tool's call id
   * and realtime session) rides on the durable call and in the trace as
   * `action:<call_id>`. Never rejects.
   */
  enable(reason: string, identity: EyeActionIdentity = {}): Promise<LocalEyeResult> {
    this.ensureSession();
    if (!this.last && this.state === "ACTIVE") return Promise.resolve(this.already("enable", identity));
    return this.enqueue("enable", () => this.runEnable(reason, identity));
  }

  /**
   * Tell the Cloud Core first, then stop the loop and release the camera.
   * Idempotent: DISABLED answers `changed: false` at once; a call while one
   * is in flight joins it. An enable still opening its camera is superseded
   * on the spot (the camera is released as soon as it comes up, if it does),
   * so "Gözünü kapat" never waits behind a permission prompt. Never rejects.
   */
  disable(reason: string, identity: EyeActionIdentity = {}): Promise<LocalEyeResult> {
    this.ensureSession();
    if (!this.last && this.state === "DISABLED") return Promise.resolve(this.already("disable", identity));
    if (this.last?.kind === "enable") this.interruptEnable();
    return this.enqueue("disable", () => this.runDisable(reason, identity));
  }

  /**
   * The bus said `eye.disabled`: stop the LOCAL loop — no durable call, the
   * Cloud Core already knows — but ONLY if that event is genuinely newer than
   * this device's own state. Applied while the store is `ACTIVE` and the
   * event (dated by `ageMs`, neither undated nor `expired`) is newer than the
   * moment ACTIVE was committed and newer than this generation's durable
   * enable (`shouldStopLocalPerception`). During ENABLING or DISABLING it
   * never does anything: a transition in flight owns the state, and the bus
   * event the page holds predates it — the stage `bus:stale_disable_ignored`
   * on the transition's trace says the request came and was declined. Returns
   * whether it stopped anything. `PerceptionSession` still self-corrects the
   * slower way on its own next tick (a 409 stops it too), so a stop declined
   * here for being stale costs at most one sampling interval if it was in
   * fact real.
   */
  stopLocalIfStale(bus: BusEyeEvent): boolean {
    const session = this.ensureSession();
    if (bus.status !== "disabled") return false;
    if (!shouldStopLocalPerception(bus, this.localFacts(), this.now())) {
      if (this.state === "ENABLING" || this.state === "ACTIVE") {
        this.stageOnce(BUS_STALE_STAGE);
        this.publish();
      }
      return false;
    }
    const gen = this.newGeneration();
    this.beginTrace("stop_local", gen);
    this.sessionGen = gen;
    session.stop(); // from ACTIVE, `onStatus` records the stop and the transition
    return true;
  }

  /**
   * One operation at a time, in order; a same-kind call joins the one in
   * flight. `enable()` / `disable()` never reject: an unexpected throw
   * anywhere in a transition is the observation `state_transition_failed`
   * (with the camera released), not an exception for the voice rig to swallow.
   */
  private enqueue(kind: Operation["kind"], run: () => Promise<LocalEyeResult>): Promise<LocalEyeResult> {
    if (this.last?.kind === kind) return this.last.promise; // join the in-flight/queued one
    const guarded = () => run().catch((err: unknown) => this.transitionFailed(kind, err));
    const promise = this.tail.then(guarded, guarded);
    this.tail = promise;
    const operation: Operation = { kind, promise };
    this.last = operation;
    const clear = () => {
      if (this.last === operation) this.last = null;
    };
    promise.then(clear, clear);
    return promise;
  }

  /** The requested state was not reached for a reason no other class names. */
  private transitionFailed(kind: Operation["kind"], err: unknown): LocalEyeResult {
    this.stage(`unexpected:${errorName(err) || "Error"}`);
    this.defusePendingEnable();
    try {
      this.sessionGen = this.gen;
      this.session?.stop();
    } catch {
      // the camera release itself failed; ERROR below is still the honest state
    }
    if (kind === "disable") this.durableEnable = null;
    return this.fail("state_transition_failed");
  }

  /** Forget the running enable's timer and promise; its camera, if it opens late, is released by the generation checks. */
  private defusePendingEnable(): PendingEnable | null {
    const pending = this.pendingEnable;
    if (!pending) return null;
    this.pendingEnable = null;
    if (pending.timer !== null) clearTimeout(pending.timer);
    pending.timer = null;
    return pending;
  }

  /**
   * Make the running enable settle now, synchronously: its timer is cleared
   * here (not on a later microtask), the camera it is waiting for is stopped
   * at the session, and its race resolves `superseded`. Nothing of that
   * enable's generation can move the state after this returns.
   */
  private interruptEnable(): void {
    const pending = this.defusePendingEnable();
    if (!pending) return;
    this.sessionGen = pending.gen;
    this.session?.stop();
    pending.supersede();
  }

  /** `stream:<readyState>` and `track:<id>` — what the device's track says after a stop; nothing when it holds no track. */
  private traceTrack(): void {
    const readyState = this.session?.trackReadyState() ?? null;
    if (readyState) this.stage(`stream:${readyState}`);
    const track = this.session?.trackShortId() ?? null;
    if (track) this.stage(`track:${track}`);
  }

  private async runEnable(reason: string, identity: EyeActionIdentity): Promise<LocalEyeResult> {
    const session = this.ensureSession();
    const parts = this.parts as EyeParts;
    if (this.state === "ACTIVE") return this.already("enable", identity);
    const gen = this.newGeneration();
    this.beginTrace("enable", gen, identity);
    this.setState("ENABLING", { error: null, errorClass: null });

    if (parts.cameraAvailable && !parts.cameraAvailable()) {
      this.stage("capability:missing");
      return this.fail("capability_missing");
    }

    // Local first: the camera, bounded. A slow prompt, a hung device, or a
    // disable arriving meanwhile all settle this race without waiting for
    // getUserMedia; a late stream is released by the session's and the
    // source's own generation checks, and its stages land as `ignored:gen<N>`.
    // Every `FrameSource.start()` is one `getUserMedia` (see `eyeInstances`).
    this.stage("getUserMedia:called");
    this.sessionGen = gen;
    const opening = session.start(this.stageFor(gen));
    opening.catch(() => {}); // observed through the race below; never unhandled
    const pending: PendingEnable = { gen, timer: null, supersede: () => {} };
    const superseded = new Promise<typeof SUPERSEDED>((resolve) => {
      pending.supersede = () => resolve(SUPERSEDED);
    });
    const timeoutMs = this.options.timeoutMs ?? LOCAL_EYE_TIMEOUT_MS;
    const timeout = new Promise<typeof TIMED_OUT>((resolve) => {
      pending.timer = setTimeout(() => resolve(TIMED_OUT), timeoutMs);
    });
    this.pendingEnable = pending;
    let outcome: "opened" | { error: unknown } | typeof TIMED_OUT | typeof SUPERSEDED;
    try {
      outcome = await Promise.race([
        opening.then(
          () => "opened" as const,
          (error: unknown) => ({ error }),
        ),
        timeout,
        superseded,
      ]);
    } finally {
      if (this.pendingEnable === pending) this.defusePendingEnable();
    }

    if (outcome === SUPERSEDED || !this.isCurrent(gen)) {
      this.stage("superseded:disable");
      return this.result(false); // a disable owns the state now
    }
    if (outcome === TIMED_OUT) {
      this.sessionGen = gen;
      session.stop();
      this.stage(`timeout:${timeoutMs}ms`);
      return this.fail("timeout");
    }
    if (outcome !== "opened") {
      this.sessionGen = gen;
      session.stop();
      const errorClass = classifyCameraError(outcome.error);
      // The two post-getUserMedia failures reported their own stage from the
      // source/session; a rejection is named by the browser's error name.
      if (errorClass === "perception_start_failed") this.stage("loop:start failed");
      else if (errorClass !== "stream_created_but_track_ended") this.stage(`getUserMedia:${errorName(outcome.error) || "Error"}`);
      if (errorClass === "permission_denied") this.permission = "denied";
      return this.fail(errorClass);
    }
    if (!session.getStatus().running) {
      // start() resolved without a loop: stop() ran while the camera was
      // opening (the session released what it opened). Nothing durable was
      // ever sent, so DISABLED is the whole truth.
      this.stage("superseded:stop");
      this.setState("DISABLED");
      return this.result(false);
    }

    eyeInstances.bump("loopsStarted");
    this.permission = "granted";

    // Durable second, only now that the loop is running. A failure here is
    // NOT a local failure: the camera is open on this device, which is the
    // honest observation, so the state stays ACTIVE with the error shown.
    // The loop self-corrects on its own next tick (a 409 stops it) if the
    // Cloud Core in fact never learned. The acknowledgement is dated: a bus
    // `eye.disabled` older than it can never be a reason to stop this camera.
    this.stage("durable:enable");
    const durable = (this.options.durable?.enable ?? enableEye)(reason, identity).then(
      () => {
        if (this.isCurrent(gen)) this.durableEnabledAt = this.now();
        else this.ignored(gen);
      },
      (err: unknown) => {
        if (!this.isCurrent(gen)) {
          this.ignored(gen);
          return;
        }
        this.stage("durable:failed");
        this.error = explainEyeError(err);
      },
    );
    this.durableEnable = durable;
    await durable;
    if (!this.isCurrent(gen)) {
      this.stage("superseded:disable");
      return this.result(false); // a newer transition owns the state
    }
    if (!this.status.running) {
      // The loop stopped by itself while the POST was on the wire (a sample
      // raced ahead and was refused): the camera is off, and that is the
      // honest terminal state — never ACTIVE over a stopped loop.
      this.stage("loop:stopped");
      this.traceTrack();
      this.setState("DISABLED");
      return this.result(false);
    }
    this.setState("ACTIVE", { errorClass: null });
    return this.result(true);
  }

  private fail(errorClass: EyeErrorClass): LocalEyeResult {
    this.setState("ERROR", { error: LOCAL_EYE_ERROR_TEXT[errorClass], errorClass });
    return this.result(true);
  }

  private async runDisable(reason: string, identity: EyeActionIdentity): Promise<LocalEyeResult> {
    const session = this.ensureSession();
    if (this.state === "DISABLED") return this.already("disable", identity);
    const gen = this.newGeneration();
    this.beginTrace("disable", gen, identity);
    this.setState("DISABLING", { error: null });
    // Never race an enable's POST still on the wire: the disable must land
    // after it — and settling it here is part of "nothing of the earlier
    // generation can still mutate state" by the time this returns.
    if (this.durableEnable) await this.durableEnable;
    this.durableEnable = null;
    try {
      // Durable first, local second (see `client.ts`'s `disableEye` doc).
      this.stage("durable:disable");
      await (this.options.durable?.disable ?? disableEye)(reason, identity);
    } catch (err) {
      this.stage("durable:failed");
      this.error = explainEyeError(err);
    } finally {
      this.sessionGen = gen;
      session.stop();
      this.stage("loop:stopped");
      this.traceTrack();
    }
    this.setState("DISABLED", { errorClass: null });
    return this.result(true);
  }

  /** Tests only: tear the session down so the next store starts from nothing. */
  dispose(): void {
    this.newGeneration();
    this.interruptEnable();
    this.sessionGen = this.gen;
    this.session?.stop();
    this.unwatch?.();
    this.unwatch = null;
    this.session = null;
    this.parts = null;
    this.listeners.clear();
    this.state = "DISABLED";
    this.status = INITIAL_STATUS;
    this.activeSince = null;
    this.durableEnabledAt = null;
    this.trace = [];
    this.snapshot = SERVER_SNAPSHOT;
  }
}

// ------------------------------------------------------------- singleton

let singleton: EyeStore | null = null;

/**
 * The tab's one store. Constructing it is free of side effects (the session
 * is built on first subscribe/snapshot, never at import), so the server can
 * import this module and still never touch a browser API; tests never call
 * this and build their own `EyeStore` from fakes.
 */
export function getEyeStore(): EyeStore {
  if (singleton) return singleton;
  singleton = new EyeStore({ build: browserEyeParts() });
  return singleton;
}

/** Tests only: replace the singleton (e.g. with one built from fakes). */
export function installEyeStore(store: EyeStore | null): void {
  singleton?.dispose();
  singleton = store;
}
