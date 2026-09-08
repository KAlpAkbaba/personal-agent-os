/**
 * The 3B Sahne panel, its two chips and the Core's readout for
 * `scene.activity` (M25 spec §6): sentences about the rows the list route
 * holds and the tokens the Cloud Core published, the last render as an
 * image shown ONLY because the row says one exists, "Render al" / "Sahneyi
 * oku" that ask the Cloud Core exactly once each, NO controls at all over a
 * tool that could not be driven, and never a scene this page rendered.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click is
 * proven the way `genesis-panel.test.tsx` proves it: the panel is hook-free,
 * so the element tree is walked to the control and its handler invoked —
 * exactly what React would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ScenesPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import {
  SCENE_ACTION_LABEL,
  SCENE_REASON_BUSY,
  SCENE_REASON_UNAVAILABLE,
  SCENE_REASON_UNKNOWN_STATE,
  SCENE_ROWS_SHOWN,
  sceneActionGate,
  sceneObjectNamesLine,
  sceneRenderAlt,
  sceneRowActions,
  sceneRowLine,
  rowHasRender,
  rowIsUnavailable,
  rowIsVerified,
} from "../../app/lib/cockpit/scene-rows";
import {
  SCENE_CONTROL_IDLE,
  SCENE_PREVIEW_NONE,
  SCENE_ROUTE_ABSENT,
  type SceneActionReceipt,
  type SceneClient,
  type SceneControlProps,
  type SceneControlState,
  type ScenePreviewProps,
  type SceneRow,
  SceneActionError,
  parseSceneReceipt,
  parseSceneRow,
  sceneActionPath,
  sceneRenderPath,
} from "../../app/lib/cockpit/scenes";
import { SCENE_OUTCOME_NO_STATE_TR, runSceneAction, sceneOutcomeText } from "../../app/lib/cockpit/useSceneControl";
import { SCENE_RUN_STATES, SCENE_TTL_MS } from "../../app/lib/uistate/contract";
import { SCENE_NO_OBJECT_NAMES } from "../../app/lib/uistate/labels";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  ARTIFACT_FACTORY,
  CAPABILITY_GENESIS,
  DOCUMENT_ANALYSIS,
  MAIL_ACTIVITY,
  SCENE_ACTIVITY,
  SCENE_ACTIVITY_BARE,
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

function row(overrides: Partial<SceneRow> = {}): SceneRow {
  return {
    scene_id: "s1",
    tool: "blender",
    scene: "Kure",
    project: "kure-lab",
    state: "applying",
    objects: null,
    object_names: [],
    mismatch: null,
    has_render: false,
    render_sha256: null,
    render_bytes: null,
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
    objects: 3,
    object_names: ["Kure", "Kamera", "Gunes"],
    has_render: true,
    render_sha256: "a1b2c3",
    render_bytes: 20_480,
  });
const MISMATCH = () =>
  row({ scene_id: "s2", scene: "Kup", state: "mismatch", objects: 2, object_names: ["Kup", "Kamera"], mismatch: "Kup.location.z" });
const UNAVAILABLE = () =>
  row({
    scene_id: "s3",
    tool: "unity",
    scene: "Arac",
    state: "unavailable",
    error_class: "dependency_unavailable",
    error_message: "No valid Unity Editor license found. Please activate your license.",
  });
const FAILED = () =>
  row({ scene_id: "s4", state: "failed", error_class: "render_failed", error_message: "Blender 1 ile çıktı." });

const ok = (rows: SceneRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });
const noop = () => {};

function controlOf(overrides: Partial<SceneControlProps> = {}): SceneControlProps {
  return { ...SCENE_CONTROL_IDLE, onRender: noop, onInspect: noop, ...overrides };
}

/** A preview holding one blob URL per scene, as the hook hands the panel. */
function previewOf(urls: Record<string, string> = {}, notice: string | null = null): ScenePreviewProps {
  return { srcFor: (sceneId) => urls[sceneId] ?? null, notice };
}

