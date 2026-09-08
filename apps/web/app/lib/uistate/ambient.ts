/**
 * The room and the release path — contract v2's two new channels.
 *
 * These are drawn beside the core, never as the core, because they are not the
 * agent's activity: `owner.likely_asleep` is a fact about the room and
 * `release.deploying` is an operation being watched. Both would be lies if they
 * displaced a genuinely thinking core (see `coreClaim` in `truth.ts`).
 *
 * Two honesty rules specific to this file:
 *
 * 1. **Presence is probabilistic and says so.** Every presence state carries a
 *    confidence the engine computed. This module never fills one in, never
 *    rounds one to certainty, and the Turkish labels say "büyük olasılıkla"
 *    where the state says `likely`. A presence state with no confidence is
 *    shown as a presence state with no confidence — it is not promoted to a fact.
 * 2. **Perception is not identity.** Nothing here derives an owner *identity*
 *    from a presence observation, and there is no field in which one could be
 *    carried. Presence is an interaction signal and never an authorisation
 *    (M18 spec §2; ADR-0055 keeps release authority with the authenticated
 *    owner session).
 *
 * The release channel is presentation only. Nothing in this client can start,
 * authorise or advance a deployment; this channel has no write path at all.
 */

import { type Severity, type UiStateEvent, isSeverity, metaNumber, metaToken } from "./contract";
import type { Claim } from "./truth";

// ------------------------------------------------------------------ the eye

export type EyeStatus =
  /** `eye.active` is current: local perception is running. */
  | "active"
  /** `eye.disabled` is current: the owner turned it off, or it never started. */
  | "disabled"
  /** Nothing has been published about the camera at all. Not the same as off. */
  | "untold";

export type EyeView = {
  status: EyeStatus;
  /** Short device token the publisher sent, e.g. a camera label. Never imagery. */
  camera: string | null;
  ageMs: number | null;
  /** True once the claim has decayed; the status is then last-known, not now. */
  expired: boolean;
  /**
   * An `eye.*` state arrived that this build does not know.
   *
   * It falls to `untold` rather than to `disabled`, and this flag exists so the
   * indicator can say *why*. A privacy indicator claiming the camera is off on
   * the strength of a state it could not read is the one failure mode this
   * whole channel must not have.
   */
  unknownState: boolean;
};

export function eyeView(claim: Claim): EyeView {
  const event = claim.event;
  const state = event?.state;
  const status: EyeStatus =
    state === "eye.active" ? "active" : state === "eye.disabled" ? "disabled" : "untold";
  return {
    status,
    camera: metaToken(event, "camera", "device"),
    ageMs: claim.ageMs,
    expired: claim.expired,
    unknownState: Boolean(state?.startsWith("eye.")) && status === "untold",
  };
}

// ------------------------------------------------------------- the presence

export type PresenceKind =
  | "present"
  | "away"
  | "returned"
  | "resting"
  | "likely_asleep"
  | "awake"
  /** No presence state has been published, or the last one decayed. */
  | "unknown";

export type PresenceView = {
  kind: PresenceKind;
  /**
   * The engine's confidence in 0..1, or `null` when it published none.
   *
   * `null` is a real answer and must be rendered as one. Substituting a default
   * would turn "we do not know how sure we are" into a number the owner would
   * reasonably read as a measurement.
   */
  confidence: number | null;
  /** How many independent signals the engine said it fused, when it said. */
  signals: number | null;
  ageMs: number | null;
  /** True when the observation has decayed. A decayed presence is `unknown`. */
  expired: boolean;
  /** The state token before expiry, so the readout can say what it *was*. */
  lastKnown: PresenceKind | null;
};

const PRESENCE_KIND: Record<string, PresenceKind> = {
  "owner.present": "present",
  "owner.away": "away",
  "owner.returned": "returned",
  "owner.resting": "resting",
  "owner.likely_asleep": "likely_asleep",
  "owner.awake": "awake",
};

export function presenceView(claim: Claim): PresenceView {
  const event = claim.event;
  const kind = event ? (PRESENCE_KIND[event.state] ?? null) : null;
  // An expired observation degrades to unknown, never to "still present": the
  // whole reason presence has a TTL is that an old frame is not evidence about
  // now (M18 spec §1).
  const effective: PresenceKind = kind === null || claim.expired ? "unknown" : kind;
  return {
    kind: effective,
    confidence: metaNumber(event, "confidence"),
    signals: metaNumber(event, "signals"),
    ageMs: claim.ageMs,
    expired: claim.expired,
    lastKnown: kind,
  };
}

/** True while the presence claim is one the owner should read as about *now*. */
export function presenceIsCurrent(view: PresenceView): boolean {
  return view.kind !== "unknown";
}

// -------------------------------------------------------------- the release

export type ReleaseStage =
  | "owner_approval_required"
  | "owner_authorized"
  | "qualifying"
  | "deploying"
  | "verifying"
  | "live"
  | "rollback"
  | "routine_armed"
  | "routine_triggered"
  | "alarm_triggered"
  | "none";

export type ReleaseView = {
  stage: ReleaseStage;
  /** Which candidate/module the stage is about, when the publisher said. */
  moduleId: string | null;
  /** Declared risk tier 1..5, or `null`. Never inferred from the stage. */
  riskTier: number | null;
  progress: number | null;
  ageMs: number | null;
  expired: boolean;
  /** True while the owner is the thing being waited on. */
  awaitingOwner: boolean;
  /** True while a production mutation is genuinely in flight. */
  inFlight: boolean;
};

