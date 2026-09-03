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
import {
  type ClientEvent,
  type EventsResponse,
  isForbiddenKey,
  type SessionCredential,
  type SessionLegPayload,
  type SessionState,
  type SidebandFrame,
  type ToolCallResponse,
  type ToolManifestEntry,
} from "./contract";
import type { Scheduler } from "./events";
import type {
  AppliedInputSettings,
  Microphone,
  MicrophoneConstraints,
  NetworkMonitor,
  Playback,
  PlaybackStop,
  SpeechDetector,
  SpeechDetectorCalibration,
  SpeechDetectorCalibrationAttempt,
  SpeechDetectorStats,
  SpeechStartDetail,
} from "./ports";
import { BUNDLED_CONTRACT, CONTRACT_PATH, type ContractDocument, createSessionFields } from "./session-contract";
import type {
  AudioInput,
  AudioOutput,
  ConnectOptions,
  OutboundAudioStats,
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
  /**
   * ADR-0047 §1: scripted outbound-rtp counters as a function of the clock
   * (e.g. one packet per 20 ms for a continuously streaming track). Absent =
   * the transport has no `outboundAudioStats` at all (the provider fallback).
   */
  uplinkStats?: (now: number) => OutboundAudioStats | null;
};

export class FakeTransport implements RealtimeTransport {
  readonly kind = "fake";
  readonly connects: Array<{ descriptor: TransportDescriptor; credential: SessionCredential }> = [];
  readonly sent: string[] = [];
  readonly audioIn: AudioInput[] = [];
  closedWith: string | null = null;
  connected = false;
  statsPolls = 0;
  private eventSinks = new Set<(event: TransportEvent) => void>();
  private audioSinks = new Set<(output: AudioOutput) => void>();
  private now: () => number;

  constructor(private readonly options: FakeTransportOptions = {}) {
    this.now = options.now ?? (() => 0);
    if (options.uplinkStats) {
      const script = options.uplinkStats;
      this.outboundAudioStats = async () => {
        this.statsPolls += 1;
        return script(this.now());
      };
    }
  }

  /** Present only when the fake was given `uplinkStats` (see FakeTransportOptions). */
  outboundAudioStats?: () => Promise<OutboundAudioStats | null>;

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
  muted = false;
  stops: number[] = [];
  mutes: number[] = [];
  unmutes: number[] = [];
  /** Scripted audio-thread delay from a silence request to the gain reaching zero at the output. */
  gainZeroDelayMs = 3;
  outputLatencyMs = 10;
  private activitySinks = new Set<(at: number) => void>();

  constructor(
    private readonly now: () => number,
    private readonly log?: (op: string) => void,
  ) {}

  attach(): void {
    /* audio path is virtual */
  }

  private silence(): PlaybackStop {
    const at = this.now();
    return { at, gainZeroAt: at + this.gainZeroDelayMs, outputLatencyMs: this.outputLatencyMs, changed: !this.muted };
  }

  stop(): PlaybackStop {
    const result = this.silence();
    this.playing = false;
    this.muted = true;
    this.stops.push(result.at);
    this.log?.("playback.stop");
    return result;
  }

  mute(): PlaybackStop {
    const result = this.silence();
    this.muted = true;
    this.mutes.push(result.at);
    this.log?.("playback.mute");
    return result;
  }

  unmute(): void {
    this.muted = false;
    this.unmutes.push(this.now());
    this.log?.("playback.unmute");
  }

  arm(): void {
    this.playing = true;
    this.muted = false;
  }

  prepare(): void {
    /* nothing to warm up */
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
  applied: AppliedInputSettings | null = null;
  opened: Array<string | undefined> = [];
  constraints: Array<Partial<MicrophoneConstraints> | undefined> = [];

  async open(deviceId?: string, constraints?: Partial<MicrophoneConstraints>): Promise<MediaStream> {
    this.opened.push(deviceId);
    this.constraints.push(constraints);
    // Node has no MediaStream; the controller only passes it through.
    this.stream = { id: `fake-mic-${this.opened.length}`, getAudioTracks: () => [] } as unknown as MediaStream;
    const requested: MicrophoneConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
      channelCount: 1,
      ...constraints,
    };
    this.applied = {
      label: "Fake USB microphone",
      deviceId: deviceId ?? "default",
      groupId: "group-1",
      echoCancellation: requested.echoCancellation,
      noiseSuppression: requested.noiseSuppression,
      autoGainControl: requested.autoGainControl,
      voiceIsolation: null,
      suppressLocalAudioPlayback: null,
      channelCount: 1,
      sampleRate: 48_000,
      inputLatencyMs: 10,
      notHonoured: requested.voiceIsolation ? ["voiceIsolation"] : [],
      requested,
      settings: { sampleRate: 48_000, channelCount: 1 },
      capabilities: null,
      supportedConstraints: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    };
    return this.stream;
  }

