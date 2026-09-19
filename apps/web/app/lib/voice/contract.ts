/**
 * The Cloud Core realtime-session contract as seen by a browser client
 * (M12 spec §4; server: services/api/app/voice/realtime_sessions/{routes,service}.py).
 *
 * Everything here mirrors what the server actually accepts and returns. The
 * event kinds in particular are the server's `CLIENT_EVENT_KINDS` verbatim —
 * an unknown kind is a 422, so the client never invents its own vocabulary.
 */

/** One entry of the tool manifest the server exposes to this session. */
export type ToolManifestEntry = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  long_running: boolean;
  preamble?: string;
};

/**
 * The per-session provider credential minted by Cloud Core through the
 * adapter. `secret` is the only secret a browser ever sees; the owner's vendor
 * key never leaves the server. It is held in memory for the life of the media
 * leg and never persisted, logged or reported.
 */
export type SessionCredential = {
  provider: string;
  /** Absent for a provider with no media leg (ADR-0173 `local-router`): nothing to authenticate against. */
  secret?: string;
  expires_at: string;
  transport: string;
  session_ref: string;
  /** Optional provider transport descriptor (see transport.ts). */
  transport_descriptor?: Record<string, unknown>;
};

/** `POST /v1/voice/realtime/sessions` and `POST .../attach` response. */
export type SessionLegPayload = {
  session_id: string;
  provider: string;
  transport: string;
  credential: SessionCredential;
  tools: ToolManifestEntry[];
  instructions: string;
  language: string;
  expires_at: string;
  state: string | SessionState;
  /** ADR-0043: the wire voice id the session was created with (owner A/B: marin | cedar). */
  voice?: string | null;
  /** ADR-0043: the owner's perceptual profile ("arbor"), applied server-side; never a wire voice. */
  voice_profile?: string | null;
  /** Optional provider transport descriptor (see transport.ts). */
  transport_descriptor?: Record<string, unknown>;
  /**
   * B20 req 223: how many seconds this provider lets ONE MEDIA LEG live, 0 when it does
   * not say. Not the session (ADR-0105: the session never expires and survives any number
   * of legs) — the leg, which OpenAI Realtime ends by itself at sixty minutes. The client
   * re-opens before the ceiling instead of finding out by being disconnected mid-sentence.
   */
  leg_max_seconds?: number;
  /** attach only */
  pending_sideband?: SidebandFrame[];
  previous_leg?: Record<string, unknown> | null;
};

/** `GET .../sessions/{id}` and the `state` block of an attach/events response. */
export type SessionState = {
  session_id: string;
  provider: string;
  transport: string;
  client_kind: string;
  state: string;
  language: string;
  plan: Record<string, unknown> | null;
  plan_id: string | null;
  narration: Record<string, unknown> | null;
  presentation: string | null;
  last_intent: string | null;
  fsm_state: string | null;
  barge_in_count: number;
  network: string | null;
  legs: number;
  pending_sideband_count: number;
  transcript_summary: string;
  voice?: string | null;
  voice_profile?: string | null;
};

/** Cloud Core → client sideband message, replayed via `/events` and `attach`. */
export type SidebandEvent =
  | "plan_changed"
  | "tool_progress"
  | "tool_completed"
  | "narration_cursor"
  | "say"
  | "leg_closed";

export type SidebandFrame = {
  type: "voice_sideband";
  session_id: string;
  event: SidebandEvent;
  payload: Record<string, unknown>;
  at: string;
};

/**
 * ADR-0077: `needs_clarification` is a research follow-up whose target could not be
 * resolved from the turn. It carries `result` (the one question to ask, as `speech`)
 * and no target; it is neither a success nor a failure, and the model receives the
 * result itself so it asks the question verbatim.
 */
export type ToolCallStatus = "running" | "succeeded" | "failed" | "needs_clarification";

