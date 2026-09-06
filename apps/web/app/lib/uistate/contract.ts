/**
 * The UI-state vocabulary, mirrored from `services/api/app/uistate/contract.py`
 * (ADR-0052). This file is a *copy of a contract*, not a second source of truth:
 * the API owns the vocabulary, and `/v1/ui/state/contract` is what the renderer
 * checks itself against at runtime.
 *
 * Two rules govern everything downstream:
 *
 * 1. **A state exists because a subsystem entered it.** Nothing here invents a
 *    state, upgrades one, or keeps one alive past the evidence for it.
 * 2. **Unknown is not idle.** If the renderer has been told nothing, or the
 *    contract has grown a state this build does not know, the honest answer is
 *    "I do not know", never a calm breathing core. A renderer that renders
 *    silence as calm is lying about the most common case.
 */

/** Contract version this client was written against (`CONTRACT_VERSION` in contract.py). */
export const KNOWN_CONTRACT_VERSION = 3;

/**
 * The oldest server contract this build can still read honestly.
 *
 * v3 is purely ADDITIVE over v2 (M18.3 §7): the event shape is unchanged and
 * the only difference is ten new state tokens. A v2 server therefore serves a
 * strict subset of what this build knows, and refusing to draw anything at all
 * because the alarm states have not shipped yet would be a worse lie than
 * saying so in one line. A server NEWER than this build is a different matter —
 * we do not know its vocabulary, so it stays a mismatch.
 */
export const MIN_SUPPORTED_CONTRACT_VERSION = 2;

export type ContractCompatibility =
  /** The server speaks exactly this build's contract. */
  | "current"
  /** Older, but a subset we can read; the new states simply never arrive. */
  | "older_supported"
  /** Too old, or newer than this build. Nothing is drawn from it. */
  | "unsupported";

export function contractCompatibility(version: number): ContractCompatibility {
  if (version === KNOWN_CONTRACT_VERSION) return "current";
  if (version >= MIN_SUPPORTED_CONTRACT_VERSION && version < KNOWN_CONTRACT_VERSION) {
    return "older_supported";
  }
  return "unsupported";
}

/** Bounds copied from the publisher, used to refuse over-long labels defensively. */
export const MAX_LABEL_CHARS = 64;
export const MAX_METADATA_KEYS = 16;

/**
 * `TAIL_SIZE` in `app/uistate/publisher.py`, and the route's `limit` ceiling.
 * Asking for more is a 422, which on every poll would read as an outage.
 */
export const SERVER_TAIL_SIZE = 64;

/**
 * Every state this build knows how to draw. Ordered as in contract.py.
 *
 * The API may legitimately grow past this list (that is a contract change on
 * its side, not a bug here). `classifyState` below routes anything unrecognised
 * to an explicit "unknown state" presentation rather than a default animation.
 */
export const UI_STATES = [
  // the agent itself
  "agent.idle",
  "agent.listening",
  "agent.thinking",
  "agent.speaking",
  "agent.researching",
  "agent.memory_retrieval",
  "agent.tool_running",
  "agent.waiting_owner",
  "agent.goal_completed",
  "agent.error",
  // the evolution lab
  "evolution.researching",
  "evolution.designing",
  "evolution.building",
  "evolution.testing",
  "evolution.shadow_ready",
  // v2 — the room. Local perception, and whether the owner is there.
  "eye.active",
  "eye.disabled",
  "owner.present",
  "owner.away",
  "owner.returned",
  "owner.resting",
  "owner.likely_asleep",
  "owner.awake",
  // v2 — acting on time rather than on request.
  "routine.armed",
  "routine.triggered",
  "alarm.triggered",
  // v2 — the owner-authorised release path (ADR-0055), made watchable.
  "release.owner_approval_required",
  "release.owner_authorized",
  "release.qualifying",
  "release.deploying",
  "release.verifying",
  "release.live",
  "release.rollback",
  // v3 (M18.3 §7) — the durable wake alarm's own lifecycle. Its own channel:
  // an alarm ringing is not the assistant thinking, and it must never displace
  // a core that is genuinely working.
  "alarm.armed",
  "alarm.firing",
  "alarm.playing",
  "alarm.greeting",
  "alarm.snoozed",
  "alarm.stopped",
  "alarm.completed",
  "alarm.failed",
  // v3 — display power, an ambient fact about the room's screens. Drawn on the
  // ambient strip and NEVER on the Core: a dark monitor says nothing about
  // what the agent is doing.
  "display.on",
  "display.off",
] as const;

