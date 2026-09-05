/**
 * What the client actually knows, reduced from the events it was given.
 *
 * Pure: no fetch, no clock of its own, no React. Every function takes `now` so
 * the whole model is testable to the millisecond, and so the renderer can never
 * accidentally derive a visual from wall-clock drift instead of from evidence.
 *
 * The type is called `CoreTruth` rather than `CoreState` on purpose. Its job is
 * to hold the difference between four things a sloppier model would collapse:
 *
 *   - the system told us it is idle            → `agent.idle` is current
 *   - the system told us nothing yet           → `current === null`
 *   - the system told us something, a while ago → current, but `expired`
 *   - we cannot reach the system at all        → `connection.kind !== "live"`
 *
 * Only the first of those is calm. The other three are shown as what they are.
 */

import {
  type Severity,
  type UiStateEvent,
  type UiStateResponse,
  ageMs,
  isSeverity,
  stateTtlMs,
} from "./contract";

/** How many events the client keeps for the cockpit's stream. Matches TAIL_SIZE. */
export const TAIL_LIMIT = 64;

export type Connection =
  /** No poll has completed yet. */
  | { kind: "connecting" }
  /** A poll succeeded; `at` is when. */
  | { kind: "live"; at: number }
  /** Reachable but the last poll failed; the last known picture is still shown. */
  | { kind: "unreachable"; error: string; since: number }
  /** The API refused the session. Nothing is known and nothing is drawn. */
  | { kind: "unauthorized" }
  /** The API speaks a contract version this build was not written against. */
  | { kind: "contract_mismatch"; version: number };

export type CoreTruth = {
  connection: Connection;
  /** Highest sequence the client has seen, sent back as `after_sequence`. */
  sequence: number;
  /** The API's `current` — the newest event overall, or null if never told anything. */
  current: UiStateEvent | null;
  /** Recent events, oldest first, bounded to `TAIL_LIMIT`. */
  recent: UiStateEvent[];
  /** The newest event seen for each state token. */
  latestByState: Record<string, UiStateEvent>;
  /** The newest event seen for each publishing subsystem. */
  latestBySubsystem: Record<string, UiStateEvent>;
  /** Completed polls. Zero means "we have not asked yet", which is not "idle". */
  polls: number;
};

export function emptyTruth(): CoreTruth {
  return {
    connection: { kind: "connecting" },
    sequence: 0,
    current: null,
    recent: [],
    latestByState: {},
    latestBySubsystem: {},
    polls: 0,
  };
}

function newer(a: UiStateEvent | undefined, b: UiStateEvent): UiStateEvent {
  if (!a) return b;
  return b.sequence >= a.sequence ? b : a;
}

/**
 * Fold one successful poll into the model.
 *
 * `current` is taken from the API rather than from the tail: the tail may be
 * empty (nothing changed since our last sequence) while the current state is
 * unchanged and still true. An empty `events` list with an unchanged `current`
 * is itself information — nothing happened — and must not be mistaken for a
 * gap in the stream.
 */
export function applyResponse(
  truth: CoreTruth,
  response: UiStateResponse,
  now: number,
): CoreTruth {
  const seen = new Set(truth.recent.map((e) => e.sequence));
  const merged = [...truth.recent];
  for (const event of response.events) {
    if (seen.has(event.sequence)) continue;
    seen.add(event.sequence);
    merged.push(event);
  }
  merged.sort((a, b) => a.sequence - b.sequence);
  const recent = merged.slice(-TAIL_LIMIT);

  const latestByState = { ...truth.latestByState };
  const latestBySubsystem = { ...truth.latestBySubsystem };
  for (const event of response.events) {
    latestByState[event.state] = newer(latestByState[event.state], event);
    latestBySubsystem[event.subsystem] = newer(latestBySubsystem[event.subsystem], event);
  }
  // The API's `current` may predate our tail window (nothing new since we last
  // asked). Index it too, so a state that stopped being re-published is still
  // attributed to the subsystem that last held it.
  if (response.current) {
    const c = response.current;
    latestByState[c.state] = newer(latestByState[c.state], c);
    latestBySubsystem[c.subsystem] = newer(latestBySubsystem[c.subsystem], c);
  }

  return {
    connection: { kind: "live", at: now },
    sequence: Math.max(truth.sequence, response.sequence, ...recent.map((e) => e.sequence), 0),
    current: response.current,
    recent,
    latestByState,
    latestBySubsystem,
    polls: truth.polls + 1,
  };
}

