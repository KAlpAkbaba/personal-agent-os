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
 * ACTIVE    -loop stopped by the Cloud Core (409) or the bus->                     DISABLED
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
 * Every camera failure is mapped onto the closed `EyeErrorClass` set the
 * server's receipt speaks (§5.2); nothing else is ever relayed. `enable()`
 * and `disable()` never reject: a failure is an observation.
 *
 * Nothing here is React; `useActivePerception.ts` is the binding.
 */

import { disableEye, enableEye, explainEyeError } from "./client";
import { LOCAL_EYE_ERROR_TEXT } from "./labels";
import {
  BrowserFrameSource,
  type FrameSource,
  PerceptionSession,
  type PerceptionOptions,
  type PerceptionStatus,
} from "./perception";
import type { CameraPermission, EyeErrorClass } from "./types";

export type EyeState = "DISABLED" | "ENABLING" | "ACTIVE" | "DISABLING" | "ERROR";

/** What `observed_after.local.state` may say (§5.1): only terminal states are observations. */
export type LocalEyeState = "ACTIVE" | "DISABLED" | "ERROR";

export type LocalEyeResult = {
  state: LocalEyeState;
  running: boolean;
  camera_label: string | null;
  error_class: EyeErrorClass | null;
  /** ISO-8601, the moment this result was read. */
  observed_at: string;
  /** False when the store was already in the requested state and did nothing. */
  changed: boolean;
};

/** §5.1: the local capability is bounded; past this an enable is `timeout`. */
export const LOCAL_EYE_TIMEOUT_MS = 8_000;

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

/**
 * `getUserMedia`'s failure names, onto the receipt's closed set (§5.1). Any
 * name we do not know is a device-side failure to open — the camera did not
 * come up — rather than a permission or capability question.
 */
export function classifyCameraError(err: unknown): EyeErrorClass {
  const name = typeof err === "object" && err !== null && "name" in err ? String((err as { name: unknown }).name) : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "permission_denied";
  if (name === "TypeError") return "capability_missing"; // `navigator.mediaDevices` missing → a TypeError at the call
  return "device_unavailable"; // NotFoundError, NotReadableError, OverconstrainedError, AbortError, …
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
  instances: emptyCounts(),
});

