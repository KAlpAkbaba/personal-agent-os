/**
 * The Üretilenler panel's row logic (M22 spec §4), kept pure and apart from
 * the client so a test can prove each sentence without a network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a verdict:
 * a render is valid because its row says `valid`, and only then does it
 * get a download link or count towards "Aç".
 */

import { artifactFormatLabel } from "../uistate/artifacts";
import { artifactVerdictWord } from "../uistate/labels";
import type { ArtifactRender, ArtifactRow } from "./artifacts";

/** True for a render the independent parser reopened and found complete (spec §3). */
export function renderIsValid(render: Pick<ArtifactRender, "state">): boolean {
  return render.state === "valid";
}

/** True for a render the parser reopened and found wanting; its row names the ref. */
export function renderIsInvalid(render: Pick<ArtifactRender, "state">): boolean {
  return render.state === "invalid";
}

/** The renders of one artifact the owner may download: exactly the valid ones. */
export function validRenders(row: Pick<ArtifactRow, "renders">): ArtifactRender[] {
  return row.renders.filter(renderIsValid);
}

/** The size in the owner's units, as M13's inbox prints it, or the statement that none came. */
export function renderSizeLabel(sizeBytes: number | null): string {
  if (sizeBytes === null) return "boyut bildirilmedi";
  return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
}

/**
 * One render on one line: "XLSX · 12 KB · doğrulandı", "PDF · 30 KB ·
 * doğrulanamadı · yer: sheet:Ozet!B5", "DOCX · 8 KB · doğrulama
 * bildirilmedi". The failing ref is printed only beside an invalid verdict,
 * and its absence there is said: a render that failed at no named place is
 * a report this page did not receive, not a render that passed.
 */
export function artifactRenderLine(render: ArtifactRender): string {
  const parts = [
    artifactFormatLabel(render.format) ?? render.format,
    renderSizeLabel(render.size_bytes),
    artifactVerdictWord(render.state),
  ];
  if (renderIsInvalid(render)) parts.push(render.failing_ref ? `yer: ${render.failing_ref}` : "yer bildirilmedi");
  return parts.join(" · ");
}

/** The artifact's kind in the owner's words when it is one the spec names (§1), verbatim otherwise. */
export const ARTIFACT_KIND_LABEL: Record<string, string> = {
  document: "belge",
  spreadsheet: "tablo",
  presentation: "sunum",
  dataset: "veri kümesi",
  page: "sayfa",
  research_report: "araştırma raporu",
};

export function artifactKindLabel(kind: string | null): string | null {
  if (!kind) return null;
  return ARTIFACT_KIND_LABEL[kind] ?? kind;
}

// ------------------------------------------------------------------ the gate

/** Said under a disabled "Aç" when the artifact has no render the parser passed. */
export const ARTIFACT_OPEN_REASON_NO_VALID_RENDER = "Doğrulanmış bir çıktı yok; doğrulanmamış bir çıktı açılmaz.";

/** Said under a disabled "Aç" while another open is in flight. */
export const ARTIFACT_OPEN_REASON_BUSY = "Bir açma isteği sürüyor; sonucu bekleniyor.";

export type ArtifactOpenGate = {
  enabled: boolean;
  reason: string | null;
  reasonKind: "no_valid_render" | "busy" | null;
};

/**
 * Whether "Aç" may be pressed for this artifact, and if not, why in words.
 *
 * The page decides nothing the Cloud Core would not: an artifact with no
 * valid render is one the Cloud Core would refuse to open (ADR-0085 §3), so
 * the chip never invites a click the gate would refuse; and one open at a
 * time, so nothing is asked of the device twice.
 */
export function artifactOpenGate(row: Pick<ArtifactRow, "renders">, busy: string | null): ArtifactOpenGate {
  if (busy !== null) return { enabled: false, reason: ARTIFACT_OPEN_REASON_BUSY, reasonKind: "busy" };
  if (validRenders(row).length === 0) {
    return { enabled: false, reason: ARTIFACT_OPEN_REASON_NO_VALID_RENDER, reasonKind: "no_valid_render" };
  }
  return { enabled: true, reason: null, reasonKind: null };
}
