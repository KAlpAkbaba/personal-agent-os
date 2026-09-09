/**
 * Voice session controller — the browser leg of a ConversationRealtime
 * session (M12 spec §4–§7, track D).
 *
 * Responsibilities, in the order they matter:
 *
 * 1. Barge-in: owner speech while the assistant is audible → STOP LOCAL
 *    PLAYBACK FIRST (synchronous), then cancel the provider's response, then
 *    report `barge_in_start` + `playback_stopped` with the client-measured
 *    stop latency. The order is fixed; a test pins it. ADR-0047 §2: a
 *    confident onset during playback mutes locally BEFORE the turn is
 *    confirmed (reversible), and every barge-in payload carries its split
 *    (detect / stop command / gain-to-zero) as numbers. The provider cancel
 *    goes out only while a provider response is ACTIVE (`provider_cancel: 1`);
 *    owner speech over audio that is merely draining after `response_done`
 *    stops local playback and reports, but cancels nothing (`provider_cancel:
 *    0`) — the provider has nothing to cancel and would answer with an error.
 *    Should such a "no active response" cancel error still arrive, it is
 *    benign: counted, reported as `error_class: cancel_noop`, never shown.
 *    The CLIENT owns interruption (the provider is configured with
 *    `interrupt_response = false`) through a two-stage policy (interruption.ts):
 *    a control phrase in any owner transcript stops at once (fast lane); any
 *    other speech onset only mutes reversibly and becomes a potential barge-in
 *    that must earn its cancel — stable onset, near-field level from the local
 *    gate, a plausible word — inside a short window, else playback resumes.
 * 2. End-of-turn with the Turkish hesitation guard: the provider's
 *    `speech_stopped` opens a hold whose length depends on the transcript
 *    tail; speech inside the hold is a continuation, not a new turn, and a
 *    response the provider started prematurely is cancelled.
 * 3. Tool relay: provider tool call → `POST .../tool-calls` (once per call_id,
 *    however many times the provider repeats it) → result back to the
 *    provider; long-running tools get the Turkish preamble now and the final
 *    outcome when the `tool_completed` sideband frame lands.
 * 4. Every timing/state transition is reported with monotonic timestamps.
 *    ADR-0047 §1/§3: mic→uplink is measured from the transport's own outbound
 *    counters (never inferred from a provider event), every local speech
 *    start is settled by exactly one `uplink_first_packet` or an explicit
 *    unmatched marker, and first audio is decomposed into provider decision,
 *    generation and local playback.
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
import { MAX_EVENT_TEXT_CHARS, MAX_SUMMARY_CHARS } from "./contract";
import { EventReporter, numbersOnly, type Scheduler, realScheduler } from "./events";
import { HesitationGuard, type HesitationGuardConfig } from "./hesitation";
import {
  BARGE_IN_CONFIRM_WINDOW_MS,
  BARGE_IN_STABLE_ONSET_MS,
  FALSE_INTERRUPTION_WINDOW_MS,
  findControlPhrase,
  hasPlausibleWord,
  isNearField,
  strongerLevel,
} from "./interruption";
import type {
  LocalActionPort,
  Microphone,
  NetworkMonitor,
  OnsetLevel,
  Playback,
  SpeechDetector,
  SpeechDetectorCalibration,
  SpeechDetectorCalibrationAttempt,
  SpeechStartDetail,
} from "./ports";
import {
  type ResolvedContract,
  droppedFieldsNotice,
  problemsNotice,
  resolveContract,
  unknownVersionNotice,
  validateCreateBody,
} from "./session-contract";
import { msOrOmit, UplinkProbe } from "./timing";
import { cloudToolName } from "./tool-names";
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
 * (ADR-0044 §7, ADR-0047). Numbers only; reported through `state` events.
 */
export type MicMetrics = {
  /** local gate opened, the provider never heard speech for it */
  false_starts: number;
  /** a false start that had already stopped the assistant (barge-in on noise) */
  false_barge_ins: number;
  /** provider-confirmed turns that produced no transcript */
  false_turns: number;
  /** provider-confirmed owner turns (what the profile learns against) */
  confirmed_turns: number;
  /** reversible playback mutes on a confident onset, and how many were reverted */
  early_mutes: number;
  early_mute_reverts: number;
  gate_opens: number;
  gated_out: number;
  click_rejects: number;
  calibrations: number;
  noise_floor_db: number | null;
  /** 1 quiet, 2 normal, 3 noisy, 4 very noisy; null before calibration (never 0) */
  env: number | null;
  /** measured residual of the assistant's playback in the microphone; null until measured */
  echo_residual_db: number | null;
  /** provider "no active response" answers to a cancel — benign, never an error state */
  cancel_noop_errors: number;
  // Two-stage interruption (interruption.ts), the funnel from onset to cancel:
  /** every owner-speech onset the client processed (local gate or provider VAD), any state */
  speech_detected: number;
  /** onsets while the assistant was speaking: muted reversibly, evidence awaited */
  potential_barge_in: number;
  /** potentials that earned the cancel (stable, near-field, a plausible word) */
  accepted_owner_interruption: number;
  /** potentials whose window elapsed (or ended as a burst): mute reverted, nothing cancelled */
  rejected_background_speech: number;
  /** control phrases ("dur", "bekle", …) that stopped the assistant through the fast lane */
  explicit_stop_command: number;
  /** accepted interruptions followed by no final owner utterance within the watch window */
  false_interruption: number;
};

/** The last measured decomposition of each latency (ADR-0047), for the diagnostics view. */
export type LatencyDetail = {
  uplink?: { basis: number; rtp_ms?: number; provider_ms?: number; gate_ms?: number; capture_lag_ms?: number; pre_roll_ms?: number };
  barge_in?: {
    playback_stopped_ms: number;
    detect_ms?: number;
    pre_roll_ms?: number;
    stop_command_ms: number;
    gain_zero_ms?: number;
    output_latency_ms?: number;
    early_mute: number;
    audible: number;
    anomaly: number;
    /** 1 when a provider response was active and cancelled; 0 when only draining audio was stopped */
    provider_cancel: number;
    /** which lane stopped the assistant: 1 explicit control phrase, 2 confirmed conversational barge-in, 0 hesitation resume */
    lane?: number;
  };
  first_audio?: { basis: number; response_created_ms?: number; first_delta_ms?: number; playback_ms?: number };
};

/**
 * M16 §3.2: the last narration cursor Cloud Core pushed on the
 * `narration_cursor` sideband — where "dur" landed, in the plan's own ids
 * (`s2` / `p3` / 0-based sentence index). Shown on /voice during acceptance.
 */
export type NarrationCursorView = {
  state: string;
  action: string | null;
  sectionId: string | null;
  paragraphId: string | null;
  sentenceIndex: number | null;
};

/** Owner-readable position: `s2 ¶p3 · 3. cümle` (the server's sentence index is 0-based). */
export function describeNarrationCursor(cursor: NarrationCursorView): string {
  const parts: string[] = [];
  if (cursor.sectionId) parts.push(cursor.sectionId);
  if (cursor.paragraphId) parts.push(`¶${cursor.paragraphId}`);
  if (cursor.sentenceIndex !== null) parts.push(`${cursor.sentenceIndex + 1}. cümle`);
  return parts.length > 0 ? parts.join(" ") : "konum yok";
}

/** A plan id (`s2`, `p3`) or an action name from a sideband payload; null when absent. */
function idOrNull(value: unknown): string | null {
  if (typeof value === "string" && value) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function narrationCursorFrom(payload: Record<string, unknown>): NarrationCursorView {
  const cursor =
    payload.cursor && typeof payload.cursor === "object" ? (payload.cursor as Record<string, unknown>) : {};
  const index = typeof cursor.sentence_index === "number" && Number.isFinite(cursor.sentence_index)
    ? Math.max(0, Math.round(cursor.sentence_index))
    : null;
  return {
    state: String(payload.state ?? ""),
    action: idOrNull(payload.action),
    sectionId: idOrNull(cursor.section_id),
    paragraphId: idOrNull(cursor.paragraph_id),
    sentenceIndex: index,
  };
}

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
  /** ADR-0047: the components behind the latencies above. */
  latencyDetail: LatencyDetail;
  toolsRunning: string[];
  sidebandLog: string[];
  /** M16 §3.2: the last `narration_cursor` sideband frame; null until one arrives. */
  narrationCursor: NarrationCursorView | null;
  /** ADR-0066: the current (or last) response's speech lifecycle, apart from the output energy. */
  speech: SpeechLifecycle;
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
  /** ADR-0047 §1: the RTP probe's poll interval and bound. */
  uplinkProbe?: { pollMs?: number; maxMs?: number };
  /** ADR-0047 §3: how long after the provider's audio-start to wait for LOCAL audibility before falling back. */
  playbackConfirmMs?: number;
  /**
   * ADR-0047 §4: extra numbers the page knows about the input path (the AGC
   * A/B benchmark from the profile), merged into the `mic_input` read-back
   * report. Numbers only; anything else is dropped.
   */
  inputEvidence?: () => Record<string, unknown>;
  /**
   * M18_ACTION_CONTRACT.md §5.1, §7.2: capabilities that live on THIS device
   * (the camera). Asked first for every tool call; when it answers, the
   * relayed `arguments` carry `observed_after` so the Cloud Core's receipt
   * is built from the real terminal state.
   */
  localActions?: LocalActionPort;
  /** Ordered operation log — tests pin ordering with it. */
  log?: (op: string) => void;
};

