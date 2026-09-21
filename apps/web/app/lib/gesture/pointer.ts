"use client";

/**
 * ADR-0199 stage 2: the pointer stream's BROWSER half. Turns Stage 2's `mouse_*`/`drag_*`/
 * `*_click` gesture events — never posted to the server as a `gesture` client event, see
 * `types.ts`'s module docstring — into the pointer WebSocket frames
 * `services/api/app/voice/realtime_sessions/pointer_ws.py` speaks, through the
 * `operator.pointer_session` tool call that opens/closes the session
 * (`LocalVoiceMode.beginPointerSession`/`endPointerSession`).
 *
 * Wire contract (ADR-0199, binding — restated here as code, not reinvented):
 * - `operator.pointer_session {"action":"begin"}` → `{status:"open", stream_token,
 *   expires_at, speech}`; the browser presents `stream_token` on the socket, never the
 *   tool response's other fields.
 * - `GET /v1/voice/realtime/sessions/{id}/pointer` — the OWNER's bearer as a `token`
 *   query parameter (a browser `WebSocket` cannot set an `Authorization` header on the
 *   handshake); first frame `{"t":"hello","stream_token":...}`.
 * - `{"t":"move","dx":int,"dy":int,"seq":n}` (|dx|,|dy| ≤ 200, coalesced to ≤ 30/s),
 *   `{"t":"button","button":"left"|"right","action":"down"|"up"|"click"}`, `{"t":"end"}`.
 * - Close codes: 4401 (no owner session), 1008 (policy refusal — bad/expired token, or
 *   the session is not live), 1000 (normal).
 *
 * Every browser primitive (`WebSocket`, `setTimeout`, `window.screen`) is injected
 * (`PointerClientDeps`) — `tests/gesture/pointer.test.ts` drives this entirely with fakes;
 * a real socket is never constructed in a test (this file's own module docstring rule,
 * same as `tracker.ts`/`localMode.ts`).
 */

import type { GestureEvent, PointerGestureName } from "./types";

// ------------------------------------------------------------------- wire types

type ButtonName = "left" | "right";
type ButtonAction = "down" | "up" | "click";

/** The subset of a real `WebSocket` this client drives. Deliberately NOT the DOM
 * `WebSocket` type itself: a real one's `onclose` carries a full `CloseEvent`, but this
 * client only ever reads `.code`, so the fake in tests never has to build one. */
