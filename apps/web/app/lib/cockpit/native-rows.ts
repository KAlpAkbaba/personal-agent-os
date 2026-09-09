/**
 * The Yerel Uygulamalar panel's row logic (M28 spec §4, §6), kept pure and
 * apart from the client so a test can prove each sentence without a network.
 *
 * Every function here reads the row as the list route sent it and says
 * either what it said or that it did not say it. Nothing infers a step, a
 * size or a verdict: a build is verified because its row says `verified`, an
 * artefact exists because the row NAMED one, and the independent reader
 * agreed because the row carried its verdict — never because a build ended
 * without an error.
 */

import {
  SHA256_PREFIX_CHARS,
  type NativeBuildState,
  type NativeTarget,
  isNativeBuildState,
  isNativeTarget,
} from "../uistate/contract";
import {
  NATIVE_ARTIFACT_UNTOLD,
  NATIVE_VERDICT_UNTOLD,
  nativeStateWord,
} from "../uistate/labels";
import { nativeStackWord, nativeStatePhrase, nativeTargetWord } from "../uistate/native";
import type { NativeBuildRow } from "./native";

/** How many of the list's builds the panel shows: the last ones, as the route orders them. */
export const NATIVE_ROWS_SHOWN = 6;

/** The row's step when it is one of the eleven this build knows, else `null`. */
export function rowState(row: Pick<NativeBuildRow, "state">): NativeBuildState | null {
  return isNativeBuildState(row.state) ? row.state : null;
}

/** The row's artefact when it is one of the five this build knows, else `null`. */
export function rowTarget(row: Pick<NativeBuildRow, "target">): NativeTarget | null {
  return isNativeTarget(row.target) ? row.target : null;
}

/**
 * True for a row whose step is `unavailable`: this machine's toolchain
 * cannot reach the target at all. Never merged with `failed` — that
 * distinction is the whole of ADR-0095 decision 3.
 */
export function rowIsUnavailable(row: Pick<NativeBuildRow, "state">): boolean {
  return row.state === "unavailable";
}

/** True for a row whose step is `mismatch`: the reader read the artefact and disagreed. */
export function rowIsMismatch(row: Pick<NativeBuildRow, "state">): boolean {
  return row.state === "mismatch";
}

/** True for a row whose step is `failed`: the build was possible and did not work. */
export function rowIsFailed(row: Pick<NativeBuildRow, "state">): boolean {
  return row.state === "failed";
}

/**
 * True for a row whose step is `verified`: the INDEPENDENT reader agreed.
 * Nothing else is — not an artefact that exists, not a compiler that exited
 * 0, not a build that ended without an error (M28 spec §4).
 */
export function rowIsVerified(row: Pick<NativeBuildRow, "state">): boolean {
  return row.state === "verified";
}

/**
 * True when the ROW named an artefact at all. The artefact line is drawn on
 * this and on nothing else — never on a step: a build that once said
 * `packaging` is not a build with a file on disk, and printing a name for
 * one would be the page inventing an application the owner could install.
 */
export function rowHasArtifact(
  row: Pick<NativeBuildRow, "artifact_name" | "artifact_sha256" | "artifact_bytes">,
): boolean {
  return row.artifact_name !== null || row.artifact_sha256 !== null || row.artifact_bytes !== null;
}

/**
 * The size in the owner's units: "68 MB" for an artefact the spec's own
 * receipt would call 68 MB (§6), "412 KB" below a megabyte, and the
 * statement that none came.
 *
 * A self-contained .NET publish is tens of megabytes, so M22's KB-only
 * wording would print "69632 KB" and make the one number the owner reads
 * unreadable. Never rounded up to a whole megabyte: 1.4 MB stays 1.4 MB,
 * because "1 MB" beside a hash is a figure that will not match what the file
 * system says.
 */