type LongRunning = { name: string; startedAt: number; preamble?: string };

/** What the current owner turn knows about its uplink (ADR-0047 §1). */
type UplinkTracking = {
  turn: number;
  /** the gate's decision time (origin of rtp_ms / provider_ms); null when the start came from the provider */
  decisionAt: number | null;
  detail: SpeechStartDetail | null;
  rtp: { rtpAt: number; rtpMs: number } | null;
  probeRunning: boolean;
  providerAt: number | null;
  settled: boolean;
};

type EarlyMute = { at: number; decisionAt: number; gainZeroAt: number | null; outputLatencyMs: number | null; candidateAt: number };

/** The onset that opened the current turn — what a fast-lane stop measures its latency from. */
type TurnOnset = {
  turn: number;
  at: number;
  source: "local" | "provider";
  detail: SpeechStartDetail | undefined;
  onsetKnown: boolean;
};

/**
 * A speech onset while the assistant was speaking (interruption.ts lane B):
 * muted reversibly, waiting inside its window for the evidence that makes it
 * an owner interruption rather than someone else in the room.
 */
type PendingInterruption = TurnOnset & {
  /** the speech has lasted BARGE_IN_STABLE_ONSET_MS (timer or an end event past it) */
  stable: boolean;
  /** an end event (provider stop / local close) has been seen */
  ended: boolean;
  /** the detector can measure onset levels at all (else the rule cannot block) */
  levelKnown: boolean;
  /** the strongest level seen for this onset */
  level: OnsetLevel | null;
  /** a provisional/final transcript carried a non-filler word */
  hadWord: boolean;
  stableTimer: unknown;
  windowTimer: unknown;
};

/** `barge_in_start.lane`: how the assistant was stopped. */
export const LANE_CODE = { hesitation_resume: 0, explicit_stop: 1, conversational: 2 } as const;

/**
 * ADR-0066: the SEMANTIC speech lifecycle of one assistant response, kept
 * apart from the instantaneous output energy. `speaking` (the UI state) runs
 * from the first audible playback until the final audio belonging to that
 * response has actually completed; RMS only sets the pulse's amplitude.
 *
 * - `generating`: `response_started` seen, nothing audible yet
 * - `audible`: first audio observed (local analyser or the provider's mark)
 * - `draining`: generation is complete (`response_done`) but audio is still
 *   playing out of the media track's buffer — the state stays `speaking`
 * - `done`: playback actually ended (the provider's `audio_stopped` for this
 *   response, the silence release, the drain cap, or an interruption)
 */
export type SpeechPhase = "idle" | "generating" | "audible" | "draining" | "done";

/** Why playback was judged finished (`audio_done.basis`). */
export type AudioDoneBasis = "provider" | "silence" | "cap" | "interrupted" | "superseded";

export type SpeechLifecycle = {
  /** the provider's response id, when the wire dialect carries one */
  responseId: string | null;
  phase: SpeechPhase;
  /** session-clock ms; null until observed */
  firstAudioAt: number | null;
  generationDoneAt: number | null;
  playbackDoneAt: number | null;
  /** how the last completed playback was judged finished; null until `done` */
  basis: AudioDoneBasis | null;
};

export const EMPTY_SPEECH: SpeechLifecycle = {
  responseId: null,
  phase: "idle",
  firstAudioAt: null,
  generationDoneAt: null,
  playbackDoneAt: null,
  basis: null,
};

/**
 * ADR-0066 fallback bounds for a drain the provider never closes: analyser
 * silence for `PLAYBACK_RELEASE_MS` after `response_done` (and after the last
 * measured energy) ends `speaking`; `PLAYBACK_DRAIN_MAX_MS` after
 * `response_done` ends it regardless. The analyser is read every
 * `PLAYBACK_POLL_MS` while draining; energy above `PLAYBACK_ENERGY_LEVEL`
 * (the bounded 0..1 `outputLevel`) counts as audio still playing.
 */
export const PLAYBACK_RELEASE_MS = 400;
export const PLAYBACK_DRAIN_MAX_MS = 8000;
export const PLAYBACK_POLL_MS = 50;
export const PLAYBACK_ENERGY_LEVEL = 0.02;

const SIDEBAND_LOG_MAX = 20;
const REQUEST_LOG_MAX = 20;
const DEFAULT_LOCAL_GRACE_MS = 700;
const DEFAULT_PLAYBACK_CONFIRM_MS = 300;

/** A read-back boolean as a payload number; unknown (null) is omitted, never a sentinel. */
const bit = (v: boolean | null): number | undefined => (v === null ? undefined : v ? 1 : 0);

/** Who opened the owner turn, as a payload number: 1 the local gate, 2 the provider's VAD. */
export const SOURCE_CODE = { local: 1, provider: 2 } as const;

/** The hesitation guard's verdict on the transcript tail, as a payload number. */
export const HESITATION_CODE = { none: 0, filler: 1, elongated: 2 } as const;

const EMPTY_MIC_METRICS: MicMetrics = {
  false_starts: 0,
  false_barge_ins: 0,
  false_turns: 0,
  confirmed_turns: 0,
  early_mutes: 0,
  early_mute_reverts: 0,
  gate_opens: 0,
  gated_out: 0,
  click_rejects: 0,
  calibrations: 0,
  noise_floor_db: null,
  env: null,
  echo_residual_db: null,
  cancel_noop_errors: 0,
  speech_detected: 0,
  potential_barge_in: 0,
  accepted_owner_interruption: 0,
  rejected_background_speech: 0,
  explicit_stop_command: 0,
  false_interruption: 0,
};

const EMPTY_COUNTERS = {
  false_starts: 0,
  false_barge_ins: 0,
  false_turns: 0,
  confirmed_turns: 0,
  early_mutes: 0,
  early_mute_reverts: 0,
  cancel_noop_errors: 0,
  speech_detected: 0,
  potential_barge_in: 0,
  accepted_owner_interruption: 0,
  rejected_background_speech: 0,
  explicit_stop_command: 0,
  false_interruption: 0,
};

/**
 * A provider error that only says "there was nothing to cancel" is not a
 * fault: the response had already completed (or been cancelled) by the time
 * the cancel reached it. Matched on the code or the message so the wire
 * dialect needs no special case; anything else is a real provider error.
 */