  close(): void {
    this.stream = null;
    this.applied = null;
  }
}

export class FakeSpeechDetector implements SpeechDetector {
  started = 0;
  counters: SpeechDetectorStats = { gate_opens: 0, gated_out: 0, click_rejects: 0, speech_ms: 0, calibrations: 0 };
  /** What `onsetCandidate()` answers (a provider-first barge-in reads it). */
  candidate: { candidateAt: number; preRollMs: number } | null = null;
  private startSinks = new Set<(at: number, detail?: SpeechStartDetail) => void>();
  private endSinks = new Set<(at: number) => void>();
  private evidenceSinks = new Set<(at: number, candidateAt: number) => void>();
  private evidenceLostSinks = new Set<(at: number) => void>();
  private calibrationSinks = new Set<
    (calibration: SpeechDetectorCalibration | SpeechDetectorCalibrationAttempt) => void
  >();

  start(): void {
    this.started += 1;
  }

  stop(): void {
    /* nothing to release */
  }

  onSpeechStart(sink: (at: number, detail?: SpeechStartDetail) => void): Unsubscribe {
    this.startSinks.add(sink);
    return () => this.startSinks.delete(sink);
  }

  onSpeechEnd(sink: (at: number) => void): Unsubscribe {
    this.endSinks.add(sink);
    return () => this.endSinks.delete(sink);
  }

  onEvidence(sink: (at: number, candidateAt: number) => void): Unsubscribe {
    this.evidenceSinks.add(sink);
    return () => this.evidenceSinks.delete(sink);
  }

  onEvidenceLost(sink: (at: number) => void): Unsubscribe {
    this.evidenceLostSinks.add(sink);
    return () => this.evidenceLostSinks.delete(sink);
  }

  onCalibration(
    sink: (calibration: SpeechDetectorCalibration | SpeechDetectorCalibrationAttempt) => void,
  ): Unsubscribe {
    this.calibrationSinks.add(sink);
    return () => this.calibrationSinks.delete(sink);
  }

  onsetCandidate(): { candidateAt: number; preRollMs: number } | null {
    return this.candidate;
  }

  stats(): SpeechDetectorStats {
    return { ...this.counters };
  }

  /**
   * A gate open at `at` (the backdated onset). Without `detail` the fake
   * behaves like the pre-ADR-0047 detector; with it the accounting flows to
   * the controller (`gate_ms`, `capture_lag_ms`, `pre_roll_ms`).
   */
  speechStart(at: number, detail?: Partial<SpeechStartDetail>): void {
    this.counters.gate_opens += 1;
    this.candidate = null;
    const full: SpeechStartDetail | undefined = detail
      ? {
          candidateAt: detail.candidateAt ?? at + (detail.preRollMs ?? 0),
          decidedAt: detail.decidedAt ?? at + (detail.preRollMs ?? 0),
          preRollMs: detail.preRollMs ?? 0,
          captureLagMs: detail.captureLagMs ?? null,
          duringPlayback: detail.duringPlayback ?? false,
        }
      : undefined;
    for (const sink of this.startSinks) sink(at, full);
  }

  speechEnd(at: number): void {
    for (const sink of this.endSinks) sink(at);
  }

  /** Playback-only confident onset (reversible mute request) and its collapse. */
  evidence(at: number, candidateAt: number): void {
    this.counters.evidence_events = (this.counters.evidence_events ?? 0) + 1;
    this.candidate = { candidateAt, preRollMs: 40 };
    for (const sink of this.evidenceSinks) sink(at, candidateAt);
  }

  evidenceLost(at: number): void {
    this.counters.evidence_lost = (this.counters.evidence_lost ?? 0) + 1;
    this.candidate = null;
    for (const sink of this.evidenceLostSinks) sink(at);
  }

  /** Script a completed calibration (numbers only, like the real detector; ADR-0047 §5 shape). */
  emitCalibration(partial: Partial<SpeechDetectorCalibration> = {}): void {
    this.counters.calibrations += 1;
    const calibration: SpeechDetectorCalibration = {
      measured: 1,
      samples: 84,
      noise_floor_db: -52.5,
      stationary_db: -55,
      spread_db: 4,
      peak_db: -30,
      clip_risk: 0,
      hum_ratio: 0.2,
      contaminated: 0,
      sensitivity_class: 2,
      env_class: 2,
      open_margin_db: 10,
      min_onset_ms: 70,
      hang_ms: 450,
      pre_roll_ms: 120,
      echo_margin_db: 6,
      trigger: 0,
      dead_frames: 0,
      ...partial,
    };
    for (const sink of this.calibrationSinks) sink(calibration);
  }

