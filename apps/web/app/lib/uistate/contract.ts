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
export const KNOWN_CONTRACT_VERSION = 1;

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
] as const;

export type KnownUiState = (typeof UI_STATES)[number];

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
 * voice it is derived from levels the client already reported, for research it
 * is a fixed per-stage figure. It is NOT an audio sample and must never be
 * described to the owner as one.
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
 */
export type StateKind = "steady" | "transient" | "moment";

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

export function stateKind(state: string): StateKind {
  return isKnownState(state) ? STATE_KINDS[state] : "transient";
}

/** How long this state may be claimed as current, in ms; `Infinity` for steady. */
export function stateTtlMs(state: string): number {
  const kind = stateKind(state);
  if (kind === "steady") return Number.POSITIVE_INFINITY;
  return kind === "moment" ? MOMENT_TTL_MS : TRANSIENT_TTL_MS;
}

/** States published by the evolution lab. Never mixed with the agent's own. */
export function isEvolutionState(state: string): boolean {
  return state.startsWith("evolution.");
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
