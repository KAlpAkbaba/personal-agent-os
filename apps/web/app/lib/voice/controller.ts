/**
 * Voice session controller — the browser leg of a ConversationRealtime
 * session (M12 spec §4–§7, track D).
 *
 * Responsibilities, in the order they matter:
 *
 * 1. Barge-in: owner speech while the assistant is audible → STOP LOCAL
 *    PLAYBACK FIRST (synchronous), then cancel the provider's response, then
 *    report `barge_in_start` + `playback_stopped` with the client-measured
 *    stop latency. The order is fixed; a test pins it.
 * 2. End-of-turn with the Turkish hesitation guard: the provider's
 *    `speech_stopped` opens a hold whose length depends on the transcript
 *    tail; speech inside the hold is a continuation, not a new turn, and a
 *    response the provider started prematurely is cancelled.
 * 3. Tool relay: provider tool call → `POST .../tool-calls` (once per call_id,
 *    however many times the provider repeats it) → result back to the
 *    provider; long-running tools get the Turkish preamble now and the final
 *    outcome when the `tool_completed` sideband frame lands.
 * 4. Every timing/state transition is reported with monotonic timestamps.
 * 5. Network loss: `network_lost`, transport torn down, `attach` on restore
 *    (fresh credential, replayed sideband), `network_restored`.
 *
 * The controller is pure orchestration: it touches no browser API directly.
 * Everything with side effects is injected (ports.ts, transport.ts), which is
 * what makes the deterministic tests possible.
 */

import type { RequestLogEntry, VoiceSessionApi } from "./api";
import { VoiceApiError } from "./api";
import type {
  FsmState,
  SessionCredential,
  SessionLegPayload,
  SidebandFrame,
  ToolCallResponse,
} from "./contract";
import { MAX_SUMMARY_CHARS } from "./contract";
import { EventReporter, numbersOnly, type Scheduler, realScheduler } from "./events";
import { HesitationGuard, type HesitationGuardConfig } from "./hesitation";
import type {
  Microphone,
  NetworkMonitor,
  Playback,
  SpeechDetector,
  SpeechDetectorCalibration,
} from "./ports";
import {
  type ResolvedContract,
  droppedFieldsNotice,
  problemsNotice,
  resolveContract,
  unknownVersionNotice,
  validateCreateBody,
} from "./session-contract";
import {
  type RealtimeTransport,
  type TransportDescriptor,
  type TransportEvent,
  resolveTransportDescriptor,
} from "./transport";

export type VoiceUiState =
  | "idle"
  | "creating"
  | "connecting"
  | "listening"
  | "speaking"
  | "tool_running"
  | "interrupted"
  | "reconnecting"
  | "closed"
  | "error";

export type LatencySample = { value: number; at: number };

/**
 * Per-session microphone / noise counters for the qualification matrix
 * (ADR-0044 §7). Numbers only; reported through `state` events.
 */
export type MicMetrics = {
  /** local gate opened, the provider never heard speech for it */
  false_starts: number;
  /** a false start that had already stopped the assistant (barge-in on noise) */
  false_barge_ins: number;
  /** provider-confirmed turns that produced no transcript */
  false_turns: number;
  gate_opens: number;
  gated_out: number;
  click_rejects: number;
  calibrations: number;
  noise_floor_db: number | null;
  /** 0 quiet, 1 normal, 2 noisy, 3 very noisy; null before calibration */
  env: number | null;
};

/**
 * ADR-0045: which realtime-session contract version the client is honouring.
 * `source` says how it was learned: the server's own document, a 404 (a v1
 * server), the bundled document because the server could not be asked
 * (`known: false`), or `unauthorized` (not signed in — no version at all).
 */
export type ContractStatus = {
  version: number | null;
  source: "server" | "legacy" | "bundled" | "unauthorized";
  known: boolean;
  /** create_session fields the honoured version accepts */
  createFields: string[];
};

export type ControllerSnapshot = {
  state: VoiceUiState;
  /**
   * The server's canonical session UUID. Kept after disconnect / a failed
   * reconnect / a failed create until a NEW session is created, so the owner
   * can still copy it for the benchmark fetch once the session is over.
   */
  sessionId: string | null;
  provider: string | null;
  transport: string | null;
  /** ADR-0043: wire voice and perceptual profile the server reported for this session. */
  voice: string | null;
  voiceProfile: string | null;
  micMetrics: MicMetrics;
  turn: number;
  assistantText: string;
  ownerText: string;
  lastError: string | null;
  /** ADR-0045: the server's reasons behind `lastError`, field by field (never a request value). */
  lastErrorLines: string[];
  /** ADR-0045: null until the server has been asked (or could not be). */
  contract: ContractStatus | null;
  /** ADR-0045: Turkish notice about the last create request (fields dropped, version unknown). */
  contractNotice: string | null;
  /** ADR-0045: the last outgoing Cloud Core requests, scrubbed (diagnostics). */
  requestLog: RequestLogEntry[];
  latency: {
    barge_in_to_stop_ms?: LatencySample;
    eot_to_first_audio_ms?: LatencySample;
    mic_to_uplink_ms?: LatencySample;
    tool_preamble_ms?: LatencySample;
    tool_done_to_speech_ms?: LatencySample;
  };
  toolsRunning: string[];
  sidebandLog: string[];
  hesitation: { held: number; resumed_within_hold: number; last?: string };
  online: boolean;
  eventsAccepted: number;
  eventsPending: number;
  legs: number;
};

