/**
 * The Native Application Factory's channel — contract v13 (M28 spec §4, §6).
 *
 * The Cloud Core publishes `native.build` at every arrow of ONE application
 * build: a project written from a fixed template, compiled by the real
 * toolchain on the owner's machine, its own tests run, packaged, and then
 * the produced artefact reopened by a reader that did NOT build it — with
 * `{app?, target?, state?, stack?, verdict?}` in its metadata: which
 * application, which of the five artefacts, the step in §4's names, the
 * stack §3's rule chose, and the independent reader's own short word.
 *
 * The rules are the creative channel's, applied to a program rather than to
 * a picture:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    application is the token the publisher sent; the target is the word it
 *    sent. A missing key is rendered as "not reported", never filled in.
 * 2. **A state this build does not know is the plain state.** The caption
 *    for a `state` outside the eleven the contract names is "Yerel
 *    uygulama" and no more — and above all it is never "doğrulandı".
 * 3. **The independent reader is the proof (M28 spec §4).** "Doğrulandı"
 *    needs `verified` and nothing else reaches it: not a compiler that
 *    exited 0, not an artefact that exists on disk, not a state this build
 *    cannot read. `unverified` is the honest middle — the file is there and
 *    nothing could check it — and `mismatch` is the reader disagreeing.
 * 4. **A toolchain this machine does not have is named, never imitated
 *    (ADR-0095 decision 3).** `unavailable` is a settled, honest posture: a
 *    missing JDK is a fact about the world, not a defect in the agent, and
 *    it never agitates.
 * 5. **Nothing here can build, package or install anything.** This channel
 *    is presentation. The builds themselves are rows the Cockpit reads from
 *    the list route (`lib/cockpit/native.ts`), and no artefact byte, no
 *    path and no compiler line ever reaches this file.
 */

