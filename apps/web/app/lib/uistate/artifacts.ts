/**
 * The Artifact Factory's channel — contract v7 (M22 spec §6).
 *
 * The Cloud Core publishes `artifact.factory` while it renders one of the
 * owner's artifacts and while an INDEPENDENT parser reopens the bytes and
 * compares them to what was asked, with `{title?, format?, verdict?,
 * failing_ref?}` in its metadata: the artifact's title, the format in
 * hand, where the render is in its loop — `rendering`, `valid`, `invalid`
 * — and, on `invalid`, the ref of the first element the parser could not
 * find, in M20's reference scheme (`sheet:Ozet!B5`, `p3`, `s4`, `h2:Giriş`).
 *
 * The rules are the document's, applied to a file being made:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The
 *    title is the token the publisher sent; the verdict is the verdict it
 *    sent. A missing key is rendered as "not reported", never filled in.
 * 2. **A verdict this build does not know is the plain state.** The caption
 *    for a `verdict` outside the three the contract names is "Dosya
 *    üretiliyor" and no more: a word we cannot read is not a verdict we may
 *    narrate — and above all it is never "doğrulandı".
 * 3. **Nothing invalid is presented as done (ADR-0085 §3).** An `invalid`
 *    verdict is captioned as one, with the ref that failed; a valid one is
 *    said only when the publisher said `valid`.
 * 4. **Nothing here can render, validate or open.** This channel is
 *    presentation. The renders themselves are rows the Cockpit reads from
 *    the list route (`lib/cockpit/artifacts.ts`), and "Aç" asks the Cloud
 *    Core to fetch and open one on the device — never this page.
 */

import {
  ARTIFACT_CAPTION_BARE,
  ARTIFACT_VERDICT_LABEL,
  type ArtifactVerdict,
  type Severity,
  type UiStateEvent,
  isArtifactState,
  isArtifactVerdict,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim } from "./truth";

/** The metadata the publisher sends with every factory event, read verbatim. */
export type ArtifactFacts = {
  /** The artifact's title (`metadata.title`), or `null` if none was sent. */
  title: string | null;
  /** The format in hand (`metadata.format`) exactly as sent, or `null`. */
  format: string | null;
  /** `metadata.verdict` exactly as sent, or `null` when none was. */
  verdictToken: string | null;
  /** The verdict when it is one of the three this build knows, else `null`. */
  verdict: ArtifactVerdict | null;
  /** The ref that failed validation (`metadata.failing_ref`), verbatim, or `null`. */
  failingRef: string | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function artifactFacts(event: UiStateEvent | null): ArtifactFacts {
  const token = metaToken(event, "verdict");
  return {
    title: metaToken(event, "title"),
    format: metaToken(event, "format"),
    verdictToken: token,
    verdict: isArtifactVerdict(token) ? token : null,
    failingRef: metaToken(event, "failing_ref"),
  };
}

// --------------------------------------------------------------- the format

/**
 * The format as the owner reads it: the published token upper-cased with
 * the plain ASCII rule (never tr-TR, where `i` would become `İ`), so `xlsx`
 * is "XLSX" and a token this build has never seen is still itself, only
 * louder. `null` when none was published.
 */
export function artifactFormatLabel(format: string | null): string | null {
  if (!format) return null;
  const trimmed = format.trim();
  return trimmed ? trimmed.toUpperCase() : null;
}

// ------------------------------------------------------------- the captions

/** The verdict in the owner's words, re-exported from the contract where it is spelled once. */
export { ARTIFACT_CAPTION_BARE, ARTIFACT_VERDICT_LABEL } from "./contract";

/**
 * The caption the Core draws under the making posture (spec §6):
 *
 *   rendering  → "Bütçe 2026 üretiliyor"           ("Bütçe 2026 · PDF üretiliyor" when the format was published)
 *   valid      → "Bütçe 2026 · XLSX · doğrulandı"
 *   invalid    → "Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)"
 *
 * — each part only if it was published. Without a title there is nothing
 * to pass a verdict on, so the caption is the bare statement and no more
 * (rule 1); a verdict this build cannot read yields the bare statement too
 * (rule 2); no verdict at all is read as the factory still at work, which
 * is the only thing a `artifact.factory` with no verdict can mean.
 */
export function artifactCaption(facts: ArtifactFacts): string {
  if (!facts.title) return ARTIFACT_CAPTION_BARE;
  if (facts.verdictToken !== null && facts.verdict === null) return ARTIFACT_CAPTION_BARE;
  const format = artifactFormatLabel(facts.format);
  const verdict = facts.verdict ?? "rendering";
  if (verdict === "rendering") {
    return format ? `${facts.title} · ${format} ${ARTIFACT_VERDICT_LABEL.rendering}` : `${facts.title} ${ARTIFACT_VERDICT_LABEL.rendering}`;
  }
  const head = [facts.title, format, ARTIFACT_VERDICT_LABEL[verdict]]
    .filter((part): part is string => part !== null)
    .join(" · ");
  return verdict === "invalid" && facts.failingRef ? `${head} (${facts.failingRef})` : head;
}

// ---------------------------------------------------------------- the view

export type ArtifactStage =
  /** `artifact.factory` is current: the Core is rendering, or reopening a render to check it. */
  | "making"
  /** Nothing has been published about the factory, or the claim decayed. */
  | "none";

export type ArtifactView = ArtifactFacts & {
  stage: ArtifactStage;
  /**
   * `"making"` when a factory event was ever published, regardless of age;
   * `null` when none was — the panel's "nothing reported", as distinct from
   * a render we stopped hearing about.
   */
  lastKnown: "making" | null;
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

export function artifactView(claim: Claim): ArtifactView {
  const event = claim.event;
  const named = event !== null && isArtifactState(event.state);
  const facts = artifactFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "making" : "none",
    lastKnown: named ? "making" : null,
    caption: artifactCaption(facts),
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually making or checking a file right now. */
export function artifactIsMaking(view: ArtifactView): boolean {
  return view.stage === "making";
}