export type KnownUiState = (typeof UI_STATES)[number];

/**
 * The wake alarm's lifecycle states (v3), in the order the dispatcher enters
 * them. `alarm.triggered` is deliberately NOT here: it is v2's release-band
 * moment ("a routine fired") and keeps its old meaning and its old place.
 */
export const ALARM_STATES = [
  "alarm.armed",
  "alarm.firing",
  "alarm.playing",
  "alarm.greeting",
  "alarm.snoozed",
  "alarm.stopped",
  "alarm.completed",
  "alarm.failed",
] as const;

export type AlarmUiState = (typeof ALARM_STATES)[number];

const ALARM_STATE_SET: ReadonlySet<string> = new Set(ALARM_STATES);

/** True for a v3 wake-alarm lifecycle state this build knows how to draw. */
export function isAlarmLifecycleState(state: string): state is AlarmUiState {
  return ALARM_STATE_SET.has(state);
}

export const DISPLAY_STATES = ["display.on", "display.off"] as const;

export type DisplayUiState = (typeof DISPLAY_STATES)[number];

/** True for a `display.*` state. Ambient band only, by construction. */
export function isDisplayState(state: string): boolean {
  return state.startsWith("display.");
}

/**
 * States that belong to the release band's own vocabulary.
 *
 * `releaseClaim` reads exactly these rather than "the newest event on the
 * release channel", because v3 put the alarm lifecycle on the same channel and
 * an alarm ringing must not blank a deployment that is genuinely in flight.
 */
export function isReleaseBandState(state: string): boolean {
  return (
    state.startsWith("release.") || state.startsWith("routine.") || state === "alarm.triggered"
  );
}

const KNOWN_STATES: ReadonlySet<string> = new Set(UI_STATES);

export function isKnownState(state: string): state is KnownUiState {
  return KNOWN_STATES.has(state);
}

/** Which subsystem published a state (SUBSYSTEMS in contract.py). */
export const SUBSYSTEMS = [
  "voice",
  "research",
  "browser",
  "memory",
  "experience",
  "goal",
  "cognitive",
  "self_model",
  "evolution",
  "deployment",
  "ledger",
  "system",
  "presence",
  // v3: the routine engine publishes the alarm lifecycle, and the ambient
  // policy engine publishes display power.
  "routine",
  "ambient",
] as const;

export type Subsystem = (typeof SUBSYSTEMS)[number];

export const SEVERITIES = ["info", "notice", "warning", "critical"] as const;
export type Severity = (typeof SEVERITIES)[number];

export function isSeverity(value: unknown): value is Severity {
  return typeof value === "string" && (SEVERITIES as readonly string[]).includes(value);
}

/**
 * Metadata values as the publisher permits them: numbers, bools and short
 * tokens. Anything else was already dropped server-side (`_clean_metadata`);
 * this type exists so no consumer can accidentally type a transcript into it.
 */
export type MetadataValue = number | boolean | string;

/**
 * One event exactly as `UiStateEvent.as_dict()` serialises it.
 *
 * `intensity` is the publisher's declared "how much is going on" in 0..1 — for
 * voice it is derived from levels the client already reported. It is NOT an audio
 * sample and must never be described to the owner as one, and it is NOT a
 * confidence: presence publishes its confidence as its own metadata figure,
 * because certainty and activity are different quantities.
 *
 * `progress` is `null` whenever the publisher does not know it, and a renderer
 * must not draw a bar for work of unknown length (ADR-0052 §2).
 */
export type UiStateEvent = {
  event_id: string;
  sequence: number;
  state: string;
  subsystem: string;
  /** ISO-8601 UTC, `Z`-suffixed. */
  at: string;
  intensity: number | null;
  progress: number | null;
  severity: string;
  status: string | null;
  task_id: string | null;
  goal_id: string | null;
  module_id: string | null;
  session_id: string | null;
  label: string | null;
  metadata: Record<string, MetadataValue>;
};

/** The body of `GET /v1/ui/state`. */
export type UiStateResponse = {
  contract_version: number;
  current: UiStateEvent | null;
  events: UiStateEvent[];
  sequence: number;
};