const RELEASE_STAGE: Record<string, ReleaseStage> = {
  "release.owner_approval_required": "owner_approval_required",
  "release.owner_authorized": "owner_authorized",
  "release.qualifying": "qualifying",
  "release.deploying": "deploying",
  "release.verifying": "verifying",
  "release.live": "live",
  "release.rollback": "rollback",
  "routine.armed": "routine_armed",
  "routine.triggered": "routine_triggered",
  "alarm.triggered": "alarm_triggered",
};

const IN_FLIGHT: ReadonlySet<ReleaseStage> = new Set<ReleaseStage>([
  "deploying",
  "verifying",
  "rollback",
]);

export function releaseView(claim: Claim): ReleaseView {
  const event = claim.event;
  const stage = event ? (RELEASE_STAGE[event.state] ?? "none") : "none";
  const effective: ReleaseStage = claim.expired ? "none" : stage;
  return {
    stage: effective,
    moduleId: event?.module_id ?? null,
    riskTier: metaNumber(event, "risk_tier"),
    progress: event?.progress ?? null,
    ageMs: claim.ageMs,
    expired: claim.expired,
    awaitingOwner: effective === "owner_approval_required",
    inFlight: IN_FLIGHT.has(effective),
  };
}

/** The one thing a viewer of this channel must never conclude. */
export const RELEASE_AUTHORITY_NOTE =
  "Bu ekran yalnızca gösterir. Üretime alma yetkisi doğrulanmış sahip oturumundadır.";

export function eventIsAmbient(event: UiStateEvent): boolean {
  return (
    event.state.startsWith("eye.") ||
    event.state.startsWith("owner.") ||
    event.state.startsWith("display.")
  );
}

// ------------------------------------------------- v3: the display (M18.3 §7)

export type DisplayState =
  /** `display.on` is current. */
  | "on"
  /** `display.off` is current: the screens were powered down, not the machine. */
  | "off"
  /** Nothing has been published about the screens. Not the same as "off". */
  | "untold";

export type DisplayView = {
  state: DisplayState;
  ageMs: number | null;
  expired: boolean;
  /** A `display.*` state this build cannot read. Falls to `untold`, and says so. */
  unknownState: boolean;
  /** Why the publisher said the screens changed, when it said. Never inferred. */
  reason: string | null;
};

export function displayView(claim: Claim): DisplayView {
  const event = claim.event;
  const token = event?.state;
  const state: DisplayState =
    token === "display.on" ? "on" : token === "display.off" ? "off" : "untold";
  return {
    state,
    ageMs: claim.ageMs,
    expired: claim.expired,
    unknownState: Boolean(token?.startsWith("display.")) && state === "untold",
    reason: metaToken(event, "reason"),
  };
}

/** The display is never drawn on the Core. Stated here so the rule has a home. */
export const DISPLAY_IS_AMBIENT_ONLY =
  "Ekran gücü ortam bilgisidir; çekirdeğin ne yaptığını anlatmaz.";

// --------------------------------------------- v3: the wake alarm (M18.3 §7)

export type AlarmStage =
  | "armed"
  | "firing"
  | "playing"
  | "greeting"
  | "snoozed"
  | "stopped"
  | "completed"
  | "failed"
  /** No wake-alarm lifecycle state is current. */
  | "none";

export type AlarmView = {
  stage: AlarmStage;
  /** The publisher's short label ("Alarm 07:30"). Never prose, never invented. */
  label: string | null;
  /**
   * The ramp level the publisher declared while playing, 0..1, or `null`.
   *
   * `null` is a real answer: the alarm is sounding but nobody said how loud.
   * The surge is then drawn at its per-stage constant and the readout says the
   * level was not reported — it is never substituted with a plausible figure.
   */
  level: number | null;
  /** True when the alarm was created as a test (`is_test`), as published. */
  isTest: boolean;
  severity: Severity;
  ageMs: number | null;
  expired: boolean;
};

const ALARM_STAGE: Record<string, AlarmStage> = {
  "alarm.armed": "armed",
  "alarm.firing": "firing",
  "alarm.playing": "playing",
  "alarm.greeting": "greeting",
  "alarm.snoozed": "snoozed",
  "alarm.stopped": "stopped",
  "alarm.completed": "completed",
  "alarm.failed": "failed",
};

/** The stages during which an alarm is actually making a noise in the room. */
const ALARM_SOUNDING: ReadonlySet<AlarmStage> = new Set<AlarmStage>([
  "firing",
  "playing",
  "greeting",
]);

export function alarmView(claim: Claim): AlarmView {
  const event = claim.event;
  const stage = event ? (ALARM_STAGE[event.state] ?? "none") : "none";
  // An expired alarm claim is not a quiet alarm: it is an alarm we have stopped
  // being told about. The stage drops to `none` (nothing is drawn) and the
  // strip words the age, exactly as a decayed presence does.
  const effective: AlarmStage = claim.expired ? "none" : stage;
  return {
    stage: effective,
    label: event?.label ?? null,
    level: effective === "none" ? null : (event?.intensity ?? null),
    isTest: metaFlag(event, "is_test"),
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the alarm is sounding: the only time the Core shows a surge. */
export function alarmIsSounding(view: AlarmView): boolean {
  return ALARM_SOUNDING.has(view.stage);
}

/** A boolean the publisher actually sent. Absent is `false`, never assumed true. */
function metaFlag(event: UiStateEvent | null | undefined, key: string): boolean {
  if (!event) return false;
  return event.metadata[key] === true;
}
