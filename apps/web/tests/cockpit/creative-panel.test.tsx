/**
 * The Yaratıcı panel, its two chips and the Core's readout for
 * `creative.activity` (M27 spec §3, §6): sentences about the rows the list
 * route holds and the tokens the Cloud Core published, the owner's original
 * beside what was produced from it as images shown ONLY because the row says
 * they exist, "Dışa aktar" / "Karşılaştır" that ask the Cloud Core exactly
 * once each, NO controls at all over an application this machine does not
 * have, and never a picture this page made.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click is
 * proven the way `scenes-panel.test.tsx` proves it: the panel is hook-free,
 * so the element tree is walked to the control and its handler invoked —
 * exactly what React would do.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CreativePanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import {
  CREATIVE_ACTION_LABEL,
  CREATIVE_REASON_BUSY,
  CREATIVE_REASON_NO_OUTPUT,
  CREATIVE_REASON_UNAVAILABLE,
  CREATIVE_REASON_UNKNOWN_STATE,
  CREATIVE_ROWS_SHOWN,
  CREATIVE_SIDE_LABEL,
  creativeActionGate,
  creativeFilesLine,
  creativeHasMetrics,
  creativeImageAlt,
  creativeMetricsLine,
  creativeRoundPhrase,
  creativeRowActions,
  creativeRowLine,
  creativeRowSides,
  rowHasImage,
  rowIsUnavailable,
  rowIsVerified,
} from "../../app/lib/cockpit/creative-rows";
import {
  CREATIVE_CONTROL_IDLE,
  CREATIVE_PREVIEW_NONE,
  CREATIVE_ROUTE_ABSENT,
  type CreativeActionReceipt,
  type CreativeClient,
  type CreativeControlProps,
  type CreativeControlState,
  type CreativePreviewProps,
  type CreativeRunRow,
  CreativeActionError,
  asCreativeRound,
  creativeActionPath,
  creativeImagePath,
  parseCreativeReceipt,
  parseCreativeRunRow,
} from "../../app/lib/cockpit/creative";
import {
  CREATIVE_OUTCOME_NO_STATE_TR,
  creativeOutcomeText,
  runCreativeAction,
} from "../../app/lib/cockpit/useCreativeControl";
import { creativeImageKey, creativeImageNotice, creativeSrcKey } from "../../app/lib/cockpit/useCreativeImages";
import { CREATIVE_RUN_STATES, CREATIVE_TTL_MS, MAX_CREATIVE_ROUNDS } from "../../app/lib/uistate/contract";
import { CREATIVE_DEFECT_UNTOLD, CREATIVE_SIMILARITY_UNTOLD } from "../../app/lib/uistate/labels";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CAPABILITY_GENESIS,
  CREATIVE_ACTIVITY,
  CREATIVE_ACTIVITY_BARE,
  DOCUMENT_ANALYSIS,
  EXECUTIVE_RUN,
  MAIL_ACTIVITY,
  SCENE_ACTIVITY,
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

function row(overrides: Partial<CreativeRunRow> = {}): CreativeRunRow {
  return {
    run_id: "c1",
    tool: "paint",
    source: "logo.png",
    output: "logo-pagentos-1.png",
    operation: "draw",
    state: "executing",
    similarity: null,
    defect: null,
    round: null,
    rounds: null,
    width: null,
    height: null,
    has_before: true,
    has_after: false,
    before_sha256: "aaa111",
    after_sha256: null,
    error_class: null,
    error_message: null,
    created_at: iso(-90_000),
    updated_at: iso(-30_000),
    ...overrides,
  };
}

const VERIFIED = () =>
  row({
    state: "verified",
    similarity: 0.92,
    width: 320,
    height: 240,
    has_after: true,
    after_sha256: "bbb222",
  });
const MISMATCH = () =>
  row({
    run_id: "c2",
    source: "afis.png",
    output: "afis-pagentos-1.png",
    state: "mismatch",
    similarity: 0.41,
    defect: "wrong_size",
    round: 2,
    rounds: 3,
    width: 640,
    height: 480,
    has_after: true,
    after_sha256: "ccc333",
  });
const UNAVAILABLE = () =>
  row({
    run_id: "c3",
    tool: "photoshop",
    source: "kapak.psd",
    output: null,
    operation: "open",
    state: "unavailable",
    has_before: true,
    has_after: false,
    error_class: "dependency_unavailable",
    error_message: "Photoshop.exe bulunamadı; Creative Cloud kurulu, Photoshop değil.",
  });
const FAILED = () =>
  row({
    run_id: "c4",
    state: "failed",
    error_class: "export_failed",
    error_message: "Pillow: cannot write mode RGBA as JPEG.",
  });

const ok = (rows: CreativeRunRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });
const noop = () => {};

function controlOf(overrides: Partial<CreativeControlProps> = {}): CreativeControlProps {
  return { ...CREATIVE_CONTROL_IDLE, onExport: noop, onCompare: noop, ...overrides };
}

/** A preview holding one blob URL per run and side, as the hook hands the panel. */
function previewOf(urls: Record<string, string> = {}, notice: string | null = null): CreativePreviewProps {
  return { srcFor: (runId, side) => urls[creativeSrcKey(runId, side)] ?? null, notice };
}

function panel(
  runs: Parameters<typeof CreativePanel>[0]["runs"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  control: CreativeControlProps = controlOf(),
  preview: CreativePreviewProps = CREATIVE_PREVIEW_NONE,
  now = T0,
) {
  return renderToStaticMarkup(
    <CreativePanel runs={runs} truth={truthOf(events)} now={now} control={control} preview={preview} />,
  );
}

function readout(events: ReturnType<typeof event>[], compact = false, now = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events), now)} compact={compact} />);
}

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

/**
 * Find the rendered element carrying every `attrs` pair, expanding function
 * components by calling them. The panel is a pure presentational component
 * with no hooks, so calling it is exactly what React would do.
 */
function findByData(root: unknown, attrs: Record<string, string>): ElementLike | null {
  const queue: unknown[] = [root];
  let guard = 0;
  while (queue.length > 0 && guard++ < 10_000) {
    const node = queue.shift();
    if (Array.isArray(node)) {
      queue.push(...node);
      continue;
    }
    if (!isElement(node)) continue;
    if (Object.entries(attrs).every(([k, v]) => node.props[k] === v)) return node;
    if (typeof node.type === "function") {
      const draw = node.type as (props: Record<string, unknown>) => unknown;
      queue.push(draw(node.props));
      continue;
    }
    const kids = node.props.children;
    if (kids !== undefined) queue.push(kids);
  }
  return null;
}

function click(node: ElementLike | null): void {
  expect(node).not.toBeNull();
  const handler = node?.props.onClick as (() => void) | undefined;
  expect(typeof handler).toBe("function");
  handler?.();
}

