/**
 * The document intelligence's channel — contract v5 (M20 spec §3).
 *
 * The Cloud Core publishes `document.analysis` while it reads, extracts,
 * retrieves from or answers about one of the owner's documents, with
 * `{file, part, step?, refs?}` in its metadata: the file's NAME (the record's
 * `name`, extension included), the reference of the place inside it in the
 * scheme of spec §2 (`p3`, `s4`, `sheet:Ozet!A5:B5`, `h2:Kararlar`, `r7`,
 * `$.ses`, `L1-40`), the step the Core is on, and — on an answer — the refs
 * it cited. `path` rides beside `file` when the Core chose to name the file
 * by path (two documents with one title, ADR-0076's rule).
 *
 * The rules are the operator's, applied to a document:
 *
 * 1. **Every field here is a published fact or an explicit `null`.** The file
 *    is the name the publisher sent; the place is its ref, spoken in the same
 *    words the Cloud Core speaks (§3) and shown verbatim when the words are
 *    not fixed. Nothing is inferred from the step, and a missing key is
 *    rendered as "not reported", never filled in.
 * 2. **No progress is drawn that was not published.** A five-page read gets
 *    no bar; the caption names the page because the publisher named it.
 * 3. **Never a document the state did not publish.** The "previous" document
 *    is the last different file the bus itself carried, and the refs are the
 *    ones on the newest event that had any. The panel fetches nothing.
 *
 * Presentation only. Nothing in this client can read, search or move a file.
 */

import {
  DOCUMENT_CAPTION_BARE,
  type DocumentRef,
  type Severity,
  type UiStateEvent,
  isDocumentState,
  isSeverity,
  metaToken,
} from "./contract";
import type { Claim, CoreTruth } from "./truth";

/** The kinds spec §2 extracts, as far as a file NAME can tell them apart. */
export type DocumentKind =
  | "docx"
  | "xlsx"
  | "pptx"
  | "pdf"
  | "md"
  | "csv"
  | "json"
  | "source"
  | "txt"
  | "image"
  | "archive"
  | "unknown";

/** B32: the picture and archive extensions the device's FileKinds tells apart. */
const IMAGE_EXTENSIONS: ReadonlySet<string> = new Set(["png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"]);

/** Spec §2: `source` = these extensions. */
const SOURCE_EXTENSIONS: ReadonlySet<string> = new Set([
  "py",
  "ps1",
  "cs",
  "ts",
  "tsx",
  "js",
  "sh",
  "sql",
  "yaml",
  "yml",
  "toml",
  "ini",
  "xml",
  "html",
  "css",
]);

const PLAIN_KINDS: ReadonlySet<string> = new Set(["docx", "xlsx", "pptx", "pdf", "md", "csv", "json", "txt"]);

/**
 * The kind of a document from its published name's extension, `null` when
 * there is no name. A reading of a published token, not a new fact: the
 * device chose the reference scheme by kind (§2), and the extension is the
 * only part of that decision the bus carries. A name without a recognised
 * extension is `unknown`, which is said as such downstream.
 */
export function documentKindOf(name: string | null): DocumentKind | null {
  if (!name) return null;
  const dot = name.lastIndexOf(".");
  if (dot < 0 || dot === name.length - 1) return "unknown";
  const ext = name.slice(dot + 1).toLowerCase();
  if (PLAIN_KINDS.has(ext)) return ext as DocumentKind;
  if (SOURCE_EXTENSIONS.has(ext)) return "source";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (ext === "zip") return "archive";
  return "unknown";
}

/** The last path segment of a published path, for the kind of a cited file. */
function nameOfPath(path: string | null): string | null {
  if (!path) return null;
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return cut < 0 ? path : path.slice(cut + 1);
}

/**
 * The metadata the publisher sends with every document event, read verbatim.
 */
export type DocumentFacts = {
  /** The file's name (`metadata.file`), or `null` if none was sent. */
  file: string | null;
  /** The file's path (`metadata.path`), only when the publisher chose to name it by path. */
  path: string | null;
  /** The reference of the place inside the file (`metadata.part`), verbatim. */
  part: string | null;
  /** The step the Core is on (`metadata.step`), verbatim. */
  step: string | null;
  /** The kind the name's extension tells, or `null` without a name. */
  kind: DocumentKind | null;
};

