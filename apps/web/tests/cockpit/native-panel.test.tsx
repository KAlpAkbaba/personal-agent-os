/**
 * The Yerel Uygulamalar panel and the Core's readout for `native.build`
 * (M28 spec §4, §6): sentences about the rows the list route holds and the
 * tokens the Cloud Core published — the application, its target and stack,
 * the step it reached, and for a build that produced something the
 * artefact's name, its size, the first characters of its sha256, and what
 * the INDEPENDENT reader concluded when it opened the file.
 *
 * Everything drawn here was published. This panel measures no file, hashes
 * nothing, and reaches no device: a build is "doğrulandı" because its row
 * says `verified`, an artefact is named because the row named it, and the
 * reader agreed because the row carried its verdict.
 *
 * Rendered with `react-dom/server` like the rest of this suite.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NativePanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import type { NativeBuildRow } from "../../app/lib/cockpit/native";
import {
  NATIVE_BUILDS_PATH,
  parseNativeBuildRow,
  parseVerdictMismatches,
  parseVerdictOk,
} from "../../app/lib/cockpit/native";
import {
  NATIVE_ROWS_SHOWN,
  nativeArtifactLine,
  nativeExpectsArtifact,
  nativeHashLabel,
  nativeIdentityLine,
  nativeRowLine,
  nativeSizeLabel,
  nativeVerdictLine,
  nativeVersionDisagrees,
  rowHasArtifact,
  rowIsUnavailable,
  rowIsVerified,
} from "../../app/lib/cockpit/native-rows";
import { NATIVE_TTL_MS, SHA256_PREFIX_CHARS } from "../../app/lib/uistate/contract";
import { NATIVE_EMPTY, NATIVE_UNTOLD, NATIVE_VERDICT_UNTOLD } from "../../app/lib/uistate/labels";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  CREATIVE_ACTIVITY,
  NATIVE_BUILD,
  NATIVE_BUILD_BARE,
  T0,
  event,
  iso,
  resetSequence,
  response,
} from "../uistate/fixtures";

// ------------------------------------------------------------------ helpers

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

function row(overrides: Partial<NativeBuildRow> = {}): NativeBuildRow {
  return {
    build_id: "n1",
    app: "Notlarim",
    target: "windows_exe",
    stack: "dotnet_wpf",
    state: "building",
    version: "0.1.0",
    artifact_name: null,
    artifact_bytes: null,
    artifact_sha256: null,
    artifact_version: null,
    verdict_ok: null,
    verdict_mismatches: [],
    error_class: null,
    error_message: null,
    created_at: iso(-600_000),
    updated_at: iso(-30_000),
    ...overrides,
  };
}

const SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90";

const VERIFIED = () =>
  row({
    state: "verified",
    artifact_name: "Notlarim.exe",
    artifact_bytes: 71_303_168,
    artifact_sha256: SHA,
    artifact_version: "0.1.0",
    verdict_ok: true,
  });

const MISMATCH = () =>
  row({
    build_id: "n2",
    state: "mismatch",
    artifact_name: "Notlarim.exe",
    artifact_bytes: 71_303_168,
    artifact_sha256: SHA,
    artifact_version: "0.0.9",
    verdict_ok: false,
    verdict_mismatches: ["version is '0.0.9', the spec asked for '0.1.0'"],
  });

const UNVERIFIED = () =>
  row({
    build_id: "n3",
    target: "windows_msix",
    state: "unverified",
    artifact_name: "Notlarim.msix",
    artifact_bytes: 412_000,
    artifact_sha256: SHA,
  });

const UNAVAILABLE = () =>
  row({
    build_id: "n4",
    app: "Sayac",
    target: "android_apk",
    stack: "android_kotlin",
    state: "unavailable",
    error_class: "dependency_unavailable",
    error_message: "JDK bulunamadı; Android derlemesi için sahip işlemi 33 gerekiyor.",
  });

const FAILED = () =>
  row({
    build_id: "n5",
    state: "failed",
    error_class: "build_failed",
    error_message: "MSB3073: dotnet publish exited with code 1.",
  });

const ok = (rows: NativeBuildRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });

function panel(
  builds: Parameters<typeof NativePanel>[0]["builds"],
  events: ReturnType<typeof event>[] = [],
  at = T0,
) {
  return renderToStaticMarkup(<NativePanel builds={builds} truth={truthOf(events, at)} now={at} />);
}

/** The markup with every tag stripped: what the owner actually reads. */
function text(html: string): string {
  return html.replace(/<[^>]*>/g, "");
}

