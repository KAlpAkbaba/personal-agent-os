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
  type StateChannel,
  type UiStateEvent,
  type UiStateResponse,
  ageMs,
  isAlarmLifecycleState,
  isCoreChannel,
  isReleaseBandState,
  isSeverity,
  stateChannel,
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
  /**
   * The contract version the server actually answered with, or `null` before
   * the first successful poll.
   *
   * Kept because a v2 server and a v3 build differ in what the owner may
   * expect to see — the alarm and display states simply never arrive — and the
   * honest thing is to say so rather than to let their absence read as "no
   * alarm is set" (M18.3 §7; `contractCompatibility`).
   */
  contractVersion: number | null;
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
    contractVersion: null,
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
    contractVersion: response.contract_version,
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
  return claimFor(truth.current, now);
}

function claimFor(event: UiStateEvent | null, now: number): Claim {
  if (!event) return { event: null, ageMs: null, expired: false };
  const age = ageMs(event, now);
  if (age === null) return { event, ageMs: null, expired: false };
  return { event, ageMs: age, expired: age > stateTtlMs(event.state, event) };
}

/** The newest event the client holds whose state token satisfies `predicate`. */
export function newestWhere(
  truth: CoreTruth,
  predicate: (state: string) => boolean,
): UiStateEvent | null {
  let best: UiStateEvent | null = null;
  for (const event of Object.values(truth.latestByState)) {
    if (!predicate(event.state)) continue;
    if (!best || event.sequence >= best.sequence) best = event;
  }
  return best;
}

/** The newest event the client holds on `channel`, expired or not. */
export function newestOn(truth: CoreTruth, channel: StateChannel): UiStateEvent | null {
  return newestWhere(truth, (state) => stateChannel(state) === channel);
}

/**
 * The still-claimable event for one *sub*-channel, addressed by state prefix.
 *
 * The ambient channel carries two independent facts — whether the camera is
 * perceiving and whether the owner is there — and they must not overwrite each
 * other. A published `owner.present` says nothing about the camera, so reading
 * the eye's status from "the newest ambient event" would let a presence update
 * silently blank the privacy indicator.
 */
export function prefixClaim(truth: CoreTruth, prefix: string, now: number): Claim {
  return claimFor(
    newestWhere(truth, (state) => state.startsWith(prefix)),
    now,
  );
}

/** Whether local perception is running, from `eye.*` alone. */
export function eyeClaim(truth: CoreTruth, now: number): Claim {
  return prefixClaim(truth, "eye.", now);
}

/** Where the owner is, from `owner.*` alone. */
export function presenceClaim(truth: CoreTruth, now: number): Claim {
  return prefixClaim(truth, "owner.", now);
}

/**
 * The release path and the routines that drive it.
 *
 * Read by vocabulary rather than by channel: v3 put the wake alarm's lifecycle
 * on the same band, and "the newest event on the release channel" would let an
 * `alarm.playing` blank a deployment that is genuinely in flight — the same
 * class of mistake `prefixClaim` exists to prevent for the eye.
 */
export function releaseClaim(truth: CoreTruth, now: number): Claim {
  return claimFor(newestWhere(truth, isReleaseBandState), now);
}

/**
 * The wake alarm's own claim: the newest v3 lifecycle state, and nothing else.
 *
 * Deliberately not a prefix claim over `alarm.`: v2's `alarm.triggered` means
 * "a routine fired" and belongs to the release band, and an unknown `alarm.*`
 * token from a newer server must not be drawn as a ringing alarm on the
 * strength of a word this build cannot read.
 */
export function alarmClaim(truth: CoreTruth, now: number): Claim {
  return claimFor(newestWhere(truth, isAlarmLifecycleState), now);
}

/** Whether the owner's screens are lit, from `display.*` alone. */
export function displayClaim(truth: CoreTruth, now: number): Claim {
  return prefixClaim(truth, "display.", now);
}

/**
 * What the core body may claim right now.
 *
 * Contract v2 put the room (`eye.*`, `owner.*`) and the release path on the same
 * bus as the agent's own activity, so the API's `current` is no longer the same
 * question as "what is the agent doing". A published `owner.likely_asleep` must
 * not blank a core that is genuinely thinking — the owner going to bed is not the
 * assistant stopping work — so the body reads the newest agent/lab event and the
 * other channels are drawn beside it, each with its own age.
 */
export function coreClaim(truth: CoreTruth, now: number): Claim {
  const current = truth.current;
  if (current && isCoreChannel(current.state)) return claimFor(current, now);
  const agent = newestOn(truth, "agent");
  const lab = newestOn(truth, "lab");
  const newest = !agent ? lab : !lab ? agent : lab.sequence > agent.sequence ? lab : agent;
  // No agent/lab event at all is not the same as no event at all: the client has
  // been told something, just nothing about what the agent is doing. `null` here
  // renders as "untold", which is exactly that statement.
  return claimFor(newest, now);
}

/** The still-claimable event on `channel`, or `null` once it has decayed. */
export function channelClaim(truth: CoreTruth, channel: StateChannel, now: number): Claim {
  return claimFor(newestOn(truth, channel), now);
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
  return age > stateTtlMs(state, event) ? null : event;
}

/** Highest severity among events still live, for the cockpit's health line. */
export function liveSeverity(truth: CoreTruth, now: number): Severity {
  const order: Severity[] = ["info", "notice", "warning", "critical"];
  let worst: Severity = "info";
  for (const event of Object.values(truth.latestByState)) {
    const age = ageMs(event, now);
    if (age !== null && age > stateTtlMs(event.state, event)) continue;
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