/** The published facts on one event, or explicit nulls for no event. */
export function documentFacts(event: UiStateEvent | null): DocumentFacts {
  const file = metaToken(event, "file");
  return {
    file,
    path: metaToken(event, "path"),
    part: metaToken(event, "part"),
    step: metaToken(event, "step"),
    kind: documentKindOf(file),
  };
}

// -------------------------------------------------------------- the place

const PAGE_OR_PARAGRAPH = /^p(\d+)$/;
const SLIDE = /^s(\d+)$/;
const SHEET_ROW = /^sheet:(.+)!A(\d+):[A-Z]+\d+$/;
const HEADING = /^h\d+:(.+)$/;
const CSV_ROW = /^r(\d+)$/;
const JSON_KEY = /^\$\.(.+)$/;
const LINE_RANGE = /^L(\d+)-(\d+)$/;

/**
 * The place, in the owner's words — exactly the wording spec §3 gives the
 * Cloud Core, so the screen and the voice say the same thing:
 *
 *   `p3`                → "3. sayfa" (PDF) / "3. paragraf" (DOCX, TXT)
 *   `s4`                → "4. slayt"
 *   `sheet:Ozet!A5:B5`  → "Ozet sayfası, 5. satır"
 *   `h2:Kararlar`       → "Kararlar bölümü"
 *   `r7`                → "7. satır"
 *   `$.ses`             → "ses anahtarı"
 *   `L1-40`             → "1-40. satırlar"
 *
 * A `p<n>` is a page for a PDF and a paragraph for a DOCX or TXT (§2); the
 * kind comes from the published name's extension. Only those three kinds
 * produce a `p<n>` at all, so a p-ref on a name the client cannot classify
 * takes the spec's general wording, "sayfa". Any ref form §3 gave no wording
 * for (a DOCX table `t2`, a newer scheme) is shown verbatim, never guessed.
 */
export function documentPartPhrase(part: string | null, kind: DocumentKind | null): string | null {
  if (!part) return null;
  let m = PAGE_OR_PARAGRAPH.exec(part);
  if (m) return kind === "docx" || kind === "txt" ? `${m[1]}. paragraf` : `${m[1]}. sayfa`;
  m = SLIDE.exec(part);
  if (m) return `${m[1]}. slayt`;
  m = SHEET_ROW.exec(part);
  if (m) return `${m[1]} sayfası, ${m[2]}. satır`;
  m = HEADING.exec(part);
  if (m) return `${m[1]} bölümü`;
  m = CSV_ROW.exec(part);
  if (m) return `${m[1]}. satır`;
  m = JSON_KEY.exec(part);
  if (m) return `${m[1]} anahtarı`;
  m = LINE_RANGE.exec(part);
  if (m) return `${m[1]}-${m[2]}. satırlar`;
  return part;
}

// --------------------------------------------------------------- the step

/**
 * Turkish for the steps the spec's tools and capabilities imply (§2, §3),
 * keyed by the bare verb. The spec fixes the tool names, not the step
 * vocabulary, so this table is a courtesy: a token it does not know is shown
 * verbatim, which is still a published fact.
 */
const STEP_LABEL: Record<string, string> = {
  search: "arıyor",
  locate: "buluyor",
  inspect: "inceliyor",
  read: "okuyor",
  extract: "çıkarıyor",
  index: "dizinliyor",
  refresh: "yeniden okuyor",
  retrieve: "arıyor",
  summarize: "özetliyor",
  answer: "yanıtlıyor",
  compare: "karşılaştırıyor",
  common_points: "ortak noktaları buluyor",
  references: "kaynakları buluyor",
  previous: "önceki belgeye dönüyor",
};

/** The step in Turkish when its verb is known, else the token as sent, else `null`. */
export function documentStepLabel(step: string | null): string | null {
  if (!step) return null;
  const verb = step.includes(".") ? step.slice(step.lastIndexOf(".") + 1) : step;
  return STEP_LABEL[verb] ?? step;
}

// ---------------------------------------------------------------- the view

export type DocumentStage =
  /** `document.analysis` is current: the Core is reading, retrieving or answering. */
  | "analysing"
  /** Nothing has been published about a document, or the claim decayed. */
  | "none";