import {
  NATIVE_CAPTION_BARE,
  NATIVE_STACK_LABEL,
  NATIVE_STATE_LABEL,
  NATIVE_TARGET_LABEL,
  type NativeBuildState,
  type NativeStack,
  type NativeTarget,
  type Severity,
  type UiStateEvent,
  isNativeBuildState,
  isNativeStack,
  isNativeState,
  isNativeTarget,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every native build event, read verbatim. */
export type NativeFacts = {
  /** `metadata.app` exactly as sent, or `null` when none was. */
  appToken: string | null;
  /** `metadata.target` exactly as sent, or `null` when none was. */
  targetToken: string | null;
  /** The artefact when it is one of the five this build knows, else `null`. */
  target: NativeTarget | null;
  /** `metadata.state` exactly as sent, or `null` when none was. */
  stateToken: string | null;
  /** The step when it is one of the eleven this build knows, else `null`. */
  state: NativeBuildState | null;
  /** `metadata.stack` exactly as sent, or `null` when none was. */
  stackToken: string | null;
  /** The stack when it is one of the four this build knows, else `null`. */
  stack: NativeStack | null;
  /** The independent reader's own short word, verbatim. Never a closed vocabulary here. */
  verdictToken: string | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function nativeFacts(event: UiStateEvent | null): NativeFacts {
  const app = metaToken(event, "app");
  const target = metaToken(event, "target");
  const token = metaToken(event, "state");
  const stack = metaToken(event, "stack");
  return {
    appToken: app,
    targetToken: target,
    target: isNativeTarget(target) ? target : null,
    stateToken: token,
    state: isNativeBuildState(token) ? token : null,
    stackToken: stack,
    stack: isNativeStack(stack) ? stack : null,
    verdictToken: metaToken(event, "verdict"),
  };
}

// -------------------------------------------------------------- the posture

/**
 * The shape the Core takes for a native build, from the published state
 * alone:
 *
 *   planning    — `planned`: the spec was accepted and the stack chosen.
 *                 Still, and nothing flowing: a decision is not a build
 *   making      — `generating`, `building`, `packaging`: a project is being
 *                 written, compiled and packed. Work in flight
 *   testing     — `testing`: its own posture, because the generated
 *                 project's tests are the first thing that can disagree
 *                 with what was asked
 *   reading     — `validating`: draws INWARD, because a reader that did not
 *                 build the file is opening it, and every claim of
 *                 "doğrulandı" downstream rests on what that reader saw
 *   verified    — `verified`: still and bright. Reachable from this ONE
 *                 state and from nothing else
 *   unverified  — `unverified`: just as still, plain and unlit. The
 *                 artefact exists and nothing could check it: nothing
 *                 disagreed, and nothing was verified either
 *   mismatch    — `mismatch`: held under restraint and NAMED; the artefact
 *                 is not what was asked for, and nothing rounds that up
 *   unavailable — `unavailable`: settled and dim. A toolchain this machine
 *                 does not have is a fact about the world, not a fault, and
 *                 it never agitates (ADR-0095 decision 3)
 *   failed      — `failed`: held under restraint, no agitation
 *
 * A state this build cannot read, or none at all, is the making posture:
 * the only thing a `native.build` with no readable state can mean is that a
 * build exists and nothing settled has been said about it.
 */
export type NativePosture =
  | "planning"
  | "making"
  | "testing"
  | "reading"
  | "verified"
  | "unverified"
  | "mismatch"
  | "unavailable"
  | "failed";

/** The states in which a build is actually doing something (M28 spec §4's lifecycle). */
export const NATIVE_WORKING_STATES: readonly NativeBuildState[] = [
  "generating",
  "building",
  "testing",
  "packaging",
  "validating",
];

const NATIVE_WORKING_SET: ReadonlySet<string> = new Set(NATIVE_WORKING_STATES);

export function nativePosture(state: NativeBuildState | null): NativePosture {
  switch (state) {
    case "planned":
      return "planning";
    case "testing":
      return "testing";
    case "validating":
      return "reading";
    case "verified":
      return "verified";
    case "unverified":
      return "unverified";
    case "mismatch":
      return "mismatch";
    case "unavailable":
      return "unavailable";
    case "failed":
      return "failed";
    default:
      return "making";
  }
}

/**
 * True for a KNOWN state in which the build is still working. A settled,
 * mismatched, unavailable or failed build is not working; a `planned` one
 * has not started; a state this build cannot read is not known to be
 * working.
 */
export function nativeIsWorking(state: NativeBuildState | null): boolean {
  return state !== null && NATIVE_WORKING_SET.has(state);
}

/**
 * True for the facts of a build the publisher said this machine's toolchain
 * cannot reach at all — the one state that is neither a success nor a
 * defect, and the one M28 exists to keep distinct from `failed`.
 */
export function nativeIsUnavailable(facts: Pick<NativeFacts, "state">): boolean {
  return facts.state === "unavailable";
}

/**
 * True ONLY for a build whose publisher said `verified` — the one door to
 * the word "doğrulandı" in this family.
 *
 * Deliberately not "an artefact exists", not "the compiler exited 0", and
 * not "the run ended without an error". M28 spec §4 makes the INDEPENDENT
 * reader the proof; a renderer that decided verification for itself would
 * be inventing the one claim this milestone is about.
 */
export function nativeIsVerified(facts: Pick<NativeFacts, "state">): boolean {
  return facts.state === "verified";
}

// ------------------------------------------------------------- the captions

/** The words, re-exported from the contract where each is spelled once. */
export {
  NATIVE_CAPTION_BARE,
  NATIVE_STACK_LABEL,
  NATIVE_STATE_LABEL,
  NATIVE_TARGET_LABEL,
} from "./contract";

/** The artefact in the owner's words: the spec's name for the five, the token verbatim for a sixth. */
export function nativeTargetWord(token: string | null): string | null {
  if (!token) return null;
  return isNativeTarget(token) ? NATIVE_TARGET_LABEL[token] : token;
}

/** The stack in the owner's words: the rule's name for the four, the token verbatim for a fifth. */
export function nativeStackWord(token: string | null): string | null {
  if (!token) return null;
  return isNativeStack(token) ? NATIVE_STACK_LABEL[token] : token;
}

/**
 * The step's word with what the publisher attached to it, and nothing else:
 *
 *   planned/generating/building/…  → the plain word
 *   verified                       → "doğrulandı" alone. The artefact's own
 *                                    facts (its name, size and hash) are the
 *                                    ROW's, never the bus's — the bus is
 *                                    content-free, and a caption carrying a
 *                                    file name would be this channel
 *                                    smuggling content past that rule
 *   mismatch                       → "uyuşmazlık — okuyucu itiraz etti"
 *                                    when the reader said something,
 *                                    "uyuşmazlık" alone when it did not
 *   unavailable                    → "bu makinede yapılamıyor"
 *
 * `null` for no state at all.
 */
export function nativeStatePhrase(facts: Pick<NativeFacts, "state" | "verdictToken">): string | null {
  const state = facts.state;
  if (state === null) return null;
  const word = NATIVE_STATE_LABEL[state];
  if (state === "mismatch" && facts.verdictToken) return `${word} — ${facts.verdictToken}`;
  return word;
}

/**
 * The caption the Core draws under the posture (M28 spec §6):
 *
 *   building, target named     → "Notlarim · Windows EXE · derleniyor"
 *   validating                 → "Notlarim · Windows EXE · çıktı okunuyor"
 *   verified                   → "Notlarim · Windows EXE · doğrulandı"
 *   unavailable                → "Notlarim · Android APK · bu makinede yapılamıyor"
 *
 * — each part only if it was published. Without a state this build can read
 * the caption is the bare token name and no more (rule 2); without an
 * application or a target it is the plain step (rule 1), which is all that
 * was said.
 */
export function nativeCaption(facts: NativeFacts): string {
  const phrase = nativeStatePhrase(facts);
  if (phrase === null) return NATIVE_CAPTION_BARE;
  const parts: string[] = [];
  if (facts.appToken) parts.push(facts.appToken);
  const target = nativeTargetWord(facts.targetToken);
  if (target) parts.push(target);
  parts.push(phrase);
  return parts.join(" · ");
}

// ---------------------------------------------------------------- the view

export type NativeStage =
  /** `native.build` is current: a build was published and the claim has not aged out. */
  | "active"
  /** Nothing has been published about a build, or the claim decayed. */
  | "none";

export type NativeView = NativeFacts & {
  stage: NativeStage;
  /**
   * `"active"` when a native build event was ever published, regardless of
   * age; `null` when none was — the panel's "nothing reported", as distinct
   * from a build we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The posture, from the facts alone. */
  posture: NativePosture;
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

export function nativeView(claim: Claim): NativeView {
  const event = claim.event;
  const named = event !== null && isNativeState(event.state);
  const facts = nativeFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    posture: nativePosture(facts.state),
    caption: nativeCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually building something right now. */
export function nativeIsActive(view: NativeView): boolean {
  return view.stage === "active";
}
