/**
 * Executive Autonomy's channel — contract v11 (M26 spec §6).
 *
 * The Cloud Core publishes `executive.run` at every transition of ONE
 * durable run of a task graph — planned, running, paused by the owner,
 * resumed, and ended in one of the four honest ends the spec allows — with
 * `{run, step, state, done, total}` in its metadata: the run's short id,
 * the step id it is on, the state in the §3 names, and how many of the
 * graph's steps have finished out of how many there are.
 *
 * The rules are the scene channel's, applied to a job that spans families:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    run is the id the publisher sent; the step is the step id it sent; the
 *    counts are the numbers it counted. A missing key is rendered as "not
 *    reported", never filled in — and in particular a run with no counts is
 *    never "0/0 adım", because nobody counted, and a `done` without a
 *    `total` is no progress at all: 3 of an unknown number is not a
 *    fraction.
 * 2. **A state this build cannot read settles nothing.** A `state` outside
 *    the seven the contract names draws as a run in progress and reads as
 *    the bare token: a word we cannot read is not an end we may narrate,
 *    and above all it is never "tamamlandı". This is the M24/M25 defect
 *    written as a rule — a Core that could not read `cancelled` drew a run
 *    the owner had given up on as one still working.
 * 3. **"Tamamlandı" has exactly one door (ADR-0089 §3).** Only `completed`
 *    reaches the word. `partial` is its own posture and says what is
 *    missing rather than rounding up; `paused` is still and calm, because
 *    the owner stopped it and a stopped-on-purpose run is not a fault;
 *    `cancelled` is settled for the same reason; `failed` is held under
 *    restraint.
 * 4. **Nothing here can start, pause, resume or cancel anything.** This
 *    channel is presentation. The runs themselves are rows the Cockpit
 *    reads from the list route (`lib/cockpit/executive.ts`), and
 *    "Duraklat / Devam / İptal" there ask the Cloud Core, which owns the
 *    signals — never this page.
 */