/** The body of `GET /v1/ui/state/contract`. */
export type UiStateContract = {
  contract_version: number;
  states: string[];
  subsystems: string[];
  severities: string[];
  metadata_rules: {
    max_keys: number;
    value_kinds: string[];
    forbidden: string;
  };
  intensity: string;
  progress: string;
};

// --------------------------------------------------------------- state kinds

/**
 * How long a state's claim stays true after the event that made it.
 *
 * This is the single most important honesty decision in the client. The bus
 * publishes *entries* into states, never exits: nothing ever says "the agent
 * stopped thinking". So a `thinking` event is a statement about a moment, and
 * the only truthful thing to do with an old one is stop claiming it.
 *
 * - `steady`  — the state describes a condition that persists until something
 *               else is published (idle, blocked on the owner, failed, a
 *               candidate sitting at SHADOW_READY). Never expires.
 * - `transient` — the state describes work in flight. If no newer event has
 *               arrived within `TRANSIENT_TTL_MS`, the client stops claiming it
 *               and shows "last known" instead. It does NOT fall back to idle:
 *               we were not told the work stopped, only that we stopped hearing.
 * - `moment`  — a thing that happened at an instant (a goal completing). Shown
 *               prominently for `MOMENT_TTL_MS`, then treated as last-known.
 * - `observation` — v2. A statement about the *room* from local perception: the
 *               owner was present, was likely asleep. These decay, and the
 *               decay is the honest part — a camera observation from forty
 *               minutes ago is not evidence about now, so it expires to
 *               unknown rather than to "still present" (M18 spec §1).
 * - `operation` — v2. A stage of a long, watched operation (a release
 *               qualifying, deploying, verifying). Minutes are normal here, so
 *               a 12-second transient TTL would report a healthy deployment as
 *               lost; but it still expires, because a `deploying` from
 *               yesterday is not a deployment happening now.
 */
export type StateKind = "steady" | "transient" | "moment" | "observation" | "operation";

const STATE_KINDS: Record<KnownUiState, StateKind> = {
  "agent.idle": "steady",
  "agent.listening": "transient",
  "agent.thinking": "transient",
  "agent.speaking": "transient",
  "agent.researching": "transient",
  "agent.memory_retrieval": "transient",
  "agent.tool_running": "transient",
  "agent.waiting_owner": "steady",
  "agent.goal_completed": "moment",
  "agent.error": "steady",
  "evolution.researching": "transient",
  "evolution.designing": "transient",
  "evolution.building": "transient",
  "evolution.testing": "transient",
  "evolution.shadow_ready": "steady",
  // The eye is either running or it is not; that holds until something changes it.
  "eye.active": "steady",
  "eye.disabled": "steady",
  // Presence decays. Every one of these is an inference from evidence with an age.
  "owner.present": "observation",
  "owner.away": "observation",
  "owner.returned": "moment",
  "owner.resting": "observation",
  "owner.likely_asleep": "observation",
  "owner.awake": "observation",
  // An armed routine stays armed; firing is an instant.
  "routine.armed": "steady",
  "routine.triggered": "moment",
  "alarm.triggered": "moment",
  // The release path: waiting-on-owner and terminal stages hold, work stages decay.
  "release.owner_approval_required": "steady",
  "release.owner_authorized": "steady",
  "release.qualifying": "operation",
  "release.deploying": "operation",
  "release.verifying": "operation",
  "release.live": "steady",
  "release.rollback": "steady",
  // v3. An armed alarm is a standing arrangement; the ringing states are a
  // watched operation; the terminal states are moments. All of them are bounded
  // by `STATE_TTL_MS` below, because "armed" from three days ago is not an
  // alarm that is armed now.
  "alarm.armed": "steady",
  "alarm.firing": "operation",
  "alarm.playing": "operation",
  "alarm.greeting": "operation",
  "alarm.snoozed": "moment",
  "alarm.stopped": "moment",
  "alarm.completed": "moment",
  "alarm.failed": "moment",
  // The display is on or off until something changes it.
  "display.on": "steady",
  "display.off": "steady",
};