export type ControllerDeps = {
  api: VoiceSessionApi;
  transportFactory: (descriptor: TransportDescriptor) => RealtimeTransport;
  playback: Playback;
  network: NetworkMonitor;
  now: () => number;
  microphone?: Microphone;
  localSpeech?: SpeechDetector;
  scheduler?: Scheduler;
  hesitation?: Partial<HesitationGuardConfig>;
  flushIntervalMs?: number;
  reattach?: { maxAttempts: number; baseDelayMs: number };
  /**
   * After the local gate closes without the provider ever confirming speech,
   * how long to wait for a late `speech_started` before calling it a false
   * start and releasing the turn. The provider's VAD confirms real speech well
   * inside this; without it a noise-opened turn would stay "speaking" forever
   * and silently disable the next barge-in.
   */
  localSpeechGraceMs?: number;
  /** Ordered operation log — tests pin ordering with it. */
  log?: (op: string) => void;
};

type LongRunning = { name: string; startedAt: number; preamble?: string };

const SIDEBAND_LOG_MAX = 20;
const REQUEST_LOG_MAX = 20;
const DEFAULT_LOCAL_GRACE_MS = 700;

const EMPTY_MIC_METRICS: MicMetrics = {
  false_starts: 0,
  false_barge_ins: 0,
  false_turns: 0,
  gate_opens: 0,
  gated_out: 0,
  click_rejects: 0,
  calibrations: 0,
  noise_floor_db: null,
  env: null,
};

export class VoiceSessionController {
  private readonly deps: ControllerDeps;
  private readonly scheduler: Scheduler;
  private readonly guard: HesitationGuard;
  private reporter: EventReporter | null = null;
  private transport: RealtimeTransport | null = null;
  private transportUnsubs: Array<() => void> = [];
  private portUnsubs: Array<() => void> = [];
  private localSpeechUnsubs: Array<() => void> = [];
  private snapshot: ControllerSnapshot;
  private listeners = new Set<(snapshot: ControllerSnapshot) => void>();

  private t0 = 0;
  private sessionId: string | null = null;
  private descriptor: TransportDescriptor | null = null;
  private closing = false;

  // turn / speech tracking
  private ownerSpeaking = false;
  private ownerSpeechStartedAt: number | null = null;
  private ownerSpeechSource: "local" | "provider" | null = null;
  private uplinkReported = false;
  private ownerTranscriptTail = "";
  private eotTimer: unknown = null;
  private lastEotAt: number | null = null;
  private prematureResponse = false;
  // noise qualification (ADR-0044 §7)
  private localGraceTimer: unknown = null;
  private metrics = { false_starts: 0, false_barge_ins: 0, false_turns: 0 };
  /** the open turn awaiting its transcript verdict: judged when the next turn starts / at close */
  private turnJudgement: { turn: number; providerConfirmed: boolean; hadTranscript: boolean } | null = null;
  private lastCalibration: SpeechDetectorCalibration | null = null;

  // response tracking
  private responseActive = false;
  private responseAudible = false;
  private awaitingFirstAudio = false;
  private bargedResponse = false;
  private assistantBuffer = "";

  // tools
  private relayed = new Map<string, Promise<void>>();
  private longRunning = new Map<string, LongRunning>();
  private pendingPreamble: { callId: string; at: number } | null = null;
  private pendingResume: { callId: string; at: number } | null = null;

  // reconnect
  private reattachAttempts = 0;
  private reattachTimer: unknown = null;

  // continuity
  private recentLines: string[] = [];

  // contract (ADR-0045): resolved once per page load when the server answered
  private contract: ResolvedContract | null = null;
  private contractProbe: Promise<ResolvedContract | null> | null = null;

  constructor(deps: ControllerDeps) {
    this.deps = deps;
    this.scheduler = deps.scheduler ?? realScheduler;
    this.guard = new HesitationGuard(deps.hesitation);
    this.snapshot = {
      state: "idle",
      sessionId: null,
      provider: null,
      transport: null,
      voice: null,
      voiceProfile: null,
      micMetrics: { ...EMPTY_MIC_METRICS },
      turn: 0,
      assistantText: "",
      ownerText: "",
      lastError: null,
      lastErrorLines: [],
      contract: null,
      contractNotice: null,
      requestLog: [],
      latency: {},
      toolsRunning: [],
      sidebandLog: [],
      hesitation: { held: 0, resumed_within_hold: 0 },
      online: deps.network.online,
      eventsAccepted: 0,
      eventsPending: 0,
      legs: 1,
    };
    this.portUnsubs.push(
      deps.api.onRequest((entry) => {
        this.patch({ requestLog: [...this.snapshot.requestLog, entry].slice(-REQUEST_LOG_MAX) });
      }),
    );
  }

  // ------------------------------------------------------------- contract

  /**
   * Ask the server which contract version it speaks (ADR-0045). Cached for
   * the page load once the server answered (200 or 404); a network failure
   * or a 401 is not cached, so the next Connect asks again. Never throws.
   */
  probeContract(): Promise<ResolvedContract | null> {
    if (this.contract) return Promise.resolve(this.contract);
    if (this.contractProbe) return this.contractProbe;
    this.contractProbe = this.deps.api
      .contract()
      .then((probe) => {
        const resolved = resolveContract(probe);
        if (resolved && resolved.known) this.contract = resolved;
        this.patch({
          contract: resolved
            ? { version: resolved.version, source: resolved.source, known: resolved.known, createFields: resolved.createFields }
            : { version: null, source: "unauthorized", known: false, createFields: [] },
          contractNotice: probe.outcome === "unknown" ? unknownVersionNotice(probe.reason) : this.snapshot.contractNotice,
        });
        this.log(`contract.${probe.outcome}${resolved ? `:v${resolved.version}` : ""}`);
        return resolved;
      })
      .finally(() => {
        this.contractProbe = null;
      });
    return this.contractProbe;
  }

