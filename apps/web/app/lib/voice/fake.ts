/**
 * Deterministic fakes for the voice client: a scriptable transport, a
 * playback that records when it was silenced, a manual scheduler/clock, a
 * network switch, a local speech detector, and an in-memory Cloud Core that
 * answers the session API exactly like `routes.py` (idempotent tool calls,
 * queued sideband replayed via `/events`, attach moving the leg).
 *
 * The page also uses `FakeTransport` when the server picks a provider whose
 * transport is `simulated` — there is no browser media path for it, so the
 * fake plays a scripted turn to exercise the wiring end-to-end.
 */

import type { Fetcher } from "./api";
import type {
  ClientEvent,
  EventsResponse,
  SessionCredential,
  SessionLegPayload,
  SessionState,
  SidebandFrame,
  ToolCallResponse,
  ToolManifestEntry,
} from "./contract";
import type { Scheduler } from "./events";
import type { Microphone, NetworkMonitor, Playback, SpeechDetector } from "./ports";
import type {
  AudioInput,
  AudioOutput,
  ConnectOptions,
  RealtimeTransport,
  TransportDescriptor,
  TransportEvent,
  Unsubscribe,
} from "./transport";

// ------------------------------------------------------------- scheduler

/** A manual clock: timers fire only when `advance()` reaches them. */
export class FakeScheduler implements Scheduler {
  private time = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  private seq = 0;

  now = (): number => this.time;

  setTimeout(fn: () => void, ms: number): number {
    const id = ++this.seq;
    this.timers.set(id, { at: this.time + Math.max(0, ms), fn });
    return id;
  }

  clearTimeout(handle: unknown): void {
    this.timers.delete(handle as number);
  }

  /** Move time forward, firing due timers in order. */
  advance(ms: number): void {
    const target = this.time + ms;
    for (;;) {
      let next: [number, { at: number; fn: () => void }] | null = null;
      for (const entry of this.timers) {
        if (entry[1].at <= target && (next === null || entry[1].at < next[1].at)) next = entry;
      }
      if (!next) break;
      this.timers.delete(next[0]);
      this.time = next[1].at;
      next[1].fn();
    }
    this.time = target;
  }

  get pendingTimers(): number {
    return this.timers.size;
  }
}

/** Let queued promise callbacks run (a few microtask turns). */
export async function settle(rounds = 8): Promise<void> {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
}

// -------------------------------------------------------------- transport

export type FakeTransportOptions = {
  log?: (op: string) => void;
  now?: () => number;
  /** Fail `connect` this many times before succeeding. */
  failConnects?: number;
};

export class FakeTransport implements RealtimeTransport {
  readonly kind = "fake";
  readonly connects: Array<{ descriptor: TransportDescriptor; credential: SessionCredential }> = [];
  readonly sent: string[] = [];
  readonly audioIn: AudioInput[] = [];
  closedWith: string | null = null;
  connected = false;
  private eventSinks = new Set<(event: TransportEvent) => void>();
  private audioSinks = new Set<(output: AudioOutput) => void>();
  private now: () => number;

  constructor(private readonly options: FakeTransportOptions = {}) {
    this.now = options.now ?? (() => 0);
  }

  async connect(
    descriptor: TransportDescriptor,
    credential: SessionCredential,
    options?: ConnectOptions,
  ): Promise<void> {
    if (options?.now) this.now = options.now;
    this.connects.push({ descriptor, credential });
    if (this.options.failConnects && this.options.failConnects > 0) {
      this.options.failConnects -= 1;
      throw new Error("fake transport: connect failed");
    }
    this.connected = true;
    this.closedWith = null;
    this.emit({ type: "connected", at: this.now() });
  }

  sendAudio(input: AudioInput): void {
    this.audioIn.push(input);
  }

  onAudio(sink: (output: AudioOutput) => void): Unsubscribe {
    this.audioSinks.add(sink);
    return () => this.audioSinks.delete(sink);
  }

  onEvent(sink: (event: TransportEvent) => void): Unsubscribe {
    this.eventSinks.add(sink);
    return () => this.eventSinks.delete(sink);
  }

  cancelResponse(): void {
    this.sent.push("cancel");
    this.options.log?.("fake.cancelResponse");
  }

  submitToolResult(callId: string, result: unknown): void {
    this.sent.push(`submit:${callId}:${JSON.stringify(result)}`);
  }

  notifyToolCompleted(callId: string, name: string, result: unknown): void {
    this.sent.push(`completed:${callId}:${name}:${JSON.stringify(result)}`);
  }

  say(text: string): void {
    this.sent.push(`say:${text}`);
  }

  close(reason = "closed"): void {
    this.connected = false;
    this.closedWith = reason;
  }

  // ---- script controls