/** `POST .../tool-calls` response (idempotent on call_id per session). */
export type ToolCallResponse = {
  call_id: string;
  name: string;
  status: ToolCallStatus;
  long_running: boolean;
  replayed: boolean;
  result?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
  preamble?: string;
};

/**
 * Timing kinds (benchmark harness, `realtime_bench.TIMING_EVENT_KINDS`) plus
 * state kinds (`service.STATE_EVENT_KINDS`). Keep in sync with the server.
 */
export const TIMING_EVENT_KINDS = [
  "mic_speech_start",
  "uplink_first_packet",
  "end_of_turn",
  "first_audio",
  "barge_in_start",
  "playback_stopped",
  "tool_call",
  "preamble_audio_start",
  "tool_done",
  "speech_resumed",
  "audio_frame",
  "response_done",
  /**
   * ADR-0066: the assistant's audio for one response actually finished
   * (payload `response_id`, `basis`: provider | silence | cap | interrupted |
   * superseded, plus `drain_ms` / `audible_ms` numbers). Fire-and-forget like
   * every timing kind; a server that does not know it yet skips it.
   */
  "audio_done",
  "network_lost",
  "network_restored",
] as const;

export const STATE_EVENT_KINDS = [
  "utterance",
  "summary",
  "intent",
  "state",
  "error",
  /** M16 §3.2: the assistant transcript spoken so far (top-level `text`), at a cut or a completion. */
  "spoken",
] as const;

export const CLIENT_EVENT_KINDS = [
  ...TIMING_EVENT_KINDS,
  ...STATE_EVENT_KINDS,
] as const;

export type ClientEventKind = (typeof CLIENT_EVENT_KINDS)[number];

/** The M4 control FSM states the server accepts in a `state` event. */
export type FsmState =
  | "IDLE"
  | "LISTENING"
  | "ASSISTANT_SPEAKING"
  | "TOOL_RUNNING"
  | "INTERRUPTED"
  | "CLOSED";

/** One element of `POST .../events` (`ClientEvent` on the server). */
export type ClientEvent = {
  kind: ClientEventKind;
  /** client-monotonic milliseconds since the session clock started, integer ≥ 0 */
  t_ms: number;
  turn: number;
  payload?: Record<string, unknown>;
  text?: string;
};

export type EventsResponse = {
  accepted: number;
  resolved_intents: Array<Record<string, unknown>>;
  pending_sideband: SidebandFrame[];
  state: SessionState;
};

/** Server-side bounds the client respects before sending. */
export const MAX_EVENTS_PER_REQUEST = 200;
export const MAX_EVENT_PAYLOAD_BYTES = 4 * 1024;
export const MAX_EVENT_TEXT_CHARS = 4000;
export const MAX_SUMMARY_CHARS = 2000;

/**
 * Payload keys the server refuses (service.py `FORBIDDEN_KEY_PARTS`, applied
 * by the route validator and the audit scrubber). The client drops such keys
 * itself so a defensive bug can never turn into a rejected batch — and so
 * audio, a credential or a transcript can never travel inside a payload.
 *
 * The server NORMALIZES a key before matching (lower-case, every non
 * alphanumeric character dropped), so `apiKey`, `api-key` and `API_KEY` are the
 * same key as `api_key`; `isForbiddenKey` applies the identical rule. Note that
 * "text" catches innocent-looking keys such as `context` — metric payloads use
 * names like `rms_db`, `noise_floor_db`, `speech_prob`, `gate_opens`.
 */
export const FORBIDDEN_PAYLOAD_KEY_PARTS = [
  "audio",
  "pcm",
  "wave",
  "secret",
  "credential",
  "token",
  "apikey",
  "password",
  "text",
  "transcript",
] as const;

/** Server-identical: true when `key` is audio/credential/transcript-shaped under any spelling. */
export function isForbiddenKey(key: unknown): boolean {
  const normalized = String(key)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
  return FORBIDDEN_PAYLOAD_KEY_PARTS.some((part) => normalized.includes(part));
}
