/**
 * The Digital Operator's channel — contract v4 (M19 spec §4).
 *
 * `OperatorTask` (Cloud Core) publishes one event per transition of the loop
 * OBSERVE → PLAN → ACT → OBSERVE AGAIN → VERIFY: `operator.running` when a
 * step is being acted through the companion, `operator.verifying` while the
 * result is re-observed and the step's postcondition checked, and
 * `operator.failed` when a step's postcondition did not hold, the focus guard
 * refused, or the task timed out. Each carries `{step, capability,
 * window_title}` in its metadata (and `error_class` on failure): the step the
 * planner named, the capability it sent, the window the companion OBSERVED.
 *
 * Two rules, both the same rule the rest of this directory lives by:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The step
 *    is the step the publisher named; the window title is the title the
 *    companion observed. Nothing is inferred from the capability name, and a
 *    missing metadata key is rendered as "not reported", never filled in.
 * 2. **No progress is drawn that was not published.** A task of five steps
 *    does not get a bar; the panel shows the step it is on because the
 *    publisher said which, and nothing about how many remain.
 *
 * This channel is presentation only. Nothing in this client can start, cancel
 * or steer an operator task; this channel has no write path at all.
 */

import { type Severity, type UiStateEvent, isSeverity, metaNumber, metaToken } from "./contract";
import type { Claim } from "./truth";

export type OperatorStage =
  /** `operator.running` is current: a step is being acted through the companion. */
  | "running"
  /** `operator.verifying` is current: the result is being re-observed and checked. */
  | "verifying"
  /** `operator.failed` is current. Held until a newer operator event replaces it. */
  | "failed"
  /** Nothing has been published about the operator, or a transient stage decayed. */
  | "none";

/**
 * The metadata the publisher sends with every operator event, read verbatim.
 *
 * `metadata.step` is read in both shapes a publisher may send it: a short
 * token (the planner's name for the step) lands in `step`; a number (the
 * zero-based index of the step in the plan, as `OperatorService` publishes
 * from `enumerate(task.steps)`) lands in `stepIndex`, with `metadata.step_count`
 * beside it when the publisher sent the plan's length. Neither is derived
 * from the other, and neither is guessed.
 */
export type OperatorFacts = {
  /** The planner's name for the step (`metadata.step` as a token), or `null` if none was sent. */
  step: string | null;
  /** The zero-based index of the step (`metadata.step` as a number), or `null`. */
  stepIndex: number | null;
  /** The plan's length (`metadata.step_count`), or `null` if the publisher did not say. */
  stepCount: number | null;
  /** The capability sent to the companion (`metadata.capability`), or `null`. */
  capability: string | null;
  /** The window title the companion OBSERVED (`metadata.window_title`), or `null`. */
  windowTitle: string | null;
  /** The failure's class (`metadata.error_class`) on `operator.failed`, or `null`. */
  errorClass: string | null;
};

export type OperatorView = OperatorFacts & {
  stage: OperatorStage;
  /**
   * The stage the newest operator event named, regardless of age. `null` when
   * nothing was ever published — which is the panel's "empty", as distinct
   * from a stage that decayed.
   */
  lastKnown: Exclude<OperatorStage, "none"> | null;
  /** The publisher's short label — the task's goal, as the owner said it. Never prose. */
  label: string | null;
  taskId: string | null;
  severity: Severity;
  ageMs: number | null;
  /** True once a transient stage has aged out; `stage` is then `none`. */
  expired: boolean;
};

const OPERATOR_STAGE: Record<string, Exclude<OperatorStage, "none">> = {
  "operator.running": "running",
  "operator.verifying": "verifying",
  "operator.failed": "failed",
};

/** A count or index the publisher sent: a non-negative integer, or nothing. */
function metaIndex(event: UiStateEvent | null, key: string): number | null {
  const value = metaNumber(event, key);
  return value !== null && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The published facts on one event, or explicit nulls for no event. */
export function operatorFacts(event: UiStateEvent | null): OperatorFacts {
  const stepCount = metaIndex(event, "step_count");
  return {
    step: metaToken(event, "step"),
    stepIndex: metaIndex(event, "step"),
    stepCount: stepCount !== null && stepCount > 0 ? stepCount : null,
    capability: metaToken(event, "capability"),
    windowTitle: metaToken(event, "window_title"),
    errorClass: metaToken(event, "error_class"),
  };
}

/**
 * The step's place in its plan, in the owner's counting: "1/3" for the first
 * of three, "2" when the publisher gave an index but no length, `null` when
 * it gave neither. The index the publisher sends is zero-based (`enumerate`),
 * so the first step is written as 1 — a reading, recorded in ADR-0082's
 * addendum, not a second source of truth.
 */
export function operatorPosition(facts: Pick<OperatorFacts, "stepIndex" | "stepCount">): string | null {
  if (facts.stepIndex === null) return null;
  const ordinal = facts.stepIndex + 1;
  return facts.stepCount === null ? `${ordinal}` : `${ordinal}/${facts.stepCount}`;
}

/**
 * The caption the Core draws under an operator posture: the step the planner
 * named, else the publisher's own label (which is the step's name when the
 * publisher sent the index instead), else the step's place in the plan —
 * every one of them a published fact, and `null` when there is none.
 */
export function operatorCaption(event: UiStateEvent, facts: OperatorFacts): string | null {
  if (facts.step) return facts.step;
  if (event.label) return event.label;
  const position = operatorPosition(facts);
  return position === null ? null : `adım ${position}`;
}

export function operatorView(claim: Claim): OperatorView {
  const event = claim.event;
  const named = event ? (OPERATOR_STAGE[event.state] ?? null) : null;
  // An expired step is not a finished step: it is a step we stopped being
  // told about. The stage drops to `none` (the Core draws last-known) and
  // `lastKnown` keeps the word so the panel can say what it *was*.
  const stage: OperatorStage = named === null || claim.expired ? "none" : named;
  return {
    ...operatorFacts(event),
    stage,
    lastKnown: named,
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the operator is actually doing something on the desktop right now. */
export function operatorIsActing(view: OperatorView): boolean {
  return view.stage === "running" || view.stage === "verifying";
}