function readout(events: ReturnType<typeof event>[], at = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events, at), at)} />);
}

// -------------------------------------------------------------- the client

describe("the client reads the row as the route sent it", () => {
  it("asks the one route and nothing else", () => {
    expect(NATIVE_BUILDS_PATH).toBe("/v1/native/builds");
  });

  it("takes a row's identity, its step and the artefact's measured facts", () => {
    const parsed = parseNativeBuildRow({
      build_id: "n1",
      app: "Notlarim",
      target: "windows_exe",
      stack: "dotnet_wpf",
      state: "verified",
      version: "0.1.0",
      artifact: { facts: { name: "Notlarim.exe", size_bytes: 71_303_168, sha256: SHA, version: "0.1.0" }, ok: true, mismatches: [] },
    });
    expect(parsed?.artifact_name).toBe("Notlarim.exe");
    expect(parsed?.artifact_bytes).toBe(71_303_168);
    expect(parsed?.artifact_sha256).toBe(SHA);
    // The version READ OUT of the file, which is not the version asked for.
    expect(parsed?.artifact_version).toBe("0.1.0");
    expect(parsed?.verdict_ok).toBe(true);
  });

  it("refuses a row with no id, which is not a build", () => {
    expect(parseNativeBuildRow({ app: "Notlarim" })).toBeNull();
    expect(parseNativeBuildRow(null)).toBeNull();
    expect(parseNativeBuildRow("n1")).toBeNull();
  });

  it("never infers the verdict from the step", () => {
    // The two are different statements: "the build settled here" and "the
    // reader that opened the file said this". A row that reported no verdict
    // says so, even when its step is `verified`.
    expect(parseVerdictOk({ state: "verified" })).toBeNull();
    expect(parseVerdictOk({ state: "verified", verdict: { ok: false } })).toBe(false);
    expect(parseVerdictOk({ verdict_ok: true })).toBe(true);
    expect(parseVerdictMismatches({ verdict: { ok: false, mismatches: ["artefact is empty"] } })).toEqual([
      "artefact is empty",
    ]);
    expect(parseVerdictMismatches({ verdict: { ok: true } })).toEqual([]);
    expect(parseVerdictMismatches({ mismatches: [1, "sürüm tutmadı"] })).toEqual(["sürüm tutmadı"]);
  });

  it("drops a zero-byte artefact size rather than printing it as a measurement", () => {
    expect(parseNativeBuildRow({ id: "n1", artifact_bytes: 0 })?.artifact_bytes).toBeNull();
    expect(parseNativeBuildRow({ id: "n1", artifact_bytes: 512 })?.artifact_bytes).toBe(512);
  });
});

// ---------------------------------------------------------------- the rows

