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
  FORBIDDEN_PAYLOAD_KEY_PARTS,
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

export type EventsPoster = (events: ClientEvent[]) => Promise<EventsResponse>;

export type Scheduler = {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
};

export const realScheduler: Scheduler = {
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

const KNOWN_KINDS: ReadonlySet<string> = new Set(CLIENT_EVENT_KINDS);

export function isForbiddenPayloadKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return FORBIDDEN_PAYLOAD_KEY_PARTS.some((part) => lowered.includes(part));
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
  private sidebandSinks = new Set<(frame: SidebandFrame) => void>();
  private responseSinks = new Set<(response: EventsResponse) => void>();
  private failureSinks = new Set<(error: unknown) => void>();
  /** total events accepted by the server; for the UI */
  accepted = 0;

  constructor(
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

  /** Queue one event; returns the shaped event (its t_ms is what was recorded). */
  report(input: ReportInput): ClientEvent {
    const event = buildClientEvent(input, this.now());
    this.queue.push(event);
    if (this.timer === null) {
      this.timer = this.scheduler.setTimeout(() => {
        this.timer = null;
        void this.flush();
      }, this.options.flushIntervalMs);
    }
    return event;
  }

  get pending(): number {
    return this.queue.length;
  }

  /** Send everything queued. Never throws; a failed batch stays queued. */
  flush(): Promise<EventsResponse | null> {
    // A flush already on the wire: run again after it, so anything queued
    // since then is delivered too (not just the batch already in flight).
    if (this.flushing) return this.flushing.then(() => this.flush());
    if (this.timer !== null) {
      this.scheduler.clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.queue.length === 0) return Promise.resolve(null);
    const batch = this.queue.slice(0, MAX_EVENTS_PER_REQUEST);
    this.flushing = this.post(batch)
      .then((response) => {
        this.queue = this.queue.slice(batch.length);
        this.accepted += response.accepted;
        for (const frame of response.pending_sideband ?? []) {
          for (const sink of this.sidebandSinks) sink(frame);
        }
        for (const sink of this.responseSinks) sink(response);
        return response;
      })
      .catch((error: unknown) => {
        for (const sink of this.failureSinks) sink(error);
        return null;
      })
      .finally(() => {
        this.flushing = null;
        if (this.queue.length > 0 && this.timer === null) {
          this.timer = this.scheduler.setTimeout(() => {
            this.timer = null;
            void this.flush();
          }, this.options.flushIntervalMs);
        }
      });
    return this.flushing;
  }

  /** Stop the timer; queued events stay for an explicit final flush. */
  dispose(): void {
    if (this.timer !== null) {
      this.scheduler.clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