import {
  EXECUTIVE_CAPTION_BARE,
  EXECUTIVE_RUN_STATE_LABEL,
  type ExecutiveRunState,
  type Severity,
  type UiStateEvent,
  asStepCount,
  isExecutiveRunState,
  isExecutiveState,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every executive event, read verbatim. */
export type ExecutiveFacts = {
  /** `metadata.run` exactly as sent, or `null` when none was. */
  run: string | null;
  /** `metadata.step` exactly as sent (`s3`), or `null` when none was. */
  step: string | null;
  /** `metadata.state` exactly as sent, or `null` when none was. */
  stateToken: string | null;
  /** The state when it is one of the seven this build knows, else `null`. */
  state: ExecutiveRunState | null;
  /** How many steps have finished, when the publisher counted. `0` is an answer. */
  done: number | null;
  /** How many steps the graph has, when the publisher counted. `0` is an answer. */
  total: number | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function executiveFacts(event: UiStateEvent | null): ExecutiveFacts {
  const token = metaToken(event, "state");
  return {
    run: metaToken(event, "run"),
    step: metaToken(event, "step"),
    stateToken: token,
    state: isExecutiveRunState(token) ? token : null,
    done: asStepCount(event?.metadata.done),
    total: asStepCount(event?.metadata.total),
  };
}

// -------------------------------------------------------------- the posture

/**
 * The shape the Core takes for one run, from the published state alone:
 *
 *   planned   — `planned`: the graph exists and nothing has run yet. Its
 *               own posture, because a plan is not work in flight
 *   running   — `running`: steps are being run
 *   paused    — `paused`: still and calm, and NOT ended. The owner said
 *               "Bekle", so the run is held exactly as a Core waiting on
 *               the owner is held — never agitated — and "Devam" is still
 *               the other answer to it
 *   completed — `completed`: still and bright. The one door to "tamamlandı"
 *   partial   — `partial`: settled but NOT done — held, and worded by what
 *               is missing (spec §3). Neither rounded up to completed nor
 *               dressed as a failure: part of the job exists
 *   cancelled — `cancelled`: settled and dim. The owner stopped it; that is
 *               a fact about a decision, not a fault
 *   failed    — `failed`: held under restraint, no agitation
 *
 * A state this build cannot read, or none at all, is the RUNNING posture:
 * the only thing an `executive.run` with no readable state can mean is that
 * a run exists and nothing settled has been said about it (rule 2).
 */
export type ExecutivePosture =
  | "planned"
  | "running"
  | "paused"
  | "completed"
  | "partial"
  | "cancelled"
  | "failed";

/** The states in which a run is not finished — the ones the owner still has levers over (spec §3, §4). */
export const EXECUTIVE_ACTIVE_STATES: readonly ExecutiveRunState[] = ["planned", "running", "paused"];

const EXECUTIVE_ACTIVE_SET: ReadonlySet<string> = new Set(EXECUTIVE_ACTIVE_STATES);

/** The four states a run ends in (spec §3): every one of them settled, only one of them done. */
export const EXECUTIVE_SETTLED_STATES: readonly ExecutiveRunState[] = ["completed", "partial", "cancelled", "failed"];

const EXECUTIVE_SETTLED_SET: ReadonlySet<string> = new Set(EXECUTIVE_SETTLED_STATES);

export function executivePosture(state: ExecutiveRunState | null): ExecutivePosture {
  switch (state) {
    case "planned":
      return "planned";
    case "paused":
      return "paused";
    case "completed":
      return "completed";
    case "partial":
      return "partial";
    case "cancelled":
      return "cancelled";
    case "failed":
      return "failed";
    default:
      return "running";
  }
}

/**
 * True for a KNOWN state in which the run has not ended — the condition
 * under which the owner's levers mean anything. A completed, partial,
 * cancelled or failed run is not going; a state this build cannot read is
 * not KNOWN to be going, which is why nothing offers a control over it.
 */
export function executiveRunIsActive(state: ExecutiveRunState | null): boolean {
  return state !== null && EXECUTIVE_ACTIVE_SET.has(state);
}

/**
 * True for a KNOWN state the run ended in. The negation is deliberately not
 * "still running": a word this build cannot read is neither settled nor
 * known to be going, and both questions must answer honestly.
 */
export function executiveRunIsSettled(state: ExecutiveRunState | null): boolean {
  return state !== null && EXECUTIVE_SETTLED_SET.has(state);
}

/**
 * The one predicate that may mean "done", and the only place the word is
 * decided. `partial` answers false here — that is the whole of ADR-0089 §3.
 */
export function executiveRunIsComplete(facts: Pick<ExecutiveFacts, "state">): boolean {
  return facts.state === "completed";
}

/** True for a run the publisher said ended with steps that did not verify — the state that must name what is missing. */
export function executiveRunIsPartial(facts: Pick<ExecutiveFacts, "state">): boolean {
  return facts.state === "partial";
}

// ------------------------------------------------------------- the captions

/** The words, re-exported from the contract where each is spelled once. */
export { EXECUTIVE_CAPTION_BARE, EXECUTIVE_RUN_STATE_LABEL } from "./contract";

/**
 * "3/5 adım" when the publisher counted BOTH, `null` when it counted one or
 * neither. A `done` without a `total` is not a fraction and a `total`
 * without a `done` is not progress: either alone would be the renderer
 * finishing a sentence the publisher started (M26 spec §6's scalars).
 */
export function executiveStepsPhrase(done: number | null, total: number | null): string | null {
  return done === null || total === null ? null : `${done}/${total} adım`;
}

/** "adım s3" when the publisher named the step, `null` when it did not. Never an index this build counted. */
export function executiveStepPhrase(step: string | null): string | null {
  return step === null ? null : `adım ${step}`;
}

/**
 * The state's word with what the publisher attached to it, and nothing
 * else — the piece every caption, facts line and row is built from:
 *
 *   planned/running/paused/cancelled/failed → the plain word
 *   completed                               → "tamamlandı"
 *   partial                                 → "kısmen bitti — eksik: s4, s5"
 *                                              when the missing steps are
 *                                              known, "kısmen bitti" alone
 *                                              when they are not
 *
 * `missing` is what did NOT verify, which the bus does not carry (v11's
 * metadata has no such key) and the run's own route does: the Cockpit's
 * rows pass it, the Core's caption passes nothing, and both spell the
 * sentence from here. `null` for no state at all.
 */
export function executiveStatePhrase(
  facts: Pick<ExecutiveFacts, "state">,
  missing: readonly string[] = [],
): string | null {
  const state = facts.state;
  if (state === null) return null;
  const word = EXECUTIVE_RUN_STATE_LABEL[state];
  if (state === "partial" && missing.length > 0) return `${word} — eksik: ${missing.join(", ")}`;
  return word;
}

/**
 * The caption the Core draws under the posture (M26 spec §6):
 *
 *   running, step and counts published → "adım s3 · çalışıyor · 3/5 adım"
 *   running, nothing else published    → "çalışıyor"
 *   paused                             → "adım s2 · duraklatıldı · 2/5 adım"
 *   completed                          → "tamamlandı · 5/5 adım"
 *   partial                            → "kısmen bitti · 3/5 adım" (the
 *                                         missing steps join it wherever
 *                                         they are known)
 *   cancelled                          → "iptal edildi · 2/5 adım"
 *
 * — each part only if it was published, and the counts only if BOTH were.
 * The run's id is deliberately not in the caption: it is an id, not a
 * sentence, and the Core says what is happening while the Cockpit's rows
 * say which run it is happening to.
 *
 * Without a state this build can read the caption is the bare token name
 * and no more (rule 2), which is true of a run in any state.
 */
export function executiveCaption(facts: ExecutiveFacts, missing: readonly string[] = []): string {
  const phrase = executiveStatePhrase(facts, missing);
  if (phrase === null) return EXECUTIVE_CAPTION_BARE;
  const parts: string[] = [];
  const step = executiveStepPhrase(facts.step);
  // The step is where the run IS, so it is said only while the run is still
  // somewhere: printing "adım s3" beside "tamamlandı" would read as a run
  // that ended in the middle of its third step.
  if (step && executiveRunIsActive(facts.state)) parts.push(step);
  parts.push(phrase);
  const steps = executiveStepsPhrase(facts.done, facts.total);
  if (steps) parts.push(steps);
  return parts.join(" · ");
}

// ---------------------------------------------------------------- the view

export type ExecutiveStage =
  /** `executive.run` is current: a run was published and the claim has not aged out. */
  | "active"
  /** Nothing has been published about a run, or the claim decayed. */
  | "none";

export type ExecutiveView = ExecutiveFacts & {
  stage: ExecutiveStage;
  /**
   * `"active"` when an executive event was ever published, regardless of
   * age; `null` when none was — the panel's "nothing reported", as distinct
   * from a run we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The posture, from the facts alone. */
  posture: ExecutivePosture;
  /** The caption, from the facts alone. */
  caption: string;
  /** The publisher's short label. Never prose. */
  label: string | null;
  taskId: string | null;
  severity: Severity;
  ageMs: number | null;
  /** True once the claim has aged out; `stage` is then `none`. */
  expired: boolean;
};

export function executiveView(claim: Claim): ExecutiveView {
  const event = claim.event;
  const named = event !== null && isExecutiveState(event.state);
  const facts = executiveFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    posture: executivePosture(facts.state),
    caption: executiveCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually carrying a run right now. */
export function executiveIsActive(view: ExecutiveView): boolean {
  return view.stage === "active";
}
