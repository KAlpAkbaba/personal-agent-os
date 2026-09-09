/**
 * Event reporting to `POST .../events` (spec §4 step 5, §8).
 *
 * - Every timestamp is client-monotonic: `t_ms` = ms since the session clock
 *   started, integer, never negative, never wall-clock.
 * - Events are batched (bounded at the server's 200 per request) and flushed
 *   on a short timer or explicitly; a flush failure keeps the batch for the
 *   next attempt, so a network loss does not lose the `network_lost` event
 *   itself — it is delivered on restore, with its original timestamp.
 * - Sideband frames the server replays in the response are handed to the
 *   subscriber; this is the only push path a web client has (ADR-0036 §5).
 * - Payloads are scrubbed of anything the server forbids (audio/credential
 *   shaped keys) and bounded to 4 KB; text to 4000 chars.
 */

import {
  CLIENT_EVENT_KINDS,
  isForbiddenKey,
  MAX_EVENTS_PER_REQUEST,
  MAX_EVENT_PAYLOAD_BYTES,
  MAX_EVENT_TEXT_CHARS,
  type ClientEvent,
  type ClientEventKind,
  type EventsResponse,
  type SidebandFrame,
} from "./contract";

export type ReportInput = {
  kind: ClientEventKind;
  /** Session-clock ms; defaults to the reporter's clock at call time. */
  t_ms?: number;
  turn?: number;
  payload?: Record<string, unknown>;
  text?: string;
};

/**
 * The reporter hands its OWN session id to the poster.
 *
 * It used to take only the events, and `VoiceController`'s closure read
 * `this.sessionId` at SEND time - so a reporter belonging to a dead session would post
 * its stale queue into whatever session happened to be current. Passing the id the
 * reporter was built for makes that structurally impossible rather than merely unlikely.
 */
export type EventsPoster = (sessionId: string, events: ClientEvent[]) => Promise<EventsResponse>;

/** Why a reporter stopped. All three are terminal; none of them ever sends again. */
export type ReporterEndReason = "gone" | "closed" | "superseded" | "exhausted";

/** Bounded retries for an ordinary transient failure, so nothing loops for ever. */
export const MAX_TRANSIENT_ATTEMPTS = 5;
/** Bounded backoff for a genuine 429, used only when the server sends no Retry-After. */
export const RATE_LIMIT_BACKOFF_MS = [1_000, 4_000, 15_000] as const;

export type Scheduler = {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
};

export const realScheduler: Scheduler = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

const KNOWN_KINDS: ReadonlySet<string> = new Set(CLIENT_EVENT_KINDS);

/** The server's rule (contract.ts `isForbiddenKey`), kept under its historical name. */
export function isForbiddenPayloadKey(key: string): boolean {
  return isForbiddenKey(key);
}

/** Drop forbidden keys recursively; bytes-like values never make it in. */
export function scrubPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(payload)) {
    if (isForbiddenPayloadKey(key)) continue;
    if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) continue;
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      out[key] = value
        .filter((v) => !(v instanceof ArrayBuffer || ArrayBuffer.isView(v)))
        .map((v) =>
          v && typeof v === "object" && !Array.isArray(v)
            ? scrubPayload(v as Record<string, unknown>)
            : v,
        );
    } else if (value && typeof value === "object") {
      out[key] = scrubPayload(value as Record<string, unknown>);
    } else {
      out[key] = value;
    }
  }
  return out;
}

/**
 * Metric payloads (ADR-0044 §7) carry NUMBERS ONLY under names the server's
 * rule accepts: booleans become 0/1, everything else — strings, objects,
 * non-finite numbers, forbidden keys — is dropped before the event is built.
 */
export function numbersOnly(payload: Record<string, unknown>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(payload)) {
    if (isForbiddenKey(key)) continue;
    if (typeof value === "boolean") out[key] = value ? 1 : 0;
    else if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
  }
  return out;
}