function panel(
  scenes: Parameters<typeof ScenesPanel>[0]["scenes"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  control: SceneControlProps = controlOf(),
  preview: ScenePreviewProps = SCENE_PREVIEW_NONE,
  now = T0,
) {
  return renderToStaticMarkup(
    <ScenesPanel scenes={scenes} truth={truthOf(events)} now={now} control={control} preview={preview} />,
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

const RECEIPT_VERIFIED: SceneActionReceipt = {
  state: "verified",
  objects: 3,
  mismatch: null,
  errorClass: null,
  summary: "Sahne okundu.",
  receiptId: "r1",
};
const RECEIPT_MISMATCH: SceneActionReceipt = {
  state: "mismatch",
  objects: 2,
  mismatch: "Kup.location.z",
  errorClass: "postcondition_failed",
  summary: null,
  receiptId: "r2",
};

function fakeClient(overrides: Partial<SceneClient> = {}): SceneClient {
  return {
    render: vi.fn(async () => RECEIPT_VERIFIED),
    inspect: vi.fn(async () => RECEIPT_MISMATCH),
    ...overrides,
  };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: SceneClient, onSettled = vi.fn()) {
  let state: SceneControlState = SCENE_CONTROL_IDLE;
  const writes: SceneControlState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: SceneControlState) => {
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

describe("the 3B Sahne panel", () => {
  it("is empty, in words, when the list route answered with no scene and the bus said nothing", () => {
    const html = panel(ok([]));
    expect(html).toContain('data-panel="scenes"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Henüz bir sahne yapılmadı.");
    expect(html).toContain('data-scene-activity="untold"');
    expect(html).toContain("3B sahne etkinliği bildirilmedi.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain('data-scenes-verified="0"');
    expect(html).toContain(">3B Sahne<");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("data-scene-outcome");
    expect(html).not.toContain("attention");
    // What the chips do — and what "doğrulandı" means — is said in words on every render.
    expect(html).toContain("Sahne, plana uyduğu araçtan geri okunduğunda doğrulanmış olur; önce değil.");
    expect(html).toContain("Sürülemeyen bir araç için düğme gösterilmez.");
    expect(html).toContain("Bu ekran editör açmaz, fare kullanmaz, kod üretmez, sahibin kendi projelerine dokunmaz.");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Henüz bir sahne yapılmadı");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Henüz bir sahne yapılmadı");

    const absent = panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/scenes yok (HTTP 404)." });
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/scenes yok (HTTP 404).");
    expect(absent).not.toContain("Henüz bir sahne yapılmadı");
    for (const html of [loading, failed, absent]) {
      expect(html).not.toContain("<button");
      expect(html).not.toContain("<img");
    }
  });

  it("lists a verified Blender scene with its object names, its render image and both chips", () => {
    const html = panel(ok([VERIFIED()]), [AGENT_IDLE()], controlOf(), previewOf({ s1: "blob:scene-1" }));
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1 doğrulandı / 1<');
    expect(html).toContain('data-scenes-verified="1"');
    expect(html).toContain('data-scene="s1"');
    expect(html).toContain('data-scene-row-tool="blender"');
    expect(html).toContain('data-scene-row-name="Kure"');
    expect(html).toContain('data-scene-row-state="verified"');
    expect(html).toContain('data-scene-row-objects="3"');
    expect(html).toContain('data-scene-row-verified="yes"');
    expect(html).toContain('data-scene-row-unavailable="no"');
    expect(html).toContain('data-scene-row-has-render="yes"');
    expect(html).toContain("30 sn önce");
    // The tool and the step, with the count the INSPECTION read.
    expect(html).toContain('data-scene-line="true">Blender · doğrulandı (3 nesne)</span>');
    // What the inspection called them, as the route listed them.
    expect(html).toContain('data-scene-objects="3">Kure, Kamera, Gunes</span>');
    // The render: an image, alt text from the scene's own name, and the src
    // the session-gated fetch produced — never the bare API URL.
    expect(html).toContain('<img class="scene-render" src="blob:scene-1"');
    expect(html).toContain('alt="Kure sahnesinin son render&#x27;ı"');
    expect(html).toContain('data-scene-render="s1"');
    expect(html).toContain('data-scene-render-sha="a1b2c3"');
    expect(html).not.toContain(sceneRenderPath("s1"));
    expect(html).not.toContain("data-scene-render-pending");
    // Both chips, enabled, nothing in flight.
    expect(html).toContain('data-scene-controls="s1"');
    expect(html).toContain('data-scene-in-flight="no"');
    expect(html).toContain('data-scene-action="render" data-scene-target="s1" data-scene-enabled="yes">Render al</button>');
    expect(html).toContain('data-scene-action="inspect" data-scene-target="s1" data-scene-enabled="yes">Sahneyi oku</button>');
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    expect(html).not.toContain("data-scene-reason");
    expect(html).not.toContain("attention");
  });

  it("draws no image at all for a row that says there is no render, and says so for one it has not fetched", () => {
    // No render on the row: no image, whatever the preview happens to hold.
    const none = panel(ok([row({ state: "verified", objects: 1 })]), [AGENT_IDLE()], controlOf(), previewOf({ s1: "blob:leftover" }));
    expect(none).toContain('data-scene-row-has-render="no"');
    expect(none).not.toContain("<img");
    expect(none).not.toContain("blob:leftover");
    expect(none).not.toContain("data-scene-render-pending");

    // A render the row promises but the page has not fetched: said, not drawn.
    const pending = panel(ok([VERIFIED()]));
    expect(pending).toContain('data-scene-row-has-render="yes"');
    expect(pending).not.toContain("<img");
    expect(pending).toContain("Render var; görüntü henüz alınmadı.");

    // A render that could not be fetched is named, and the row's line stays.
    const failed = panel(ok([VERIFIED()]), [AGENT_IDLE()], controlOf(), previewOf({}, "Render alınamadı (s1): HTTP 503"));
    expect(failed).toContain("data-scene-render-notice");
    expect(failed).toContain("Render alınamadı (s1): HTTP 503");
    expect(failed).toContain('data-scene-line="true">Blender · doğrulandı (3 nesne)</span>');
  });

  it("lists an unavailable Unity row with the licence in the tool's own words and NO controls", () => {
    const html = panel(ok([UNAVAILABLE()]), [AGENT_IDLE()], controlOf(), previewOf({ s3: "blob:never" }));
    expect(html).toContain('data-scene="s3"');
    expect(html).toContain('data-scene-row-tool="unity"');
    expect(html).toContain('data-scene-row-state="unavailable"');
    expect(html).toContain('data-scene-row-unavailable="yes"');
    expect(html).toContain('data-scene-line="true">Unity · lisans yok — yapılamadı</span>');
    // The licensing client's own sentence, as the row carried it.
    expect(html).toContain("data-scene-error-message");
    expect(html).toContain("No valid Unity Editor license found.");
    // No control over an editor this machine cannot drive — not disabled: absent.
    expect(html).not.toContain("<button");
    expect(html).not.toContain("data-scene-controls");
    expect(html).not.toContain("data-scene-action");
    // No image either: the row promised none.
    expect(html).not.toContain("<img");
    // And an unavailable tool is not a failure, and not "attention".
    expect(html).not.toContain("başarısız");
    expect(html).not.toContain("attention");
    expect(html).not.toContain("doğrulandı<");
  });

  it("lists a mismatch naming the object the comparison named, and draws attention to it", () => {
    const html = panel(ok([MISMATCH()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-scene="s2"');
    expect(html).toContain('data-scene-row-state="mismatch"');
    expect(html).toContain('data-scene-row-mismatch="Kup.location.z"');
    expect(html).toContain('data-scene-line="true">Blender · uyuşmazlık — Kup.location.z</span>');
    expect(html).toContain('data-scene-objects="2">Kup, Kamera</span>');
    // A mismatch is never dressed as done.
    expect(html).not.toContain("doğrulandı");
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).toContain('data-scenes-verified="0"');
    // A mismatch is still a scene in a tool that answers: both chips stand.
    expect((html.match(/<button/g) ?? []).length).toBe(2);
    // A mismatch with no object named says the plain word, and invents none.
    const bare = panel(ok([row({ state: "mismatch", objects: 2 })]));
    expect(bare).toContain('data-scene-line="true">Blender · uyuşmazlık</span>');
    expect(bare).toContain('data-scene-row-mismatch=""');
  });

  it("lists a failed scene naming its error, and draws attention to it", () => {
    const html = panel(ok([FAILED()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-scene-row-failed="yes"');
    expect(html).toContain('data-scene-line="true">Blender · başarısız</span>');
    expect(html).toContain('data-scene-error-message="true">Blender 1 ile çıktı.</span>');
    // A message is printed only beside the two steps that have one to give.
    const working = panel(ok([row({ state: "applying", error_message: "eski mesaj" })]));
    expect(working).not.toContain("data-scene-error-message");
  });

  it("says what a row did not report rather than filling it in, and gives a word it cannot read no chips", () => {
    const html = panel(ok([row({ tool: null, scene: null, state: null, updated_at: null, created_at: null })]));
    expect(html).toContain(">sahne adı bildirilmedi<");
    expect(html).toContain('data-scene-row-tool=""');
    expect(html).toContain('data-scene-row-name=""');
    expect(html).toContain('data-scene-row-state=""');
    expect(html).toContain('data-scene-row-objects=""');
    expect(html).toContain('data-scene-line="true">durum bildirilmedi</span>');
    // Never read back: no object line at all — "read and named none" is a
    // different answer, and only that one gets a line.
    expect(html).not.toContain("data-scene-objects=");
    // No state is not a scene known to be driveable: nothing invites a click.
    expect(html).not.toContain("<button");
    // A newer server's word is printed verbatim — still a published fact — and earns no chip either.
    const unknown = panel(ok([row({ state: "exported" })]));
    expect(unknown).toContain('data-scene-line="true">Blender · exported</span>');
    expect(unknown).not.toContain("<button");
    // An inspection that listed no names says so.
    const named = panel(ok([row({ state: "verified", objects: 0, object_names: [] })]));
    expect(named).toContain(`data-scene-objects="0">${SCENE_NO_OBJECT_NAMES}</span>`);
    expect(named).toContain('data-scene-line="true">Blender · doğrulandı (0 nesne)</span>');
  });

  it("shows the last scenes as the route orders them, bounded, and counts the verified ones in the badge", () => {
    const rows = Array.from({ length: SCENE_ROWS_SHOWN + 3 }, (_, i) =>
      row({ scene_id: `s${i}`, scene: `Sahne${i}`, state: i < 2 ? "verified" : "applying" }),
    );
    const html = panel(ok(rows));
    expect(html).toContain(`data-panel-badge="true">2 doğrulandı / ${SCENE_ROWS_SHOWN + 3}<`);
    expect(html).toContain('data-scenes-verified="2"');
    expect((html.match(/data-scene="s\d+"/g) ?? []).length).toBe(SCENE_ROWS_SHOWN);
    expect(html).toContain('data-scene="s0"');
    expect(html).not.toContain(`data-scene="s${SCENE_ROWS_SHOWN}"`);
  });

  it("each chip asks for that scene exactly once, and only through its own handler", () => {
    const onRender = vi.fn();
    const onInspect = vi.fn();
    const rows = [VERIFIED(), MISMATCH(), UNAVAILABLE()];
    const tree = (
      <ScenesPanel
        scenes={ok(rows)}
        truth={truthOf([AGENT_IDLE()])}
        now={T0}
        control={controlOf({ onRender, onInspect })}
        preview={SCENE_PREVIEW_NONE}
      />
    );

    click(findByData(tree, { "data-scene-action": "render", "data-scene-target": "s1" }));
    expect(onRender).toHaveBeenCalledTimes(1);
    expect(onRender).toHaveBeenCalledWith("s1");
    expect(onInspect).not.toHaveBeenCalled();

    click(findByData(tree, { "data-scene-action": "inspect", "data-scene-target": "s2" }));
    expect(onInspect).toHaveBeenCalledTimes(1);
    expect(onInspect).toHaveBeenCalledWith("s2");
    expect(onRender).toHaveBeenCalledTimes(1);

    // An unavailable tool has no chip to find at all.
    expect(findByData(tree, { "data-scene-action": "render", "data-scene-target": "s3" })).toBeNull();
    expect(findByData(tree, { "data-scene-action": "inspect", "data-scene-target": "s3" })).toBeNull();
  });

  it("disables every chip while one call is in flight, marking the scene and the action it is, with the reason said once", () => {
    const html = panel(ok([VERIFIED(), MISMATCH()]), [AGENT_IDLE()], controlOf({ busy: { action: "render", id: "s1" } }));
    expect(html).toContain('data-scene-controls="s1" data-scene-in-flight="yes" data-scene-in-flight-action="render"');
    expect(html).toContain('data-scene-controls="s2" data-scene-in-flight="no" data-scene-in-flight-action=""');
    expect(html).toContain('data-scene-action="render" data-scene-target="s1" data-scene-enabled="no" disabled=""');
    expect(html).toContain('data-scene-action="inspect" data-scene-target="s1" data-scene-enabled="no" disabled=""');
    expect(html).toContain('data-scene-action="render" data-scene-target="s2" data-scene-enabled="no" disabled=""');
    expect(html).toContain('data-scene-reason="busy" data-scene-reason-for="render,inspect"');
    expect(html).toContain(SCENE_REASON_BUSY);
    expect(html).not.toContain(`Render al, Sahneyi oku: ${SCENE_REASON_BUSY}`);
    // Said once per scene, not once per chip.
    expect((html.match(/data-scene-reason="busy"/g) ?? []).length).toBe(2);
  });

  it("prints the last call's answer, dated, with the scene and the action it was about", () => {
    const outcome = { action: "inspect" as const, id: "s1", ok: true, text: "Doğrulandı (3 nesne) · Sahne okundu.", at: T0 - 5_000 };
    const html = panel(ok([VERIFIED()]), [AGENT_IDLE()], controlOf({ outcome }));
    expect(html).toContain('data-scene-outcome="inspect"');
    expect(html).toContain('data-scene-ok="yes"');
    expect(html).toContain('data-scene-target="s1"');
    expect(html).toContain("Doğrulandı (3 nesne) · Sahne okundu. · 5 sn önce");

    const refused = panel(
      ok([VERIFIED()]),
      [AGENT_IDLE()],
      controlOf({ outcome: { ...outcome, ok: false, text: "Araç sürülemedi; yapılamadı." } }),
    );
    expect(refused).toContain('data-scene-ok="no"');
    expect(refused).toContain("panel-unknown");
    expect(refused).toContain("Araç sürülemedi; yapılamadı.");
  });

  it("states the bus activity in the spec's words with its age and posture, and last-known once it aged out", () => {
    const live = panel(ok([]), [SCENE_ACTIVITY("blender", "Kure", "rendering")], controlOf(), SCENE_PREVIEW_NONE, T0 + 3_000);
    expect(live).toContain('data-scene-stage="active"');
    expect(live).toContain('data-scene-activity="active"');
    expect(live).toContain('data-scene-posture="rendering"');
    expect(live).toContain('data-scene-caption="Blender · Kure · render alınıyor"');
    expect(live).toContain("Blender · Kure · render alınıyor · 3 sn önce");
    expect(live).not.toContain("Son bilinen");
    // The list is the list: the bus rendering something does not put a row on
    // it, nor a chip, nor an image.
    expect(live).toContain("Henüz bir sahne yapılmadı.");
    expect(live).not.toContain("<button");
    expect(live).not.toContain("<img");

    const creating = panel(ok([]), [SCENE_ACTIVITY("blender", null, "creating")], controlOf(), SCENE_PREVIEW_NONE, T0 + 3_000);
    expect(creating).toContain('data-scene-posture="making"');
    // The apostrophe of the locative is HTML-escaped in the markup; the sentence is what is asserted.
    expect(creating).toContain("Blender&#x27;da sahne kuruluyor · 3 sn önce");

    const unavailable = panel(ok([]), [SCENE_ACTIVITY("unity", null, "unavailable")], controlOf(), SCENE_PREVIEW_NONE, T0 + 3_000);
    expect(unavailable).toContain('data-scene-posture="unavailable"');
    expect(unavailable).toContain("Unity · lisans yok — yapılamadı · 3 sn önce");

    const stale = panel(ok([]), [SCENE_ACTIVITY("blender", "Kure", "verified", 3)], controlOf(), SCENE_PREVIEW_NONE, T0 + SCENE_TTL_MS + 1_000);
    expect(stale).toContain('data-scene-stage="none"');
    expect(stale).toContain('data-scene-last-known="active"');
    expect(stale).toContain('data-scene-posture="verified"');
    expect(stale).toContain("Son bilinen: Blender · Kure · doğrulandı (3 nesne) · 2 dk önce");

    // A document, mail, artifact, app or genesis event is not a scene event.
    for (const other of [[DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()]]) {
      const html = panel(ok([]), other);
      expect(html).toContain('data-scene-activity="untold"');
      expect(html).toContain('data-scene-posture=""');
    }
  });
});

// -------------------------------------------------------------- the rows

describe("the rows", () => {
  it("a scene is verified, unavailable or has a render because its row says so", () => {
    expect(rowIsVerified({ state: "verified" })).toBe(true);
    expect(rowIsVerified({ state: "VERIFIED" })).toBe(false);
    expect(rowIsVerified({ state: "rendering" })).toBe(false);
    expect(rowIsUnavailable({ state: "unavailable" })).toBe(true);
    expect(rowIsUnavailable({ state: null })).toBe(false);
    // The image hangs on the flag alone, never on a step or a sha.
    expect(rowHasRender({ has_render: true })).toBe(true);
    expect(rowHasRender({ has_render: false })).toBe(false);
  });

  it("draws both chips for a driveable scene and NONE for a tool that could not be driven or a step it cannot read", () => {
    for (const state of ["creating", "applying", "rendering", "inspecting", "verified", "mismatch", "failed"]) {
      expect(sceneRowActions({ state }), state).toEqual(["render", "inspect"]);
    }
    expect(sceneRowActions({ state: "unavailable" })).toEqual([]);
    expect(sceneRowActions({ state: null })).toEqual([]);
    expect(sceneRowActions({ state: "exported" })).toEqual([]);
    expect(SCENE_ACTION_LABEL).toEqual({ render: "Render al", inspect: "Sahneyi oku" });
  });

  it("the gate opens each chip only for a row the Cloud Core would not refuse, while nothing is in flight", () => {
    const busy = { action: "render" as const, id: "s9" };
    for (const state of [...SCENE_RUN_STATES, null, "exported"]) {
      for (const action of ["render", "inspect"] as const) {
        expect(sceneActionGate({ state }, action, busy), `${state}/${action}`).toEqual({
          enabled: false,
          reason: SCENE_REASON_BUSY,
          reasonKind: "busy",
        });
      }
    }
    const open = { enabled: true, reason: null, reasonKind: null };
    for (const state of SCENE_RUN_STATES.filter((s) => s !== "unavailable")) {
      for (const action of ["render", "inspect"] as const) {
        expect(sceneActionGate({ state }, action, null), `${state}/${action}`).toEqual(open);
      }
    }
    for (const action of ["render", "inspect"] as const) {
      expect(sceneActionGate({ state: "unavailable" }, action, null)).toEqual({
        enabled: false,
        reason: SCENE_REASON_UNAVAILABLE,
        reasonKind: "unavailable",
      });
      for (const state of [null, "exported"]) {
        expect(sceneActionGate({ state }, action, null), `${state}/${action}`).toEqual({
          enabled: false,
          reason: SCENE_REASON_UNKNOWN_STATE,
          reasonKind: "unknown_state",
        });
      }
    }
  });

  it("lines each row with its tool and its step, and the count and the object only where they belong", () => {
    expect(sceneRowLine(row({ state: "rendering" }))).toBe("Blender · render alınıyor");
    expect(sceneRowLine(VERIFIED())).toBe("Blender · doğrulandı (3 nesne)");
    expect(sceneRowLine(MISMATCH())).toBe("Blender · uyuşmazlık — Kup.location.z");
    expect(sceneRowLine(UNAVAILABLE())).toBe("Unity · lisans yok — yapılamadı");
    expect(sceneRowLine(FAILED())).toBe("Blender · başarısız");
    // An unavailable Blender gets no licence invented for it.
    expect(sceneRowLine(row({ state: "unavailable" }))).toBe("Blender · yapılamadı");
    expect(sceneRowLine(row({ tool: null, state: "unavailable" }))).toBe("yapılamadı");
    // A mismatching object beside a step that is not a mismatch is not one.
    expect(sceneRowLine(row({ state: "verified", objects: 1, mismatch: "Kup" }))).toBe("Blender · doğrulandı (1 nesne)");
    expect(sceneRowLine(row({ state: null }))).toBe("Blender · durum bildirilmedi");
    expect(sceneRowLine(row({ tool: "godot", state: "exported" }))).toBe("godot · exported");
  });

  it("names the render for a reader from the scene's own name, and says so when there is none", () => {
    expect(sceneRenderAlt({ scene: "Kure" })).toBe("Kure sahnesinin son render'ı");
    expect(sceneRenderAlt({ scene: null })).toBe("Adı bildirilmeyen sahnenin son render'ı");
    expect(sceneRenderAlt({ scene: null })).not.toBe("");
  });

  it("lists the inspection's object names, or says it listed none", () => {
    expect(sceneObjectNamesLine({ object_names: ["Kure", "Kamera"] })).toBe("Kure, Kamera");
    expect(sceneObjectNamesLine({ object_names: [] })).toBe(SCENE_NO_OBJECT_NAMES);
  });
});

// ------------------------------------------------------------ the client

describe("the client's shapes", () => {
  it("addresses one scene by path segment, never by query", () => {
    expect(sceneActionPath("s1", "render")).toBe("/v1/scenes/s1/render");
    expect(sceneActionPath("s1", "inspect")).toBe("/v1/scenes/s1/inspect");
    expect(sceneRenderPath("s1")).toBe("/v1/scenes/s1/render");
    expect(sceneActionPath("a/b", "render")).toBe("/v1/scenes/a%2Fb/render");
  });

  it("reads a row's fields verbatim, and refuses a row that is not a scene", () => {
    const parsed = parseSceneRow({
      scene_id: "s1",
      tool: "blender",
      scene: "Kure",
      state: "verified",
      objects: 3,
      object_names: ["Kure", "Kamera"],
      render_sha256: "a1b2c3",
    });
    expect(parsed?.scene_id).toBe("s1");
    expect(parsed?.objects).toBe(3);
    expect(parsed?.object_names).toEqual(["Kure", "Kamera"]);
    // No flag, but a stored render's identity: the row does say one exists.
    expect(parsed?.has_render).toBe(true);
    // An explicit flag beats the identity, in both directions.
    expect(parseSceneRow({ id: "s2", has_render: false, render_sha256: "x" })?.has_render).toBe(false);
    expect(parseSceneRow({ id: "s3", has_render: true })?.has_render).toBe(true);
    // Nothing at all said about a render is no render.
    expect(parseSceneRow({ id: "s4", state: "verified" })?.has_render).toBe(false);
    // A row with no id is not a scene.
    expect(parseSceneRow({ tool: "blender" })).toBeNull();
    expect(parseSceneRow(null)).toBeNull();
    // A count that is not a whole non-negative number is no count.
    expect(parseSceneRow({ id: "s5", objects: "3" })?.objects).toBeNull();
    expect(parseSceneRow({ id: "s6", object_names: "Kure" })?.object_names).toEqual([]);
  });

  it("reads a receipt from any of the shapes, and never invents a step", () => {
    expect(parseSceneReceipt({ state: "verified", objects: 3 })).toMatchObject({ state: "verified", objects: 3 });
    expect(parseSceneReceipt({ receipt: { state: "mismatch" }, scene: { mismatch: "Kup.z" } })).toMatchObject({
      state: "mismatch",
      mismatch: "Kup.z",
    });
    expect(parseSceneReceipt({ receipt: {}, inspection: { objects: 5 } }).objects).toBe(5);
    // A 2xx with nothing in it names nothing.
    const bare = parseSceneReceipt({});
    expect(bare.state).toBeNull();
    expect(bare.objects).toBeNull();
    expect(bare.mismatch).toBeNull();
    expect(parseSceneReceipt(null).state).toBeNull();
  });
});

// ------------------------------------------------------------ the runner

describe("the action runner", () => {
  it("makes exactly one call with the scene's id, writes busy then the receipt's outcome, and reloads the list", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runSceneAction(ports, "render", "s1")).toBe(true);
    expect(client.render).toHaveBeenCalledTimes(1);
    expect(client.render).toHaveBeenCalledWith("s1");
    expect(client.inspect).not.toHaveBeenCalled();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: { action: "render", id: "s1" }, outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      action: "render",
      id: "s1",
      ok: true,
      text: "Doğrulandı (3 nesne) · Sahne okundu.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);

    const second = portsOf(fakeClient());
    await runSceneAction(second.ports, "inspect", "s2");
    expect(second.ports.client.inspect).toHaveBeenCalledTimes(1);
    expect(second.ports.client.inspect).toHaveBeenCalledWith("s2");
    expect(second.ports.client.render).not.toHaveBeenCalled();
    expect(second.current().outcome?.text).toBe("Uyuşmazlık — Kup.location.z");
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    const deferred: { release: ((receipt: SceneActionReceipt) => void) | null } = { release: null };
    const render = vi.fn(async () => RECEIPT_VERIFIED);
    render.mockImplementationOnce(
      () =>
        new Promise<SceneActionReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ render });
    const { ports, current } = portsOf(client);

    const first = runSceneAction(ports, "render", "s1");
    expect(current().busy).toEqual({ action: "render", id: "s1" });
    expect(await runSceneAction(ports, "render", "s1")).toBe(false);
    expect(await runSceneAction(ports, "inspect", "s1")).toBe(false);
    expect(await runSceneAction(ports, "render", "s2")).toBe(false);
    expect(render).toHaveBeenCalledTimes(1);
    expect(client.inspect).not.toHaveBeenCalled();

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_VERIFIED);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second render is the Cloud Core's to refuse, not this page's to hide.
    expect(await runSceneAction(ports, "render", "s1")).toBe(true);
    expect(render).toHaveBeenCalledTimes(2);
  });

  it("turns the Cloud Core's refusal — and a route not there yet — into the owner's words, says nothing happened, and still reloads", async () => {
    const client = fakeClient({
      render: vi.fn(async () => {
        throw new SceneActionError(409, "dependency_unavailable", "unity editor license missing");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runSceneAction(ports, "render", "s3")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toBe("Araç sürülemedi; yapılamadı.");
    expect(outcome?.text).not.toContain("doğrulandı");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    const absent = fakeClient({
      inspect: vi.fn(async () => {
        throw new SceneActionError(404, SCENE_ROUTE_ABSENT, "Bu Cloud Core sürümünde /v1/scenes/s1/inspect yok (HTTP 404).");
      }),
    });
    const second = portsOf(absent);
    await runSceneAction(second.ports, "inspect", "s1");
    expect(second.current().outcome?.ok).toBe(false);
    expect(second.current().outcome?.text).toBe("Bu Cloud Core sürümünde /v1/scenes/s1/inspect yok (HTTP 404). Yapılmadı.");
  });

  it("never says 'doğrulandı' or 'render alındı' on the strength of a 2xx alone", () => {
    const none: SceneActionReceipt = { state: null, objects: null, mismatch: null, errorClass: null, summary: null, receiptId: "r1" };
    expect(sceneOutcomeText("render", none)).toBe(SCENE_OUTCOME_NO_STATE_TR.render);
    expect(sceneOutcomeText("render", none)).not.toMatch(/doğrulandı|alındı/);
    expect(sceneOutcomeText("inspect", none)).toBe(SCENE_OUTCOME_NO_STATE_TR.inspect);
    expect(sceneOutcomeText("render", { ...none, state: "rendering" })).toBe("Render alınıyor");
    expect(sceneOutcomeText("inspect", { ...none, state: "verified", objects: 3 })).toBe("Doğrulandı (3 nesne)");
    expect(sceneOutcomeText("inspect", { ...none, state: "verified" })).toBe("Doğrulandı");
    expect(sceneOutcomeText("inspect", { ...none, state: "mismatch", mismatch: "Kup.z" })).toBe("Uyuşmazlık — Kup.z");
    // The licence wording needs the tool the caller knows the scene is in.
    expect(sceneOutcomeText("render", { ...none, state: "unavailable" }, "unity")).toBe("Lisans yok — yapılamadı");
    expect(sceneOutcomeText("render", { ...none, state: "unavailable" }, "blender")).toBe("Yapılamadı");
    expect(sceneOutcomeText("render", { ...none, state: "unavailable" })).toBe("Yapılamadı");
    // A step this build cannot read is printed as the token, never as one of the eight.
    expect(sceneOutcomeText("render", { ...none, state: "exported", summary: "yazıldı" })).toBe("durum: exported · yazıldı");
    // A summary with no state rides beside the no-state sentence.
    expect(sceneOutcomeText("render", { ...none, summary: "İletildi." })).toBe(`${SCENE_OUTCOME_NO_STATE_TR.render} · İletildi.`);
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for a scene run", () => {
  it("headlines the making posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([SCENE_ACTIVITY("blender", null, "creating")]);
    expect(html).toContain('data-core-kind="scene_activity"');
    expect(html).toContain('data-core-state="scene.activity"');
    expect(html).toContain('data-core-subsystem="creative3d"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain('data-scene-posture="making"');
    expect(html).toContain("3B sahne");
    expect(html).toContain('data-label="true">Blender&#x27;da sahne kuruluyor</p>');
    expect(html).toContain("data-scene-facts");
    expect(html).toContain('data-scene-tool="blender"');
    expect(html).toContain('data-scene-tool-label="Blender"');
    expect(html).toContain('data-scene-name=""');
    expect(html).toContain('data-scene-state="creating"');
    expect(html).toContain('data-scene-objects=""');
    expect(html).toContain('data-scene-unavailable="no"');
    expect(html).toContain("araç: Blender · sahne bildirilmedi · durum: sahne kuruluyor");
    expect(html).toContain("doğrulanmış sayılmaz");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-app-facts");
    expect(html).not.toContain("data-genesis-facts");
    expect(html).not.toContain("data-artifact-facts");
    // No control on the Core: "Render al" is the Cockpit's, from the row.
    expect(html).not.toContain("<button");
  });

  it("marks the rendering and the reading postures apart", () => {
    const rendering = readout([SCENE_ACTIVITY("blender", "Kure", "rendering")]);
    expect(rendering).toContain('data-scene-posture="rendering"');
    expect(rendering).toContain('data-label="true">Blender · Kure · render alınıyor</p>');

    const reading = readout([SCENE_ACTIVITY("blender", "Kure", "inspecting")]);
    expect(reading).toContain('data-scene-posture="reading"');
    expect(reading).toContain('data-label="true">Blender · Kure · sahne okunuyor</p>');
  });

  it("marks the verified posture with its count, and the mismatch without dressing it as done", () => {
    const verified = readout([SCENE_ACTIVITY("blender", "Kure", "verified", 3)]);
    expect(verified).toContain('data-scene-posture="verified"');
    expect(verified).toContain('data-label="true">Blender · Kure · doğrulandı (3 nesne)</p>');
    expect(verified).toContain('data-scene-objects="3"');
    expect(verified).toContain("durum: doğrulandı · nesneler: 3");

    const mismatch = readout([SCENE_ACTIVITY("blender", "Kure", "mismatch", 2)]);
    expect(mismatch).toContain('data-scene-posture="mismatch"');
    expect(mismatch).toContain('data-label="true">Blender · Kure · uyuşmazlık</p>');
    expect(mismatch).not.toContain("doğrulandı<");
  });

  it("draws an undriveable Unity as a settled posture with its licence, and never as an error", () => {
    const html = readout([SCENE_ACTIVITY("unity", null, "unavailable")]);
    expect(html).toContain('data-scene-posture="unavailable"');
    expect(html).toContain('data-scene-unavailable="yes"');
    expect(html).toContain('data-label="true">Unity · lisans yok — yapılamadı</p>');
    expect(html).toContain("araç: Unity · sahne bildirilmedi · durum: yapılamadı");
    // Not an error headline, not an error severity, not a failure's word.
    expect(html).not.toContain('data-core-kind="error"');
    expect(html).toContain('data-severity="info"');
    expect(html).not.toContain("başarısız");
    expect(html).not.toContain("<button");
    // The failure is a different posture, and says so.
    const failed = readout([SCENE_ACTIVITY("blender", "Kure", "failed")]);
    expect(failed).toContain('data-scene-posture="failed"');
    expect(failed).toContain('data-scene-unavailable="no"');
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([SCENE_ACTIVITY_BARE()]);
    expect(html).toContain('data-label="true">3B sahne</p>');
    expect(html).toContain("araç bildirilmedi · sahne bildirilmedi · durum bildirilmedi");
    expect(html).toContain('data-scene-tool=""');
    expect(html).toContain('data-scene-state=""');
    expect(html).toContain('data-scene-posture="making"');
  });

  it("keeps the caption and the posture in the compact form and drops the long line", () => {
    const html = readout([SCENE_ACTIVITY("unity", null, "unavailable")], true);
    expect(html).toContain("Unity · lisans yok — yapılamadı");
    expect(html).toContain('data-scene-posture="unavailable"');
    expect(html).not.toContain("data-scene-facts");
  });

  it("names the aged-out run as last-known rather than as working, with its facts", () => {
    const html = readout([SCENE_ACTIVITY("blender", "Kure", "rendering")], false, T0 + SCENE_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="scene.activity"');
    expect(html).toContain("3B sahne");
    expect(html).toContain("data-scene-facts");
    expect(html).toContain("araç: Blender · sahne: Kure");
    // The posture follows the facts, not the life of the claim: what it WAS is still a fact.
    expect(html).toContain('data-scene-posture="rendering"');
  });

  it("prints no scene line or posture for any other kind", () => {
    for (const events of [[AGENT_IDLE()], [DOCUMENT_ANALYSIS()], [MAIL_ACTIVITY()], [ARTIFACT_FACTORY()], [APP_FACTORY()], [CAPABILITY_GENESIS()]]) {
      const html = readout(events);
      expect(html).not.toContain("data-scene-facts");
      expect(html).not.toContain("data-scene-posture");
    }
  });
});