export interface PointerSocketLike {
  send(data: string): void;
  close(code?: number): void;
  onopen: (() => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: ((event?: unknown) => void) | null;
}

/** `operator.pointer_session`'s two actions, through `LocalVoiceMode` — never called
 * directly against `VoiceSessionApi`: the tool call needs the SAME turn/receipt plumbing
 * `runGesture` already goes through, and takes NO utterance (the tool's own contract). */
export type PointerSessionPort = {
  beginPointerSession(): Promise<{ status: string; stream_token?: string } | null>;
  endPointerSession(): Promise<void>;
};

export type GainStorage = { get(): number; set(value: number): void };

const GAIN_STORAGE_KEY = "pagentos.pointer.gain";
/** Owner's brief: "gain ~2.5 screen widths per frame width". */
export const DEFAULT_GAIN = 2.5;
export const MIN_GAIN = 0.5;
export const MAX_GAIN = 10;

/** The real `localStorage`-backed gain, per-browser (task brief); tolerant of a browser
 * that refuses storage (private mode) — same posture as `controller.ts`'s toggle storage. */
export function browserPointerGainStorage(): GainStorage {
  return {
    get(): number {
      try {
        if (typeof window === "undefined") return DEFAULT_GAIN;
        const raw = window.localStorage.getItem(GAIN_STORAGE_KEY);
        const value = raw === null ? NaN : Number(raw);
        return Number.isFinite(value) && value >= MIN_GAIN && value <= MAX_GAIN ? value : DEFAULT_GAIN;
      } catch {
        return DEFAULT_GAIN;
      }
    },
    set(value: number): void {
      try {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(GAIN_STORAGE_KEY, String(value));
      } catch {
        /* per-viewer convenience only; nothing to recover */
      }
    },
  };
}

/** `apiBase` (`http(s)://host[:port]`) → the pointer route's own `ws(s)://` URL, the
 * OWNER's bearer as the `token` query parameter (module docstring — a browser `WebSocket`
 * cannot set a header on the handshake). */
export function pointerWsUrl(apiBase: string, sessionId: string, token: string): string {
  const wsBase = apiBase.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
  return `${wsBase}/v1/voice/realtime/sessions/${encodeURIComponent(sessionId)}/pointer?token=${encodeURIComponent(token)}`;
}

/** Close code → the Turkish HUD line (closed set, task brief). Never a raw status code. */
export const POINTER_CLOSE_REASON_TR: Readonly<Record<number, string>> = {
  4401: "Oturum doğrulanamadı; tekrar giriş yapın.",
  1008: "Akış reddedildi.",
  1000: "Fare akışı kapatıldı.",
};
export const POINTER_CLOSE_REASON_DEFAULT_TR = "Bağlantı koptu.";
export const POINTER_OPEN_FAILED_TR = "Fare akışı açılamadı.";

// ----------------------------------------------------------------- deps/snapshot

export type PointerClientDeps = {
  sessionPort: PointerSessionPort;
  /** Builds the socket URL from whatever the tab currently has (session id, bearer) — or
   * `null` when either is missing, which fails the stream open the same way a rejected
   * `beginPointerSession()` does. Never called before `beginPointerSession()` resolves:
   * the URL never needs to carry `stream_token` (module docstring — that travels in the
   * `hello` frame instead). */
  wsUrl: () => string | null;
  createSocket: (url: string) => PointerSocketLike;
  /** `window.screen.width`, injected — the pixel scale `mouse_move`/`drag_move`'s frame
   * units are multiplied into (task brief: `dx = round(frameDx * screen.width * gain)`,
   * `dy` likewise — BOTH axes scaled by width, verbatim from the brief). */
  screenWidth: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  gainStorage?: GainStorage;
};

export type PointerStreamState = "idle" | "opening" | "open";
export type PointerStreamMode = "mouse" | "drag" | null;

export type PointerStreamSnapshot = {
  mode: PointerStreamMode;
  state: PointerStreamState;
  gain: number;
  lastCloseReason: string | null;
};

const OFF_SNAPSHOT: PointerStreamSnapshot = { mode: null, state: "idle", gain: DEFAULT_GAIN, lastCloseReason: null };

/** ≤ 30 sends/s (ADR-0199): a flush at most every ⌈1000/30⌉ ms. Exported so
 * `pointer.test.ts` can drive the fake timer without duplicating this figure. */
export const POINTER_FLUSH_INTERVAL_MS = 34;
/** "keep the socket open for 10s of idleness then end" (task brief). Exported for the
 * same reason as `POINTER_FLUSH_INTERVAL_MS`. */
export const POINTER_IDLE_CLOSE_MS = 10_000;
/** Mirrors the server's own `pointer_session.MAX_MOVE_DELTA` (ADR-0199's wire frame
 * shape) — clamped here too so a large accumulated coalesced delta is never silently
 * DROPPED by the server for being out of range; it is clamped, not dropped. */
export const POINTER_MAX_MOVE_DELTA = 200;

function clamp(value: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, value));
}

/**
 * One pointer-streaming episode per `GestureController` (one tab). `handleEvent` is the
 * only thing a caller needs to call — everything else (opening/closing the session,
 * coalescing moves, queuing button frames until the socket is ready, the 10s idle
 * close) is internal. See the module docstring for the wire contract this speaks.
 */
export class PointerStreamClient {
  private readonly gainStorage: GainStorage;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;
  private snapshot: PointerStreamSnapshot;
  private readonly listeners = new Set<() => void>();

  private socket: PointerSocketLike | null = null;
  /** `true` once `hello` has been sent on the CURRENT socket. */
  private ready = false;
  private seq = 0;
  private pendingDx = 0;
  private pendingDy = 0;
  private flushTimer: unknown | null = null;
  private pendingButtons: Array<{ button: ButtonName; action: ButtonAction }> = [];
  /** Local bookkeeping only — "a held left button is ALWAYS released before the socket
   * ends" (task brief): `endSession()` reads this before closing. */
  private buttonDown = false;
  private idleTimer: unknown | null = null;
  private opening = false;
  /** Bumped on every `endSession()`/unexpected close, so a `beginPointerSession()` still
   * in flight from a PREVIOUS episode can never land into a newer one. */
  private generation = 0;

  constructor(private readonly deps: PointerClientDeps) {
    this.gainStorage = deps.gainStorage ?? browserPointerGainStorage();
    this.setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
    this.snapshot = { ...OFF_SNAPSHOT, gain: this.gainStorage.get() };
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): PointerStreamSnapshot => this.snapshot;
  getServerSnapshot = (): PointerStreamSnapshot => OFF_SNAPSHOT;

