/**
 * The Yaratıcı panel's row logic (M27 spec §3, §6), kept pure and apart from
 * the client so a test can prove each sentence and each gate without a
 * network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a step: a
 * run is verified because its row says `verified`, an image exists because
 * its row says so, and an application is undriveable because its row says
 * `unavailable` — and only then is the row drawn without controls, because
 * there is nothing on the other side to ask.
 */

import {
  type CreativeRunState,
  type CreativeTool,
  MAX_CREATIVE_ROUNDS,
  isCreativeRunState,
  isCreativeTool,
} from "../uistate/contract";
import {
  CREATIVE_DEFECT_UNTOLD,
  CREATIVE_SIMILARITY_UNTOLD,
  creativeStateWord,
} from "../uistate/labels";
import {
  creativeDefectWord,
  creativeOperationWord,
  creativeSimilarityPhrase,
  creativeStatePhrase,
  creativeToolWord,
} from "../uistate/creative";
import type { CreativeAction, CreativeBusy, CreativeImageSide, CreativeRunRow } from "./creative";

/** How many of the list's runs the panel shows: the last ones, as the route orders them. */
export const CREATIVE_ROWS_SHOWN = 6;

/** The chips' words, in the spec's order: write the file, then measure it against what was asked. */
export const CREATIVE_ACTION_LABEL: Record<CreativeAction, string> = {
  export: "Dışa aktar",
  compare: "Karşılaştır",
};

/** The two pictures' words, said above each image so neither can be mistaken for the other. */
export const CREATIVE_SIDE_LABEL: Record<CreativeImageSide, string> = {
  before: "Önce",
  after: "Sonra",
};

/** The row's step when it is one of the twelve this build knows, else `null`. */
export function rowState(row: Pick<CreativeRunRow, "state">): CreativeRunState | null {
  return isCreativeRunState(row.state) ? row.state : null;
}

/** The row's application when it is one of the four this build knows, else `null` — used for the "kurulu değil" wording alone. */
export function rowTool(row: Pick<CreativeRunRow, "tool">): CreativeTool | null {
  return isCreativeTool(row.tool) ? row.tool : null;
}

/** True for a row whose step is `unavailable`: the application could not be driven at all. */
export function rowIsUnavailable(row: Pick<CreativeRunRow, "state">): boolean {
  return row.state === "unavailable";
}

/** True for a row whose step is `mismatch`: the comparison disagreed, and the defect is named. */
export function rowIsMismatch(row: Pick<CreativeRunRow, "state">): boolean {
  return row.state === "mismatch";
}

/** True for a row whose step is `failed`: drawn to the owner's attention, with its error. */
export function rowIsFailed(row: Pick<CreativeRunRow, "state">): boolean {
  return row.state === "failed";
}

/**
 * True for a row whose step is `verified`: the comparison MATCHED. Nothing
 * else is — not a high similarity, not an output that exists, not a run that
 * ended without an error (ADR-0093 decision 4).
 */
export function rowIsVerified(row: Pick<CreativeRunRow, "state">): boolean {
  return row.state === "verified";
}

/**
 * True when the ROW says the given picture exists to fetch. The images are
 * drawn on this and on nothing else — never on a step, never on a name,
 * never on hope: an `<img>` for a picture nobody stored would show the owner
 * a broken image where the honest answer is that there is none.
 */
export function rowHasImage(row: Pick<CreativeRunRow, "has_before" | "has_after">, side: CreativeImageSide): boolean {
  return side === "before" ? row.has_before : row.has_after;
}

/** The sides this row says it has a picture for, in the before → after order the panel draws them. */
export function creativeRowSides(row: Pick<CreativeRunRow, "has_before" | "has_after">): CreativeImageSide[] {
  const sides: CreativeImageSide[] = [];
  if (row.has_before) sides.push("before");
  if (row.has_after) sides.push("after");
  return sides;
}

/**
 * Which controls a row shows at all: "Dışa aktar" for a run whose step this
 * build can read and whose application the Cloud Core could drive;
 * "Karşılaştır" only ALSO when the row says an output exists, because a
 * comparison needs something to compare. NONE for an `unavailable` row
 * (there is no application to ask), and none for a step this build cannot
 * read — the row logic does not know the run is there to be driven, and a
 * chip that invited a click on that guess would be the page inventing state.
 * The owner's voice ("Bunu PNG olarak dışa aktar") still reaches the Cloud
 * Core, which decides on its own terms.
 */
export function creativeRowActions(row: Pick<CreativeRunRow, "state" | "has_after">): CreativeAction[] {
  if (rowState(row) === null || rowIsUnavailable(row)) return [];
  return row.has_after ? ["export", "compare"] : ["export"];
}

/**
 * The alt text for one picture: what it IS, named from the file the row
 * carried. A run the row did not name gets a sentence that says so — never
 * an empty `alt`, and never a name this page made up.
 */
export function creativeImageAlt(row: Pick<CreativeRunRow, "source" | "output">, side: CreativeImageSide): string {
  const name = side === "before" ? row.source : row.output;
  if (side === "before") return name ? `${name}: sahibin özgün görseli` : "Adı bildirilmeyen özgün görsel";
  return name ? `${name}: üretilen görsel` : "Adı bildirilmeyen üretilen görsel";
}

/**
 * One run on one line under its name: "Paint · çizim · doğrulandı (benzerlik
 * %92)", "Paint · uyuşmazlık — ölçü tutmadı", "Photoshop · kurulu değil —
 * yapılamadı", "durum bildirilmedi". The application and the operation are
 * printed whenever the row carried them (the token verbatim for a word this
 * build cannot read); the step carries the similarity on `verified` and the
 * defect on `mismatch`, each only when the row named it.
 */