export function nativeSizeLabel(bytes: number | null): string {
  if (bytes === null) return "boyut bildirilmedi";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

/**
 * The first characters of the artefact's sha256, or the statement that none
 * came. A prefix because the owner's use for it is recognising the same file
 * twice; the full digest stays on the row and on the receipt.
 */
export function nativeHashLabel(sha256: string | null): string {
  if (sha256 === null) return "özet bildirilmedi";
  return sha256.slice(0, SHA256_PREFIX_CHARS);
}

/**
 * The application and its target on one line: "Notlarim · Windows EXE ·
 * WPF", each part only when the row carried it, and the statement that the
 * row named no application at all — a build with no name is a row this page
 * received, not an application.
 */
export function nativeIdentityLine(row: NativeBuildRow): string {
  const parts: string[] = [row.app ?? "uygulama adı bildirilmedi"];
  const target = nativeTargetWord(row.target);
  if (target) parts.push(target);
  else parts.push("hedef bildirilmedi");
  const stack = nativeStackWord(row.stack);
  if (stack) parts.push(stack);
  if (row.version) parts.push(`sürüm ${row.version}`);
  return parts.join(" · ");
}

/**
 * The step on one line: "derleniyor", "doğrulandı", "uyuşmazlık — sürüm
 * tutmadı", "bu makinede yapılamıyor", "durum bildirilmedi". A token this
 * build cannot read is printed verbatim — it is still a published fact, and
 * it is still not one of the eleven.
 */
export function nativeRowLine(row: NativeBuildRow): string {
  const state = rowState(row);
  const phrase = nativeStatePhrase({ state, verdictToken: null });
  return phrase ?? nativeStateWord(row.state);
}

/**
 * The artefact on one line: "Notlarim.exe · 68 MB · a1b2c3d4e5f6", each part
 * as the row reported it and each absence said.
 *
 * Drawn only for a row that named an artefact (`rowHasArtifact`); a build
 * still compiling has produced nothing, and "boyut bildirilmedi" there would
 * be noise rather than a fact.
 */
export function nativeArtifactLine(row: NativeBuildRow): string {
  return [
    row.artifact_name ?? NATIVE_ARTIFACT_UNTOLD,
    nativeSizeLabel(row.artifact_bytes),
    nativeHashLabel(row.artifact_sha256),
  ].join(" · ");
}

/**
 * What the INDEPENDENT reader said, in the owner's words, or `null` when the
 * row carried no verdict to report.
 *
 * "Bağımsız okuyucu doğruladı" is said for `verdict_ok === true` and for
 * nothing else. A `false` NAMES every disagreement the reader listed, in its
 * order, because the owner's next question is always which; a `false` with
 * no named disagreement says that too, rather than implying there were none.
 *
 * The absence is reported for exactly the three states in which an artefact
 * is expected to exist and therefore to have been read
 * (`app/nativefactory/models.py::ARTIFACT_BEARING_STATES`): there, silence
 * from the reader IS the fact. A build that is still compiling has nothing
 * to have read yet, so it gets no verdict line at all.
 */
export function nativeVerdictLine(row: NativeBuildRow): string | null {
  if (row.verdict_ok === true) return "bağımsız okuyucu doğruladı";
  if (row.verdict_ok === false) {
    return row.verdict_mismatches.length
      ? `bağımsız okuyucu itiraz etti: ${row.verdict_mismatches.join("; ")}`
      : "bağımsız okuyucu itiraz etti; nedeni bildirilmedi";
  }
  return nativeExpectsArtifact(row) ? NATIVE_VERDICT_UNTOLD : null;
}

/**
 * True for the three steps in which an artefact is expected to exist and to
 * have been read back — the Cloud Core's own `ARTIFACT_BEARING_STATES`.
 * Below that, the reader has not run and its silence means nothing.
 */
export function nativeExpectsArtifact(row: Pick<NativeBuildRow, "state">): boolean {
  return rowIsVerified(row) || rowIsMismatch(row) || row.state === "unverified";
}

/**
 * True when the artefact's version was read OUT of the file and disagrees
 * with the version the spec asked for.
 *
 * Both figures are the row's own — one from the spec, one from the reader —
 * so this compares two published facts rather than deciding anything. It is
 * drawn beside a `mismatch` to say what the reader actually saw; when either
 * figure is missing there is nothing to compare and this is `false`, which
 * is not "they agreed".
 */
export function nativeVersionDisagrees(
  row: Pick<NativeBuildRow, "version" | "artifact_version">,
): boolean {
  return row.version !== null && row.artifact_version !== null && row.version !== row.artifact_version;
}