  emit(event: TransportEvent): void {
    for (const sink of this.eventSinks) sink(event);
  }

  emitAudio(output: AudioOutput = { kind: "pcm16", data: new ArrayBuffer(0) }): void {
    for (const sink of this.audioSinks) sink(output);
  }
}

// --------------------------------------------------------------- playback

export class FakePlayback implements Playback {
  playing = false;
  stops: number[] = [];
  private activitySinks = new Set<(at: number) => void>();

  constructor(
    private readonly now: () => number,
    private readonly log?: (op: string) => void,
  ) {}

  attach(): void {
    /* audio path is virtual */
  }

  stop(): number {
    this.playing = false;
    const at = this.now();
    this.stops.push(at);
    this.log?.("playback.stop");
    return at;
  }

  arm(): void {
    this.playing = true;
  }

  onActivity(sink: (at: number) => void): Unsubscribe {
    this.activitySinks.add(sink);
    return () => this.activitySinks.delete(sink);
  }

  /** First audible energy of the current response. */
  activity(at = this.now()): void {
    for (const sink of this.activitySinks) sink(at);
  }

  dispose(): void {
    this.activitySinks.clear();
  }
}

// ---------------------------------------------------- mic / vad / network

export class FakeMicrophone implements Microphone {
  stream: MediaStream | null = null;
  opened: Array<string | undefined> = [];

  async open(deviceId?: string): Promise<MediaStream> {
    this.opened.push(deviceId);
    // Node has no MediaStream; the controller only passes it through.
    this.stream = { id: `fake-mic-${this.opened.length}`, getAudioTracks: () => [] } as unknown as MediaStream;
    return this.stream;
  }

  close(): void {
    this.stream = null;
  }
}

export class FakeSpeechDetector implements SpeechDetector {
  started = 0;
  private startSinks = new Set<(at: number) => void>();
  private endSinks = new Set<(at: number) => void>();

  start(): void {
    this.started += 1;
  }

  stop(): void {
    /* nothing to release */
  }

  onSpeechStart(sink: (at: number) => void): Unsubscribe {
    this.startSinks.add(sink);
    return () => this.startSinks.delete(sink);
  }

  onSpeechEnd(sink: (at: number) => void): Unsubscribe {
    this.endSinks.add(sink);
    return () => this.endSinks.delete(sink);
  }

  speechStart(at: number): void {
    for (const sink of this.startSinks) sink(at);
  }

  speechEnd(at: number): void {
    for (const sink of this.endSinks) sink(at);
  }
}

export class FakeNetwork implements NetworkMonitor {
  online = true;
  private sinks = new Set<(online: boolean) => void>();

  onChange(sink: (online: boolean) => void): Unsubscribe {
    this.sinks.add(sink);
    return () => this.sinks.delete(sink);
  }

  set(online: boolean): void {
    if (this.online === online) return;
    this.online = online;
    for (const sink of this.sinks) sink(online);
  }
}

// ------------------------------------------------------------- cloud core

export type RecordedRequest = { method: string; path: string; body: unknown };

export type FakeCloudCoreOptions = {
  transport?: string;
  provider?: string;
  descriptor?: Record<string, unknown>;
  tools?: ToolManifestEntry[];
  /** name → response for a relayed tool call */
  toolResponses?: Record<string, Partial<ToolCallResponse>>;
};

/**
 * In-memory stand-in for `/v1/voice/realtime/sessions/*` with the behaviour
 * the real service has: idempotent `tool-calls` on call_id, `events` replaying
 * queued sideband frames, `attach` minting a new credential and moving the
 * leg, 409 for a stale leg, 410 once closed.
 */
export class FakeCloudCore {
  readonly requests: RecordedRequest[] = [];
  readonly events: ClientEvent[] = [];
  readonly toolCalls = new Map<string, ToolCallResponse>();
  pendingSideband: SidebandFrame[] = [];
  minted = 0;
  legs = 1;
  closed: string | null = null;
  private failures = new Map<string, number[]>();
  /** which owner leg currently holds the session; the client is leg "web-1" */
  currentLeg = "web-1";
  clientLeg = "web-1";
  private readonly sessionId = "11111111-2222-4333-8444-555555555555";

  constructor(private readonly options: FakeCloudCoreOptions = {}) {}

  /** Make the next call to `pathSuffix` answer with `status` (queued FIFO). */
  failNext(pathSuffix: string, status: number): void {
    const list = this.failures.get(pathSuffix) ?? [];
    list.push(status);
    this.failures.set(pathSuffix, list);
  }

  queueSideband(event: SidebandFrame["event"], payload: Record<string, unknown>): void {
    this.pendingSideband.push({
      type: "voice_sideband",
      session_id: this.sessionId,
      event,
      payload,
      at: "2026-09-02T00:00:00Z",
    });
  }