  private patch(partial: Partial<PointerStreamSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...partial };
    for (const listener of this.listeners) listener();
  }

  /** The owner's "Fare kazancı" ± control. Persists (per-browser) and applies to the
   * NEXT `mouse_move`/`drag_move` sent — never rewrites a frame already on the wire. */
  setGain(value: number): void {
    if (!Number.isFinite(value)) return;
    const clamped = clamp(value, MIN_GAIN, MAX_GAIN);
    this.gainStorage.set(clamped);
    this.patch({ gain: clamped });
  }

  /** The one entry point: `GestureController` routes every Stage 2 `GestureEvent` here
   * instead of `LocalVoiceMode.dispatchGesture` (types.ts's `isPointerGestureName`). */
  handleEvent(event: GestureEvent): void {
    switch (event.name as PointerGestureName) {
      case "mouse_start":
        this.onStart("mouse");
        break;
      case "drag_start":
        this.onStart("drag");
        this.queueButton("left", "down");
        break;
      case "mouse_move":
      case "drag_move":
        this.onMove(event.dx ?? 0, event.dy ?? 0);
        break;
      case "left_click":
        this.queueButton("left", "click");
        break;
      case "right_click":
        this.queueButton("right", "click");
        break;
      case "drag_end":
        this.queueButton("left", "up");
        this.onStop();
        break;
      case "mouse_end":
        this.onStop();
        break;
      default:
        break; // not a Stage 2 event; nothing to do
    }
  }

  // ---------------------------------------------------------------- session lifecycle

  private onStart(mode: PointerStreamMode): void {
    this.cancelIdleTimer();
    this.patch({ mode });
    if (this.socket || this.opening) return; // an episode is already live/opening: reuse it
    this.opening = true;
    const generation = ++this.generation;
    this.patch({ state: "opening" });
    this.deps.sessionPort.beginPointerSession().then(
      (result) => this.onBeginResolved(generation, result),
      () => this.onBeginResolved(generation, null),
    );
  }

  private onBeginResolved(generation: number, result: { status: string; stream_token?: string } | null): void {
    if (generation !== this.generation) return; // a newer episode (or an end) superseded this
    this.opening = false;
    if (!result || result.status !== "open" || !result.stream_token) {
      this.patch({ state: "idle", mode: null, lastCloseReason: POINTER_OPEN_FAILED_TR });
      return;
    }
    this.openSocket(generation, result.stream_token);
  }

  private openSocket(generation: number, streamToken: string): void {
    const url = this.deps.wsUrl();
    if (!url) {
      this.patch({ state: "idle", mode: null, lastCloseReason: POINTER_OPEN_FAILED_TR });
      return;
    }
    const socket = this.deps.createSocket(url);
    this.socket = socket;
    this.ready = false;
    socket.onopen = () => {
      if (this.socket !== socket || generation !== this.generation) return;
      socket.send(JSON.stringify({ t: "hello", stream_token: streamToken }));
      this.ready = true;
      this.patch({ state: "open" });
      this.drainQueue();
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.handleUnexpectedClose(event?.code ?? 1000);
    };
    socket.onerror = () => {
      /* a real WebSocket always follows an error with close; nothing extra to do here */
    };
  }

  /** Everything queued while the socket was still connecting/authenticating. */
  private drainQueue(): void {
    if (!this.ready || !this.socket) return;
    while (this.pendingButtons.length > 0) {
      const next = this.pendingButtons.shift();
      if (next) this.sendButtonFrame(next.button, next.action);
    }
    this.flushMove();
  }

  private onStop(): void {
    this.patch({ mode: null });
    this.cancelIdleTimer();
    this.idleTimer = this.setTimer(() => {
      this.idleTimer = null;
      this.endSession();
    }, POINTER_IDLE_CLOSE_MS);
  }

  private cancelIdleTimer(): void {
    if (this.idleTimer !== null) {
      this.clearTimer(this.idleTimer);
      this.idleTimer = null;
    }
  }

  /** The deliberate end path — idle timeout, or the controller/page tearing this client
   * down. Always releases a held button first (task brief), then `{"t":"end"}`, then
   * closes, then the tool's own `{"action":"end"}` (belt and braces with the socket's own
   * ending — `_pointer_session_end` is idempotent when nothing is open). */
  private endSession(): void {
    this.generation += 1; // invalidate any begin() still in flight
    if (this.buttonDown) this.queueButton("left", "up");
    const socket = this.socket;
    if (socket) {
      if (this.ready) {
        try {
          socket.send(JSON.stringify({ t: "end" }));
        } catch {
          /* closing anyway */
        }
      }
      try {
        socket.close(1000);
      } catch {
        /* already closing */
      }
    }
    this.resetConnectionState();
    this.patch({ state: "idle", mode: null });
    this.deps.sessionPort.endPointerSession().catch(() => {
      /* best effort: the socket's own ending already reached the same receipt path */
    });
  }

  /** The socket closed WITHOUT us asking — the server's own doing (unauthorized, policy,
   * a dead device) or the network. Nothing more can be sent, so a held button cannot be
   * released over the wire here (task brief's guarantee covers OUR OWN ending paths;
   * `buttonDown` is still cleared locally so a later episode starts clean). */
  private handleUnexpectedClose(code: number): void {
    this.cancelIdleTimer();
    this.generation += 1;
    this.resetConnectionState();
    const reason = POINTER_CLOSE_REASON_TR[code] ?? POINTER_CLOSE_REASON_DEFAULT_TR;
    this.patch({ state: "idle", mode: null, lastCloseReason: reason });
  }

  private resetConnectionState(): void {
    this.socket = null;
    this.ready = false;
    this.opening = false;
    this.buttonDown = false;
    this.pendingButtons = [];
    this.pendingDx = 0;
    this.pendingDy = 0;
    if (this.flushTimer !== null) {
      this.clearTimer(this.flushTimer);
      this.flushTimer = null;
    }
  }

  // ------------------------------------------------------------------------ moves

  private onMove(frameDx: number, frameDy: number): void {
    const width = this.deps.screenWidth();
    const gain = this.snapshot.gain;
    // Task brief, verbatim: "dx = round(frameDx × screen.width × gain), dy likewise" —
    // both axes scaled by WIDTH, not height (frame coordinates are normalized the same
    // way on both axes; see recognizer.ts's own "frame width/height = 1.0" convention).
    this.pendingDx += Math.round(frameDx * width * gain);
    this.pendingDy += Math.round(frameDy * width * gain);
    if (this.flushTimer === null) {
      this.flushTimer = this.setTimer(() => {
        this.flushTimer = null;
        this.flushMove();
      }, POINTER_FLUSH_INTERVAL_MS);
    }
  }

  private flushMove(): void {
    if (!this.ready || !this.socket) return; // stays queued in pendingDx/pendingDy
    // A button frame (queueButton) can flush early, ahead of the scheduled timer —
    // cancel it so it does not fire again later as a harmless but wasteful no-op.
    if (this.flushTimer !== null) {
      this.clearTimer(this.flushTimer);
      this.flushTimer = null;
    }
    if (this.pendingDx === 0 && this.pendingDy === 0) return;
    const dx = clamp(this.pendingDx, -POINTER_MAX_MOVE_DELTA, POINTER_MAX_MOVE_DELTA);
    const dy = clamp(this.pendingDy, -POINTER_MAX_MOVE_DELTA, POINTER_MAX_MOVE_DELTA);
    this.pendingDx = 0;
    this.pendingDy = 0;
    this.seq += 1;
    this.socket.send(JSON.stringify({ t: "move", dx, dy, seq: this.seq }));
  }

  // ---------------------------------------------------------------------- buttons

  private queueButton(button: ButtonName, action: ButtonAction): void {
    if (action === "down") this.buttonDown = true;
    if (action === "up") this.buttonDown = false;
    if (this.ready && this.socket) {
      // Flush any pending move FIRST so the button lands where the cursor already is.
      this.flushMove();
      this.sendButtonFrame(button, action);
    } else {
      this.pendingButtons.push({ button, action });
    }
  }

  private sendButtonFrame(button: ButtonName, action: ButtonAction): void {
    if (!this.socket) return;
    this.socket.send(JSON.stringify({ t: "button", button, action }));
  }

  /** Tests / page teardown only: ends any live episode without waiting for the idle timer. */
  dispose(): void {
    this.cancelIdleTimer();
    if (this.socket) this.endSession();
    if (this.flushTimer !== null) {
      this.clearTimer(this.flushTimer);
      this.flushTimer = null;
    }
    this.listeners.clear();
  }
}