/**
 * Per-state lifetimes for v3, in ms, exactly as `docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md`
 * §7 states them.
 *
 * They exist because these states do not fit the five kinds: an armed alarm is
 * steady in nature but must not be claimed for ever (a twelve-hour-old "armed"
 * is the horizon of one night), and the ringing states last minutes rather than
 * the twelve seconds a transient gets. The publisher's own `ttl_s` still beats
 * every figure here.
 */
const STATE_TTL_MS: Partial<Record<KnownUiState, number>> = {
  "alarm.armed": 12 * 60 * 60_000,
  "alarm.firing": 120_000,
  "alarm.playing": 20 * 60_000,
  "alarm.greeting": 60_000,
  "alarm.snoozed": 5 * 60_000,
  "alarm.stopped": 5 * 60_000,
  "alarm.completed": 5 * 60_000,
  "alarm.failed": 5 * 60_000,
  "display.on": 24 * 60 * 60_000,
  "display.off": 24 * 60 * 60_000,
};

/**
 * A transient state older than this is no longer claimed as current.
 *
 * Chosen against the real publishers rather than for looks: voice publishes on
 * every turn boundary (sub-second), research once per ranking stage, the
 * self-model indexer once per phase. Twelve seconds is comfortably longer than
 * any of those gaps and short enough that a dropped poll is visible to the
 * owner rather than silently drawn as ongoing work.
 */
export const TRANSIENT_TTL_MS = 12_000;

/** How long `agent.goal_completed` stays a headline before becoming history. */
export const MOMENT_TTL_MS = 20_000;

/**
 * Default lifetime of a perception observation, when the publisher did not say.
 *
 * Deliberately the client's *conservative* guess and nothing more: the presence
 * engine owns the real staleness policy, and when it publishes `ttl_s` that
 * figure wins (see `stateTtlMs`). Five minutes is short enough that an owner who
 * left the room is not still drawn as present, and long enough that a presence
 * state which is only republished on change does not flicker to unknown.
 */
export const OBSERVATION_TTL_MS = 300_000;

/** Default lifetime of a release stage. Deployments take minutes, not seconds. */
export const OPERATION_TTL_MS = 900_000;

export function stateKind(state: string): StateKind {
  return isKnownState(state) ? STATE_KINDS[state] : "transient";
}

const DEFAULT_TTL_MS: Record<StateKind, number> = {
  steady: Number.POSITIVE_INFINITY,
  transient: TRANSIENT_TTL_MS,
  moment: MOMENT_TTL_MS,
  observation: OBSERVATION_TTL_MS,
  operation: OPERATION_TTL_MS,
};

/**
 * How long this state may be claimed as current, in ms; `Infinity` for steady.
 *
 * `event` is optional so callers that only have a token still get the default.
 * When the publisher sent `ttl_s`, it is preferred over every default here: the
 * subsystem that made the observation knows how long it is good for, and the
 * client guessing over the top of that would be the renderer inventing truth.
 */
export function stateTtlMs(state: string, event?: UiStateEvent | null): number {
  const declared = event ? metaNumber(event, "ttl_s") : null;
  if (declared !== null && declared > 0) return declared * 1000;
  const perState = isKnownState(state) ? STATE_TTL_MS[state] : undefined;
  if (perState !== undefined) return perState;
  return DEFAULT_TTL_MS[stateKind(state)];
}

/**
 * Which conversation a state belongs to.
 *
 * v2 put four different kinds of statement on one bus, and they must not
 * displace one another. `owner.likely_asleep` is a fact about the room; it is
 * not the agent going quiet, and publishing it must never blank a core that is
 * genuinely thinking. So the core body draws the `agent`/`lab` channels, and
 * ambient and release are drawn as their own bands with their own ages.
 */
export type StateChannel = "agent" | "lab" | "ambient" | "release";

export function stateChannel(state: string): StateChannel {
  if (state.startsWith("evolution.")) return "lab";
  // v3 adds `display.*` to the room: whether the screens are lit is a fact
  // about the owner's desk, never about the agent's activity.
  if (state.startsWith("eye.") || state.startsWith("owner.") || state.startsWith("display."))
    return "ambient";
  // v3's alarm lifecycle joins v2's `alarm.triggered` off the core body. The
  // band splits them again by vocabulary (`isReleaseBandState`), so a ringing
  // alarm cannot blank a deployment.
  if (state.startsWith("release.") || state.startsWith("routine.") || state.startsWith("alarm."))
    return "release";
  return "agent";
}