  private credential(): SessionCredential {
    this.minted += 1;
    return {
      provider: this.options.provider ?? "fake-provider",
      secret: `ephemeral-${this.minted}`,
      expires_at: "2026-09-02T00:10:00Z",
      transport: this.options.transport ?? "webrtc",
      session_ref: `ref:${this.sessionId}`,
    };
  }

  private state(): SessionState {
    return {
      session_id: this.sessionId,
      provider: this.options.provider ?? "fake-provider",
      transport: this.options.transport ?? "webrtc",
      client_kind: "web",
      state: this.closed ? "closed" : "active",
      language: "tr-TR",
      plan: null,
      plan_id: null,
      narration: null,
      presentation: null,
      last_intent: null,
      fsm_state: null,
      barge_in_count: this.events.filter((e) => e.kind === "barge_in_start").length,
      network: null,
      legs: this.legs,
      pending_sideband_count: this.pendingSideband.length,
      transcript_summary: "",
    };
  }

  private legPayload(): SessionLegPayload {
    return {
      session_id: this.sessionId,
      provider: this.options.provider ?? "fake-provider",
      transport: this.options.transport ?? "webrtc",
      credential: this.credential(),
      tools: this.options.tools ?? [],
      instructions: "Sen PagentOS'un sesli asistanısın.",
      language: "tr-TR",
      expires_at: "2026-09-02T01:00:00Z",
      state: "created",
      ...(this.options.descriptor ? { transport_descriptor: this.options.descriptor } : {}),
    };
  }

  private json(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  /** The `Fetcher` the client is given. */
  fetcher: Fetcher = async (path, init = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    const body = typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    this.requests.push({ method, path, body });
    for (const [suffix, list] of this.failures) {
      if (path.endsWith(suffix) && list.length) {
        const status = list.shift() as number;
        return this.json(status, { detail: `forced ${status}` });
      }
    }
    const base = "/v1/voice/realtime/sessions";
    if (path === base && method === "POST") return this.json(201, this.legPayload());
    if (!path.startsWith(`${base}/`)) return this.json(404, { detail: "unknown" });
    const rest = path.slice(base.length + 1).split("/");
    if (rest[0] !== this.sessionId) return this.json(404, { detail: "unknown realtime session" });
    const verb = rest[1];
    if (this.closed && verb !== "close") return this.json(410, { detail: "closed" });
    if (verb === undefined) return this.json(200, this.state());
    if (verb === "close") {
      this.closed = String((body as { reason?: string })?.reason ?? "client_closed");
      return this.json(200, { session_id: this.sessionId, state: "closed" });
    }
    if (verb === "attach") {
      if (this.currentLeg !== this.clientLeg) this.legs += 1;
      this.currentLeg = this.clientLeg;
      const pending = this.pendingSideband;
      this.pendingSideband = [];
      return this.json(200, {
        ...this.legPayload(),
        state: this.state(),
        pending_sideband: pending,
        previous_leg: null,
      });
    }
    if (this.currentLeg !== this.clientLeg) return this.json(409, { detail: { leg: "mismatch" } });
    if (verb === "tool-calls") {
      const call = body as { call_id: string; name: string; arguments: Record<string, unknown> };
      const existing = this.toolCalls.get(call.call_id);
      if (existing) return this.json(200, { ...existing, replayed: true });
      const preset = this.options.toolResponses?.[call.name];
      const response: ToolCallResponse = {
        call_id: call.call_id,
        name: call.name,
        status: "succeeded",
        long_running: false,
        replayed: false,
        result: { echo: call.arguments },
        ...preset,
      };
      this.toolCalls.set(call.call_id, response);
      return this.json(200, response);
    }
    if (verb === "events") {
      const batch = (body as { events: ClientEvent[] }).events;
      for (const event of batch) {
        if (event.payload) {
          for (const key of Object.keys(event.payload)) {
            const lowered = key.toLowerCase();
            if (["audio", "pcm", "wave", "secret", "credential", "api_key"].some((p) => lowered.includes(p))) {
              return this.json(422, { detail: `forbidden payload key ${key}` });
            }
          }
        }
      }
      this.events.push(...batch);
      const pending = this.pendingSideband;
      this.pendingSideband = [];
      const response: EventsResponse = {
        accepted: batch.length,
        resolved_intents: [],
        pending_sideband: pending,
        state: this.state(),
      };
      return this.json(200, response);
    }
    return this.json(404, { detail: "unknown verb" });
  };

  /** Another client took the leg (what `attach` from elsewhere does). */
  stealLeg(): void {
    this.currentLeg = "other";
  }

  kinds(): string[] {
    return this.events.map((e) => e.kind);
  }
}
