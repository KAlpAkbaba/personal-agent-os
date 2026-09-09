/**
 * The Creative Tools Operator's channel — contract v12 (M27 spec §3, §6).
 *
 * The Cloud Core publishes `creative.activity` at every arrow of ONE
 * creative run: the owner's image is analysed, a `CreativePlan` is built as
 * data, its operations are applied through the most structured interface the
 * installed application really offers, the output is reopened by an
 * INDEPENDENT reader, exported, and compared with what was asked — with
 * `{tool?, operation?, state?, similarity?, defect?}` in its metadata: which
 * of the four applications is being driven, the operation from §2's closed
 * vocabulary, the step in §3's names, the bounded aggregate the comparison
 * measured and the defect it named.
 *
 * The rules are the scene channel's, applied to a picture rather than to a
 * geometry:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    tool is the token the publisher sent; the operation is the word it
 *    sent; the similarity is the figure the COMPARISON measured. A missing
 *    key is rendered as "not reported", never filled in — and in particular
 *    a run with no similarity is never "%0 benzerlik", because nobody
 *    measured.
 * 2. **A state this build does not know is the plain state.** The caption
 *    for a `state` outside the twelve the contract names is "Görsel
 *    çalışması" and no more: a word we cannot read is not a step we may
 *    narrate — and above all it is never "doğrulandı".
 * 3. **The comparison is the proof (ADR-0093 decision 4).** "Doğrulandı"
 *    needs `verified` and nothing else reaches it: not a high similarity,
 *    not an export that was written, not a state this build cannot read. A
 *    `mismatch` is worded as one and NAMES the defect that disagreed; a run
 *    with nothing to compare against is `unverified`, which is neither.
 * 4. **An application that is not there is named, never imitated
 *    (ADR-0093 decision 3).** `unavailable` is a settled, honest posture
 *    said as a plain inability, and "kurulu değil" is added only for the two
 *    applications the detection MEASURED as absent.
 * 5. **Nothing here can draw, export or compare.** This channel is
 *    presentation. The runs themselves are rows the Cockpit reads from the
 *    list route (`lib/cockpit/creative.ts`), and "Dışa aktar" / "Karşılaştır"
 *    there ask the Cloud Core — never this page. No image byte reaches this
 *    file: the before/after pictures are fetched from the owner-session-gated
 *    image route by the panel's own hook.
 */