const RECEIPT_VERIFIED: CreativeActionReceipt = {
  state: "verified",
  similarity: 0.92,
  defect: null,
  errorClass: null,
  summary: "Çıktı yeniden açıldı.",
  receiptId: "r1",
};
const RECEIPT_MISMATCH: CreativeActionReceipt = {
  state: "mismatch",
  similarity: 0.41,
  defect: "wrong_size",
  errorClass: "postcondition_failed",
  summary: null,
  receiptId: "r2",
};

function fakeClient(overrides: Partial<CreativeClient> = {}): CreativeClient {
  return {
    export: vi.fn(async () => RECEIPT_VERIFIED),
    compare: vi.fn(async () => RECEIPT_MISMATCH),
    ...overrides,
  };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: CreativeClient, onSettled = vi.fn()) {
  let state: CreativeControlState = CREATIVE_CONTROL_IDLE;
  const writes: CreativeControlState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: CreativeControlState) => {
        state = next;
        writes.push(next);
      },
      onSettled,
      now: () => T0,
    },
    writes,
    current: () => state,
    onSettled,
  };
}

// ---------------------------------------------------------------- the panel

describe("the Yaratıcı panel", () => {
  it("is empty, in words, when the list route answered with no run and the bus said nothing", () => {
    const html = panel(ok([]));
    expect(html).toContain('data-panel="creative"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Henüz bir görsel çalışması yapılmadı.");
    expect(html).toContain('data-creative-activity="untold"');
    expect(html).toContain("Görsel çalışması etkinliği bildirilmedi.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain('data-creative-verified="0"');
    expect(html).toContain(">Yaratıcı<");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("data-creative-outcome");
    expect(html).not.toContain("attention");
    // What the chips do, what "doğrulandı" means, and the promise about the
    // owner's own file, are said in words on every render.
    expect(html).toContain(
      "Çıktı sahibin özgün dosyasının yanına YENİ bir dosya olarak yazılır; özgün dosyanın üzerine yazılmaz ve hiçbir şey silinmez.",
    );
    expect(html).toContain(
      "Bir görsel, bağımsız bir okuyucuyla açılıp istenenle karşılaştırıldığında doğrulanmış olur; önce değil.",
    );
    expect(html).toContain("Kurulu olmayan bir uygulama için düğme gösterilmez, taklit de edilmez.");
    expect(html).toContain("Bu ekran uygulama açmaz, fare kullanmaz, piksel çizmez, kod üretmez.");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Henüz bir görsel çalışması yapılmadı");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Henüz bir görsel çalışması yapılmadı");

    const absent = panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/creative/runs yok (HTTP 404)." });
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/creative/runs yok (HTTP 404).");
    expect(absent).not.toContain("Henüz bir görsel çalışması yapılmadı");
    for (const html of [loading, failed, absent]) {
      expect(html).not.toContain("<button");
      expect(html).not.toContain("<img");
    }
  });

  it("lists a verified Paint run with its metrics, both pictures and both chips", () => {
    const html = panel(
      ok([VERIFIED()]),
      [AGENT_IDLE()],
      controlOf(),
      previewOf({ "c1#before": "blob:before-1", "c1#after": "blob:after-1" }),
    );
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1 doğrulandı / 1<');
    expect(html).toContain('data-creative-verified="1"');
    expect(html).toContain('data-creative-run="c1"');
    expect(html).toContain('data-creative-row-tool="paint"');
    expect(html).toContain('data-creative-row-source="logo.png"');
    expect(html).toContain('data-creative-row-output="logo-pagentos-1.png"');
    expect(html).toContain('data-creative-row-operation="draw"');
    expect(html).toContain('data-creative-row-state="verified"');
    expect(html).toContain('data-creative-row-similarity="0.92"');
    expect(html).toContain('data-creative-row-verified="yes"');
    expect(html).toContain('data-creative-row-unavailable="no"');
    expect(html).toContain('data-creative-row-has-before="yes"');
    expect(html).toContain('data-creative-row-has-after="yes"');
    expect(html).toContain("30 sn önce");
    // The promise that the original is not touched, kept where it can be read.
    expect(html).toContain("logo.png → logo-pagentos-1.png");
    // The application, the operation and the step, with the figure the
    // COMPARISON measured.
    expect(html).toContain('data-creative-line="true">Paint · çizim · doğrulandı (benzerlik %92)</span>');
    // What the comparison measured, on its own line.
    expect(html).toContain('data-creative-metrics="0.92">320×240 · (benzerlik %92)</span>');
    // Both pictures, each labelled, with the src the session-gated fetch
    // produced — never the bare API URL.
    expect(html).toContain('data-creative-image-count="2"');
    expect(html).toContain('data-creative-figure="before"');
    expect(html).toContain('data-creative-figure="after"');
    expect(html).toContain(">Önce</figcaption>");
    expect(html).toContain(">Sonra</figcaption>");
    expect(html).toContain('<img class="creative-image" src="blob:before-1"');
    expect(html).toContain('<img class="creative-image" src="blob:after-1"');
    expect(html).toContain('alt="logo.png: sahibin özgün görseli"');
    expect(html).toContain('alt="logo-pagentos-1.png: üretilen görsel"');
    expect(html).toContain('data-creative-image-sha="aaa111"');
    expect(html).toContain('data-creative-image-sha="bbb222"');
    expect(html).not.toContain(creativeImagePath("c1", "after"));
    expect(html).not.toContain("data-creative-image-pending");
    // Both chips, enabled, nothing in flight.
    expect(html).toContain('data-creative-controls="c1"');
    expect(html).toContain('data-creative-in-flight="no"');
    expect(html).toContain('data-creative-action="export" data-creative-target="c1" data-creative-enabled="yes">Dışa aktar</button>');
    expect(html).toContain('data-creative-action="compare" data-creative-target="c1" data-creative-enabled="yes">Karşılaştır</button>');
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    expect(html).not.toContain("data-creative-reason");
    expect(html).not.toContain("attention");
  });

  it("draws no picture the row does not promise, and says so for one it has not fetched", () => {
    // No output on the row: one figure, not two, whatever the preview holds.
    const one = panel(ok([row()]), [AGENT_IDLE()], controlOf(), previewOf({ "c1#before": "blob:b", "c1#after": "blob:leftover" }));
    expect(one).toContain('data-creative-row-has-after="no"');
    expect(one).toContain('data-creative-image-count="1"');
    expect(one).toContain('data-creative-figure="before"');
    expect(one).not.toContain('data-creative-figure="after"');
    expect(one).not.toContain("blob:leftover");

    // A row that promises neither draws no image block at all.
    const none = panel(ok([row({ has_before: false, has_after: false })]));
    expect(none).not.toContain("data-creative-images");
    expect(none).not.toContain("<img");

    // Pictures the row promises but the page has not fetched: said, not drawn.
    const pending = panel(ok([VERIFIED()]));
    expect(pending).toContain('data-creative-row-has-after="yes"');
    expect(pending).not.toContain("<img");
    expect(pending).toContain('data-creative-image-pending="before"');
    expect(pending).toContain('data-creative-image-pending="after"');
    expect(pending).toContain("Görsel var; henüz alınmadı.");

    // A picture that could not be fetched is named, and the row's line stays.
    const failed = panel(ok([VERIFIED()]), [AGENT_IDLE()], controlOf(), previewOf({}, "Görsel alınamadı (c1 · sonra): HTTP 503"));
    expect(failed).toContain("data-creative-image-notice");
    expect(failed).toContain("Görsel alınamadı (c1 · sonra): HTTP 503");
    expect(failed).toContain('data-creative-line="true">Paint · çizim · doğrulandı (benzerlik %92)</span>');
  });

  it("lists an uninstalled Photoshop row in the spec's own words and NO controls", () => {
    const html = panel(ok([UNAVAILABLE()]), [AGENT_IDLE()], controlOf(), previewOf({ "c3#after": "blob:never" }));
    expect(html).toContain('data-creative-run="c3"');
    expect(html).toContain('data-creative-row-tool="photoshop"');
    expect(html).toContain('data-creative-row-state="unavailable"');
    expect(html).toContain('data-creative-row-unavailable="yes"');
    expect(html).toContain('data-creative-line="true">Photoshop · açma · kurulu değil — yapılamadı</span>');
    // The run's own sentence, as the row carried it.
    expect(html).toContain("data-creative-error-message");
    expect(html).toContain("Photoshop.exe bulunamadı");
    // No control over an application this machine does not have — not disabled: absent.
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-creative-controls");
    expect(html).not.toContain("data-creative-action");
    // No output picture either: the row promised none.
    expect(html).not.toContain("blob:never");
    expect(html).not.toContain('data-creative-figure="after"');
    // And an application that is not installed is not a failure, and not "attention".
    expect(html).not.toContain("başarısız");
    expect(html).not.toContain("attention");
    expect(html).not.toContain("doğrulandı<");
  });

  it("lists a mismatch naming the defect the comparison named, with its round, and draws attention to it", () => {
    const html = panel(ok([MISMATCH()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-creative-run="c2"');
    expect(html).toContain('data-creative-row-state="mismatch"');
    expect(html).toContain('data-creative-row-defect="wrong_size"');
    expect(html).toContain('data-creative-line="true">Paint · çizim · uyuşmazlık — ölçü tutmadı</span>');
    expect(html).toContain('data-creative-metrics="0.41">640×480 · (benzerlik %41) · ölçü tutmadı</span>');
    expect(html).toContain('data-creative-round="2">2/3. tur</span>');
    // A mismatch is never dressed as done.
    expect(html).not.toContain("doğrulandı");
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).toContain('data-creative-verified="0"');
    // A mismatch is still a run in an application that answers, with an output: both chips stand.
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    // A mismatch with no defect named says the plain word, and invents none.
    const bare = panel(ok([row({ state: "mismatch", similarity: 0.4 })]));
    expect(bare).toContain('data-creative-line="true">Paint · çizim · uyuşmazlık</span>');
    expect(bare).toContain('data-creative-row-defect=""');
    expect(bare).toContain(CREATIVE_DEFECT_UNTOLD);
  });

  it("lists a failed run naming its error, and draws attention to it", () => {
    const html = panel(ok([FAILED()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-creative-row-failed="yes"');
    expect(html).toContain('data-creative-line="true">Paint · çizim · başarısız</span>');
    expect(html).toContain('data-creative-error-message="true">Pillow: cannot write mode RGBA as JPEG.</span>');
    // A message is printed only beside the two steps that have one to give.
    const working = panel(ok([row({ state: "executing", error_message: "eski mesaj" })]));
    expect(working).not.toContain("data-creative-error-message");
  });

  it("says what a row did not report rather than filling it in, and gives a word it cannot read no chips", () => {
    const html = panel(
      ok([
        row({
          tool: null,
          source: null,
          output: null,
          operation: null,
          state: null,
          has_before: false,
          updated_at: null,
          created_at: null,
        }),
      ]),
    );
    expect(html).toContain(">dosya adı bildirilmedi<");
    expect(html).toContain('data-creative-row-tool=""');
    expect(html).toContain('data-creative-row-source=""');
    expect(html).toContain('data-creative-row-operation=""');
    expect(html).toContain('data-creative-row-state=""');
    expect(html).toContain('data-creative-row-similarity=""');
    expect(html).toContain('data-creative-line="true">durum bildirilmedi</span>');
    // No comparison ever ran: no metrics line at all. "Not measured yet" and
    // "measured and reported nothing" are different answers.
    expect(html).not.toContain("data-creative-metrics");
    // No state is not a run known to be driveable: nothing invites a click.
    expect(html).not.toContain("<button");
    // A newer server's word is printed verbatim — still a published fact — and earns no chip either.
    const unknown = panel(ok([row({ state: "retouching" })]));
    expect(unknown).toContain('data-creative-line="true">Paint · çizim · retouching</span>');
    expect(unknown).not.toContain("<button");
    // A verified run whose comparison published no figure says so rather than "%0".
    const nofigure = panel(ok([row({ state: "verified" })]));
    expect(nofigure).toContain('data-creative-line="true">Paint · çizim · doğrulandı</span>');
    expect(nofigure).toContain(CREATIVE_SIMILARITY_UNTOLD);
    expect(nofigure).not.toContain("benzerlik %0");
  });

  it("shows only the export chip for a run with no output to compare, and says why it is not there", () => {
    // The row cannot take a comparison: the control is ABSENT, not disabled.
    const html = panel(ok([row({ state: "executing", has_after: false })]));
    expect(html).toContain('data-creative-action="export"');
    expect(html).not.toContain('data-creative-action="compare"');
    expect((html.match(/<button/g) ?? []).length).toBe(1);
    // The gate still answers for it, in words, for anything that asks.
    expect(creativeActionGate({ state: "executing", has_after: false }, "compare", null)).toEqual({
      enabled: false,
      reason: CREATIVE_REASON_NO_OUTPUT,
      reasonKind: "no_output",
    });
  });

  it("shows the last runs as the route orders them, bounded, and counts the verified ones in the badge", () => {
    const rows = Array.from({ length: CREATIVE_ROWS_SHOWN + 3 }, (_, i) =>
      row({ run_id: `c${i}`, source: `g${i}.png`, state: i < 2 ? "verified" : "executing" }),
    );
    const html = panel(ok(rows));
    expect(html).toContain(`data-panel-badge="true">2 doğrulandı / ${CREATIVE_ROWS_SHOWN + 3}<`);
    expect(html).toContain('data-creative-verified="2"');
    expect((html.match(/data-creative-run="c\d+"/g) ?? []).length).toBe(CREATIVE_ROWS_SHOWN);
    expect(html).toContain('data-creative-run="c0"');
    expect(html).not.toContain(`data-creative-run="c${CREATIVE_ROWS_SHOWN}"`);
  });

  it("each chip asks for that run exactly once, and only through its own handler", () => {
    const onExport = vi.fn();
    const onCompare = vi.fn();
    const rows = [VERIFIED(), MISMATCH(), UNAVAILABLE()];
    const tree = (
      <CreativePanel
        runs={ok(rows)}
        truth={truthOf([AGENT_IDLE()])}
        now={T0}
        control={controlOf({ onExport, onCompare })}
        preview={CREATIVE_PREVIEW_NONE}
      />
    );

    click(findByData(tree, { "data-creative-action": "export", "data-creative-target": "c1" }));
    expect(onExport).toHaveBeenCalledTimes(1);
    expect(onExport).toHaveBeenCalledWith("c1");
    expect(onCompare).not.toHaveBeenCalled();

    click(findByData(tree, { "data-creative-action": "compare", "data-creative-target": "c2" }));
    expect(onCompare).toHaveBeenCalledTimes(1);
    expect(onCompare).toHaveBeenCalledWith("c2");
    expect(onExport).toHaveBeenCalledTimes(1);

    // An application that is not installed has no chip to find at all.
    expect(findByData(tree, { "data-creative-action": "export", "data-creative-target": "c3" })).toBeNull();
    expect(findByData(tree, { "data-creative-action": "compare", "data-creative-target": "c3" })).toBeNull();
  });

  it("disables every chip while one call is in flight, marking the run and the action it is, with the reason said once", () => {
    const html = panel(ok([VERIFIED(), MISMATCH()]), [AGENT_IDLE()], controlOf({ busy: { action: "export", id: "c1" } }));
    expect(html).toContain('data-creative-controls="c1" data-creative-in-flight="yes" data-creative-in-flight-action="export"');
    expect(html).toContain('data-creative-controls="c2" data-creative-in-flight="no" data-creative-in-flight-action=""');
    expect(html).toContain('data-creative-action="export" data-creative-target="c1" data-creative-enabled="no" disabled=""');
    expect(html).toContain('data-creative-action="compare" data-creative-target="c1" data-creative-enabled="no" disabled=""');
    expect(html).toContain('data-creative-action="export" data-creative-target="c2" data-creative-enabled="no" disabled=""');
    expect(html).toContain('data-creative-reason="busy" data-creative-reason-for="export,compare"');
    expect(html).toContain(CREATIVE_REASON_BUSY);
    expect(html).not.toContain(`Dışa aktar, Karşılaştır: ${CREATIVE_REASON_BUSY}`);
    // Said once per run, not once per chip.
    expect((html.match(/data-creative-reason="busy"/g) ?? []).length).toBe(2);
  });

  it("prints the last call's answer, dated, with the run and the action it was about", () => {
    const outcome = {
      action: "compare" as const,
      id: "c1",
      ok: true,
      text: "Doğrulandı (benzerlik %92) · Çıktı yeniden açıldı.",
      at: T0 - 5_000,
    };
    const html = panel(ok([VERIFIED()]), [AGENT_IDLE()], controlOf({ outcome }));
    expect(html).toContain('data-creative-outcome="compare"');
    expect(html).toContain('data-creative-ok="yes"');
    expect(html).toContain('data-creative-target="c1"');
    expect(html).toContain("Doğrulandı (benzerlik %92) · Çıktı yeniden açıldı. · 5 sn önce");

    const refused = panel(
      ok([VERIFIED()]),
      [AGENT_IDLE()],
      controlOf({ outcome: { ...outcome, ok: false, text: "Uygulama sürülemedi; yapılamadı." } }),
    );
    expect(refused).toContain('data-creative-ok="no"');
    expect(refused).toContain("panel-unknown");
    expect(refused).toContain("Uygulama sürülemedi; yapılamadı.");
  });

  it("states the bus activity in the spec's words with its age and posture, and last-known once it aged out", () => {
    const live = panel(ok([]), [CREATIVE_ACTIVITY("paint", "draw", "comparing")], controlOf(), CREATIVE_PREVIEW_NONE, T0 + 3_000);
    expect(live).toContain('data-creative-stage="active"');
    expect(live).toContain('data-creative-activity="active"');
    expect(live).toContain('data-creative-posture="comparing"');
    expect(live).toContain('data-creative-caption="Paint · çizim · karşılaştırılıyor"');
    expect(live).toContain("Paint · çizim · karşılaştırılıyor · 3 sn önce");
    expect(live).not.toContain("Son bilinen");
    // The list is the list: the bus comparing something does not put a row on
    // it, nor a chip, nor a picture.
    expect(live).toContain("Henüz bir görsel çalışması yapılmadı.");
    expect(live).not.toContain("<button");
    expect(live).not.toContain("<img");

    const executing = panel(ok([]), [CREATIVE_ACTIVITY("paint", "draw", "executing")], controlOf(), CREATIVE_PREVIEW_NONE, T0 + 3_000);
    expect(executing).toContain('data-creative-posture="making"');
    // The apostrophe of the locative is HTML-escaped in the markup; the sentence is what is asserted.
    expect(executing).toContain("Paint&#x27;te çizim uygulanıyor · 3 sn önce");

    const unavailable = panel(
      ok([]),
      [CREATIVE_ACTIVITY("photoshop", "open", "unavailable")],
      controlOf(),
      CREATIVE_PREVIEW_NONE,
      T0 + 3_000,
    );
    expect(unavailable).toContain('data-creative-posture="unavailable"');
    expect(unavailable).toContain("Photoshop · açma · kurulu değil — yapılamadı · 3 sn önce");

    const stale = panel(
      ok([]),
      [CREATIVE_ACTIVITY("paint", "draw", "verified", 0.92)],
      controlOf(),
      CREATIVE_PREVIEW_NONE,
      T0 + CREATIVE_TTL_MS + 1_000,
    );
    expect(stale).toContain('data-creative-stage="none"');
    expect(stale).toContain('data-creative-last-known="active"');
    expect(stale).toContain('data-creative-posture="verified"');
    expect(stale).toContain("Son bilinen: Paint · çizim · doğrulandı (benzerlik %92) · 1 dk önce");

    // No other family's event is a creative event — the 3D family's least of all.
    for (const other of [
      [DOCUMENT_ANALYSIS()],
      [MAIL_ACTIVITY()],
      [ARTIFACT_FACTORY()],
      [APP_FACTORY()],
      [CAPABILITY_GENESIS()],
      [SCENE_ACTIVITY()],
      [EXECUTIVE_RUN()],
    ]) {
      const html = panel(ok([]), other);
      expect(html).toContain('data-creative-activity="untold"');
      expect(html).toContain('data-creative-posture=""');
    }
  });
});

// ------------------------------------------------------------- the layout

/**
 * The row's stacked lines have to STACK.
 *
 * `.panel li` is a plain block and the lines under a row are sibling
 * `<span>`s, which are inline: without a rule of their own they run into one
 * another and the owner reads "uyuşmazlık — ölçü tutmadı640×480" as one
 * word. That is not a style preference — it is two separate published facts
 * presented as one string, which is the class of thing this whole family
 * exists to refuse. (The same defect is visible on the M25 "3B Sahne" panel,
 * which is main's to fix; this test holds THIS panel to the rule.)
 *
 * A stylesheet cannot be asserted by rendering, so the two halves are held
 * to each other instead: the markup must carry the class, and `core.css`
 * must carry the rule that class needs. Either one alone is silent.
 */
describe("the row's lines stack rather than running together", () => {
  it("marks every creative row with the class the stylesheet's stacking rule keys on", () => {
    const html = panel(ok([MISMATCH()]));
    expect(html).toContain('<li class="creative-run"');
    // Every stacked line is a direct child span of that li, so one rule reaches all of them.
    for (const attr of ["data-creative-line", "data-creative-metrics", "data-creative-round"]) {
      expect(html, attr).toContain(attr);
    }
  });

  it("keeps the stacking rule in core.css, for the class the markup carries", () => {
    const css = readFileSync(join(__dirname, "..", "..", "app", "core", "core.css"), "utf-8");
    const rule = /\.creative-run\s*>\s*span\s*\{[^}]*display:\s*block[^}]*\}/;
    expect(rule.test(css), "core.css has no `.creative-run > span { display: block }` rule").toBe(true);
    // Not vacuous: the same matcher finds nothing for a class that is not there.
    expect(/\.creative-nonexistent\s*>\s*span\s*\{[^}]*display:\s*block[^}]*\}/.test(css)).toBe(false);
  });
});

// -------------------------------------------------------------- the rows

describe("the rows", () => {
  it("a run is verified, unavailable or has a picture because its row says so", () => {
    expect(rowIsVerified({ state: "verified" })).toBe(true);
    expect(rowIsVerified({ state: "VERIFIED" })).toBe(false);
    expect(rowIsVerified({ state: "exporting" })).toBe(false);
    expect(rowIsUnavailable({ state: "unavailable" })).toBe(true);
    expect(rowIsUnavailable({ state: null })).toBe(false);
    // The images hang on the flags alone, never on a step or a sha.
    expect(rowHasImage({ has_before: true, has_after: false }, "before")).toBe(true);
    expect(rowHasImage({ has_before: true, has_after: false }, "after")).toBe(false);
    expect(creativeRowSides({ has_before: true, has_after: true })).toEqual(["before", "after"]);
    expect(creativeRowSides({ has_before: false, has_after: true })).toEqual(["after"]);
    expect(creativeRowSides({ has_before: false, has_after: false })).toEqual([]);
    expect(CREATIVE_SIDE_LABEL).toEqual({ before: "Önce", after: "Sonra" });
  });

  it("draws the export chip for a driveable run, the compare chip only with an output, and NONE otherwise", () => {
    for (const state of [
      "analysing",
      "planning",
      "executing",
      "inspecting",
      "exporting",
      "comparing",
      "correcting",
      "verified",
      "unverified",
      "mismatch",
      "failed",
    ]) {
      expect(creativeRowActions({ state, has_after: true }), state).toEqual(["export", "compare"]);
      expect(creativeRowActions({ state, has_after: false }), state).toEqual(["export"]);
    }
    // The words a database ROW might carry that this vocabulary does not have.
    // M25 shipped exactly this drift: `/v1/scenes` sent the row's own word and
    // the panel showed no chips on any real scene. A row saying nothing this
    // build can read still shows none, which is the honest case.
    for (const dbWord of ["applied", "rendered", "dependency_unavailable", "planned", "exported", "compared"]) {
      expect(creativeRowActions({ state: dbWord, has_after: true }), dbWord).toEqual([]);
    }
    expect(creativeRowActions({ state: "unavailable", has_after: true })).toEqual([]);
    expect(creativeRowActions({ state: null, has_after: true })).toEqual([]);
    expect(CREATIVE_ACTION_LABEL).toEqual({ export: "Dışa aktar", compare: "Karşılaştır" });
  });

  it("the gate opens each chip only for a row the Cloud Core would not refuse, while nothing is in flight", () => {
    const busy = { action: "export" as const, id: "c9" };
    for (const state of [...CREATIVE_RUN_STATES, null, "retouching"]) {
      for (const action of ["export", "compare"] as const) {
        expect(creativeActionGate({ state, has_after: true }, action, busy), `${state}/${action}`).toEqual({
          enabled: false,
          reason: CREATIVE_REASON_BUSY,
          reasonKind: "busy",
        });
      }
    }
    const open = { enabled: true, reason: null, reasonKind: null };
    for (const state of CREATIVE_RUN_STATES.filter((s) => s !== "unavailable")) {
      for (const action of ["export", "compare"] as const) {
        expect(creativeActionGate({ state, has_after: true }, action, null), `${state}/${action}`).toEqual(open);
      }
      // Without an output only the export stands.
      expect(creativeActionGate({ state, has_after: false }, "export", null), state).toEqual(open);
      expect(creativeActionGate({ state, has_after: false }, "compare", null).reasonKind, state).toBe("no_output");
    }
    for (const action of ["export", "compare"] as const) {
      expect(creativeActionGate({ state: "unavailable", has_after: true }, action, null)).toEqual({
        enabled: false,
        reason: CREATIVE_REASON_UNAVAILABLE,
        reasonKind: "unavailable",
      });
      for (const state of [null, "retouching"]) {
        expect(creativeActionGate({ state, has_after: true }, action, null), `${state}/${action}`).toEqual({
          enabled: false,
          reason: CREATIVE_REASON_UNKNOWN_STATE,
          reasonKind: "unknown_state",
        });
      }
    }
  });

  it("lines each row with its application, its operation and its step, and the figure and the defect only where they belong", () => {
    expect(creativeRowLine(row({ state: "exporting" }))).toBe("Paint · çizim · dışa aktarılıyor");
    expect(creativeRowLine(VERIFIED())).toBe("Paint · çizim · doğrulandı (benzerlik %92)");
    expect(creativeRowLine(MISMATCH())).toBe("Paint · çizim · uyuşmazlık — ölçü tutmadı");
    expect(creativeRowLine(UNAVAILABLE())).toBe("Photoshop · açma · kurulu değil — yapılamadı");
    expect(creativeRowLine(FAILED())).toBe("Paint · çizim · başarısız");
    // An unavailable Paint gets no reason invented for it: Paint IS installed.
    expect(creativeRowLine(row({ state: "unavailable" }))).toBe("Paint · çizim · yapılamadı");
    expect(creativeRowLine(row({ tool: null, operation: null, state: "unavailable" }))).toBe("yapılamadı");
    // A defect beside a step that is not a mismatch is not a mismatch.
    expect(creativeRowLine(row({ state: "verified", similarity: 0.8, defect: "color_drift" }))).toBe(
      "Paint · çizim · doğrulandı (benzerlik %80)",
    );
    expect(creativeRowLine(row({ state: null }))).toBe("Paint · çizim · durum bildirilmedi");
    expect(creativeRowLine(row({ tool: "gimp", operation: "erase", state: "retouching" }))).toBe("gimp · erase · retouching");
  });

  it("lines the comparison's own measurements, and says which of them nobody reported", () => {
    expect(creativeMetricsLine(VERIFIED())).toBe("320×240 · (benzerlik %92)");
    expect(creativeMetricsLine(MISMATCH())).toBe("640×480 · (benzerlik %41) · ölçü tutmadı");
    // A verified run that measured nothing says so rather than "%0".
    expect(creativeMetricsLine(row({ state: "verified" }))).toBe(CREATIVE_SIMILARITY_UNTOLD);
    // A mismatch that named no defect says that too: what disagreed is unknown.
    expect(creativeMetricsLine(row({ state: "mismatch", similarity: 0.5 }))).toBe(
      `(benzerlik %50) · ${CREATIVE_DEFECT_UNTOLD}`,
    );
    // One dimension alone is not a size, so neither is printed.
    expect(creativeMetricsLine(row({ state: "verified", width: 320, similarity: 0.9 }))).toBe("(benzerlik %90)");
    // And a run that never reached the comparison has no line at all.
    expect(creativeHasMetrics(row({ state: "executing" }))).toBe(false);
    expect(creativeHasMetrics(row({ state: "verified" }))).toBe(true);
    expect(creativeHasMetrics(row({ state: "mismatch" }))).toBe(true);
    expect(creativeHasMetrics(row({ state: "executing", similarity: 0.5 }))).toBe(true);
  });

  it("names the two files so the owner can see the original was not overwritten", () => {
    expect(creativeFilesLine({ source: "logo.png", output: "logo-pagentos-1.png" })).toBe("logo.png → logo-pagentos-1.png");
    expect(creativeFilesLine({ source: "logo.png", output: null })).toBe("logo.png");
    expect(creativeFilesLine({ source: null, output: "yeni.png" })).toBe("yeni.png");
    expect(creativeFilesLine({ source: null, output: null })).toBeNull();
    // The output is never the source: that is the whole promise.
    expect(creativeFilesLine({ source: "logo.png", output: "logo-pagentos-1.png" })).not.toBe("logo.png → logo.png");
  });

  it("counts a correction round only when one ran, and never calls a first pass one", () => {
    expect(creativeRoundPhrase({ round: 2, rounds: 3 })).toBe("2/3. tur");
    expect(creativeRoundPhrase({ round: 1, rounds: null })).toBe(`1/${MAX_CREATIVE_ROUNDS}. tur`);
    expect(creativeRoundPhrase({ round: null, rounds: 3 })).toBeNull();
    // A `0` is not a round: no correction has run.
    expect(asCreativeRound(0)).toBeNull();
    expect(asCreativeRound(1)).toBe(1);
    expect(asCreativeRound(MAX_CREATIVE_ROUNDS)).toBe(MAX_CREATIVE_ROUNDS);
    expect(asCreativeRound(MAX_CREATIVE_ROUNDS + 1)).toBeNull();
    expect(asCreativeRound("2")).toBeNull();
    expect(asCreativeRound(1.5)).toBeNull();
  });

  it("names each picture for a reader from the file it came from, and says so when there is none", () => {
    expect(creativeImageAlt({ source: "logo.png", output: "logo-pagentos-1.png" }, "before")).toBe(
      "logo.png: sahibin özgün görseli",
    );
    expect(creativeImageAlt({ source: "logo.png", output: "logo-pagentos-1.png" }, "after")).toBe(
      "logo-pagentos-1.png: üretilen görsel",
    );
    expect(creativeImageAlt({ source: null, output: null }, "before")).toBe("Adı bildirilmeyen özgün görsel");
    expect(creativeImageAlt({ source: null, output: null }, "after")).toBe("Adı bildirilmeyen üretilen görsel");
    for (const side of ["before", "after"] as const) {
      expect(creativeImageAlt({ source: null, output: null }, side)).not.toBe("");
    }
  });
});

// ------------------------------------------------------------ the client

describe("the client's shapes", () => {
  it("addresses one run by path segment, never by query", () => {
    expect(creativeActionPath("c1", "export")).toBe("/v1/creative/runs/c1/export");
    expect(creativeActionPath("c1", "compare")).toBe("/v1/creative/runs/c1/compare");
    expect(creativeImagePath("c1", "before")).toBe("/v1/creative/runs/c1/image/before");
    expect(creativeImagePath("c1", "after")).toBe("/v1/creative/runs/c1/image/after");
    expect(creativeActionPath("a/b", "export")).toBe("/v1/creative/runs/a%2Fb/export");
  });

  it("reads a row's fields verbatim, and refuses a row that is not a run", () => {
    const parsed = parseCreativeRunRow({
      run_id: "c1",
      tool: "paint",
      source: "logo.png",
      output: "logo-pagentos-1.png",
      operation: "draw",
      state: "verified",
      similarity: 0.92,
      width: 320,
      height: 240,
      after_sha256: "bbb222",
    });
    expect(parsed?.run_id).toBe("c1");
    expect(parsed?.similarity).toBe(0.92);
    expect(parsed?.width).toBe(320);
    // No flag, but a stored picture's identity (or a name): the row does say one exists.
    expect(parsed?.has_before).toBe(true);
    expect(parsed?.has_after).toBe(true);
    // An explicit flag beats the identity, in both directions.
    expect(parseCreativeRunRow({ id: "c2", has_after: false, after_sha256: "x" })?.has_after).toBe(false);
    expect(parseCreativeRunRow({ id: "c3", has_after: true })?.has_after).toBe(true);
    // Nothing at all said about a picture is no picture.
    expect(parseCreativeRunRow({ id: "c4", state: "verified" })?.has_after).toBe(false);
    expect(parseCreativeRunRow({ id: "c4", state: "verified" })?.has_before).toBe(false);
    // A row with no id is not a run.
    expect(parseCreativeRunRow({ tool: "paint" })).toBeNull();
    expect(parseCreativeRunRow(null)).toBeNull();
    // A similarity that is not a bounded fraction is no similarity.
    expect(parseCreativeRunRow({ id: "c5", similarity: 92 })?.similarity).toBeNull();
    expect(parseCreativeRunRow({ id: "c6", similarity: "0.9" })?.similarity).toBeNull();
    expect(parseCreativeRunRow({ id: "c7", width: -1 })?.width).toBeNull();
  });

  it("caches one picture per side per identity, so NEW bytes replace the old ones", () => {
    const first = VERIFIED();
    expect(creativeImageKey(first, "before")).toBe("c1#before#aaa111");
    expect(creativeImageKey(first, "after")).toBe("c1#after#bbb222");
    // The two sides of one run never share a key, whatever their shas.
    expect(creativeImageKey(first, "before")).not.toBe(creativeImageKey(first, "after"));
    // A new output: a different key, so it is fetched again rather than the
    // old picture being shown for the new one.
    expect(creativeImageKey({ ...first, after_sha256: "ddd444" }, "after")).not.toBe(creativeImageKey(first, "after"));
    expect(creativeImageKey({ ...first }, "after")).toBe(creativeImageKey(first, "after"));
    // With no sha, the row's own update time keeps them apart.
    expect(
      creativeImageKey({ run_id: "c1", before_sha256: null, after_sha256: null, updated_at: iso(-1_000) }, "after"),
    ).not.toBe(creativeImageKey({ run_id: "c1", before_sha256: null, after_sha256: null, updated_at: iso(-2_000) }, "after"));
    // The panel and the hook agree on what the src is filed under.
    expect(creativeSrcKey("c1", "after")).toBe("c1#after");
  });

  it("says a picture it could not fetch, naming the run, the side and the reason", () => {
    expect(creativeImageNotice("c1", "after", new Error("HTTP 503"))).toBe("Görsel alınamadı (c1 · sonra): HTTP 503");
    expect(creativeImageNotice("c1", "before", "boom")).toBe("Görsel alınamadı (c1 · önce): boom");
    // Never "there is no picture": the row said there is one.
    expect(creativeImageNotice("c1", "after", new Error("HTTP 503"))).not.toContain("yok");
  });

  it("reads a receipt from any of the shapes, and never invents a step", () => {
    expect(parseCreativeReceipt({ state: "verified", similarity: 0.92 })).toMatchObject({
      state: "verified",
      similarity: 0.92,
    });
    expect(parseCreativeReceipt({ receipt: { state: "mismatch" }, comparison: { defect: "color_drift" } })).toMatchObject({
      state: "mismatch",
      defect: "color_drift",
    });
    expect(parseCreativeReceipt({ receipt: {}, comparison: { similarity: 0.5 } }).similarity).toBe(0.5);
    expect(parseCreativeReceipt({ receipt: {}, run: { state: "failed", error_class: "export_failed" } })).toMatchObject({
      state: "failed",
      errorClass: "export_failed",
    });
    // A 2xx with nothing in it names nothing.
    const bare = parseCreativeReceipt({});
    expect(bare.state).toBeNull();
    expect(bare.similarity).toBeNull();
    expect(bare.defect).toBeNull();
    expect(parseCreativeReceipt(null).state).toBeNull();
  });
});

// ------------------------------------------------------------ the runner

describe("the action runner", () => {
  it("makes exactly one call with the run's id, writes busy then the receipt's outcome, and reloads the list", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runCreativeAction(ports, "export", "c1")).toBe(true);
    expect(client.export).toHaveBeenCalledTimes(1);
    expect(client.export).toHaveBeenCalledWith("c1");
    expect(client.compare).not.toHaveBeenCalled();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: { action: "export", id: "c1" }, outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      action: "export",
      id: "c1",
      ok: true,
      text: "Doğrulandı (benzerlik %92) · Çıktı yeniden açıldı.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);

    const second = portsOf(fakeClient());
    await runCreativeAction(second.ports, "compare", "c2");
    expect(second.ports.client.compare).toHaveBeenCalledTimes(1);
    expect(second.ports.client.compare).toHaveBeenCalledWith("c2");
    expect(second.ports.client.export).not.toHaveBeenCalled();
    expect(second.current().outcome?.text).toBe("Uyuşmazlık — ölçü tutmadı");
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    const deferred: { release: ((receipt: CreativeActionReceipt) => void) | null } = { release: null };
    const exportFn = vi.fn(async () => RECEIPT_VERIFIED);
    exportFn.mockImplementationOnce(
      () =>
        new Promise<CreativeActionReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ export: exportFn });
    const { ports, current } = portsOf(client);

    const first = runCreativeAction(ports, "export", "c1");
    expect(current().busy).toEqual({ action: "export", id: "c1" });
    expect(await runCreativeAction(ports, "export", "c1")).toBe(false);
    expect(await runCreativeAction(ports, "compare", "c1")).toBe(false);
    expect(await runCreativeAction(ports, "export", "c2")).toBe(false);
    expect(exportFn).toHaveBeenCalledTimes(1);
    expect(client.compare).not.toHaveBeenCalled();

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_VERIFIED);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second export is the
    // Cloud Core's to refuse, not this page's to hide.
    expect(await runCreativeAction(ports, "export", "c1")).toBe(true);
    expect(exportFn).toHaveBeenCalledTimes(2);
  });

  it("turns the Cloud Core's refusal — and a route not there yet — into the owner's words, says nothing happened, and still reloads", async () => {
    const client = fakeClient({
      export: vi.fn(async () => {
        throw new CreativeActionError(409, "dependency_unavailable", "photoshop.exe not found");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runCreativeAction(ports, "export", "c3")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toBe("Uygulama sürülemedi; yapılamadı.");
    expect(outcome?.text).not.toContain("doğrulandı");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    // The one refusal that is a promise rather than a fault: the original is
    // never overwritten (ADR-0093 decision 5), and the sentence says so.
    const overwrite = portsOf(
      fakeClient({
        export: vi.fn(async () => {
          throw new CreativeActionError(409, "overwrite_refused", "would overwrite logo.png");
        }),
      }),
    );
    await runCreativeAction(overwrite.ports, "export", "c1");
    expect(overwrite.current().outcome?.text).toBe("Özgün dosyanın üzerine yazılmaz; hiçbir şey değiştirilmedi.");

    const absent = fakeClient({
      compare: vi.fn(async () => {
        throw new CreativeActionError(
          404,
          CREATIVE_ROUTE_ABSENT,
          "Bu Cloud Core sürümünde /v1/creative/runs/c1/compare yok (HTTP 404).",
        );
      }),
    });
    const second = portsOf(absent);
    await runCreativeAction(second.ports, "compare", "c1");
    expect(second.current().outcome?.ok).toBe(false);
    expect(second.current().outcome?.text).toBe(
      "Bu Cloud Core sürümünde /v1/creative/runs/c1/compare yok (HTTP 404). Yapılmadı.",
    );
  });

  it("never says 'doğrulandı' or 'dışa aktarıldı' on the strength of a 2xx alone", () => {
    const none: CreativeActionReceipt = {
      state: null,
      similarity: null,
      defect: null,
      errorClass: null,
      summary: null,
      receiptId: "r1",
    };
    expect(creativeOutcomeText("export", none)).toBe(CREATIVE_OUTCOME_NO_STATE_TR.export);
    expect(creativeOutcomeText("export", none)).not.toMatch(/doğrulandı|aktarıldı/);
    expect(creativeOutcomeText("compare", none)).toBe(CREATIVE_OUTCOME_NO_STATE_TR.compare);
    expect(creativeOutcomeText("export", { ...none, state: "exporting" })).toBe("Dışa aktarılıyor");
    expect(creativeOutcomeText("compare", { ...none, state: "verified", similarity: 0.92 })).toBe(
      "Doğrulandı (benzerlik %92)",
    );
    expect(creativeOutcomeText("compare", { ...none, state: "verified" })).toBe("Doğrulandı");
    expect(creativeOutcomeText("compare", { ...none, state: "mismatch", defect: "empty_output" })).toBe(
      "Uyuşmazlık — çıktı boş",
    );
    // The "kurulu değil" wording needs the application the caller knows the run is in.
    expect(creativeOutcomeText("export", { ...none, state: "unavailable" }, "photoshop")).toBe("Kurulu değil — yapılamadı");
    expect(creativeOutcomeText("export", { ...none, state: "unavailable" }, "paint")).toBe("Yapılamadı");
    expect(creativeOutcomeText("export", { ...none, state: "unavailable" })).toBe("Yapılamadı");
    // A step this build cannot read is printed as the token, never as one of the twelve.
    expect(creativeOutcomeText("export", { ...none, state: "retouching", summary: "yazıldı" })).toBe(
      "durum: retouching · yazıldı",
    );
    // A summary with no state rides beside the no-state sentence.
    expect(creativeOutcomeText("export", { ...none, summary: "İletildi." })).toBe(
      `${CREATIVE_OUTCOME_NO_STATE_TR.export} · İletildi.`,
    );
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for a creative run", () => {
  it("headlines the making posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([CREATIVE_ACTIVITY("paint", "draw", "executing")]);
    expect(html).toContain('data-core-kind="creative_activity"');
    expect(html).toContain('data-core-state="creative.activity"');
    expect(html).toContain('data-core-subsystem="creative"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain('data-creative-posture="making"');
    expect(html).toContain("Görsel çalışması");
    expect(html).toContain('data-label="true">Paint&#x27;te çizim uygulanıyor</p>');
    expect(html).toContain("data-creative-facts");
    expect(html).toContain('data-creative-tool="paint"');
    expect(html).toContain('data-creative-tool-label="Paint"');
    expect(html).toContain('data-creative-operation="draw"');
    expect(html).toContain('data-creative-state="executing"');
    expect(html).toContain('data-creative-similarity=""');
    expect(html).toContain('data-creative-unavailable="no"');
    expect(html).toContain("uygulama: Paint · işlem: çizim · durum: düzenleme uygulanıyor");
    expect(html).toContain("doğrulanmış sayılmaz");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-scene-facts");
    expect(html).not.toContain("data-executive-facts");
    // No control on the Core: "Dışa aktar" is the Cockpit's, from the row.
    expect(html).not.toContain("<button");
  });

  it("marks the reading, exporting and comparing postures apart", () => {
    const analysing = readout([CREATIVE_ACTIVITY("paint", "inspect", "analysing")]);
    expect(analysing).toContain('data-creative-posture="reading"');
    expect(analysing).toContain('data-label="true">Paint · inceleme · görsel inceleniyor</p>');

    const exporting = readout([CREATIVE_ACTIVITY("paint", "export", "exporting")]);
    expect(exporting).toContain('data-creative-posture="exporting"');
    expect(exporting).toContain('data-label="true">Paint · dışa aktarma · dışa aktarılıyor</p>');

    const comparing = readout([CREATIVE_ACTIVITY("paint", "draw", "comparing")]);
    expect(comparing).toContain('data-creative-posture="comparing"');
    expect(comparing).toContain('data-label="true">Paint · çizim · karşılaştırılıyor</p>');
  });

  it("marks the verified posture with its figure, and the mismatch with its defect, without dressing it as done", () => {
    const verified = readout([CREATIVE_ACTIVITY("paint", "draw", "verified", 0.92)]);
    expect(verified).toContain('data-creative-posture="verified"');
    expect(verified).toContain('data-label="true">Paint · çizim · doğrulandı (benzerlik %92)</p>');
    expect(verified).toContain('data-creative-similarity="0.92"');
    expect(verified).toContain("durum: doğrulandı · (benzerlik %92)");

    const mismatch = readout([CREATIVE_ACTIVITY("paint", "draw", "mismatch", 0.41, "wrong_size")]);
    expect(mismatch).toContain('data-creative-posture="mismatch"');
    expect(mismatch).toContain('data-creative-defect="wrong_size"');
    expect(mismatch).toContain('data-label="true">Paint · çizim · uyuşmazlık — ölçü tutmadı</p>');
    expect(mismatch).not.toContain("doğrulandı<");
  });

  it("draws an uninstalled Photoshop as a settled posture that says so, and never as an error", () => {
    const html = readout([CREATIVE_ACTIVITY("photoshop", "open", "unavailable")]);
    expect(html).toContain('data-creative-posture="unavailable"');
    expect(html).toContain('data-creative-unavailable="yes"');
    expect(html).toContain('data-label="true">Photoshop · açma · kurulu değil — yapılamadı</p>');
    expect(html).toContain("uygulama: Photoshop · işlem: açma · durum: yapılamadı");
    // Not an error headline, not an error severity, not a failure's word.
    expect(html).not.toContain('data-core-kind="error"');
    expect(html).toContain('data-severity="info"');
    expect(html).not.toContain("başarısız");
    expect(html).not.toContain("<button");
    // The failure is a different posture, and says so.
    const failed = readout([CREATIVE_ACTIVITY("paint", "draw", "failed")]);
    expect(failed).toContain('data-creative-posture="failed"');
    expect(failed).toContain('data-creative-unavailable="no"');
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([CREATIVE_ACTIVITY_BARE()]);
    expect(html).toContain('data-label="true">Görsel çalışması</p>');
    expect(html).toContain("uygulama bildirilmedi · işlem bildirilmedi · durum bildirilmedi");
    expect(html).toContain('data-creative-tool=""');
    expect(html).toContain('data-creative-state=""');
    expect(html).toContain('data-creative-posture="making"');
  });

  it("keeps the caption and the posture in the compact form and drops the long line", () => {
    const html = readout([CREATIVE_ACTIVITY("photoshop", "open", "unavailable")], true);
    expect(html).toContain("Photoshop · açma · kurulu değil — yapılamadı");
    expect(html).toContain('data-creative-posture="unavailable"');
    expect(html).not.toContain("data-creative-facts");
  });

  it("names the aged-out run as last-known rather than as working, with its facts", () => {
    const html = readout([CREATIVE_ACTIVITY("paint", "export", "exporting")], false, T0 + CREATIVE_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="creative.activity"');
    expect(html).toContain("Görsel çalışması");
    expect(html).toContain("data-creative-facts");
    expect(html).toContain("uygulama: Paint · işlem: dışa aktarma");
    // The posture follows the facts, not the life of the claim.
    expect(html).toContain('data-creative-posture="exporting"');
  });

  it("prints no creative line or posture for any other kind", () => {
    for (const events of [
      [AGENT_IDLE()],
      [DOCUMENT_ANALYSIS()],
      [MAIL_ACTIVITY()],
      [ARTIFACT_FACTORY()],
      [APP_FACTORY()],
      [CAPABILITY_GENESIS()],
      [SCENE_ACTIVITY()],
      [EXECUTIVE_RUN()],
    ]) {
      const html = readout(events);
      expect(html).not.toContain("data-creative-facts");
      expect(html).not.toContain("data-creative-posture");
    }
  });
});