export function classifyProviderError(code: string | undefined, message: string): "cancel_noop" | null {
  if (code === "response_cancel_not_active") return "cancel_noop";
  if (/no active response/i.test(message)) return "cancel_noop";
  return null;
}

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
  private uplink: UplinkTracking | null = null;
  private probe: UplinkProbe | null = null;
  private localCloseAt: number | null = null;
  private ownerTranscriptTail = "";
  private eotTimer: unknown = null;
  private lastEotAt: number | null = null;
  private prematureResponse = false;
  // noise qualification (ADR-0044 §7, ADR-0047)
  private localGraceTimer: unknown = null;
  private metrics = { ...EMPTY_COUNTERS };
  /** the open turn awaiting its transcript verdict: judged when the next turn starts / at close */
  private turnJudgement: { turn: number; providerConfirmed: boolean; hadTranscript: boolean } | null = null;
  private lastCalibration: SpeechDetectorCalibration | null = null;

  // response tracking
  private responseActive = false;
  private responseAudible = false;
  private awaitingFirstAudio = false;
  private responseStartedAt: number | null = null;
  private audioStartedAt: number | null = null;
  private playbackConfirmTimer: unknown = null;
  private bargedResponse = false;
  private earlyMute: EarlyMute | null = null;
  // two-stage interruption (interruption.ts)
  private pending: PendingInterruption | null = null;
  private turnOnset: TurnOnset | null = null;
  /** the turn whose control phrase already stopped the assistant (a final transcript must not stop twice) */
  private stopHandledTurn: number | null = null;
  private falseInterruptionTimer: unknown = null;
  /** the assistant's transcript for the CURRENT response only; reset on response_started */
  private assistantBuffer = "";
  /** 1-based count of provider responses this leg saw (`spoken.payload.response_seq`) */
  private responseSeq = 0;
  private responseTurn = 0;
  /** a `spoken` event went out for the current response (a cut or a completion, never both) */
  private spokenReported = false;
  // ADR-0066: the speech lifecycle, per response (mirrored on the snapshot as `speech`)
  private speech: SpeechLifecycle = EMPTY_SPEECH;
  /** the provider's `audio_stopped` for the current response arrived before its `response_done` */
  private providerStoppedAt: number | null = null;
  /** last moment the output path measurably carried energy (analyser activity or level) */
  private lastOutputEnergyAt: number | null = null;
  private drainPollTimer: unknown = null;
  private drainCapTimer: unknown = null;

  // tools
  private relayed = new Map<string, Promise<void>>();
  private longRunning = new Map<string, LongRunning>();
  private pendingPreamble: { callId: string; at: number } | null = null;
  private pendingResume: { callId: string; at: number } | null = null;

  // reconnect
  private reattachAttempts = 0;
  private reattachTimer: unknown = null;
  /** The reconnect series currently running, so a second trigger joins it. */
  private reattachRun: Promise<void> | null = null;
  /** The `POST .../attach` currently on the wire, so callers share one request. */
  private attachInFlight: Promise<void> | null = null;

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
      latencyDetail: {},
      toolsRunning: [],
      sidebandLog: [],
      narrationCursor: null,
      speech: EMPTY_SPEECH,
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
    this.metrics = { ...EMPTY_COUNTERS };
    this.turnJudgement = null;
    this.lastCalibration = null;
    this.uplink = null;
    this.earlyMute = null;
    this.localCloseAt = null;
    this.dropPending();
    this.clearFalseInterruptionWatch();
    this.turnOnset = null;
    this.stopHandledTurn = null;
    this.patch({
      state: "creating",
      lastError: null,
      lastErrorLines: [],
      contractNotice: null,
      assistantText: "",
      ownerText: "",
      micMetrics: { ...EMPTY_MIC_METRICS },
      latency: {},
      latencyDetail: {},
      speech: EMPTY_SPEECH,
    });
    this.speech = EMPTY_SPEECH;
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
    // A new session supersedes the old one ATOMICALLY: the previous reporter is ended
    // before the new one exists, so no timer, no in-flight retry and no queued event from
    // the old conversation can outlive it. This used to just overwrite the field, leaving
    // the old reporter's timer running - and because its poster read `this.sessionId` at
    // SEND time, its stale queue would have gone into the NEW session.
    this.reporter?.end("superseded");
    this.sessionId = payload.session_id;
    this.reporter = new EventReporter(
      payload.session_id,
      // The id comes from the reporter, not from `this`: a reporter can only ever post to
      // the session it was built for.
      (sessionId, events) => this.deps.api.events(sessionId, events),
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
    this.reportInputReadBack();
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

  /**
   * ADR-0047 §4: what the browser ACTUALLY applied to the input track, as
   * numbers, plus the page's input evidence (AGC A/B). Verified by read-back
   * — a requested constraint the browser ignored shows up as `not_honoured`.
   */
  private reportInputReadBack(): void {
    const applied = this.deps.microphone?.applied;
    if (!applied || !this.reporter) return;
    this.reporter.report({
      kind: "state",
      turn: this.snapshot.turn,
      payload: numbersOnly({
        mic_input: 1,
        aec: bit(applied.echoCancellation),
        ns: bit(applied.noiseSuppression),
        agc: bit(applied.autoGainControl),
        voice_isolation: bit(applied.voiceIsolation),
        sample_rate: applied.sampleRate ?? undefined,
        channels: applied.channelCount ?? undefined,
        input_latency_ms: applied.inputLatencyMs ?? undefined,
        not_honoured: applied.notHonoured.length,
        ...this.deps.inputEvidence?.(),
      }),
    });
    this.log("report.mic_input");
  }

  /** Subscribe once per leg (a reattach re-wires instead of stacking sinks). */
  private wireLocalSpeech(detector: SpeechDetector): void {
    for (const unsub of this.localSpeechUnsubs) unsub();
    this.localSpeechUnsubs = [
      detector.onSpeechStart((at, detail) => this.onOwnerSpeechStart(at - this.t0, "local", detail ? this.localDetail(detail) : undefined)),
      detector.onSpeechEnd((at) => this.onLocalSpeechEnd(at - this.t0)),
    ];
    if (detector.onCalibration) {
      this.localSpeechUnsubs.push(detector.onCalibration((c) => this.onCalibration(c)));
    }
    if (detector.onEvidence) {
      this.localSpeechUnsubs.push(detector.onEvidence((at, candidateAt) => this.onLocalEvidence(at - this.t0, candidateAt - this.t0)));
    }
    if (detector.onEvidenceLost) {
      this.localSpeechUnsubs.push(detector.onEvidenceLost((at) => this.onLocalEvidenceLost(at - this.t0)));
    }
  }

  /** The detector's accounting is on the device clock; the session clock starts at t0. */
  private localDetail(detail: SpeechStartDetail): SpeechStartDetail {
    return {
      ...detail,
      candidateAt: detail.candidateAt - this.t0,
      decidedAt: detail.decidedAt - this.t0,
    };
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
    this.reportInputReadBack();
  }

  /** Close the media leg and the server session; report the summary first. */
  async disconnect(reason = "client_closed"): Promise<void> {
    if (this.closing || !this.sessionId) return;
    this.closing = true;
    this.clearEotTimer();
    this.clearReattachTimer();
    this.clearLocalGraceTimer();
    this.clearPlaybackConfirmTimer();
    this.dropPending();
    this.clearFalseInterruptionWatch();
    // ADR-0066: a response still playing at close is cut by the close, and
    // its `audio_done` goes out with the final batch, before CLOSED.
    this.finishPlayback(this.now(), "interrupted", false);
    if (this.reporter) {
      this.settleUplink({ session_end: 1 });
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
    this.probe?.cancel();
    this.probe = null;
    this.clearPlaybackConfirmTimer();
    this.dropPending();
    this.clearFalseInterruptionWatch();
    this.deps.playback.stop();
    // The leg is gone: whatever was still playing was cut with it (ADR-0066).
    this.finishPlayback(this.now(), "interrupted", false);
    this.transport?.close(reason);
    this.transport = null;
    this.responseActive = false;
    this.responseAudible = false;
    this.earlyMute = null;
  }

  private async closeServerSession(reason: string): Promise<void> {
    if (!this.sessionId) return;
    try {
      await this.deps.api.close(this.sessionId, reason);
    } catch {
      /* already closed/expired server-side; nothing else to do */
    }
  }

  /**
   * Put the controller in its error state and, where it is safe, tell the server.
   *
   * `viaReporter: false` is the whole point. On 2026-09-09 this method was reached FROM
   * `onReportFailure`, and its last line reported the failure through the very reporter
   * whose POST had just failed - pushing another event onto the queue that had failed to
   * drain, which re-armed the flush timer, which POSTed again, which failed again. One
   * request per flush interval against a session the server had already declared gone,
   * until the rate limiter answered 429 and `/core` said `connect_failed`.
   *
   * The reporter now refuses to be revived on its own account, so this is the second wall
   * rather than the only one - but a failure in the telemetry channel must not be
   * announced through that same channel, and saying so here makes the rule visible where
   * someone would otherwise reintroduce it.
   */
  private fail(message: string, lines: string[] = [], { viaReporter = true } = {}): void {
    this.patch({ state: "error", lastError: message, lastErrorLines: lines });
    if (!viaReporter) return;
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
        this.onPendingSpeechEnded(event.at);
        this.onOwnerSpeechStopped(event.at);
        return;
      case "owner_transcript":
        this.onOwnerTranscript(event.text, event.final, event.at);
        return;
      case "response_started":
        this.onResponseStarted(event.at, event.responseId);
        return;
      case "response_text":
        this.onResponseText(event.text, event.final);
        return;
      case "audio_started":
        this.onProviderAudioStarted(event.at, event.responseId);
        return;
      case "audio_stopped":
        this.onProviderAudioStopped(event.at, event.responseId);
        return;
      case "response_done":
        this.onResponseDone(event.at);
        return;
      case "response_cancelled":
        this.responseActive = false;
        this.responseAudible = false;
        this.awaitingFirstAudio = false;
        this.clearPlaybackConfirmTimer();
        // The response is gone: a potential barge-in has nothing left to decide.
        this.dropPending();
        this.revertEarlyMute(event.at);
        // ADR-0066: a cancelled response is not speaking, whoever cancelled it.
        this.finishPlayback(event.at, "interrupted", false);
        if (this.snapshot.state === "interrupted" || this.snapshot.state === "speaking") {
          this.setState("listening", "LISTENING");
        }
        return;
      case "tool_call":
        void this.relayToolCall(event.callId, event.name, event.arguments, event.at);
        return;
      case "error": {
        if (classifyProviderError(event.code, event.message) === "cancel_noop") {
          // Benign: the cancel found nothing to cancel. Never an error state,
          // never shown; counted and reported so the benchmark can see it.
          this.metrics.cancel_noop_errors += 1;
          this.log("provider.cancel_noop");
          this.refreshMicMetrics();
          this.reporter?.report({
            kind: "error",
            t_ms: event.at,
            turn: this.snapshot.turn,
            payload: { error_class: "cancel_noop", cancel_noop_errors: this.metrics.cancel_noop_errors },
          });
          return;
        }
        this.patch({ lastError: event.message });
        this.reporter?.report({
          kind: "error",
          turn: this.snapshot.turn,
          payload: { error_class: event.code ?? "provider_error" },
        });
        return;
      }
      case "disconnected":
        this.onNetworkLost(event.reason, event.at);
        return;
    }
  }

  // ------------------------------------------------------ owner speech

  private onOwnerSpeechStart(at: number, source: "local" | "provider", detail?: SpeechStartDetail): void {
    if (this.closing || !this.reporter) return;
    if (this.ownerSpeaking) {
      // A second signal for the same speech (local detector first, provider
      // VAD later): the provider hearing it confirms the turn; the uplink
      // itself was measured from the transport's counters (ADR-0047 §1).
      if (source === "provider" && !this.uplinkReported && this.ownerSpeechStartedAt !== null) {
        this.uplinkReported = true;
        this.clearLocalGraceTimer();
        if (this.turnJudgement) this.turnJudgement.providerConfirmed = true;
        this.metrics.confirmed_turns += 1;
        this.onProviderConfirmed(at);
      }
      return;
    }
    // ADR-0047 §2: a provider-first start during playback is not "now" — if
    // the gate has an onset candidate under evaluation, that is the measured
    // onset; otherwise the onset is unknown and the sample is flagged.
    let onsetKnown = source === "local";
    if (source === "provider") {
      const candidate = this.deps.localSpeech?.onsetCandidate?.() ?? null;
      if (candidate) {
        const candidateAt = candidate.candidateAt - this.t0;
        const onset = Math.max(0, candidateAt - candidate.preRollMs);
        if (onset <= at) {
          detail = { candidateAt, decidedAt: at, preRollMs: candidate.preRollMs, captureLagMs: null, duringPlayback: this.responseAudible };
          at = onset;
          onsetKnown = true;
        }
      }
    }
    const resumed = this.guard.speechStarted(at);
    this.clearEotTimer();
    if (resumed) {
      // Continuation of the same turn: no new turn, no mic_speech_start.
      this.ownerSpeaking = true;
      this.patch({ hesitation: { ...this.guard.stats(), last: "resumed_within_hold" } });
      if (this.prematureResponse && (this.responseActive || this.deps.playback.playing)) {
        // The guard's own verdict: the owner had not finished, the response was
        // premature. Cancelled at once, as before — lane B is for responses
        // that started legitimately.
        this.bargeIn(at, source, detail, onsetKnown, numbersOnly({ hesitation_resume: 1, lane: LANE_CODE.hesitation_resume }));
      }
      this.prematureResponse = false;
      return;
    }
    this.prematureResponse = false;
    // Interruption is no longer decided here: while the assistant speaks, the
    // onset becomes a potential barge-in (reversible mute now, cancel only on
    // evidence — interruption.ts lane B) once the turn is booked below.
    const interruptible = this.responseActive || this.deps.playback.playing;
    this.settleUplink({ superseded: 1 });
    this.judgeOpenTurn();
    this.ownerSpeaking = true;
    this.ownerSpeechStartedAt = at;
    this.ownerSpeechSource = source;
    this.uplinkReported = source === "provider";
    this.ownerTranscriptTail = "";
    const turn = this.snapshot.turn + 1;
    this.turnJudgement = { turn, providerConfirmed: source === "provider", hadTranscript: false };
    if (source === "provider") this.metrics.confirmed_turns += 1;
    this.metrics.speech_detected += 1;
    this.turnOnset = { turn, at, source, detail, onsetKnown };
    this.patch({ turn, ownerText: "" });
    // Numbers only, through the one filter (ADR-0047 review): a string here
    // would be dropped by numbersOnly(), and a test sweeps every timing payload.
    const payload = numbersOnly({
      source: SOURCE_CODE[source],
      gate_ms: detail ? msOrOmit(detail.decidedAt - detail.candidateAt) : undefined,
      capture_lag_ms: detail ? msOrOmit(detail.captureLagMs) : undefined,
      pre_roll_ms: detail ? msOrOmit(detail.preRollMs) : undefined,
    });
    this.reporter.report({ kind: "mic_speech_start", t_ms: at, turn, payload });
    this.uplink = {
      turn,
      decisionAt: detail ? detail.decidedAt : null,
      detail: detail ?? null,
      rtp: null,
      probeRunning: false,
      providerAt: null,
      settled: false,
    };
    if (source === "local") {
      this.startUplinkProbe();
    } else {
      // No local onset: nothing to measure against. Explicit, never silent.
      this.uplink.settled = true;
      this.reportMicMetrics({ unmatched: 1, provider_first: 1 }, at);
    }
    if (interruptible) {
      // The assistant keeps its "speaking" state until the evidence decides.
      this.beginPotentialBargeIn(this.turnOnset);
    } else if (this.snapshot.state !== "tool_running" && this.snapshot.state !== "interrupted") {
      this.setState("listening");
    }
  }

  // ------------------------------------------------------ uplink (§1)

  /** Poll the transport's outbound counters for the first packet after the gate's decision. */
  private startUplinkProbe(): void {
    const tracking = this.uplink;
    const stats = this.transport?.outboundAudioStats?.bind(this.transport);
    if (!tracking || tracking.decisionAt === null || !stats) return;
    this.probe?.cancel();
    const probe = new UplinkProbe({
      stats,
      now: () => this.now(),
      scheduler: this.scheduler,
      pollMs: this.deps.uplinkProbe?.pollMs,
      maxMs: this.deps.uplinkProbe?.maxMs,
    });
    this.probe = probe;
    tracking.probeRunning = true;
    void probe.run(tracking.decisionAt).then((result) => {
      if (this.probe === probe) this.probe = null;
      if (this.uplink !== tracking) return;
      tracking.probeRunning = false;
      if (result) tracking.rtp = { rtpAt: result.rtpAt, rtpMs: result.rtpMs };
      this.log(result ? `uplink.rtp:${result.rtpMs}` : "uplink.rtp:none");
      if (tracking.providerAt !== null) this.reportUplink(tracking);
    });
  }

  /** The provider's `speech_started` for the current local turn. */
  private onProviderConfirmed(at: number): void {
    const tracking = this.uplink;
    if (!tracking || tracking.settled) return;
    tracking.providerAt = at;
    if (!tracking.probeRunning) this.reportUplink(tracking);
    // else: the probe's completion reports (bounded by its maxMs).
  }

  /** Exactly one `uplink_first_packet` per local start (ADR-0047 §1). */
  private reportUplink(tracking: UplinkTracking): void {
    if (tracking.settled || !this.reporter) return;
    tracking.settled = true;
    const origin = tracking.decisionAt ?? this.ownerSpeechStartedAt ?? 0;
    const providerMs = tracking.providerAt === null ? undefined : msOrOmit(tracking.providerAt - origin);
    const basis = tracking.rtp ? 1 : 0;
    const tMs = tracking.rtp ? tracking.rtp.rtpAt : (tracking.providerAt ?? this.now());
    const payload = numbersOnly({
      basis,
      rtp_ms: tracking.rtp ? msOrOmit(tracking.rtp.rtpMs) : undefined,
      provider_ms: providerMs,
    });
    this.reporter.report({ kind: "uplink_first_packet", t_ms: tMs, turn: tracking.turn, payload });
    const start = this.ownerSpeechStartedAt ?? 0;
    const detail = tracking.detail;
    this.patch({
      latency: { ...this.snapshot.latency, mic_to_uplink_ms: { value: Math.max(0, Math.round(tMs - start)), at: tMs } },
      latencyDetail: {
        ...this.snapshot.latencyDetail,
        uplink: {
          basis,
          rtp_ms: payload.rtp_ms,
          provider_ms: payload.provider_ms,
          gate_ms: detail ? msOrOmit(detail.decidedAt - detail.candidateAt) : undefined,
          capture_lag_ms: detail ? msOrOmit(detail.captureLagMs) : undefined,
          pre_roll_ms: detail ? msOrOmit(detail.preRollMs) : undefined,
        },
      },
    });
    this.log(`report.uplink:${basis}`);
  }

  /**
   * The current local start will never get a provider confirmation (false
   * start, network loss, session end, superseded by a new start): say so
   * explicitly on a follow-up `state` event rather than losing it silently.
   */
  private settleUplink(reason: Record<string, number>): void {
    const tracking = this.uplink;
    if (!tracking || tracking.settled) return;
    tracking.settled = true;
    this.probe?.cancel();
    this.probe = null;
    this.reportMicMetrics({ unmatched: 1, ...reason });
  }

  // ---------------------------------------------------- barge-in (§2)

  /**
   * ADR-0047 §2: the gate saw a confident, speech-like onset while the
   * assistant was audible. Mute locally NOW — reversibly: if the onset
   * collapses before the gate opens, the gain is restored and nothing was
   * cancelled. The irreversible cancel waits for the confirmed open.
   */
  private onLocalEvidence(at: number, candidateAt: number): void {
    if (this.closing || this.bargedResponse || this.earlyMute) return;
    if (!(this.responseAudible || this.deps.playback.playing)) return;
    const decisionAt = this.now();
    const stop = this.deps.playback.mute();
    this.earlyMute = {
      at: stop.at - this.t0,
      decisionAt,
      gainZeroAt: stop.gainZeroAt === null ? null : stop.gainZeroAt - this.t0,
      outputLatencyMs: stop.outputLatencyMs,
      candidateAt,
    };
    this.metrics.early_mutes += 1;
    this.log(`playback.mute@${Math.round(at)}`);
    this.refreshMicMetrics();
  }

  private onLocalEvidenceLost(at: number): void {
    // A potential barge-in owns the mute until its window decides; the gate's
    // collapsing candidate is then just one more (negative) level sample.
    if (this.pending) return;
    this.revertEarlyMute(at);
  }

  /**
   * The ONE reversal path (ADR-0047 review): a pending early mute is undone
   * here whether the candidate collapsed or the response ended underneath it
   * — the gain must never stay at zero until the next `arm()`.
   */
  private revertEarlyMute(at: number): void {
    if (!this.earlyMute || this.bargedResponse) return;
    this.earlyMute = null;
    this.deps.playback.unmute();
    this.metrics.early_mute_reverts += 1;
    this.log(`playback.unmute@${Math.round(at)}`);
    this.refreshMicMetrics();
  }

  /** Stops the assistant; false when there was nothing (left) to stop. `turn` defaults to the next turn (hesitation resume). */
  private bargeIn(
    at: number,
    source: "local" | "provider",
    detail: SpeechStartDetail | undefined,
    onsetKnown: boolean,
    extra: Record<string, number> = {},
    turn: number = this.snapshot.turn + 1,
  ): boolean {
    if (!this.reporter || this.bargedResponse) return false;
    this.bargedResponse = true;
    this.dropPending();
    const decisionAt = this.now();
    const audible = this.responseAudible;
    // Only an ACTIVE provider response can be cancelled. After `response_done`
    // the audio may still be draining locally: that is stopped here too, but
    // the provider has nothing to cancel and answers a cancel with an error.
    const providerCancel = this.responseActive;
    const early = this.earlyMute;
    this.earlyMute = null;
    // 1. stop local playback FIRST — the latency-critical action (an early
    //    mute already silenced it; the stop is then bookkeeping)
    const stop = this.deps.playback.stop();
    // 2. cancel the provider's in-flight response (when there is one)
    let cancelSentAt: number | null = null;
    if (providerCancel) {
      this.transport?.cancelResponse();
      cancelSentAt = this.now();
      this.log("transport.cancel");
    } else {
      this.log("transport.cancel_skipped");
    }
    // 3. report with the client-measured latency, decomposed
    const stopAt = early ? early.at : stop.at - this.t0;
    const gainZeroAt = early ? early.gainZeroAt : stop.gainZeroAt === null ? null : stop.gainZeroAt - this.t0;
    const gainDecisionAt = early ? early.decisionAt : decisionAt;
    const stopMs = Math.max(0, Math.round(stopAt - at));
    // A stop latency is only a measurement when something audible was stopped
    // and the onset is known; otherwise the sample is flagged, never silent.
    const anomaly = !audible || !onsetKnown ? 1 : 0;
    const breakdown = numbersOnly({
      playback_stopped_ms: stopMs,
      detect_ms: detail ? msOrOmit(detail.decidedAt - detail.candidateAt) : undefined,
      pre_roll_ms: detail ? msOrOmit(detail.preRollMs) : undefined,
      stop_command_ms: cancelSentAt === null ? undefined : msOrOmit(cancelSentAt - decisionAt),
      gain_zero_ms: gainZeroAt === null ? undefined : msOrOmit(gainZeroAt - gainDecisionAt),
      output_latency_ms: msOrOmit(early ? early.outputLatencyMs : stop.outputLatencyMs),
      early_mute: early ? 1 : 0,
      audible: audible ? 1 : 0,
      anomaly,
      provider_cancel: providerCancel ? 1 : 0,
      source: SOURCE_CODE[source],
      ...numbersOnly(extra),
    });
    // M16 §3.2: what the assistant had said when it was cut — queued only
    // (no await, nothing before the stop), so Cloud Core can place the
    // narration cursor at the sentence after the last one fully spoken.
    this.reportSpoken(0, at);
    this.reporter.report({ kind: "barge_in_start", t_ms: at, turn, payload: breakdown });
    this.reporter.report({ kind: "playback_stopped", t_ms: stopAt, turn, payload: { anomaly, early_mute: early ? 1 : 0 } });
    this.log("report.barge_in");
    this.responseActive = false;
    this.responseAudible = false;
    this.awaitingFirstAudio = false;
    this.clearPlaybackConfirmTimer();
    // ADR-0066: the interruption ends the speech lifecycle at the stop, now —
    // never at a later provider event and never on a timer.
    this.finishPlayback(stopAt, "interrupted", false);
    this.patch({
      latency: { ...this.snapshot.latency, barge_in_to_stop_ms: { value: stopMs, at } },
      latencyDetail: {
        ...this.snapshot.latencyDetail,
        barge_in: {
          playback_stopped_ms: stopMs,
          detect_ms: breakdown.detect_ms,
          pre_roll_ms: breakdown.pre_roll_ms,
          stop_command_ms: breakdown.stop_command_ms ?? 0,
          gain_zero_ms: breakdown.gain_zero_ms,
          output_latency_ms: breakdown.output_latency_ms,
          early_mute: breakdown.early_mute,
          audible: breakdown.audible,
          anomaly,
          provider_cancel: breakdown.provider_cancel,
          lane: breakdown.lane,
        },
      },
    });
    this.setState("interrupted", "INTERRUPTED");
    return true;
  }

  // ------------------------------------- two-stage interruption (lanes A/B)

  /**
   * Lane B: the onset of the turn just booked happened while the assistant was
   * speaking. Mute locally NOW (reversibly) and wait for the evidence inside
   * `BARGE_IN_CONFIRM_WINDOW_MS`: a stable onset, a near-field level from the
   * gate, a plausible word. Nothing is cancelled here.
   */
  private beginPotentialBargeIn(onset: TurnOnset | null): void {
    if (!onset || !this.reporter || this.bargedResponse) return;
    if (this.pending) this.rejectPending(this.now(), { superseded: 1 });
    if (!this.earlyMute) {
      const decisionAt = this.now();
      const stop = this.deps.playback.mute();
      this.earlyMute = {
        at: stop.at - this.t0,
        decisionAt,
        gainZeroAt: stop.gainZeroAt === null ? null : stop.gainZeroAt - this.t0,
        outputLatencyMs: stop.outputLatencyMs,
        candidateAt: onset.detail?.candidateAt ?? onset.at,
      };
      this.metrics.early_mutes += 1;
      this.log(`playback.mute@${Math.round(onset.at)}`);
    }
    this.metrics.potential_barge_in += 1;
    const now = this.now();
    const pending: PendingInterruption = {
      ...onset,
      stable: false,
      ended: false,
      levelKnown: typeof this.deps.localSpeech?.onsetLevel === "function",
      level: strongerLevel(null, this.sampleOnsetLevel()),
      hadWord: false,
      stableTimer: null,
      windowTimer: null,
    };
    // Stability is speech DURATION (from the reported onset); the window is
    // decision time (from now), so a late provider VAD never shortens it.
    pending.stableTimer = this.scheduler.setTimeout(
      () => {
        pending.stableTimer = null;
        pending.stable = true;
        this.evaluatePending();
      },
      Math.max(0, BARGE_IN_STABLE_ONSET_MS - (now - onset.at)),
    );
    pending.windowTimer = this.scheduler.setTimeout(() => {
      pending.windowTimer = null;
      this.rejectPending(this.now(), { window_elapsed: 1 });
    }, BARGE_IN_CONFIRM_WINDOW_MS);
    this.pending = pending;
    this.log(`barge_in.potential@${Math.round(onset.at)}`);
    this.refreshMicMetrics();
  }

  private sampleOnsetLevel(): OnsetLevel | null | undefined {
    return this.deps.localSpeech?.onsetLevel?.();
  }

  /** Re-check lane B's three conditions with the freshest level sample. */
  private evaluatePending(): void {
    const pending = this.pending;
    if (!pending) return;
    pending.level = strongerLevel(pending.level, this.sampleOnsetLevel());
    const near = pending.levelKnown ? isNearField(pending.level) : undefined;
    if (!pending.stable || !pending.hadWord || near === false) return;
    this.confirmPending(pending, near);
  }

  private confirmPending(pending: PendingInterruption, near: boolean | undefined): void {
    const now = this.now();
    this.clearPendingTimers(pending);
    this.pending = null;
    this.log(`barge_in.accepted@${Math.round(now)}`);
    const stopped = this.bargeIn(
      pending.at,
      pending.source,
      pending.detail,
      pending.onsetKnown,
      numbersOnly({
        lane: LANE_CODE.conversational,
        confirm_ms: msOrOmit(now - pending.at),
        near_field: near === undefined ? undefined : near ? 1 : 0,
        margin_db: pending.level?.marginDb,
        spectral: pending.level?.spectralScore,
      }),
      pending.turn,
    );
    if (!stopped) return;
    this.metrics.accepted_owner_interruption += 1;
    this.refreshMicMetrics();
    this.watchFalseInterruption(pending.turn);
  }

  /** The window elapsed, the speech was a burst, or the turn was released: playback resumes, nothing was cancelled. */
  private rejectPending(now: number, reason: Record<string, number>): void {
    const pending = this.pending;
    if (!pending) return;
    this.clearPendingTimers(pending);
    this.pending = null;
    this.metrics.rejected_background_speech += 1;
    this.revertEarlyMute(now);
    this.log(`barge_in.rejected@${Math.round(now)}`);
    const near = pending.levelKnown ? isNearField(pending.level) : undefined;
    this.reporter?.report({
      kind: "state",
      t_ms: now,
      turn: pending.turn,
      payload: numbersOnly({
        background_speech_rejected: 1,
        onset_ms: msOrOmit(now - pending.at),
        stable: pending.stable ? 1 : 0,
        near_field: near === undefined ? undefined : near ? 1 : 0,
        plausible_word: pending.hadWord ? 1 : 0,
        margin_db: pending.level?.marginDb,
        spectral: pending.level?.spectralScore,
        source: SOURCE_CODE[pending.source],
        ...reason,
      }),
    });
    this.refreshMicMetrics();
  }

  /** Forget a potential barge-in without a verdict (the response ended or the leg closed). */
  private dropPending(): void {
    const pending = this.pending;
    if (!pending) return;
    this.clearPendingTimers(pending);
    this.pending = null;
    this.log("barge_in.dropped");
  }

  private clearPendingTimers(pending: PendingInterruption): void {
    if (pending.stableTimer !== null) this.scheduler.clearTimeout(pending.stableTimer);
    if (pending.windowTimer !== null) this.scheduler.clearTimeout(pending.windowTimer);
    pending.stableTimer = null;
    pending.windowTimer = null;
  }

  /** The speech behind a potential barge-in ended at `at` (provider stop or local close). */
  private onPendingSpeechEnded(at: number): void {
    const pending = this.pending;
    if (!pending || pending.ended) return;
    pending.ended = true;
    if (at - pending.at < BARGE_IN_STABLE_ONSET_MS) {
      // A burst, whatever else arrives: revert now so playback resumes at once
      // (the fast lane still stops on a control phrase transcribed later).
      this.rejectPending(this.now(), { short_burst: 1 });
      return;
    }
    pending.stable = true;
    this.evaluatePending();
  }

  /**
   * Lane A: a control phrase in the owner's transcript stops the assistant at
   * once — through the same barge-in path, whether a potential barge-in is
   * still waiting, was already rejected, or never existed for this speech.
   */
  private fastLaneStop(at: number): void {
    const pending = this.pending;
    const onset =
      pending ?? (this.turnOnset && this.turnOnset.turn === this.snapshot.turn ? this.turnOnset : null);
    const turn = onset ? onset.turn : this.snapshot.turn;
    const stopped = onset
      ? this.bargeIn(onset.at, onset.source, onset.detail, onset.onsetKnown, numbersOnly({ lane: LANE_CODE.explicit_stop, after_reject: pending ? 0 : 1 }), turn)
      : this.bargeIn(at, "provider", undefined, false, numbersOnly({ lane: LANE_CODE.explicit_stop }), turn);
    if (!stopped) return;
    this.stopHandledTurn = this.snapshot.turn;
    this.metrics.explicit_stop_command += 1;
    this.log("barge_in.explicit_stop");
    this.refreshMicMetrics();
    // Speech already over and nothing to cancel (draining audio): there is no
    // later event to leave INTERRUPTED on, so leave it now.
    if (!this.ownerSpeaking && !this.responseActive) this.setState("listening", "LISTENING");
  }

  /** An accepted conversational interruption must be followed by something said to the assistant. */
  private watchFalseInterruption(turn: number): void {
    this.clearFalseInterruptionWatch();
    this.falseInterruptionTimer = this.scheduler.setTimeout(() => {
      this.falseInterruptionTimer = null;
      this.metrics.false_interruption += 1;
      this.log("barge_in.false_interruption");
      this.reporter?.report({
        kind: "state",
        t_ms: this.now(),
        turn,
        payload: numbersOnly({ false_interruption: 1, wait_ms: FALSE_INTERRUPTION_WINDOW_MS }),
      });
      this.refreshMicMetrics();
    }, FALSE_INTERRUPTION_WINDOW_MS);
  }

  private clearFalseInterruptionWatch(): void {
    if (this.falseInterruptionTimer !== null) {
      this.scheduler.clearTimeout(this.falseInterruptionTimer);
      this.falseInterruptionTimer = null;
    }
  }

  /**
   * The local gate closed. Real speech is ended by the provider's
   * `speech_stopped`; this only matters when the provider never heard speech
   * for a locally opened turn — after a short grace it was noise.
   */
  private onLocalSpeechEnd(at: number): void {
    if (this.closing) return;
    this.localCloseAt = at;
    // For a locally opened turn the provider never confirmed, the gate's
    // close is the only end signal a potential barge-in will get.
    if (this.pending && this.pending.source === "local" && !this.uplinkReported) this.onPendingSpeechEnded(at);
    if (!this.ownerSpeaking) return;
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
    this.rejectPending(this.now(), { false_start: 1 });
    const wasBargeIn = this.snapshot.state === "interrupted";
    this.metrics.false_starts += 1;
    if (wasBargeIn) this.metrics.false_barge_ins += 1;
    this.log(wasBargeIn ? "gate.false_barge_in" : "gate.false_start");
    // The unmatched marker rides on the same metrics event (ADR-0047 §1).
    if (this.uplink && !this.uplink.settled) {
      this.uplink.settled = true;
      this.probe?.cancel();
      this.probe = null;
      this.reportMicMetrics({ false_start: 1, false_barge_in: wasBargeIn ? 1 : 0, unmatched: 1 }, at);
    } else {
      this.reportMicMetrics({ false_start: 1, false_barge_in: wasBargeIn ? 1 : 0 }, at);
    }
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

  /**
   * ADR-0047 §5: a measurement is reported as `mic_calibration: 1` with
   * `measured: 1` and `samples`; a window that measured nothing (dead input,
   * contaminated retry) is `mic_calibration_attempt: 1` with `measured: 0`,
   * so the server's "calibration in force" is never a window that saw zeros.
   */
  private onCalibration(calibration: SpeechDetectorCalibration | SpeechDetectorCalibrationAttempt): void {
    if (this.closing || !this.reporter) return;
    if (calibration.measured === 1) {
      this.lastCalibration = calibration;
      this.reporter.report({
        kind: "state",
        turn: this.snapshot.turn,
        payload: numbersOnly({ mic_calibration: 1, ...calibration }),
      });
      this.log("report.mic_calibration");
    } else {
      this.reporter.report({
        kind: "state",
        turn: this.snapshot.turn,
        payload: numbersOnly({ mic_calibration_attempt: 1, ...calibration }),
      });
      this.log("report.mic_calibration_attempt");
    }
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
            echo_residual_db: stats.echo_residual_db ?? null,
          }
        : {}),
      false_starts: this.metrics.false_starts,
      false_barge_ins: this.metrics.false_barge_ins,
      false_turns: this.metrics.false_turns,
      confirmed_turns: this.metrics.confirmed_turns,
      early_mutes: this.metrics.early_mutes,
      early_mute_reverts: this.metrics.early_mute_reverts,
      cancel_noop_errors: this.metrics.cancel_noop_errors,
      speech_detected: this.metrics.speech_detected,
      potential_barge_in: this.metrics.potential_barge_in,
      accepted_owner_interruption: this.metrics.accepted_owner_interruption,
      rejected_background_speech: this.metrics.rejected_background_speech,
      explicit_stop_command: this.metrics.explicit_stop_command,
      false_interruption: this.metrics.false_interruption,
      noise_floor_db: this.lastCalibration?.noise_floor_db ?? null,
      env: this.lastCalibration?.env_class ?? null,
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
        confirmed_turns: this.metrics.confirmed_turns,
        early_mutes: this.metrics.early_mutes,
        early_mute_reverts: this.metrics.early_mute_reverts,
        cancel_noop_errors: this.metrics.cancel_noop_errors,
        speech_detected: this.metrics.speech_detected,
        potential_barge_in: this.metrics.potential_barge_in,
        accepted_owner_interruption: this.metrics.accepted_owner_interruption,
        rejected_background_speech: this.metrics.rejected_background_speech,
        explicit_stop_command: this.metrics.explicit_stop_command,
        false_interruption: this.metrics.false_interruption,
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
    // ADR-0047 §3: how far the provider's stop trails the local gate's close
    // (the owner's perceived end of speech), when the local close came first.
    const localClose = this.localCloseAt;
    const vadLagMs =
      localClose !== null && this.ownerSpeechStartedAt !== null && localClose >= this.ownerSpeechStartedAt && localClose <= at
        ? msOrOmit(at - localClose)
        : undefined;
    const commit = (): void => {
      this.eotTimer = null;
      this.guard.expire();
      this.lastEotAt = at;
      this.reporter?.report({
        kind: "end_of_turn",
        t_ms: at,
        turn,
        payload: numbersOnly({ hold_ms: decision.holdMs, hesitation: HESITATION_CODE[decision.reason], vad_lag_ms: vadLagMs }),
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

  private onOwnerTranscript(text: string, final: boolean, at: number): void {
    if (this.turnJudgement && text.trim()) this.turnJudgement.hadTranscript = true;
    if (final) {
      this.ownerTranscriptTail = text;
      this.patch({ ownerText: text });
      this.remember(`Sahip: ${text}`);
      // Something was said to the assistant: the interruption was not false.
      if (text.trim()) this.clearFalseInterruptionWatch();
      // Cloud Core resolves intents from the transcript (spec §5); the client
      // only reports it.
      this.reporter?.report({ kind: "utterance", turn: this.snapshot.turn, text });
    } else {
      this.ownerTranscriptTail += text;
      this.patch({ ownerText: this.ownerTranscriptTail });
    }
    if (!(this.responseActive || this.deps.playback.playing)) return;
    // Lane A first: a control phrase in THIS fragment or in the turn's
    // accumulated tail (a phrase split across deltas) stops immediately.
    if (this.stopHandledTurn !== this.snapshot.turn && (findControlPhrase(text) || findControlPhrase(this.ownerTranscriptTail))) {
      this.fastLaneStop(at);
      return;
    }
    // Lane B: a plausible word is one of the three pieces of evidence.
    if (this.pending && !this.pending.hadWord && (hasPlausibleWord(text) || hasPlausibleWord(this.ownerTranscriptTail))) {
      this.pending.hadWord = true;
      this.evaluatePending();
    }
  }

  // ---------------------------------------------------- assistant speech

  private onResponseStarted(at: number, responseId?: string): void {
    // ADR-0066: a response that starts while the previous one is still
    // draining takes the lifecycle over. The old audio's real end is unknown
    // from here on, and the record says so (`basis: superseded`) rather than
    // borrowing a later `audio_stopped` that may belong to either.
    this.finishPlayback(at, "superseded", false);
    this.responseActive = true;
    // The turn this response belongs to. `spoken` describes what the ASSISTANT said, so
    // it is reported against this turn even when the owner's transcript (the fast control
    // lane's trigger) has already opened the next one.
    this.responseTurn = this.snapshot.turn;
    this.responseAudible = false;
    this.awaitingFirstAudio = true;
    this.responseStartedAt = at;
    this.audioStartedAt = null;
    this.providerStoppedAt = null;
    this.lastOutputEnergyAt = null;
    this.clearPlaybackConfirmTimer();
    this.bargedResponse = false;
    // A new response: whatever was pending against the old one is moot, and a
    // control phrase may stop this one too.
    this.dropPending();
    this.stopHandledTurn = null;
    this.earlyMute = null;
    this.assistantBuffer = "";
    this.responseSeq += 1;
    this.spokenReported = false;
    this.prematureResponse = this.guard.isHolding;
    this.deps.playback.arm();
    this.setSpeech({
      responseId: responseId ?? null,
      phase: "generating",
      firstAudioAt: null,
      generationDoneAt: null,
      playbackDoneAt: null,
      basis: null,
    });
    this.log(`response.started@${Math.round(at)}`);
    if (this.snapshot.state !== "tool_running") this.setState("speaking", "ASSISTANT_SPEAKING");
  }

  /** ADR-0066: does a provider event carrying `responseId` belong to the current response? */
  private isCurrentResponse(responseId: string | undefined): boolean {
    // Either side without an id (a dialect that carries none) is taken as
    // the current response; two ids that disagree are a stale event.
    return responseId === undefined || this.speech.responseId === null || responseId === this.speech.responseId;
  }

  /**
   * The provider says its audio started (WebRTC `output_audio_buffer.started`).
   * ADR-0047 §3: this is the generation component, not first audio — the
   * first AUDIBLE sample is what the local analyser reports; if it never does
   * inside `playbackConfirmMs`, the provider's mark is used with `basis: 0`.
   */
  private onProviderAudioStarted(at: number, responseId?: string): void {
    if (!this.isCurrentResponse(responseId)) {
      this.log("audio.started.stale");
      return;
    }
    if (this.speech.phase === "done" || this.speech.phase === "idle") return;
    // The provider's buffer is playing (again): the lifecycle may not close on
    // a stop that came before this start.
    this.responseAudible = true;
    this.providerStoppedAt = null;
    if (this.audioStartedAt === null) this.audioStartedAt = at;
    if (!this.awaitingFirstAudio) return;
    this.clearPlaybackConfirmTimer();
    this.playbackConfirmTimer = this.scheduler.setTimeout(() => {
      this.playbackConfirmTimer = null;
      this.reportFirstAudio(at, 0);
    }, this.deps.playbackConfirmMs ?? DEFAULT_PLAYBACK_CONFIRM_MS);
  }

  /**
   * The provider says its audio for a response finished playing (WebRTC
   * `output_audio_buffer.stopped`). ADR-0066: this — for THIS response — is
   * the authoritative end of playback. Before `response_done` it is remembered
   * (generation may still add audio); after it, it closes the drain.
   */
  private onProviderAudioStopped(at: number, responseId?: string): void {
    if (!this.isCurrentResponse(responseId)) {
      this.log("audio.stopped.stale");
      return;
    }
    this.responseAudible = false;
    if (this.speech.phase === "draining") {
      this.finishPlayback(at, "provider", true);
    } else if (this.speech.phase === "audible" || this.speech.phase === "generating") {
      this.providerStoppedAt = at;
    }
  }

  /** Local: the first audible energy after `arm()`. */
  private onAudioActivity(at: number): void {
    this.lastOutputEnergyAt = at;
    if (!this.awaitingFirstAudio) return;
    this.reportFirstAudio(at, 1);
  }

  private reportFirstAudio(at: number, basis: 0 | 1): void {
    if (!this.reporter || !this.awaitingFirstAudio) return;
    this.awaitingFirstAudio = false;
    this.clearPlaybackConfirmTimer();
    this.responseAudible = true;
    if (this.lastOutputEnergyAt === null || this.lastOutputEnergyAt < at) this.lastOutputEnergyAt = at;
    if (this.speech.phase === "generating") this.setSpeech({ ...this.speech, phase: "audible", firstAudioAt: at });
    const turn = this.snapshot.turn;
    const responseCreated =
      this.lastEotAt !== null && this.responseStartedAt !== null && this.responseStartedAt >= this.lastEotAt
        ? msOrOmit(this.responseStartedAt - this.lastEotAt)
        : undefined;
    const firstDelta =
      this.audioStartedAt !== null && this.responseStartedAt !== null ? msOrOmit(this.audioStartedAt - this.responseStartedAt) : undefined;
    const playback = basis === 1 && this.audioStartedAt !== null ? msOrOmit(at - this.audioStartedAt) : undefined;
    const payload = numbersOnly({
      basis,
      response_created_ms: responseCreated,
      first_delta_ms: firstDelta,
      playback_ms: playback,
    });
    this.reporter.report({ kind: "first_audio", t_ms: at, turn, payload });
    const latency = { ...this.snapshot.latency };
    const latencyDetail = {
      ...this.snapshot.latencyDetail,
      first_audio: { basis, response_created_ms: responseCreated, first_delta_ms: firstDelta, playback_ms: playback },
    };
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
    this.log(`report.first_audio:${basis}`);
    this.patch({ latency, latencyDetail });
  }

  private clearPlaybackConfirmTimer(): void {
    if (this.playbackConfirmTimer !== null) {
      this.scheduler.clearTimeout(this.playbackConfirmTimer);
      this.playbackConfirmTimer = null;
    }
  }

  private onResponseText(text: string, final: boolean): void {
    this.assistantBuffer = final ? text : this.assistantBuffer + text;
    this.patch({ assistantText: this.assistantBuffer });
    if (final && text) this.remember(`Asistan: ${text}`);
  }

  /**
   * M16 §3.2 `spoken`: the assistant transcript of the current response as
   * top-level `text` (the owner's transcript never enters it — the buffer only
   * ever holds `response_text`), with a numbers-only payload. `final: 0` at a
   * barge-in with whatever was buffered (possibly nothing), `final: 1` at a
   * completion when something was said. At most one per response. Longer than
   * the server's text limit keeps the TAIL — the alignment needs the most
   * recent sentences — and says so with `truncated: 1`.
   */
  private reportSpoken(final: 0 | 1, at: number): void {
    if (!this.reporter || this.spokenReported) return;
    const full = this.assistantBuffer;
    if (final === 1 && !full) return;
    this.spokenReported = true;
    const truncated = full.length > MAX_EVENT_TEXT_CHARS;
    const text = truncated ? full.slice(full.length - MAX_EVENT_TEXT_CHARS) : full;
    const payload: Record<string, number> = { final, response_seq: this.responseSeq, chars: full.length };
    if (truncated) payload.truncated = 1;
    this.reporter.report({ kind: "spoken", t_ms: at, turn: this.responseTurn, text, payload });
  }

  private onResponseDone(at: number): void {
    this.responseActive = false;
    this.awaitingFirstAudio = false;
    this.clearPlaybackConfirmTimer();
    // A candidate still pending when the response ends: restore the gain now —
    // unless a potential barge-in owns the mute; the audio may still be
    // draining and its window (≤ BARGE_IN_CONFIRM_WINDOW_MS) decides.
    if (!this.pending) this.revertEarlyMute(at);
    // A response that was cut already reported what was heard; a completed
    // one reports its whole transcript (once, and only when there is one).
    if (!this.bargedResponse) this.reportSpoken(1, at);
    this.reporter?.report({ kind: "response_done", t_ms: at, turn: this.snapshot.turn });
    // ADR-0066: `response_done` is the end of GENERATION. Over WebRTC the
    // media track still holds whatever was generated but not yet played, so
    // the speech lifecycle — and the `speaking` state — end only when that
    // audio has actually finished: the provider's `audio_stopped` for this
    // response, analyser silence after generation, the drain cap, or an
    // interruption. Nothing here invents speech: a response whose audio never
    // started, or already stopped, ends now.
    if (this.speech.phase === "generating" || this.speech.phase === "audible") {
      if (this.responseAudible) {
        this.setSpeech({ ...this.speech, phase: "draining", generationDoneAt: at });
        this.log(`playback.draining@${Math.round(at)}`);
        this.armDrainTimers(at);
      } else if (this.audioStartedAt !== null || this.speech.firstAudioAt !== null) {
        // Audio was heard and the provider already closed its buffer.
        this.setSpeech({ ...this.speech, generationDoneAt: at });
        this.finishPlayback(this.providerStoppedAt ?? at, "provider", false);
      } else {
        // No audio at all (a tool-only or text-only response): nothing to drain.
        this.setSpeech({ ...this.speech, phase: "done", generationDoneAt: at, playbackDoneAt: at });
      }
    }
    if (this.longRunning.size > 0) {
      this.setState("tool_running", "TOOL_RUNNING");
    } else if (this.snapshot.state === "speaking" && this.speech.phase !== "draining") {
      this.setState("listening", "LISTENING");
    }
  }

  // ------------------------------------------------ playback lifecycle (ADR-0066)

  private setSpeech(speech: SpeechLifecycle): void {
    this.speech = speech;
    this.patch({ speech });
  }

  /**
   * While draining: the analyser is read every `PLAYBACK_POLL_MS`; silence
   * for `PLAYBACK_RELEASE_MS` after both `response_done` and the last measured
   * energy ends playback (`basis: silence`); `PLAYBACK_DRAIN_MAX_MS` after
   * `response_done` ends it whatever the analyser says (`basis: cap`). A path
   * this client silenced itself (an early mute, a potential barge-in) is not
   * silence — the audio is still flowing under the mute — so the release
   * waits, and the cap or the lane's own verdict decides.
   */
  private armDrainTimers(generationDoneAt: number): void {
    this.clearDrainTimers();
    this.drainCapTimer = this.scheduler.setTimeout(() => {
      this.drainCapTimer = null;
      this.finishPlayback(this.now(), "cap", true);
    }, PLAYBACK_DRAIN_MAX_MS);
    const poll = (): void => {
      this.drainPollTimer = null;
      if (this.speech.phase !== "draining") return;
      const now = this.now();
      const level = this.deps.playback.outputLevel?.() ?? null;
      if (level !== null && level > PLAYBACK_ENERGY_LEVEL) this.lastOutputEnergyAt = now;
      const silencedLocally = this.earlyMute !== null || this.pending !== null;
      const quietSince = Math.max(generationDoneAt, this.lastOutputEnergyAt ?? generationDoneAt);
      if (!silencedLocally && now - quietSince >= PLAYBACK_RELEASE_MS) {
        this.finishPlayback(now, "silence", true);
        return;
      }
      this.drainPollTimer = this.scheduler.setTimeout(poll, PLAYBACK_POLL_MS);
    };
    this.drainPollTimer = this.scheduler.setTimeout(poll, PLAYBACK_POLL_MS);
  }

  private clearDrainTimers(): void {
    if (this.drainPollTimer !== null) {
      this.scheduler.clearTimeout(this.drainPollTimer);
      this.drainPollTimer = null;
    }
    if (this.drainCapTimer !== null) {
      this.scheduler.clearTimeout(this.drainCapTimer);
      this.drainCapTimer = null;
    }
  }

  /**
   * The one end of the speech lifecycle. Idempotent per response: the first
   * caller's `basis` is the record. Reports `audio_done` once, and only when
   * audio had actually started for the response (a response that never
   * became audible has no playback to finish). `settle` says whether a
   * `speaking` state may become `listening` here — false for an interruption
   * (the barge-in path sets `interrupted` itself) and for a takeover.
   */
  private finishPlayback(at: number, basis: AudioDoneBasis, settle: boolean): void {
    const speech = this.speech;
    if (speech.phase === "idle" || speech.phase === "done") return;
    this.clearDrainTimers();
    this.responseAudible = false;
    const audioHeard = this.audioStartedAt !== null || speech.firstAudioAt !== null;
    this.setSpeech({ ...speech, phase: "done", playbackDoneAt: at, basis: audioHeard ? basis : null });
    this.log(`playback.done:${basis}@${Math.round(at)}`);
    if (audioHeard && this.reporter) {
      const payload: Record<string, unknown> = {
        basis,
        drain_ms: speech.generationDoneAt === null ? undefined : msOrOmit(at - speech.generationDoneAt),
        audible_ms: speech.firstAudioAt === null ? undefined : msOrOmit(at - speech.firstAudioAt),
      };
      if (speech.responseId !== null) payload.response_id = speech.responseId;
      this.reporter.report({ kind: "audio_done", t_ms: at, turn: this.responseTurn, payload });
    }
    if (settle && this.snapshot.state === "speaking" && this.longRunning.size === 0) {
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
    // M18_ACTION_CONTRACT.md §5.1: this device's own capability runs FIRST;
    // what it observed rides along so the receipt is verified, not assumed.
    const observed = await this.runLocalAction(callId, name, args);
    const relayed = observed ? { ...args, observed_after: observed } : args;
    let response: ToolCallResponse;
    try {
      response = await this.relayWithReattach(callId, name, relayed);
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
      payload: { call_id: callId, name, status: response.status, replayed: response.replayed ? 1 : 0 },
    });
    // ADR-0077: a clarification is handed to the model as the result it is - the
    // question to ask, with `status: "needs_clarification"` inside it - never wrapped
    // as a failure, which would have the model apologise instead of asking.
    this.transport?.submitToolResult(
      callId,
      response.status === "succeeded" || response.status === "needs_clarification"
        ? (response.result ?? {})
        : { status: "failed", error: response.error ?? {} },
    );
    this.log(`tool.submit:${callId}`);
    if (this.longRunning.size === 0) this.setState("listening", "LISTENING");
  }

  /**
   * The local half of a tool call, when this client has one for it. `null`
   * when the port has nothing to do for this tool (the relay is unchanged)
   * and — defensively — when the port throws: the call is then relayed
   * without `observed_after`, which the server reads as a missing local
   * capability rather than as success.
   *
   * `name` arrives in the PROVIDER's spelling (`eye__disable`: the vendor
   * allows no dot in a function name, see `tool-names.ts`) and is relayed to
   * the Cloud Core in that same spelling, untouched. The port, though, speaks
   * the Cloud Core's names (`eye.disable`), so THIS is the one place the name
   * is normalised — the owner's 2026-09-06 run reached the port as
   * `eye__disable`, matched nothing, and every eye command was recorded as
   * `capability_missing` while the camera sat idle.
   *
   * The port also receives the action's identity — `call_id` (the provider's
   * tool call id, which the Cloud Core uses as the receipt's `action_id`) and
   * `session_id` (this realtime session) — so the device's own durable call
   * and its trace name the same command the receipt will. Only the port sees
   * these: the RELAYED `arguments` are the provider's own, unchanged.
   */
  private async runLocalAction(
    callId: string,
    name: string,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown> | null> {
    const port = this.deps.localActions;
    if (!port) return null;
    try {
      const observed = await port.run(cloudToolName(name), { ...args, call_id: callId, session_id: this.sessionId });
      if (observed) this.log(`tool.local:${callId}`);
      return observed;
    } catch (error) {
      this.log(`tool.local_error:${callId}:${describe(error)}`);
      return null;
    }
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
      case "narration_cursor": {
        const cursor = narrationCursorFrom(payload);
        this.patch({ narrationCursor: cursor });
        const action = cursor.action ? ` (${cursor.action})` : "";
        this.pushSideband(`anlatım imleci: ${cursor.state}${action} — ${describeNarrationCursor(cursor)}`);
        return;
      }
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
    this.settleUplink({ network_lost: 1 });
    this.teardownLeg(reason);
    this.ownerSpeaking = false;
    this.patch({ state: "reconnecting" });
    if (this.deps.network.online) void this.reattachLoop();
  }

  /**
   * Run the reconnect series, or join the one already running.
   *
   * Three things decide a reconnect is needed - the backoff timer, a network-lost, and
   * the network coming back - and before this guard each of them started its own series,
   * with its own attach in flight and its own attempt count. A flapping network produced
   * a burst of concurrent attaches; §6 of the 2026-09-09 report calls for exactly one.
   */
  private reattachLoop(): Promise<void> {
    if (this.reattachRun) return this.reattachRun;
    const run = this.runReattachLoop().finally(() => {
      this.reattachRun = null;
    });
    this.reattachRun = run;
    return run;
  }

  private async runReattachLoop(): Promise<void> {
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

  /**
   * `POST .../attach`: fresh credential, replayed sideband, new media leg.
   *
   * Single-flight. A tool relay that meets a stale leg and a reconnect series can both
   * want an attach at the same instant; they share the one on the wire rather than
   * minting two credentials and moving the leg twice.
   */
  private reattach(): Promise<void> {
    if (this.attachInFlight) return this.attachInFlight;
    const run = this.runAttach().finally(() => {
      this.attachInFlight = null;
    });
    this.attachInFlight = run;
    return run;
  }

  private async runAttach(): Promise<void> {
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
        // Terminal. The reporter has already ended itself on the 410; this records the
        // state for the owner WITHOUT going back through the channel that just failed.
        this.fail("Oturum sunucuda kapanmış.", [], { viaReporter: false });
        this.teardownLeg("gone");
        this.patch({ state: "closed" });
        return;
      }
      if (error.status === 429) {
        // The reporter backs off on its own (bounded, honouring Retry-After). Say so, and
        // do NOT reconnect: a reconnect storm is what made this a 429 in the first place.
        this.patch({ lastError: "Sunucu hız sınırı uyguluyor; bekleniyor." });
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