export function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Shape one event exactly as the server validates it. */
export function buildClientEvent(input: ReportInput, defaultT: number): ClientEvent {
  if (!KNOWN_KINDS.has(input.kind)) {
    throw new Error(`unknown client event kind ${JSON.stringify(input.kind)}`);
  }
  const t = Math.max(0, Math.round(input.t_ms ?? defaultT));
  const event: ClientEvent = {
    kind: input.kind,
    t_ms: Number.isFinite(t) ? t : 0,
    turn: Math.max(0, Math.round(input.turn ?? 0)),
  };
  if (input.payload) {
    let payload = scrubPayload(input.payload);
    if (utf8Length(JSON.stringify(payload)) > MAX_EVENT_PAYLOAD_BYTES) {
      payload = { truncated: true, keys: Object.keys(payload).slice(0, 20) };
    }
    event.payload = payload;
  }
  if (input.text !== undefined) {
    event.text = input.text.slice(0, MAX_EVENT_TEXT_CHARS);
  }
  return event;
}

export class EventReporter {
  private queue: ClientEvent[] = [];
  private timer: unknown = null;
  private flushing: Promise<EventsResponse | null> | null = null;
  /** Set once, never cleared. A reporter that has ended never sends again. */
  private endedReason: ReporterEndReason | null = null;
  /** True while failure sinks run: a sink's own `report()` must not re-arm the timer. */
  private notifyingFailure = false;
  private transientAttempts = 0;
  private rateLimitAttempts = 0;
  /** The delay the next arm should use, set by the failure branch that decided it. */
  private nextDelayMs: number | null = null;
  private sidebandSinks = new Set<(frame: SidebandFrame) => void>();
  private responseSinks = new Set<(response: EventsResponse) => void>();
  private failureSinks = new Set<(error: unknown) => void>();
  /** total events accepted by the server; for the UI */
  accepted = 0;

  constructor(
    private readonly sessionId: string,
    private readonly post: EventsPoster,
    private readonly now: () => number,
    private readonly options: { flushIntervalMs: number; scheduler?: Scheduler } = {
      flushIntervalMs: 250,
    },
  ) {}

  private get scheduler(): Scheduler {
    return this.options.scheduler ?? realScheduler;
  }

  onSideband(sink: (frame: SidebandFrame) => void): () => void {
    this.sidebandSinks.add(sink);
    return () => this.sidebandSinks.delete(sink);
  }

  onResponse(sink: (response: EventsResponse) => void): () => void {
    this.responseSinks.add(sink);
    return () => this.responseSinks.delete(sink);
  }

  onFailure(sink: (error: unknown) => void): () => void {
    this.failureSinks.add(sink);
    return () => this.failureSinks.delete(sink);
  }

  /** The session this reporter is bound to, for the whole of its life. */
  get session(): string {
    return this.sessionId;
  }

  /** True once this reporter has ended. It will never send again. */
  get ended(): boolean {
    return this.endedReason !== null;
  }

  get endReason(): ReporterEndReason | null {
    return this.endedReason;
  }

  /**
   * End this reporter, terminally.
   *
   * The queue is DROPPED rather than kept: every one of these reasons means the events
   * cannot be delivered where they belong, and the one thing worse than losing telemetry
   * is posting one conversation's events into another (M18.2's own rule that a session is
   * the unit of correlation). `dispose()` used to clear only the timer and leave both the
   * queue and the ability to send, which is what let a dead session keep talking.
   */
  end(reason: ReporterEndReason): void {
    if (this.endedReason !== null) return;
    this.endedReason = reason;
    this.queue = [];
    if (this.timer !== null) {
      this.scheduler.clearTimeout(this.timer);
      this.timer = null;
    }
  }

  /** Queue one event; returns the shaped event (its t_ms is what was recorded). */
  report(input: ReportInput): ClientEvent {
    const event = buildClientEvent(input, this.now());
    // Ended: shape it (callers read the return value) but never queue or schedule.
    if (this.endedReason !== null) return event;
    this.queue.push(event);
    // While failure sinks are running, a sink's own report() must not re-arm the timer:
    // that is the recursion the 2026-09-09 incident rode. The event is queued and will go
    // out with the next legitimate flush, if this reporter is still alive by then.
    if (this.timer === null && !this.notifyingFailure) this.arm(this.options.flushIntervalMs);
    return event;
  }