  // ------------------------------------------------------------ observers

  subscribe(listener: (snapshot: ControllerSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => {
      this.listeners.delete(listener);
    };
  }

  getSnapshot(): ControllerSnapshot {
    return this.snapshot;
  }

  private patch(partial: Partial<ControllerSnapshot>): void {
    this.snapshot = {
      ...this.snapshot,
      ...partial,
      hesitation: { ...this.snapshot.hesitation, ...this.guard.stats(), ...partial.hesitation },
      eventsAccepted: this.reporter?.accepted ?? this.snapshot.eventsAccepted,
      eventsPending: this.reporter?.pending ?? 0,
    };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  private log(op: string): void {
    this.deps.log?.(op);
  }

  private now(): number {
    return Math.max(0, this.deps.now() - this.t0);
  }

  private setState(state: VoiceUiState, fsm?: FsmState): void {
    this.patch({ state });
    if (fsm && this.reporter) {
      this.reporter.report({ kind: "state", turn: this.snapshot.turn, payload: { state: fsm } });
    }
  }

  // ------------------------------------------------------------ lifecycle

  /** Create the session, open the microphone and the media leg. */
  async connect(options: { deviceId?: string; language?: string; voice?: string } = {}): Promise<void> {
    if (this.snapshot.state !== "idle" && this.snapshot.state !== "closed" && this.snapshot.state !== "error") {
      return;
    }
    this.closing = false;
    this.t0 = this.deps.now();
    this.metrics = { false_starts: 0, false_barge_ins: 0, false_turns: 0 };
    this.turnJudgement = null;
    this.lastCalibration = null;
    this.patch({
      state: "creating",
      lastError: null,
      lastErrorLines: [],
      contractNotice: null,
      assistantText: "",
      ownerText: "",
      micMetrics: { ...EMPTY_MIC_METRICS },
    });
    // ADR-0045: honour the contract version of the server we are talking to.
    const contract = await this.probeContract();
    if (!contract) {
      this.fail("Oturum açık değil: Cloud Core kimlik doğrulaması gerekiyor; yeniden giriş yapın.");
      return;
    }
    const wanted: Record<string, unknown> = {
      client_kind: "web",
      language: options.language,
      ...(options.voice ? { voice: options.voice } : {}),
    };
    const checked = validateCreateBody(wanted, contract.version, contract.createSession);
    if (checked.problems.length > 0) {
      // The server would answer 422; say why before sending anything.
      this.fail(`Oturum isteği sözleşmeye (v${contract.version}) uymuyor`, [problemsNotice(checked.problems)]);
      return;
    }
    const dropped = droppedFieldsNotice(contract.version, checked.dropped);
    if (dropped) {
      this.log(`contract.dropped:${checked.dropped.join(",")}`);
      this.patch({ contractNotice: [this.snapshot.contractNotice, dropped].filter(Boolean).join(" ") });
    }
    let payload: SessionLegPayload;
    try {
      payload = await this.deps.api.create(checked.body);
    } catch (error) {
      this.fail(`Oturum oluşturulamadı: ${describe(error)}`, linesOf(error));
      return;
    }
    this.sessionId = payload.session_id;
    this.reporter = new EventReporter(
      (events) => {
        if (!this.sessionId) return Promise.reject(new Error("no session"));
        return this.deps.api.events(this.sessionId, events);
      },
      () => this.now(),
      { flushIntervalMs: this.deps.flushIntervalMs ?? 250, scheduler: this.scheduler },
    );
    this.reporter.onSideband((frame) => this.onSideband(frame));
    this.reporter.onFailure((error) => this.onReportFailure(error));
    this.patch({
      sessionId: payload.session_id,
      provider: payload.provider,
      transport: payload.transport,
      voice: payload.voice ?? null,
      voiceProfile: payload.voice_profile ?? null,
      state: "connecting",
    });
    this.portUnsubs.push(
      this.deps.network.onChange((online) => this.onNetworkChange(online)),
    );
    try {
      await this.openLeg(payload, options.deviceId);
    } catch (error) {
      this.fail(`Medya bağlantısı kurulamadı: ${describe(error)}`, linesOf(error));
      await this.closeServerSession("connect_failed");
      return;
    }
    this.setState("listening", "LISTENING");
  }

  /** Open (or re-open) the media leg with a fresh credential. */
  private async openLeg(payload: SessionLegPayload, deviceId?: string): Promise<void> {
    this.descriptor = resolveTransportDescriptor(payload);
    const credential: SessionCredential = payload.credential;
    let microphone: MediaStream | undefined;
    if (this.deps.microphone) {
      microphone = this.deps.microphone.stream ?? (await this.deps.microphone.open(deviceId));
      if (this.deps.localSpeech) {
        this.deps.localSpeech.stop();
        this.deps.localSpeech.start(microphone);
        this.wireLocalSpeech(this.deps.localSpeech);
      }
    }
    const transport = this.deps.transportFactory(this.descriptor);
    this.transport = transport;
    this.transportUnsubs.push(
      transport.onEvent((event) => this.onTransportEvent(event)),
      transport.onAudio((output) => this.deps.playback.attach(output)),
    );
    this.portUnsubs.push(this.deps.playback.onActivity((at) => this.onAudioActivity(at - this.t0)));
    await transport.connect(this.descriptor, credential, {
      microphone,
      now: () => this.now(),
    });
    this.log("transport.connected");
  }

  /** Subscribe once per leg (a reattach re-wires instead of stacking sinks). */
  private wireLocalSpeech(detector: SpeechDetector): void {
    for (const unsub of this.localSpeechUnsubs) unsub();
    this.localSpeechUnsubs = [
      detector.onSpeechStart((at) => this.onOwnerSpeechStart(at - this.t0, "local")),
      detector.onSpeechEnd((at) => this.onLocalSpeechEnd(at - this.t0)),
    ];
    if (detector.onCalibration) {
      this.localSpeechUnsubs.push(detector.onCalibration((c) => this.onCalibration(c)));
    }
  }

  /** Swap the microphone without dropping the session. */
  async switchMicrophone(deviceId: string): Promise<void> {
    if (!this.deps.microphone || !this.transport) return;
    this.deps.microphone.close();
    const stream = await this.deps.microphone.open(deviceId);
    const [track] = stream.getAudioTracks();
    if (track) this.transport.sendAudio({ kind: "track", track });
    this.deps.localSpeech?.stop();
    this.deps.localSpeech?.start(stream);
  }

  /** Close the media leg and the server session; report the summary first. */
  async disconnect(reason = "client_closed"): Promise<void> {
    if (this.closing || !this.sessionId) return;
    this.closing = true;
    this.clearEotTimer();
    this.clearReattachTimer();
    this.clearLocalGraceTimer();
    if (this.reporter) {
      this.judgeOpenTurn();
      if (this.deps.localSpeech) this.reportMicMetrics({ session_end: 1 });
      const summary = this.recentLines.join(" | ").slice(0, MAX_SUMMARY_CHARS);
      if (summary) this.reporter.report({ kind: "summary", text: summary });
      this.reporter.report({ kind: "state", turn: this.snapshot.turn, payload: { state: "CLOSED" } });
      await this.reporter.flush();
      this.reporter.dispose();
    }
    this.teardownLeg("client_closed");
    this.deps.microphone?.close();
    this.deps.localSpeech?.stop();
    await this.closeServerSession(reason);
    this.patch({ state: "closed", toolsRunning: [] });
  }

  private teardownLeg(reason: string): void {
    for (const unsub of this.transportUnsubs) unsub();
    this.transportUnsubs = [];
    this.deps.playback.stop();
    this.transport?.close(reason);
    this.transport = null;
    this.responseActive = false;
    this.responseAudible = false;
  }

  private async closeServerSession(reason: string): Promise<void> {
    if (!this.sessionId) return;
    try {
      await this.deps.api.close(this.sessionId, reason);
    } catch {
      /* already closed/expired server-side; nothing else to do */
    }
  }

  private fail(message: string, lines: string[] = []): void {
    this.patch({ state: "error", lastError: message, lastErrorLines: lines });
    this.reporter?.report({ kind: "error", payload: { error_class: "client_error", message } });
  }

  dispose(): void {
    for (const unsub of this.portUnsubs) unsub();
    this.portUnsubs = [];
    for (const unsub of this.localSpeechUnsubs) unsub();
    this.localSpeechUnsubs = [];
    this.clearLocalGraceTimer();
    this.teardownLeg("dispose");
    this.reporter?.dispose();
    this.listeners.clear();
  }

  // ---------------------------------------------------------- transport

  private onTransportEvent(event: TransportEvent): void {
    if (this.closing) return;
    switch (event.type) {
      case "connected":
        if (this.snapshot.state === "connecting" || this.snapshot.state === "reconnecting") {
          this.setState("listening");
        }
        return;
      case "speech_started":
        this.onOwnerSpeechStart(event.at, "provider");
        return;
      case "speech_stopped":
        this.onOwnerSpeechStopped(event.at);
        return;
      case "owner_transcript":
        this.onOwnerTranscript(event.text, event.final);
        return;
      case "response_started":
        this.onResponseStarted(event.at);
        return;
      case "response_text":
        this.onResponseText(event.text, event.final);
        return;
      case "audio_started":
        this.onAudioActivity(event.at);
        return;
      case "audio_stopped":
        this.responseAudible = false;
        return;
      case "response_done":
        this.onResponseDone(event.at);
        return;
      case "response_cancelled":
        this.responseActive = false;
        this.responseAudible = false;
        this.awaitingFirstAudio = false;
        if (this.snapshot.state === "interrupted") this.setState("listening", "LISTENING");
        return;
      case "tool_call":
        void this.relayToolCall(event.callId, event.name, event.arguments, event.at);
        return;
      case "error":
        this.patch({ lastError: event.message });
        this.reporter?.report({
          kind: "error",
          turn: this.snapshot.turn,
          payload: { error_class: event.code ?? "provider_error" },
        });
        return;
      case "disconnected":
        this.onNetworkLost(event.reason, event.at);
        return;
    }
  }

  // ------------------------------------------------------ owner speech

  private onOwnerSpeechStart(at: number, source: "local" | "provider"): void {
    if (this.closing || !this.reporter) return;
    if (this.ownerSpeaking) {
      // A second signal for the same speech (local detector first, provider
      // VAD later): the provider hearing it is the closest thing a WebRTC
      // client has to "first uplink packet acknowledged".
      if (source === "provider" && !this.uplinkReported && this.ownerSpeechStartedAt !== null) {
        this.uplinkReported = true;
        this.clearLocalGraceTimer();
        if (this.turnJudgement) this.turnJudgement.providerConfirmed = true;
        this.reporter.report({
          kind: "uplink_first_packet",
          t_ms: at,
          turn: this.snapshot.turn,
          payload: { basis: "provider_speech_started" },
        });
        this.patch({
          latency: {
            ...this.snapshot.latency,
            mic_to_uplink_ms: { value: Math.max(0, at - this.ownerSpeechStartedAt), at },
          },
        });
      }
      return;
    }
    const resumed = this.guard.speechStarted(at);
    this.clearEotTimer();
    if (resumed) {
      // Continuation of the same turn: no new turn, no mic_speech_start.
      this.ownerSpeaking = true;
      this.patch({ hesitation: { ...this.guard.stats(), last: "resumed_within_hold" } });
      if (this.prematureResponse && (this.responseActive || this.deps.playback.playing)) {
        this.bargeIn(at, source, { hesitation_resume: true });
      }
      this.prematureResponse = false;
      return;
    }
    this.prematureResponse = false;
    if (this.responseActive || this.deps.playback.playing) {
      this.bargeIn(at, source);
    }
    this.judgeOpenTurn();
    this.ownerSpeaking = true;
    this.ownerSpeechStartedAt = at;
    this.ownerSpeechSource = source;
    this.uplinkReported = source === "provider";
    this.ownerTranscriptTail = "";
    const turn = this.snapshot.turn + 1;
    this.turnJudgement = { turn, providerConfirmed: source === "provider", hadTranscript: false };
    this.patch({ turn, ownerText: "" });
    this.reporter.report({ kind: "mic_speech_start", t_ms: at, turn, payload: { source } });
    if (this.snapshot.state !== "tool_running" && this.snapshot.state !== "interrupted") {
      this.setState("listening");
    }
  }

  private bargeIn(at: number, source: "local" | "provider", extra: Record<string, unknown> = {}): void {
    if (!this.reporter || this.bargedResponse) return;
    this.bargedResponse = true;
    // 1. stop local playback FIRST — the latency-critical action
    const stoppedAt = this.deps.playback.stop();
    // 2. cancel the provider's in-flight response
    this.transport?.cancelResponse();
    this.log("transport.cancel");
    // 3. report with the client-measured latency
    const stopMs = Math.max(0, Math.round(stoppedAt - this.t0 - at));
    const turn = this.snapshot.turn + 1;
    this.reporter.report({
      kind: "barge_in_start",
      t_ms: at,
      turn,
      payload: { playback_stopped_ms: stopMs, source, ...extra },
    });
    this.reporter.report({ kind: "playback_stopped", t_ms: at + stopMs, turn });
    this.log("report.barge_in");
    this.responseActive = false;
    this.responseAudible = false;
    this.awaitingFirstAudio = false;
    this.patch({
      latency: { ...this.snapshot.latency, barge_in_to_stop_ms: { value: stopMs, at } },
    });
    this.setState("interrupted", "INTERRUPTED");
  }

  /**
   * The local gate closed. Real speech is ended by the provider's
   * `speech_stopped`; this only matters when the provider never heard speech
   * for a locally opened turn — after a short grace it was noise.
   */
  private onLocalSpeechEnd(at: number): void {
    if (this.closing || !this.ownerSpeaking) return;
    if (this.ownerSpeechSource !== "local" || this.uplinkReported) return;
    this.clearLocalGraceTimer();
    const grace = this.deps.localSpeechGraceMs ?? DEFAULT_LOCAL_GRACE_MS;
    this.localGraceTimer = this.scheduler.setTimeout(() => {
      this.localGraceTimer = null;
      this.onLocalFalseStart(at);
    }, grace);
  }

  private onLocalFalseStart(at: number): void {
    if (this.closing || !this.ownerSpeaking) return;
    if (this.ownerSpeechSource !== "local" || this.uplinkReported) return;
    this.ownerSpeaking = false;
    this.ownerSpeechSource = null;
    this.ownerSpeechStartedAt = null;
    this.turnJudgement = null; // counted as a false start, not a false turn
    const wasBargeIn = this.snapshot.state === "interrupted";
    this.metrics.false_starts += 1;
    if (wasBargeIn) this.metrics.false_barge_ins += 1;
    this.log(wasBargeIn ? "gate.false_barge_in" : "gate.false_start");
    this.reportMicMetrics({ false_start: 1, false_barge_in: wasBargeIn ? 1 : 0 }, at);
    if (wasBargeIn) this.setState("listening", "LISTENING");
  }

  private clearLocalGraceTimer(): void {
    if (this.localGraceTimer !== null) {
      this.scheduler.clearTimeout(this.localGraceTimer);
      this.localGraceTimer = null;
    }
  }

  /** A provider-confirmed turn that never produced a transcript was noise. */
  private judgeOpenTurn(): void {
    const judgement = this.turnJudgement;
    this.turnJudgement = null;
    if (!judgement || !judgement.providerConfirmed || judgement.hadTranscript) return;
    this.metrics.false_turns += 1;
    this.log(`gate.false_turn:${judgement.turn}`);
    this.reportMicMetrics({ false_turn: 1 });
  }

  private onCalibration(calibration: SpeechDetectorCalibration): void {
    if (this.closing || !this.reporter) return;
    this.lastCalibration = calibration;
    this.reporter.report({
      kind: "state",
      turn: this.snapshot.turn,
      payload: numbersOnly({ mic_calibration: 1, ...calibration }),
    });
    this.log("report.mic_calibration");
    this.refreshMicMetrics();
  }

  private currentMicMetrics(): MicMetrics {
    const stats = this.deps.localSpeech?.stats?.();
    return {
      ...EMPTY_MIC_METRICS,
      ...(stats
        ? {
            gate_opens: stats.gate_opens,
            gated_out: stats.gated_out,
            click_rejects: stats.click_rejects,
            calibrations: stats.calibrations,
          }
        : {}),
      false_starts: this.metrics.false_starts,
      false_barge_ins: this.metrics.false_barge_ins,
      false_turns: this.metrics.false_turns,
      noise_floor_db: this.lastCalibration?.noise_floor_db ?? null,
      env: this.lastCalibration?.env ?? null,
    };
  }

  private refreshMicMetrics(): void {
    this.patch({ micMetrics: this.currentMicMetrics() });
  }

  /** `state` event, numbers only (ADR-0044 §7); `extra` marks what just happened. */
  private reportMicMetrics(extra: Record<string, number> = {}, tMs?: number): void {
    this.refreshMicMetrics();
    if (!this.reporter) return;
    const stats = this.deps.localSpeech?.stats?.() ?? {};
    this.reporter.report({
      kind: "state",
      t_ms: tMs,
      turn: this.snapshot.turn,
      payload: numbersOnly({
        mic_metrics: 1,
        ...stats,
        false_starts: this.metrics.false_starts,
        false_barge_ins: this.metrics.false_barge_ins,
        false_turns: this.metrics.false_turns,
        ...extra,
      }),
    });
    this.log("report.mic_metrics");
  }

  private onOwnerSpeechStopped(at: number): void {
    if (!this.ownerSpeaking || !this.reporter) return;
    this.clearLocalGraceTimer();
    this.ownerSpeaking = false;
    const decision = this.guard.decide(this.ownerTranscriptTail, at);
    this.patch({ hesitation: { ...this.guard.stats(), last: decision.reason } });
    this.clearEotTimer();
    const turn = this.snapshot.turn;
    const commit = (): void => {
      this.eotTimer = null;
      this.guard.expire();
      this.lastEotAt = at;
      this.reporter?.report({
        kind: "end_of_turn",
        t_ms: at,
        turn,
        payload: { hold_ms: decision.holdMs, hesitation: decision.reason },
      });
      this.log("report.end_of_turn");
      if (this.snapshot.state === "interrupted") this.setState("listening", "LISTENING");
    };
    if (decision.holdMs === 0) {
      commit();
    } else {
      this.eotTimer = this.scheduler.setTimeout(commit, decision.holdMs);
    }
  }

  private clearEotTimer(): void {
    if (this.eotTimer !== null) {
      this.scheduler.clearTimeout(this.eotTimer);
      this.eotTimer = null;
    }
  }

  private onOwnerTranscript(text: string, final: boolean): void {
    if (this.turnJudgement && text.trim()) this.turnJudgement.hadTranscript = true;
    if (final) {
      this.ownerTranscriptTail = text;
      this.patch({ ownerText: text });
      this.remember(`Sahip: ${text}`);
      // Cloud Core resolves intents from the transcript (spec §5); the client
      // only reports it.
      this.reporter?.report({ kind: "utterance", turn: this.snapshot.turn, text });
    } else {
      this.ownerTranscriptTail += text;
      this.patch({ ownerText: this.ownerTranscriptTail });
    }
  }

  // ---------------------------------------------------- assistant speech

  private onResponseStarted(at: number): void {
    this.responseActive = true;
    this.responseAudible = false;
    this.awaitingFirstAudio = true;
    this.bargedResponse = false;
    this.assistantBuffer = "";
    this.prematureResponse = this.guard.isHolding;
    this.deps.playback.arm();
    this.log(`response.started@${Math.round(at)}`);
    if (this.snapshot.state !== "tool_running") this.setState("speaking", "ASSISTANT_SPEAKING");
  }

  private onAudioActivity(at: number): void {
    if (!this.reporter || !this.awaitingFirstAudio) return;
    this.awaitingFirstAudio = false;
    this.responseAudible = true;
    const turn = this.snapshot.turn;
    this.reporter.report({ kind: "first_audio", t_ms: at, turn });
    const latency = { ...this.snapshot.latency };
    if (this.lastEotAt !== null && at >= this.lastEotAt) {
      latency.eot_to_first_audio_ms = { value: Math.round(at - this.lastEotAt), at };
      this.lastEotAt = null;
    }
    if (this.pendingPreamble) {
      this.reporter.report({
        kind: "preamble_audio_start",
        t_ms: at,
        turn,
        payload: { call_id: this.pendingPreamble.callId },
      });
      latency.tool_preamble_ms = { value: Math.round(at - this.pendingPreamble.at), at };
      this.pendingPreamble = null;
    }
    if (this.pendingResume) {
      this.reporter.report({
        kind: "speech_resumed",
        t_ms: at,
        turn,
        payload: { call_id: this.pendingResume.callId },
      });
      latency.tool_done_to_speech_ms = { value: Math.round(at - this.pendingResume.at), at };
      this.pendingResume = null;
    }
    this.patch({ latency });
  }

  private onResponseText(text: string, final: boolean): void {
    this.assistantBuffer = final ? text : this.assistantBuffer + text;
    this.patch({ assistantText: this.assistantBuffer });
    if (final && text) this.remember(`Asistan: ${text}`);
  }

  private onResponseDone(at: number): void {
    this.responseActive = false;
    this.awaitingFirstAudio = false;
    this.reporter?.report({ kind: "response_done", t_ms: at, turn: this.snapshot.turn });
    if (this.longRunning.size > 0) {
      this.setState("tool_running", "TOOL_RUNNING");
    } else if (this.snapshot.state === "speaking") {
      this.setState("listening", "LISTENING");
    }
  }

  // -------------------------------------------------------------- tools

  /** Relay exactly once per call_id, however often the provider repeats it. */
  private relayToolCall(
    callId: string,
    name: string,
    args: Record<string, unknown>,
    at: number,
  ): Promise<void> {
    const existing = this.relayed.get(callId);
    if (existing) {
      this.log(`tool.duplicate:${callId}`);
      return existing;
    }
    const promise = this.doRelayToolCall(callId, name, args, at);
    this.relayed.set(callId, promise);
    return promise;
  }

  private async doRelayToolCall(
    callId: string,
    name: string,
    args: Record<string, unknown>,
    at: number,
  ): Promise<void> {
    if (!this.reporter || !this.sessionId) return;
    this.reporter.report({ kind: "tool_call", t_ms: at, turn: this.snapshot.turn, payload: { call_id: callId, name } });
    this.log(`tool.relay:${callId}`);
    this.patch({ toolsRunning: [...this.snapshot.toolsRunning, name] });
    this.setState("tool_running", "TOOL_RUNNING");
    let response: ToolCallResponse;
    try {
      response = await this.relayWithReattach(callId, name, args);
    } catch (error) {
      this.patch({ toolsRunning: this.snapshot.toolsRunning.filter((n) => n !== name) });
      this.reporter.report({ kind: "tool_done", turn: this.snapshot.turn, payload: { call_id: callId, name, status: "failed" } });
      this.transport?.submitToolResult(callId, {
        status: "failed",
        error: { error_class: "dependency_unavailable", message: describe(error) },
      });
      this.log(`tool.submit:${callId}`);
      this.setState("listening", "LISTENING");
      return;
    }
    if (response.status === "running") {
      this.longRunning.set(callId, { name, startedAt: this.now(), preamble: response.preamble });
      this.pendingPreamble = { callId, at: this.now() };
      this.transport?.submitToolResult(callId, {
        status: "running",
        preamble: response.preamble ?? null,
        result: response.result ?? null,
      });
      this.log(`tool.submit:${callId}`);
      return;
    }
    this.patch({ toolsRunning: this.snapshot.toolsRunning.filter((n) => n !== name) });
    this.reporter.report({
      kind: "tool_done",
      turn: this.snapshot.turn,
      payload: { call_id: callId, name, status: response.status, replayed: response.replayed },
    });
    this.transport?.submitToolResult(
      callId,
      response.status === "succeeded"
        ? (response.result ?? {})
        : { status: "failed", error: response.error ?? {} },
    );
    this.log(`tool.submit:${callId}`);
    if (this.longRunning.size === 0) this.setState("listening", "LISTENING");
  }

  private async relayWithReattach(
    callId: string,
    name: string,
    args: Record<string, unknown>,
  ): Promise<ToolCallResponse> {
    if (!this.sessionId) throw new Error("no session");
    try {
      return await this.deps.api.toolCall(this.sessionId, { call_id: callId, name, arguments: args });
    } catch (error) {
      if (error instanceof VoiceApiError && error.legMismatch) {
        // Another leg took the session and we did not notice yet: take it back.
        await this.reattach();
        return await this.deps.api.toolCall(this.sessionId, { call_id: callId, name, arguments: args });
      }
      throw error;
    }
  }

  // ----------------------------------------------------------- sideband

  private onSideband(frame: SidebandFrame): void {
    if (this.closing) return;
    const payload = frame.payload ?? {};
    switch (frame.event) {
      case "tool_completed": {
        const callId = String(payload.call_id ?? "");
        const entry = this.longRunning.get(callId);
        const name = String(payload.name ?? entry?.name ?? "");
        if (!entry) {
          this.log(`sideband.tool_completed.unknown:${callId}`);
          this.pushSideband(`tool_completed (bilinmeyen çağrı ${callId})`);
          return;
        }
        this.longRunning.delete(callId);
        const at = this.now();
        this.reporter?.report({
          kind: "tool_done",
          t_ms: at,
          turn: this.snapshot.turn,
          payload: { call_id: callId, name, status: String(payload.status ?? "") },
        });
        this.pendingResume = { callId, at };
        this.transport?.notifyToolCompleted(
          callId,
          name,
          payload.status === "succeeded"
            ? (payload.result ?? {})
            : { status: "failed", error: payload.error ?? {} },
        );
        this.log(`tool.completed:${callId}`);
        this.patch({ toolsRunning: this.snapshot.toolsRunning.filter((n) => n !== name) });
        this.pushSideband(`${name} tamamlandı (${String(payload.status ?? "")})`);
        if (this.longRunning.size === 0 && this.snapshot.state === "tool_running") {
          this.setState("listening", "LISTENING");
        }
        return;
      }
      case "say": {
        const text = String(payload.text ?? "");
        if (text) {
          this.transport?.say(text);
          this.log("transport.say");
        }
        this.pushSideband(`söyle: ${text}`);
        return;
      }
      case "plan_changed":
        this.pushSideband(`plan değişti: ${String(payload.scope ?? "")} (rev ${String(payload.revision ?? "?")})`);
        return;
      case "tool_progress":
        this.pushSideband(`ilerleme: ${JSON.stringify(payload).slice(0, 120)}`);
        return;
      case "narration_cursor":
        this.pushSideband(`anlatım imleci: ${String(payload.state ?? "")}`);
        return;
      case "leg_closed":
        this.pushSideband(`bu bağlantı devralındı (${String(payload.new_client_kind ?? "")})`);
        this.closing = true;
        this.clearEotTimer();
        this.clearReattachTimer();
        this.teardownLeg("attached_elsewhere");
        this.reporter?.dispose();
        this.patch({ state: "closed", toolsRunning: [] });
        return;
    }
  }

  private pushSideband(line: string): void {
    this.patch({ sidebandLog: [...this.snapshot.sidebandLog, line].slice(-SIDEBAND_LOG_MAX) });
  }

  private remember(line: string): void {
    this.recentLines = [...this.recentLines, line.slice(0, 200)].slice(-10);
  }

  // ------------------------------------------------------------ network

  private onNetworkChange(online: boolean): void {
    this.patch({ online });
    if (this.closing) return;
    if (!online) {
      this.onNetworkLost("offline", this.now());
    } else if (this.snapshot.state === "reconnecting") {
      this.clearReattachTimer();
      void this.reattachLoop();
    }
  }

  private onNetworkLost(reason: string, at: number): void {
    if (this.closing || !this.reporter) return;
    if (this.snapshot.state === "reconnecting") return;
    this.reporter.report({ kind: "network_lost", t_ms: at, turn: this.snapshot.turn, payload: { reason } });
    this.log(`network.lost:${reason}`);
    this.clearEotTimer();
    this.clearLocalGraceTimer();
    this.teardownLeg(reason);
    this.ownerSpeaking = false;
    this.patch({ state: "reconnecting" });
    if (this.deps.network.online) void this.reattachLoop();
  }

  private async reattachLoop(): Promise<void> {
    const policy = this.deps.reattach ?? { maxAttempts: 5, baseDelayMs: 500 };
    try {
      await this.reattach();
      this.reattachAttempts = 0;
      this.setState("listening", "LISTENING");
    } catch (error) {
      if (error instanceof VoiceApiError && error.gone) {
        this.fail("Oturum sunucuda kapanmış; yeniden bağlanılamaz.");
        this.patch({ state: "closed" });
        return;
      }
      this.reattachAttempts += 1;
      if (this.reattachAttempts >= policy.maxAttempts) {
        this.fail(`Yeniden bağlanılamadı: ${describe(error)}`, linesOf(error));
        return;
      }
      const delay = policy.baseDelayMs * 2 ** (this.reattachAttempts - 1);
      this.reattachTimer = this.scheduler.setTimeout(() => {
        this.reattachTimer = null;
        if (this.deps.network.online && !this.closing) void this.reattachLoop();
      }, delay);
    }
  }

  /** `POST .../attach`: fresh credential, replayed sideband, new media leg. */
  private async reattach(): Promise<void> {
    if (!this.sessionId || !this.reporter) throw new Error("no session");
    const payload = await this.deps.api.attach(this.sessionId, { transport: this.descriptor?.kind });
    this.log("api.attach");
    this.teardownLeg("reattach");
    await this.openLeg(payload);
    this.patch({ legs: payload.state && typeof payload.state === "object" ? payload.state.legs : this.snapshot.legs });
    for (const frame of payload.pending_sideband ?? []) this.onSideband(frame);
    this.reporter.report({ kind: "network_restored", turn: this.snapshot.turn });
    this.log("network.restored");
    await this.reporter.flush();
  }

  private clearReattachTimer(): void {
    if (this.reattachTimer !== null) {
      this.scheduler.clearTimeout(this.reattachTimer);
      this.reattachTimer = null;
    }
  }

  private onReportFailure(error: unknown): void {
    if (this.closing) return;
    if (error instanceof VoiceApiError) {
      if (error.gone) {
        this.fail("Oturum sunucuda kapanmış.");
        this.teardownLeg("gone");
        this.patch({ state: "closed" });
        return;
      }
      if (error.legMismatch && this.snapshot.state !== "reconnecting") {
        this.onNetworkLost("leg_mismatch", this.now());
        return;
      }
      if (error.status >= 500) return; // keep the batch, retry on the timer
      // 4xx other than the above: the batch itself is bad; report but do not loop.
      this.patch({ lastError: `Olay raporu reddedildi: HTTP ${error.status}` });
      return;
    }
    // Network-level failure: the browser will tell us when it is back.
    if (this.snapshot.state !== "reconnecting") this.onNetworkLost("events_unreachable", this.now());
  }

  /** Test/diagnostic hook: flush queued events now. */
  flushEvents(): Promise<unknown> {
    return this.reporter?.flush() ?? Promise.resolve(null);
  }
}

function describe(error: unknown): string {
  if (error instanceof VoiceApiError) return `HTTP ${error.status}`;
  if (error instanceof Error) return error.message;
  return String(error);
}

/** The server's field/reason lines behind an API error (ADR-0045); empty for anything else. */
function linesOf(error: unknown): string[] {
  return error instanceof VoiceApiError ? error.lines : [];
}