/** States that drive the core body itself. */
export function isCoreChannel(state: string): boolean {
  const channel = stateChannel(state);
  return channel === "agent" || channel === "lab";
}

/** States published by the evolution lab. Never mixed with the agent's own. */
export function isEvolutionState(state: string): boolean {
  return state.startsWith("evolution.");
}

/** Presence inferences. Every one of these is probabilistic and carries a confidence. */
export function isPresenceState(state: string): boolean {
  return state.startsWith("owner.");
}

// ------------------------------------------------------------------ parsing

function asFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clamp01(value: number | null): number | null {
  if (value === null) return null;
  return Math.max(0, Math.min(1, value));
}

/**
 * Parse one event defensively.
 *
 * The API is trusted to be well-formed, but this is the boundary where a
 * partially-deployed API or a proxy could hand us something else, and a
 * renderer that throws takes the whole page with it. Anything unusable becomes
 * `null` (unknown) rather than a plausible-looking default.
 */
export function parseEvent(raw: unknown): UiStateEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const state = typeof o.state === "string" ? o.state : null;
  const at = typeof o.at === "string" ? o.at : null;
  if (!state || !at) return null;

  const metadata: Record<string, MetadataValue> = {};
  const rawMeta = o.metadata;
  if (rawMeta && typeof rawMeta === "object") {
    for (const [key, value] of Object.entries(rawMeta as Record<string, unknown>).slice(
      0,
      MAX_METADATA_KEYS,
    )) {
      if (typeof value === "number" && Number.isFinite(value)) metadata[key] = value;
      else if (typeof value === "boolean") metadata[key] = value;
      else if (typeof value === "string") metadata[key] = value.slice(0, MAX_LABEL_CHARS);
      // objects/arrays are content-shaped; the publisher drops them and so do we
    }
  }

  return {
    event_id: typeof o.event_id === "string" ? o.event_id : "",
    sequence: asFiniteNumber(o.sequence) ?? 0,
    state,
    subsystem: typeof o.subsystem === "string" ? o.subsystem : "system",
    at,
    intensity: clamp01(asFiniteNumber(o.intensity)),
    progress: clamp01(asFiniteNumber(o.progress)),
    severity: isSeverity(o.severity) ? o.severity : "info",
    status: typeof o.status === "string" && o.status ? o.status : null,
    task_id: typeof o.task_id === "string" && o.task_id ? o.task_id : null,
    goal_id: typeof o.goal_id === "string" && o.goal_id ? o.goal_id : null,
    module_id: typeof o.module_id === "string" && o.module_id ? o.module_id : null,
    session_id: typeof o.session_id === "string" && o.session_id ? o.session_id : null,
    label: typeof o.label === "string" && o.label ? o.label.slice(0, MAX_LABEL_CHARS) : null,
    metadata,
  };
}

export function parseResponse(raw: unknown): UiStateResponse | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const events = Array.isArray(o.events)
    ? o.events.map(parseEvent).filter((e): e is UiStateEvent => e !== null)
    : [];
  return {
    contract_version: asFiniteNumber(o.contract_version) ?? 0,
    current: parseEvent(o.current),
    events,
    sequence: asFiniteNumber(o.sequence) ?? 0,
  };
}

/** Milliseconds since `at`, or `null` when the timestamp is unusable. */
export function ageMs(event: UiStateEvent, now: number): number | null {
  const at = Date.parse(event.at);
  if (Number.isNaN(at)) return null;
  return Math.max(0, now - at);
}

/**
 * A number the publisher actually sent, under one of the given keys.
 *
 * The single accessor for every "how many sources / how many lessons" question
 * in the renderer, and the reason the empty states are truthful: when nothing
 * published a count, this returns `null` and the caller draws nothing. There is
 * deliberately no default-to-something overload.
 */
export function metaNumber(
  event: UiStateEvent | null,
  ...keys: string[]
): number | null {
  if (!event) return null;
  for (const key of keys) {
    const value = event.metadata[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

/** A short token the publisher actually sent, under one of the given keys. */
export function metaToken(event: UiStateEvent | null, ...keys: string[]): string | null {
  if (!event) return null;
  for (const key of keys) {
    const value = event.metadata[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}