  private arm(delayMs: number): void {
    if (this.endedReason !== null || this.timer !== null) return;
    this.timer = this.scheduler.setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, delayMs);
  }

  private notifyFailure(error: unknown): void {
    this.notifyingFailure = true;
    try {
      for (const sink of this.failureSinks) sink(error);
    } finally {
      this.notifyingFailure = false;
    }
  }

  get pending(): number {
    return this.queue.length;
  }

  /** Send everything queued. Never throws; a failed batch stays queued. */
  flush(): Promise<EventsResponse | null> {
    if (this.endedReason !== null) return Promise.resolve(null);
    // A flush already on the wire: run again after it, so anything queued
    // since then is delivered too (not just the batch already in flight).
    if (this.flushing) return this.flushing.then(() => this.flush());
    if (this.timer !== null) {
      this.scheduler.clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.queue.length === 0) return Promise.resolve(null);
    const batch = this.queue.slice(0, MAX_EVENTS_PER_REQUEST);
    this.flushing = this.post(this.sessionId, batch)
      .then((response) => {
        this.queue = this.queue.slice(batch.length);
        this.transientAttempts = 0;
        this.rateLimitAttempts = 0;
        this.accepted += response.accepted;
        for (const frame of response.pending_sideband ?? []) {
          for (const sink of this.sidebandSinks) sink(frame);
        }
        for (const sink of this.responseSinks) sink(response);
        return response;
      })
      .catch((error: unknown) => {
        const status = statusOf(error);
        // 410 GONE is terminal for this session, and it is decided BEFORE the sinks run:
        // whatever a sink does - including reporting the failure, which is what the
        // controller used to do - it cannot revive an ended reporter.
        if (status === 410) {
          this.end("gone");
        } else if (status === 429) {
          this.rateLimitAttempts += 1;
          if (this.rateLimitAttempts > RATE_LIMIT_BACKOFF_MS.length) this.end("exhausted");
          else this.nextDelayMs = retryAfterMs(error) ?? RATE_LIMIT_BACKOFF_MS[this.rateLimitAttempts - 1];
        } else {
          this.transientAttempts += 1;
          if (this.transientAttempts >= MAX_TRANSIENT_ATTEMPTS) this.end("exhausted");
          else this.nextDelayMs = this.options.flushIntervalMs;
        }
        this.notifyFailure(error);
        return null;
      })
      .finally(() => {
        this.flushing = null;
        const delay = this.nextDelayMs ?? this.options.flushIntervalMs;
        this.nextDelayMs = null;
        if (this.endedReason === null && this.queue.length > 0) this.arm(delay);
      });
    return this.flushing;
  }

  /**
   * End this reporter because its session is closing normally.
   *
   * Kept as `dispose()` for its callers, but it is now TERMINAL: it used to clear only the
   * timer and leave the queue and the ability to send, so a delayed callback that fired
   * after a successful close still posted to a closed session (the race the incident's
   * §7 names). Flush before disposing if the final batch matters - `VoiceController.close`
   * does exactly that.
   */
  dispose(): void {
    this.end("closed");
  }
}

/**
 * The HTTP status behind a rejection, or `null` when it was not an HTTP failure.
 *
 * Deliberately duck-typed rather than importing `VoiceApiError`: this module is the one
 * `app/lib/voice/api.ts` posts THROUGH, and importing the error class back would be a
 * cycle. A network failure has no status and is treated as transient, which is right - the
 * browser will say when it is back.
 */
export function statusOf(error: unknown): number | null {
  if (typeof error !== "object" || error === null) return null;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? status : null;
}

/**
 * `Retry-After` in milliseconds, when the server sent one we can trust.
 *
 * Honoured over our own backoff because the server knows its own window. Seconds and
 * HTTP-date forms are both accepted; anything else, or a value outside a sane bound, falls
 * through to the local schedule rather than letting a header park the client for an hour.
 */
export function retryAfterMs(error: unknown, nowMs: number = Date.now()): number | null {
  if (typeof error !== "object" || error === null) return null;
  const raw = (error as { retryAfter?: unknown }).retryAfter;
  if (raw === undefined || raw === null) return null;
  const text = String(raw).trim();
  if (text === "") return null;
  const seconds = Number(text);
  const ms = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(text) - nowMs;
  if (!Number.isFinite(ms) || ms < 0) return null;
  return Math.min(ms, 60_000);
}