export type EyeDurable = {
  enable: (reason: string) => Promise<void>;
  disable: (reason: string) => Promise<void>;
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

const TIMED_OUT = Symbol("timed_out");
const SUPERSEDED = Symbol("superseded");

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

  /** Operations run one at a time, in order; `last` is what a same-kind call joins. */
  private tail: Promise<unknown> = Promise.resolve();
  private last: Operation | null = null;
  /** Resolves the running enable's race early (a disable or a bus stop arrived). */
  private supersedeEnable: (() => void) | null = null;
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
      start: (deviceId) => {
        eyeInstances.bump("cameraOpens");
        return source.start(deviceId);
      },
      sample: (reduce) => source.sample(reduce),
      stop: () => source.stop(),
      label: () => source.label(),
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

  private onStatus(status: PerceptionStatus): void {
    this.status = status;
    // The loop stopped on its own: the Cloud Core answered 409 (the durable
    // flag went off elsewhere — another device, or the voice safety net) or
    // the bus stop ran. Either way this device is no longer perceiving, and
    // the state must say so rather than keep claiming ACTIVE.
    if (this.state === "ACTIVE" && !status.running) this.state = "DISABLED";
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
          instances: eyeInstances.snapshot(),
        }
      : SERVER_SNAPSHOT;
    for (const listener of this.listeners) listener();
  }

  private setState(state: EyeState, patch: { error?: string | null; errorClass?: EyeErrorClass | null } = {}): void {
    this.state = state;
    if ("error" in patch) this.error = patch.error ?? null;
    if ("errorClass" in patch) this.errorClass = patch.errorClass ?? null;
    this.publish();
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
    };
  }

  // ------------------------------------------------------------- commands

  /**
   * Open the camera on this device, start the sampling loop, then tell the
   * Cloud Core. Idempotent: ACTIVE answers `changed: false` at once; a call
   * while one is in flight joins it. Never rejects.
   */
  enable(reason: string): Promise<LocalEyeResult> {
    this.ensureSession();
    if (!this.last && this.state === "ACTIVE") return Promise.resolve(this.result(false));
    return this.enqueue("enable", () => this.runEnable(reason));
  }

  /**
   * Tell the Cloud Core first, then stop the loop and release the camera.
   * Idempotent: DISABLED answers `changed: false` at once; a call while one
   * is in flight joins it. An enable still opening its camera is superseded
   * on the spot (the camera is released as soon as it comes up, if it does),
   * so "Gözünü kapat" never waits behind a permission prompt. Never rejects.
   */
  disable(reason: string): Promise<LocalEyeResult> {
    this.ensureSession();
    if (!this.last && this.state === "DISABLED") return Promise.resolve(this.result(false));
    if (this.last?.kind === "enable") this.interruptEnable();
    return this.enqueue("disable", () => this.runDisable(reason));
  }

  /**
   * Stops the LOCAL loop only — no durable call. For reacting to a disable
   * that already happened elsewhere (another device, or the voice safety
   * net): the Cloud Core is already told, so re-telling it here would just
   * be a second, redundant ledger row. `PerceptionSession` also self-corrects
   * the slower way on its own next tick (a 409 stops it too), so this only
   * makes that reaction faster — a latency improvement, not the safety
   * mechanism itself.
   */
  stopLocalOnly(): void {
    const session = this.ensureSession();
    const wasEnabling = this.state === "ENABLING";
    this.interruptEnable();
    session.stop();
    if (this.state === "ACTIVE" || wasEnabling) this.setState("DISABLED");
  }

  private enqueue(kind: Operation["kind"], run: () => Promise<LocalEyeResult>): Promise<LocalEyeResult> {
    if (this.last?.kind === kind) return this.last.promise; // join the in-flight/queued one
    const promise = this.tail.then(run, run);
    this.tail = promise;
    const operation: Operation = { kind, promise };
    this.last = operation;
    const clear = () => {
      if (this.last === operation) this.last = null;
    };
    promise.then(clear, clear);
    return promise;
  }

  /** Make the running enable settle now (its camera, if it opens late, is released by the session's generation check). */
  private interruptEnable(): void {
    if (!this.supersedeEnable) return;
    this.session?.stop();
    this.supersedeEnable();
    this.supersedeEnable = null;
  }

  private async runEnable(reason: string): Promise<LocalEyeResult> {
    const session = this.ensureSession();
    const parts = this.parts as EyeParts;
    if (this.state === "ACTIVE") return this.result(false);
    this.setState("ENABLING", { error: null, errorClass: null });

    if (parts.cameraAvailable && !parts.cameraAvailable()) {
      return this.fail("capability_missing");
    }

    // Local first: the camera, bounded. A slow prompt, a hung device, or a
    // disable arriving meanwhile all settle this race without waiting for
    // getUserMedia; the session's own generation check releases a late stream.
    const opening = session.start();
    opening.catch(() => {}); // observed through the race below; never unhandled
    let timer: ReturnType<typeof setTimeout> | null = null;
    const superseded = new Promise<typeof SUPERSEDED>((resolve) => {
      this.supersedeEnable = () => resolve(SUPERSEDED);
    });
    const timeout = new Promise<typeof TIMED_OUT>((resolve) => {
      timer = setTimeout(() => resolve(TIMED_OUT), this.options.timeoutMs ?? LOCAL_EYE_TIMEOUT_MS);
    });
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
      if (timer !== null) clearTimeout(timer);
      this.supersedeEnable = null;
    }

    if (outcome === SUPERSEDED) return this.result(false); // a disable/bus stop owns the state now
    if (outcome === TIMED_OUT) {
      session.stop();
      return this.fail("timeout");
    }
    if (outcome !== "opened") {
      session.stop();
      const errorClass = classifyCameraError(outcome.error);
      if (errorClass === "permission_denied") this.permission = "denied";
      return this.fail(errorClass);
    }
    if (!session.getStatus().running) {
      // start() resolved without a loop: stop() ran while the camera was
      // opening (the session released what it opened). Nothing durable was
      // ever sent, so DISABLED is the whole truth.
      this.setState("DISABLED");
      return this.result(false);
    }

    eyeInstances.bump("loopsStarted");
    this.permission = "granted";

    // Durable second, only now that the loop is running. A failure here is
    // NOT a local failure: the camera is open on this device, which is the
    // honest observation, so the state stays ACTIVE with the error shown.
    // The loop self-corrects on its own next tick (a 409 stops it) if the
    // Cloud Core in fact never learned.
    const durable = (this.options.durable?.enable ?? enableEye)(reason).then(
      () => undefined,
      (err: unknown) => {
        this.error = explainEyeError(err);
      },
    );
    this.durableEnable = durable;
    await durable;
    if (this.state !== "ENABLING") return this.result(false); // a disable/bus stop ran meanwhile
    this.setState("ACTIVE", { errorClass: null });
    return this.result(true);
  }

  private fail(errorClass: EyeErrorClass): LocalEyeResult {
    this.setState("ERROR", { error: LOCAL_EYE_ERROR_TEXT[errorClass], errorClass });
    return this.result(true);
  }

  private async runDisable(reason: string): Promise<LocalEyeResult> {
    const session = this.ensureSession();
    if (this.state === "DISABLED") return this.result(false);
    this.setState("DISABLING", { error: null });
    // Never race an enable's POST still on the wire: the disable must land after it.
    if (this.durableEnable) await this.durableEnable;
    this.durableEnable = null;
    try {
      // Durable first, local second (see `client.ts`'s `disableEye` doc).
      await (this.options.durable?.disable ?? disableEye)(reason);
    } catch (err) {
      this.error = explainEyeError(err);
    } finally {
      session.stop();
    }
    this.setState("DISABLED", { errorClass: null });
    return this.result(true);
  }

  /** Tests only: tear the session down so the next store starts from nothing. */
  dispose(): void {
    this.interruptEnable();
    this.session?.stop();
    this.unwatch?.();
    this.unwatch = null;
    this.session = null;
    this.parts = null;
    this.listeners.clear();
    this.state = "DISABLED";
    this.status = INITIAL_STATUS;
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