export function creativeRowLine(row: CreativeRunRow): string {
  const state = rowState(row);
  const phrase = creativeStatePhrase(
    { state, tool: rowTool(row), similarity: row.similarity, defectToken: row.defect },
    row.defect,
  );
  const parts: string[] = [];
  const tool = creativeToolWord(row.tool);
  if (tool) parts.push(tool);
  const operation = creativeOperationWord(row.operation);
  if (operation) parts.push(operation);
  parts.push(phrase ?? creativeStateWord(row.state));
  return parts.join(" · ");
}

/**
 * What the comparison measured, on one line: "1024×768 · benzerlik %92 ·
 * ölçü tutmadı" — the produced dimensions, the bounded aggregate, and the
 * defect it named, each only when the row reported it.
 *
 * Drawn only for a row that HAS a comparison to report; a run still
 * executing has measured nothing yet, and printing "benzerlik ölçülmedi"
 * there would be noise rather than a fact. The absences ARE said for the two
 * states that rest on a completed comparison, because there the silence is
 * the fact: a `verified` with no figure was verified by something nobody
 * published a number for, and a `mismatch` with no defect disagreed about
 * something nobody named.
 */
export function creativeMetricsLine(row: CreativeRunRow): string {
  const parts: string[] = [];
  if (row.width !== null && row.height !== null) parts.push(`${row.width}×${row.height}`);
  const similarity = creativeSimilarityPhrase(row.similarity);
  if (similarity) parts.push(similarity);
  else parts.push(CREATIVE_SIMILARITY_UNTOLD);
  const defect = creativeDefectWord(row.defect);
  if (defect) parts.push(defect);
  else if (rowIsMismatch(row)) parts.push(CREATIVE_DEFECT_UNTOLD);
  return parts.join(" · ");
}

/**
 * True when a row has a comparison to report at all: it measured something,
 * or its step is one of the two that rest on a completed comparison. A run
 * that never reached the comparison has no metrics line — "not measured yet"
 * and "measured and reported nothing" are different answers.
 */
export function creativeHasMetrics(row: CreativeRunRow): boolean {
  if (row.similarity !== null) return true;
  if (row.width !== null && row.height !== null) return true;
  return rowIsVerified(row) || rowIsMismatch(row);
}

/**
 * Which correction round the run is on, in the owner's words: "2/3. tur"
 * when the row counted both, "2. tur" when it counted only the round, and
 * `null` when no correction round was reported at all — a first pass is not
 * a correction, and calling it "1. tur" would say the run had already
 * disagreed with itself once.
 */
export function creativeRoundPhrase(row: Pick<CreativeRunRow, "round" | "rounds">): string | null {
  if (row.round === null) return null;
  const total = row.rounds !== null && row.rounds > 0 ? row.rounds : MAX_CREATIVE_ROUNDS;
  return `${row.round}/${total}. tur`;
}

/**
 * The two file names on one line: "logo.png → logo-pagentos-1.png", which is
 * the promise ADR-0093 decision 5 makes, kept where the owner can see it —
 * the original is never overwritten and the output is a NEW file beside it.
 * `null` when the row named neither.
 */
export function creativeFilesLine(row: Pick<CreativeRunRow, "source" | "output">): string | null {
  if (row.source === null && row.output === null) return null;
  if (row.output === null) return row.source;
  if (row.source === null) return row.output;
  return `${row.source} → ${row.output}`;
}

// ------------------------------------------------------------------ the gate

/** Said under a disabled chip while another call is in flight. */
export const CREATIVE_REASON_BUSY = "Bir istek sürüyor; sonucu bekleniyor.";

/** Said for a chip asked of an application that could not be driven (the panel never draws one; the gate still answers). */
export const CREATIVE_REASON_UNAVAILABLE = "Uygulama sürülemiyor; istenecek bir şey yok.";

/** Said for a chip asked of a row whose step this build cannot read (the panel never draws one; the gate still answers). */
export const CREATIVE_REASON_UNKNOWN_STATE = "Çalışmanın durumu bilinmiyor; istek gönderilmedi.";

/** Said for a comparison asked of a row with no output (the panel never draws one; the gate still answers). */
export const CREATIVE_REASON_NO_OUTPUT = "Karşılaştırılacak bir çıktı yok; istek gönderilmedi.";

export type CreativeActionGate = {
  enabled: boolean;
  reason: string | null;
  reasonKind: "busy" | "unavailable" | "unknown_state" | "no_output" | null;
};

/**
 * Whether one chip may be pressed for this row, and if not, why in words.
 *
 * The page decides nothing the Cloud Core would not: an export or a
 * comparison is for a run in an application that can be driven (spec §4), a
 * comparison additionally needs an output to measure (spec §3), so the chip
 * never invites a click the gate would refuse; and one call at a time, so no
 * application is asked to start twice.
 */
export function creativeActionGate(
  row: Pick<CreativeRunRow, "state" | "has_after">,
  action: CreativeAction,
  busy: CreativeBusy | null,
): CreativeActionGate {
  if (busy !== null) return { enabled: false, reason: CREATIVE_REASON_BUSY, reasonKind: "busy" };
  if (rowIsUnavailable(row)) return { enabled: false, reason: CREATIVE_REASON_UNAVAILABLE, reasonKind: "unavailable" };
  if (rowState(row) === null) {
    return { enabled: false, reason: CREATIVE_REASON_UNKNOWN_STATE, reasonKind: "unknown_state" };
  }
  if (action === "compare" && !row.has_after) {
    return { enabled: false, reason: CREATIVE_REASON_NO_OUTPUT, reasonKind: "no_output" };
  }
  return { enabled: true, reason: null, reasonKind: null };
}