/**
 * Record a failed poll without discarding what we already knew.
 *
 * The last known picture stays on screen — but `connection` changes, and every
 * consumer renders that difference, so the owner is never shown a stale core
 * that looks live.
 */
export function applyError(truth: CoreTruth, error: string, now: number): CoreTruth {
  const since = truth.connection.kind === "unreachable" ? truth.connection.since : now;
  return { ...truth, connection: { kind: "unreachable", error, since } };
}

/**
 * Takes no prior state on purpose: everything the client knew was read with a
 * session the API has just refused, so none of it may be carried forward.
 */
export function applyUnauthorized(): CoreTruth {
  return { ...emptyTruth(), connection: { kind: "unauthorized" } };
}

export function applyContractMismatch(truth: CoreTruth, version: number): CoreTruth {
  return { ...truth, connection: { kind: "contract_mismatch", version } };
}

// ------------------------------------------------------------------- claims

/**
 * What may honestly be claimed about the current state right now.
 *
 * `expired` is the whole point: the bus publishes entries into states and never
 * exits, so an old `agent.thinking` is evidence that it *was* thinking, not
 * that it *is*. An expired claim is rendered as last-known, never as idle and
 * never as ongoing work.
 */
export type Claim = {
  event: UiStateEvent | null;
  /** Age of the event in ms, `null` when unknown or when there is no event. */
  ageMs: number | null;
  /** True when the event is older than its state's TTL. */
  expired: boolean;
};

export function currentClaim(truth: CoreTruth, now: number): Claim {
  const event = truth.current;
  if (!event) return { event: null, ageMs: null, expired: false };
  const age = ageMs(event, now);
  if (age === null) return { event, ageMs: null, expired: false };
  return { event, ageMs: age, expired: age > stateTtlMs(event.state) };
}

/**
 * The latest event for `state`, but only while it may still be claimed.
 *
 * Used by the cockpit panels: "is the lab building something?" is answered by a
 * live `evolution.building`, not by one from twenty minutes ago.
 */
export function liveEventFor(
  truth: CoreTruth,
  state: string,
  now: number,
): UiStateEvent | null {
  const event = truth.latestByState[state];
  if (!event) return null;
  const age = ageMs(event, now);
  if (age === null) return event;
  return age > stateTtlMs(state) ? null : event;
}

/** Highest severity among events still live, for the cockpit's health line. */
export function liveSeverity(truth: CoreTruth, now: number): Severity {
  const order: Severity[] = ["info", "notice", "warning", "critical"];
  let worst: Severity = "info";
  for (const event of Object.values(truth.latestByState)) {
    const age = ageMs(event, now);
    if (age !== null && age > stateTtlMs(event.state)) continue;
    if (!isSeverity(event.severity)) continue;
    if (order.indexOf(event.severity) > order.indexOf(worst)) worst = event.severity;
  }
  return worst;
}

/** Events for the cockpit stream, newest first. */
export function recentDescending(truth: CoreTruth, limit = 12): UiStateEvent[] {
  return truth.recent.slice(-limit).toReversed();
}

/**
 * Whether the client has ever been told anything at all.
 *
 * Distinct from "the agent is idle": before the first event of a process's
 * life the bus genuinely holds nothing, and `GET /v1/ui/state` answers
 * `current: null`. The renderer says so rather than drawing a calm core.
 */
export function hasEverBeenTold(truth: CoreTruth): boolean {
  return truth.current !== null || truth.recent.length > 0;
}