import {
  CREATIVE_CAPTION_BARE,
  CREATIVE_DEFECT_LABEL,
  CREATIVE_NOT_INSTALLED,
  CREATIVE_NOT_INSTALLED_TOOLS,
  CREATIVE_OPERATION_LABEL,
  CREATIVE_STATE_LABEL,
  CREATIVE_TOOL_LABEL,
  CREATIVE_TOOL_LOCATIVE,
  type CreativeDefect,
  type CreativeOperation,
  type CreativeRunState,
  type CreativeTool,
  type Severity,
  type UiStateEvent,
  asSimilarity,
  isCreativeDefect,
  isCreativeOperation,
  isCreativeRunState,
  isCreativeState,
  isCreativeTool,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every creative event, read verbatim. */
export type CreativeFacts = {
  /** `metadata.tool` exactly as sent, or `null` when none was. */
  toolToken: string | null;
  /** The application when it is one of the four this build knows, else `null`. */
  tool: CreativeTool | null;
  /** `metadata.operation` exactly as sent, or `null` when none was. */
  operationToken: string | null;
  /** The operation when it is one of the twelve this build knows, else `null`. */
  operation: CreativeOperation | null;
  /** `metadata.state` exactly as sent, or `null` when none was. */
  stateToken: string | null;
  /** The step when it is one of the twelve this build knows, else `null`. */
  state: CreativeRunState | null;
  /** The comparison's bounded aggregate in 0..1, when one was measured. `0` is an answer. */
  similarity: number | null;
  /** `metadata.defect` exactly as sent, or `null` when none was. */
  defectToken: string | null;
  /** The defect when it is one of the four this build knows, else `null`. */
  defect: CreativeDefect | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function creativeFacts(event: UiStateEvent | null): CreativeFacts {
  const tool = metaToken(event, "tool");
  const operation = metaToken(event, "operation");
  const token = metaToken(event, "state");
  const defect = metaToken(event, "defect");
  return {
    toolToken: tool,
    tool: isCreativeTool(tool) ? tool : null,
    operationToken: operation,
    operation: isCreativeOperation(operation) ? operation : null,
    stateToken: token,
    state: isCreativeRunState(token) ? token : null,
    similarity: asSimilarity(event?.metadata.similarity),
    defectToken: defect,
    defect: isCreativeDefect(defect) ? defect : null,
  };
}

// -------------------------------------------------------------- the posture

/**
 * The shape the Core takes for a creative run, from the published state
 * alone:
 *
 *   reading     — `analysing`, `inspecting`: an image is being READ, either
 *                 the owner's input before the plan or the output after it.
 *                 The one posture in this family that draws inward, because
 *                 everything downstream rests on what was read
 *   planning    — `planning`: the plan is being built as DATA and nothing
 *                 has been applied to any file yet. Still, and no flow
 *   making      — `executing`: the plan's operations are being applied
 *   exporting   — `exporting`: its own posture, because writing a file in
 *                 the asked-for format is not the same act as making what
 *                 goes in it
 *   comparing   — `comparing`: the produced image is being measured against
 *                 what was asked. Its own posture, because this is the step
 *                 every claim of "doğrulandı" comes from
 *   correcting  — `correcting`: the comparison disagreed and a follow-up
 *                 plan is running (≤ 3 rounds). Working, but held: something
 *                 has already been found wrong, and drawing it exactly like
 *                 a first pass would hide that
 *   verified    — `verified`: the comparison matched; still and bright.
 *                 Reachable from this ONE state and from nothing else
 *   unverified  — `unverified`: the run did what was asked and there was
 *                 nothing to compare it against. Settled and plain: nothing
 *                 disagreed, and nothing was verified either
 *   mismatch    — `mismatch`: held under restraint and NAMED; the picture is
 *                 not what was asked for, and nothing rounds that up
 *   unavailable — `unavailable`: settled and dim. An application that is not
 *                 installed is a fact about this machine, not a fault in the
 *                 agent, and it never agitates (ADR-0093 decision 3)
 *   failed      — `failed`: held under restraint, no agitation
 *
 * A state this build cannot read, or none at all, is the making posture:
 * the only thing a `creative.activity` with no readable state can mean is
 * that a run exists and nothing settled has been said about it.
 */
export type CreativePosture =
  | "reading"
  | "planning"
  | "making"
  | "exporting"
  | "comparing"
  | "correcting"
  | "verified"
  | "unverified"
  | "mismatch"
  | "unavailable"
  | "failed";

/** The states in which a creative run is actually doing something (M27 spec §3's loop). */
export const CREATIVE_WORKING_STATES: readonly CreativeRunState[] = [
  "analysing",
  "planning",
  "executing",
  "inspecting",
  "exporting",
  "comparing",
  "correcting",
];

const CREATIVE_WORKING_SET: ReadonlySet<string> = new Set(CREATIVE_WORKING_STATES);

export function creativePosture(state: CreativeRunState | null): CreativePosture {
  switch (state) {
    case "analysing":
    case "inspecting":
      return "reading";
    case "planning":
      return "planning";
    case "exporting":
      return "exporting";
    case "comparing":
      return "comparing";
    case "correcting":
      return "correcting";
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
 * True for a KNOWN state in which the run is still working — the one
 * condition under which a second "Dışa aktar" would be asking for a run that
 * is already busy. A settled, mismatched, unavailable or failed run is not
 * working; a state this build cannot read is not known to be working.
 */
export function creativeRunIsWorking(state: CreativeRunState | null): boolean {
  return state !== null && CREATIVE_WORKING_SET.has(state);
}

/**
 * True for the facts of a run the publisher said could not be driven at
 * all: the one state that earns no control anywhere, because there is
 * nothing on the other side to ask.
 */
export function creativeIsUnavailable(facts: Pick<CreativeFacts, "state">): boolean {
  return facts.state === "unavailable";
}

/**
 * True ONLY for a run whose publisher said `verified` — the one door to the
 * word "doğrulandı" anywhere in this build.
 *
 * Deliberately not a similarity threshold, not "an export exists", and not
 * "the run ended without an error". ADR-0093 decision 4 makes the comparison
 * the proof; a renderer that decided verification for itself from a number
 * would be inventing the one claim this milestone is about.
 */
export function creativeIsVerified(facts: Pick<CreativeFacts, "state">): boolean {
  return facts.state === "verified";
}

// ------------------------------------------------------------- the captions

/** The words, re-exported from the contract where each is spelled once. */
export {
  CREATIVE_CAPTION_BARE,
  CREATIVE_DEFECT_LABEL,
  CREATIVE_NOT_INSTALLED,
  CREATIVE_OPERATION_LABEL,
  CREATIVE_STATE_LABEL,
  CREATIVE_TOOL_LABEL,
  CREATIVE_TOOL_LOCATIVE,
} from "./contract";

/** The application in the owner's words: the spec's name for the four, the token verbatim for a fifth. */
export function creativeToolWord(token: string | null): string | null {
  if (!token) return null;
  return isCreativeTool(token) ? CREATIVE_TOOL_LABEL[token] : token;
}

/** The operation in the owner's words: the spec's word for the twelve, the token verbatim for a thirteenth. */
export function creativeOperationWord(token: string | null): string | null {
  if (!token) return null;
  return isCreativeOperation(token) ? CREATIVE_OPERATION_LABEL[token] : token;
}

/** The defect in the owner's words: the spec's word for the four, the token verbatim for a fifth. */
export function creativeDefectWord(token: string | null): string | null {
  if (!token) return null;
  return isCreativeDefect(token) ? CREATIVE_DEFECT_LABEL[token] : token;
}

/**
 * "(benzerlik %92)" when the comparison measured, `null` when nobody did.
 *
 * `0` is a measurement, so it is printed rather than folded into "nothing
 * was measured". The figure is a percentage of the bounded aggregate
 * `app/creative/compare.py` produces from its tile grid. It is NOT SSIM, and
 * that name appears nowhere in this build except beside those two words —
 * ADR-0093 decision 4 struck it, and `creative-states.test.ts` reads these
 * files to keep it struck.
 */
export function creativeSimilarityPhrase(similarity: number | null): string | null {
  return similarity === null ? null : `(benzerlik %${Math.round(similarity * 100)})`;
}

/**
 * True for an `unavailable` this build may word as "kurulu değil": one whose
 * publisher named an application the 2026-09-08 detection MEASURED as
 * absent. An `unavailable` Paint (present on this machine and on the runner)
 * or Figma (no desktop application to install at all) is "yapılamadı" alone,
 * because this build does not know why and inventing a reason is the thing
 * this family exists to refuse.
 */
export function creativeSaysNotInstalled(tool: CreativeTool | null): boolean {
  return tool !== null && CREATIVE_NOT_INSTALLED_TOOLS.includes(tool);
}

/**
 * The step's word with everything the publisher attached to it, and nothing
 * else — the one piece every caption, facts line and row is built from:
 *
 *   analysing/planning/executing/…  → the plain word
 *   verified                        → "doğrulandı (benzerlik %92)" when measured
 *   mismatch                        → "uyuşmazlık — ölçü tutmadı" when the
 *                                      defect is known, "uyuşmazlık" alone
 *                                      when it is not
 *   unavailable                     → "kurulu değil — yapılamadı" for
 *                                      Photoshop and Illustrator (the two the
 *                                      detection measured absent), and
 *                                      "yapılamadı" for anything else
 *   failed                          → "başarısız"
 *
 * `defect` overrides the published token where a caller has a better one —
 * the Cockpit's rows pass the list route's own defect, the Core's caption
 * passes nothing and falls back to the bus's — and both spell the sentence
 * from here. `null` for no state at all.
 */
export function creativeStatePhrase(
  facts: Pick<CreativeFacts, "state" | "tool" | "similarity" | "defectToken">,
  defect: string | null = null,
): string | null {
  const state = facts.state;
  if (state === null) return null;
  const word = CREATIVE_STATE_LABEL[state];
  switch (state) {
    case "verified": {
      const similarity = creativeSimilarityPhrase(facts.similarity);
      return similarity ? `${word} ${similarity}` : word;
    }
    case "mismatch": {
      const named = creativeDefectWord(defect ?? facts.defectToken);
      return named ? `${word} — ${named}` : word;
    }
    case "unavailable":
      return creativeSaysNotInstalled(facts.tool) ? `${CREATIVE_NOT_INSTALLED} — ${word}` : word;
    default:
      return word;
  }
}

/**
 * The caption the Core draws under the posture (M27 spec §6):
 *
 *   executing, no operation named → "Paint'te düzenleme uygulanıyor"
 *   executing, operation named    → "Paint'te çizim uygulanıyor"
 *   analysing                     → "Paint · çizim · görsel inceleniyor"
 *   comparing                     → "Paint · çizim · karşılaştırılıyor"
 *   verified                      → "Paint · çizim · doğrulandı (benzerlik %92)"
 *   mismatch                      → "Paint · çizim · uyuşmazlık — ölçü tutmadı"
 *   unavailable                   → "Photoshop · kurulu değil — yapılamadı"
 *   failed                        → "Paint · çizim · başarısız"
 *
 * — each part only if it was published. `executing` is the one step said in
 * the locative, and for a plain reason: it is the only one where the work
 * happens INSIDE the application rather than being said about it (the
 * analysis, the plan and the comparison are the Cloud Core's own work over
 * the file). Everything else has the application as a subject.
 *
 * Without a state this build can read the caption is the bare token name and
 * no more (rule 2), which is true of a run in any step; without a tool or an
 * operation it is the plain step (rule 1), which is all that was said.
 */
export function creativeCaption(facts: CreativeFacts, defect: string | null = null): string {
  const phrase = creativeStatePhrase(facts, defect);
  if (phrase === null) return CREATIVE_CAPTION_BARE;
  const operation = creativeOperationWord(facts.operationToken);
  if (facts.state === "executing") {
    const place = facts.tool === null ? null : CREATIVE_TOOL_LOCATIVE[facts.tool];
    if (place === null) return operation ? `${operation} uygulanıyor` : phrase;
    return operation ? `${place} ${operation} uygulanıyor` : `${place} ${phrase}`;
  }
  const parts: string[] = [];
  const tool = creativeToolWord(facts.toolToken);
  if (tool) parts.push(tool);
  if (operation) parts.push(operation);
  parts.push(phrase);
  return parts.join(" · ");
}

// ---------------------------------------------------------------- the view

export type CreativeStage =
  /** `creative.activity` is current: a creative run was published and the claim has not aged out. */
  | "active"
  /** Nothing has been published about a creative run, or the claim decayed. */
  | "none";

export type CreativeView = CreativeFacts & {
  stage: CreativeStage;
  /**
   * `"active"` when a creative event was ever published, regardless of age;
   * `null` when none was — the panel's "nothing reported", as distinct from
   * a run we stopped hearing about.
   */
  lastKnown: "active" | null;
  /** The posture, from the facts alone. */
  posture: CreativePosture;
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

export function creativeView(claim: Claim): CreativeView {
  const event = claim.event;
  const named = event !== null && isCreativeState(event.state);
  const facts = creativeFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "active" : "none",
    lastKnown: named ? "active" : null,
    posture: creativePosture(facts.state),
    caption: creativeCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually doing something with a picture right now. */
export function creativeIsActive(view: CreativeView): boolean {
  return view.stage === "active";
}