  /** Script a window that measured nothing (dead input / contaminated retry). */
  emitCalibrationAttempt(partial: Partial<SpeechDetectorCalibrationAttempt> = {}): void {
    const attempt: SpeechDetectorCalibrationAttempt = {
      measured: 0,
      samples: 0,
      dead_frames: 150,
      contaminated: 0,
      trigger: 0,
      retry: 1,
      ...partial,
    };
    for (const sink of this.calibrationSinks) sink(attempt);
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

/**
 * How the fake answers `GET /v1/voice/realtime/contract` (ADR-0045):
 * - `served` (default): 200 with the bundled document, i.e. a current server;
 * - a document: 200 with exactly that (a newer server than the bundle);
 * - `legacy`: 404, the route does not exist — a contract v1 server;
 * - `unauthorized`: 401 (what the real API answers without a valid bearer);
 * - `network`: the fetcher throws, as `fetch` does when the host is unreachable.
 */
export type FakeContractMode = "served" | "legacy" | "unauthorized" | "network" | ContractDocument;

export type FakeCloudCoreOptions = {
  transport?: string;
  provider?: string;
  descriptor?: Record<string, unknown>;
  tools?: ToolManifestEntry[];
  /** name → response for a relayed tool call */
  toolResponses?: Record<string, Partial<ToolCallResponse>>;
  contract?: FakeContractMode;
  /** a v1 server: `extra="forbid"` refuses `voice` exactly like the deployed release did */
  legacyCreate?: boolean;
};

type ForcedFailure = { status: number; detail: unknown };

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
  private failures = new Map<string, ForcedFailure[]>();
  /** which owner leg currently holds the session; the client is leg "web-1" */
  currentLeg = "web-1";
  clientLeg = "web-1";
  private readonly sessionId = "11111111-2222-4333-8444-555555555555";

  constructor(private readonly options: FakeCloudCoreOptions = {}) {}

  /** Make the next call to `pathSuffix` answer with `status` (queued FIFO), optionally with a body. */
  failNext(pathSuffix: string, status: number, detail?: unknown): void {
    const list = this.failures.get(pathSuffix) ?? [];
    list.push({ status, detail: detail === undefined ? { detail: `forced ${status}` } : detail });
    this.failures.set(pathSuffix, list);
  }

  /** The deployed v1 server's answer to a body carrying `voice` (verbatim shape). */
  static extraForbidden(field: string): unknown {
    return {
      detail: [
        {
          type: "extra_forbidden",
          loc: ["body", field],
          msg: "Extra inputs are not permitted",
          input: "REDACTED-BY-TEST",
          url: "https://errors.pydantic.dev/2.11/v/extra_forbidden",
        },
      ],
    };
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

  /** The wire voice the session was created with (ADR-0043); null until create. */
  voice: string | null = null;

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
      voice: this.voice,
      voice_profile: "arbor",
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
        const forced = list.shift() as ForcedFailure;
        return this.json(forced.status, forced.detail);
      }
    }
    if (path === CONTRACT_PATH && method === "GET") {
      const mode = this.options.contract ?? "served";
      if (mode === "network") throw new TypeError("Failed to fetch");
      if (mode === "unauthorized") return this.json(401, { detail: "Not authenticated" });
      if (mode === "legacy") return this.json(404, { detail: "Not Found" });
      return this.json(200, mode === "served" ? BUNDLED_CONTRACT : mode);
    }
    const base = "/v1/voice/realtime/sessions";
    if (path === base && method === "POST") {
      if (this.options.legacyCreate) {
        const extra = Object.keys((body as Record<string, unknown>) ?? {}).find(
          (key) => !createSessionFields(1).includes(key),
        );
        if (extra) return this.json(422, FakeCloudCore.extraForbidden(extra));
      }
      const wanted = (body as { voice?: unknown } | undefined)?.voice;
      // routes.py: optional, pattern ^[a-z]{2,16}$; anything else is a 422.
      if (wanted !== undefined && (typeof wanted !== "string" || !/^[a-z]{2,16}$/.test(wanted))) {
        return this.json(422, { detail: "invalid voice" });
      }
      this.voice = typeof wanted === "string" ? wanted : null;
      return this.json(201, this.legPayload());
    }
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
          // service.py `is_forbidden_key`: normalized (lower-case, separators
          // dropped) substring match against FORBIDDEN_KEY_PARTS.
          for (const key of Object.keys(event.payload)) {
            if (isForbiddenKey(key)) {
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