describe("a row says what it said, and says its absences", () => {
  it("prints the size in the units the receipt uses", () => {
    // §6's own receipt says "68 MB"; M22's KB-only wording would print
    // "69632 KB" and make the one number the owner reads unreadable.
    expect(nativeSizeLabel(71_303_168)).toBe("68 MB");
    expect(nativeSizeLabel(1_572_864)).toBe("1.5 MB");
    expect(nativeSizeLabel(412_000)).toBe("402 KB");
    expect(nativeSizeLabel(null)).toBe("boyut bildirilmedi");
  });

  it("prints a hash prefix, never the whole digest and never a made-up one", () => {
    expect(nativeHashLabel(SHA)).toBe(SHA.slice(0, SHA256_PREFIX_CHARS));
    expect(nativeHashLabel(SHA).length).toBe(SHA256_PREFIX_CHARS);
    expect(nativeHashLabel(null)).toBe("özet bildirilmedi");
  });

  it("names the application, target, stack and version, and says which the row omitted", () => {
    expect(nativeIdentityLine(row())).toBe("Notlarim · Windows EXE · WPF · sürüm 0.1.0");
    expect(nativeIdentityLine(row({ app: null, target: null, stack: null, version: null }))).toBe(
      "uygulama adı bildirilmedi · hedef bildirilmedi",
    );
    // A target this build cannot read is still a published fact.
    expect(nativeIdentityLine(row({ target: "ios_project", stack: null, version: null }))).toContain("ios_project");
  });

  it("prints the step the row named, or the token verbatim", () => {
    expect(nativeRowLine(row())).toBe("derleniyor");
    expect(nativeRowLine(row({ state: "verified" }))).toBe("doğrulandı");
    expect(nativeRowLine(row({ state: "unavailable" }))).toBe("bu makinede yapılamıyor");
    expect(nativeRowLine(row({ state: "signing" }))).toBe("signing");
    expect(nativeRowLine(row({ state: null }))).toBe("durum bildirilmedi");
  });

  it("draws an artefact line only for a row that NAMED an artefact", () => {
    expect(rowHasArtifact(row())).toBe(false);
    expect(rowHasArtifact(VERIFIED())).toBe(true);
    // Not from a step: a build that once said `packaging` is not a build with
    // a file on disk, and naming one would be this page inventing an
    // application the owner could install.
    expect(rowHasArtifact(row({ state: "packaging" }))).toBe(false);
    expect(nativeArtifactLine(VERIFIED())).toBe(`Notlarim.exe · 68 MB · ${SHA.slice(0, SHA256_PREFIX_CHARS)}`);
    expect(nativeArtifactLine(row({ artifact_name: "Notlarim.exe" }))).toBe(
      "Notlarim.exe · boyut bildirilmedi · özet bildirilmedi",
    );
  });

  it("says what the independent reader concluded, and its silence where it matters", () => {
    expect(nativeVerdictLine(VERIFIED())).toBe("bağımsız okuyucu doğruladı");
    expect(nativeVerdictLine(MISMATCH())).toBe(
      "bağımsız okuyucu itiraz etti: version is '0.0.9', the spec asked for '0.1.0'",
    );
    // A disagreement with nothing named is said as one, never as "there were
    // no disagreements".
    expect(nativeVerdictLine(row({ verdict_ok: false }))).toBe(
      "bağımsız okuyucu itiraz etti; nedeni bildirilmedi",
    );
    // The three steps where an artefact is expected to exist are the three
    // where a silent reader is itself the fact.
    expect(nativeVerdictLine(UNVERIFIED())).toBe(NATIVE_VERDICT_UNTOLD);
    expect(nativeExpectsArtifact(UNVERIFIED())).toBe(true);
    // While a build still compiling has nothing to have read, so there is no
    // verdict line at all — "not read yet" and "read and said nothing" are
    // different answers.
    expect(nativeVerdictLine(row({ state: "building" }))).toBeNull();
    expect(nativeVerdictLine(UNAVAILABLE())).toBeNull();
    expect(nativeExpectsArtifact(row({ state: "building" }))).toBe(false);
  });

  it("compares two published versions and never decides one", () => {
    expect(nativeVersionDisagrees(MISMATCH())).toBe(true);
    expect(nativeVersionDisagrees(VERIFIED())).toBe(false);
    // A missing figure is not agreement.
    expect(nativeVersionDisagrees(row({ version: "0.1.0", artifact_version: null }))).toBe(false);
    expect(nativeVersionDisagrees(row({ version: null, artifact_version: "0.1.0" }))).toBe(false);
  });

  it("reaches 'doğrulandı' only through the published step", () => {
    expect(rowIsVerified(VERIFIED())).toBe(true);
    // Not from an artefact that exists, not from a reader that agreed beside
    // a step that is not `verified`.
    expect(rowIsVerified(row({ artifact_name: "Notlarim.exe", verdict_ok: true }))).toBe(false);
    expect(rowIsVerified(UNVERIFIED())).toBe(false);
    // And an unreachable toolchain is never a failure.
    expect(rowIsUnavailable(UNAVAILABLE())).toBe(true);
    expect(rowIsUnavailable(FAILED())).toBe(false);
  });
});

// --------------------------------------------------------------- the panel