export type DocumentView = DocumentFacts & {
  stage: DocumentStage;
  /**
   * `"analysing"` when a document event was ever published, regardless of
   * age; `null` when none was — which is the panel's "empty", as distinct
   * from a document whose analysis we stopped hearing about.
   */
  lastKnown: "analysing" | null;
  /** The place, in the owner's words, or `null` when no part was published. */
  partPhrase: string | null;
  /** The step in Turkish, or the token as sent, or `null`. */
  stepLabel: string | null;
  /** The refs this event cited. Empty unless the publisher sent any. */
  refs: DocumentRef[];
  /** The publisher's short label. Never prose. */
  label: string | null;
  taskId: string | null;
  severity: Severity;
  ageMs: number | null;
  /** True once the claim has aged out; `stage` is then `none`. */
  expired: boolean;
};

export function documentView(claim: Claim): DocumentView {
  const event = claim.event;
  const named = event !== null && isDocumentState(event.state);
  const facts = documentFacts(event);
  return {
    ...facts,
    stage: named && !claim.expired ? "analysing" : "none",
    lastKnown: named ? "analysing" : null,
    partPhrase: documentPartPhrase(facts.part, facts.kind),
    stepLabel: documentStepLabel(facts.step),
    refs: event?.refs ?? [],
    label: event?.label ?? null,
    taskId: event?.task_id ?? null,
    severity: isSeverity(event?.severity) ? event.severity : "info",
    ageMs: claim.ageMs,
    expired: claim.expired,
  };
}

/** True while the Core is actually working on a document right now. */
export function documentIsAnalysing(view: DocumentView): boolean {
  return view.stage === "analysing";
}

// ------------------------------------------------------------- the caption

/** The caption when the publisher named no file: the state, and nothing it did not say. */
export { DOCUMENT_CAPTION_BARE } from "./contract";

/**
 * The caption the Core draws under a reading posture: the file's name, the
 * place in the owner's words, the step — each one only if it was published,
 * joined the way the readout joins facts. Without a file there is nothing
 * to name a place in, so the caption is the bare statement and no more:
 * "3. sayfa" of an unnamed document would be a place with no document.
 */
export function documentCaption(facts: DocumentFacts): string {
  if (!facts.file) return DOCUMENT_CAPTION_BARE;
  return [facts.file, documentPartPhrase(facts.part, facts.kind), documentStepLabel(facts.step)]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

// ------------------------------------------------------------ the history

/** A document the bus carried earlier, with the event it came on. */
export type DocumentHistoryEntry = {
  event: UiStateEvent;
  facts: DocumentFacts;
};

/**
 * The document published before the current one: the newest document event
 * in the tail whose `file` differs from `current`'s. `null` when the tail
 * holds no earlier, different file — a previous document the Core knows
 * about but never published on this bus is not one this client may name.
 * Read from `truth.recent` (the bounded tail), never fetched.
 */
export function previousDocument(truth: CoreTruth, current: UiStateEvent | null): DocumentHistoryEntry | null {
  const currentFile = current ? metaToken(current, "file") : null;
  const currentSequence = current?.sequence ?? Number.POSITIVE_INFINITY;
  for (let i = truth.recent.length - 1; i >= 0; i -= 1) {
    const event = truth.recent[i];
    if (!isDocumentState(event.state) || event.sequence >= currentSequence) continue;
    const facts = documentFacts(event);
    if (facts.file === null || facts.file === currentFile) continue;
    return { event, facts };
  }
  return null;
}

/**
 * The last answer's refs: the newest document event in the tail that carried
 * any, with the event so the panel can date them. `null` when no event in the
 * tail cited anything — an answer is `{speech, refs}` by contract (§3), so a
 * bus with no refs on it has shown this client no answer.
 */
export function lastAnswerRefs(truth: CoreTruth): { event: UiStateEvent; refs: DocumentRef[] } | null {
  for (let i = truth.recent.length - 1; i >= 0; i -= 1) {
    const event = truth.recent[i];
    if (!isDocumentState(event.state)) continue;
    const refs = event.refs ?? [];
    if (refs.length > 0) return { event, refs };
  }
  return null;
}

/** The kind of a cited file, from the ref's path when it has one. */
export function refKind(ref: DocumentRef): DocumentKind | null {
  return documentKindOf(nameOfPath(ref.path));
}