describe("the Yerel Uygulamalar panel", () => {
  it("says the route answered and holds nothing, which is not silence", () => {
    const html = panel(ok([]));
    expect(html).toContain('data-panel="native"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain(NATIVE_EMPTY);
    // And the bus's own silence is its own sentence.
    expect(html).toContain(NATIVE_UNTOLD);
  });

  it("says 'henüz yok' for a Cloud Core without the route, never an empty list", () => {
    const html = panel({ kind: "absent", detail: `Bu Cloud Core sürümünde ${NATIVE_BUILDS_PATH} yok (HTTP 404).` });
    expect(html).toContain("Henüz yok.");
    expect(html).toContain(NATIVE_BUILDS_PATH);
    expect(html).not.toContain(NATIVE_EMPTY);
  });

  it("says 'alınamadı' for a request that failed, never an empty list", () => {
    const html = panel({ kind: "failed", error: "HTTP 503" });
    expect(html).toContain("Alınamadı: HTTP 503");
    expect(html).not.toContain(NATIVE_EMPTY);
  });

  it("draws a verified build with the artefact its reader measured", () => {
    const html = panel(ok([VERIFIED()]), [NATIVE_BUILD("Notlarim", "windows_exe", "verified", "dotnet_wpf", "ok")]);
    expect(html).toContain('data-native-build="n1"');
    expect(html).toContain('data-native-row-verified="yes"');
    expect(html).toContain('data-native-row-verdict="ok"');
    expect(html).toContain("Notlarim · Windows EXE · WPF · sürüm 0.1.0");
    expect(html).toContain("doğrulandı");
    expect(html).toContain("Notlarim.exe");
    expect(html).toContain("68 MB");
    expect(html).toContain(SHA.slice(0, SHA256_PREFIX_CHARS));
    expect(html).toContain("bağımsız okuyucu doğruladı");
    // The whole digest never reaches the owner's EYE: it is unreadable on a
    // row and the use for it is recognising the same file twice. The full
    // published value stays on the row's data attribute, where a harness can
    // read it and no reader has to.
    expect(text(html)).not.toContain(SHA);
    expect(text(html)).toContain(SHA.slice(0, SHA256_PREFIX_CHARS));
    expect(html).toContain(`data-native-row-sha256="${SHA}"`);
    // The badge counts what the ROWS say, never what the bus said.
    expect(html).toContain("1 doğrulandı / 1");
  });

  it("names every disagreement on a mismatch, and both versions side by side", () => {
    const html = panel(ok([MISMATCH()]));
    expect(html).toContain('data-native-row-verdict="mismatch"');
    expect(html).toContain("uyuşmazlık");
    expect(html).toContain("version is &#x27;0.0.9&#x27;, the spec asked for &#x27;0.1.0&#x27;");
    expect(html).toContain('data-native-version-disagrees="yes"');
    expect(html).toContain("istenen sürüm 0.1.0 · çıktıdaki sürüm 0.0.9");
    // A mismatch draws the owner's attention; it is not rounded up to done.
    expect(html).toContain("attention");
    expect(html).not.toContain("doğrulandı");
  });

  it("keeps a toolchain this machine does not have apart from a failure", () => {
    const html = panel(ok([UNAVAILABLE()]));
    expect(html).toContain('data-native-row-unavailable="yes"');
    expect(html).toContain('data-native-row-failed="no"');
    expect(html).toContain("bu makinede yapılamıyor");
    // The Cloud Core's own sentence, never one this page guessed.
    expect(html).toContain("JDK bulunamadı");
    expect(html).not.toContain("başarısız");
    // An unreachable toolchain is not an alarm.
    expect(panel(ok([UNAVAILABLE()]))).not.toContain("attention");
  });

  it("has no controls at all, for any row in any state", () => {
    // Starting a twenty-minute compiler and installing a signed package are
    // asked for by voice through the ONE router, which gates them. A chip
    // here would be a second authority surface for the same act.
    const html = panel(ok([VERIFIED(), MISMATCH(), UNVERIFIED(), UNAVAILABLE(), FAILED()]));
    expect(html).not.toContain("<button");
    expect(html).not.toContain("core-chip");
  });

  it("shows the last builds and no more", () => {
    const many = Array.from({ length: NATIVE_ROWS_SHOWN + 3 }, (_, i) => row({ build_id: `n${i}` }));
    const html = panel(ok(many));
    const drawn = html.match(/data-native-build="/g) ?? [];
    expect(drawn.length).toBe(NATIVE_ROWS_SHOWN);
    // The count in the badge is the whole list, not what fitted.
    expect(html).toContain(`${many.length}`);
  });

  it("words an aged-out bus claim as last-known, never as a compiler still running", () => {
    const stale = panel(ok([]), [NATIVE_BUILD()], T0);
    expect(stale).toContain('data-native-activity="active"');
    const later = renderToStaticMarkup(
      <NativePanel
        builds={ok([])}
        truth={truthOf([NATIVE_BUILD()], T0)}
        now={T0 + NATIVE_TTL_MS + 1_000}
      />,
    );
    expect(later).toContain("Son bilinen:");
    expect(later).toContain('data-native-activity="none"');
  });

  it("ignores another family's event entirely", () => {
    const html = panel(ok([]), [CREATIVE_ACTIVITY()]);
    expect(html).toContain(NATIVE_UNTOLD);
    expect(html).not.toContain('data-native-activity="active"');
  });

  it("draws a bus event whose publisher named nothing as a build and no more", () => {
    const html = panel(ok([]), [NATIVE_BUILD_BARE()]);
    expect(html).toContain('data-native-activity="active"');
    expect(html).toContain("Yerel uygulama");
    expect(html).not.toContain("doğrulandı");
  });
});

// ------------------------------------------------- the row's separate lines

describe("a row's separate facts render as separate lines", () => {
  /**
   * The defect `panel-row-lines.test.tsx` was written for, held for this
   * panel specifically.
   *
   * `.panel li` is a plain block and a row's body is a sequence of sibling
   * `<span>`s, so before the shared `.panel li > span { display: block }`
   * rule the released M23–M26 panels rendered "doğrulandı (3 nesne)Kure,
   * Kamera, Gunes" — two separately published facts read as one garbled
   * claim. Here that would be "doğrulandıNotlarim.exe · 68 MB · a1b2c3…",
   * which would read as a step whose name contains a file.
   *
   * So this panel's own fact lines are asserted to be DIRECT children of the
   * `<li>`, which is the exact shape the shared rule selects. A row that
   * wrapped them in a `<div>` would escape it silently and be back to one
   * run-together line.
   */
  it("puts every published fact in its own direct-child span of the row", () => {
    const html = panel(ok([MISMATCH()]));
    const li = html.slice(html.indexOf("<li"), html.indexOf("</li>"));
    // The identity line lives in `.event-row`, which is a flex <div> of its
    // own; the facts below it are the direct-child spans the rule reaches.
    for (const marker of ["data-native-line", "data-native-artifact", "data-native-verdict", "data-native-version-disagrees"]) {
      expect(li, marker).toContain(`<span class="muted" ${marker}=`);
    }
    // And no fact span is nested inside another element that would take it
    // out of `.panel li > span`.
    expect(li).not.toMatch(/<div[^>]*>\s*<span class="muted" data-native-line/);
  });

  it("keeps the step and the artefact as two separate statements", () => {
    const html = panel(ok([VERIFIED()]));
    // The exact run-together string the shared rule prevents. If the two
    // facts were ever emitted into one span, this is what the owner would
    // read.
    expect(html).not.toContain("doğrulandıNotlarim.exe");
    // They really are two spans, in this order.
    const step = html.indexOf("data-native-line");
    const artifact = html.indexOf("data-native-artifact");
    expect(step).toBeGreaterThan(-1);
    expect(artifact).toBeGreaterThan(step);
  });
});

// -------------------------------------------------------------- the readout

describe("the Core's readout for the Native Application Factory", () => {
  it("headlines the making posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([NATIVE_BUILD("Notlarim", "windows_exe", "building")]);
    expect(html).toContain('data-core-kind="native_build"');
    expect(html).toContain('data-core-state="native.build"');
    expect(html).toContain('data-core-subsystem="nativefactory"');
    expect(html).toContain("Yerel uygulama");
    expect(html).toContain("Yerel uygulamalar");
    expect(html).toContain("data-native-facts");
    expect(html).toContain('data-native-app="Notlarim"');
    expect(html).toContain('data-native-target="windows_exe"');
    expect(html).toContain('data-native-target-label="Windows EXE"');
    expect(html).toContain('data-native-state="building"');
    expect(html).toContain('data-native-unavailable="no"');
    expect(html).toContain("uygulama: Notlarim · hedef: Windows EXE · durum: derleniyor");
    expect(html).not.toContain("core-progress-fill");
  });

  it("marks an unreachable toolchain so a harness can tell it from a failure", () => {
    const html = readout([NATIVE_BUILD("Sayac", "android_apk", "unavailable", "android_kotlin")]);
    expect(html).toContain('data-native-unavailable="yes"');
    expect(html).toContain("bu makinede yapılamıyor");
    const failed = readout([NATIVE_BUILD("Notlarim", "windows_exe", "failed")]);
    expect(failed).toContain('data-native-unavailable="no"');
  });

  it("never carries an artefact's name, size or hash onto the Core", () => {
    // The bus is content-free (ADR-0052 §3): those three facts are the
    // Cockpit row's, read from the list route.
    const html = readout([NATIVE_BUILD("Notlarim", "windows_exe", "verified", "dotnet_wpf", "ok")]);
    expect(html).not.toContain(".exe");
    expect(html).not.toContain(" MB");
    expect(html).not.toContain(SHA.slice(0, SHA256_PREFIX_CHARS));
  });

  it("tells the Core nothing when no build was published", () => {
    const html = readout([AGENT_IDLE()]);
    expect(html).not.toContain("data-native-facts");
  });
});
