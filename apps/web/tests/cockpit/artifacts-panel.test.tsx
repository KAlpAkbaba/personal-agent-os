/**
 * The Üretilenler panel, its "Aç" chip and the Core's readout for
 * `artifact.factory` (M22 spec §4, §6): sentences about the rows the list
 * route holds and the tokens the Cloud Core published, a link per render
 * the independent parser passed, a chip that asks the Cloud Core to open
 * one exactly once, and never a render this page validated itself.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click
 * is proven the way `mail-panel.test.tsx` proves it: the panel is hook-free,
 * so the element tree is walked to the control and its handler invoked —
 * exactly what React would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ARTIFACT_ROWS_SHOWN, ArtifactsPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import { ARTIFACT_OPEN_REASON_BUSY, ARTIFACT_OPEN_REASON_NO_VALID_RENDER } from "../../app/lib/cockpit/artifact-rows";
import {
  ARTIFACT_OPEN_IDLE,
  type ArtifactClient,
  type ArtifactOpenProps,
  type ArtifactOpenReceipt,
  type ArtifactOpenState,
  type ArtifactRender,
  type ArtifactRow,
  ArtifactOpenError,
  OPEN_ROUTE_ABSENT,
} from "../../app/lib/cockpit/artifacts";
import { OPEN_OUTCOME_NO_STATE_TR, openOutcomeText, runArtifactOpen } from "../../app/lib/cockpit/useArtifactOpen";
import { ARTIFACT_FACTORY_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  ARTIFACT_FACTORY,
  ARTIFACT_FACTORY_BARE,
  DOCUMENT_ANALYSIS,
  MAIL_ACTIVITY,
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

function render(overrides: Partial<ArtifactRender> = {}): ArtifactRender {
  return { format: "xlsx", mime_type: null, size_bytes: 12_288, content_hash: "h1", state: "valid", failing_ref: null, ...overrides };
}

function row(overrides: Partial<ArtifactRow> = {}): ArtifactRow {
  return {
    artifact_id: "a1",
    title: "Bütçe 2026",
    kind: "spreadsheet",
    state: "READY",
    created_at: iso(-90_000),
    updated_at: iso(-30_000),
    renders: [render()],
    ...overrides,
  };
}

const INVALID_PDF = () => render({ format: "pdf", size_bytes: 30_000, state: "invalid", failing_ref: "sheet:Ozet!B5" });
const UNVALIDATED_DOCX = () => render({ format: "docx", size_bytes: 8_000, state: null });

const ok = (rows: ArtifactRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });
const noop = () => {};

function openOf(overrides: Partial<ArtifactOpenProps> = {}): ArtifactOpenProps {
  return { ...ARTIFACT_OPEN_IDLE, onOpen: noop, ...overrides };
}

function panel(
  artifacts: Parameters<typeof ArtifactsPanel>[0]["artifacts"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  open: ArtifactOpenProps = openOf(),
  extra: Partial<Pick<Parameters<typeof ArtifactsPanel>[0], "onDownload" | "notice" | "now">> = {},
) {
  return renderToStaticMarkup(
    <ArtifactsPanel artifacts={artifacts} truth={truthOf(events)} now={extra.now ?? T0} open={open} onDownload={extra.onDownload} notice={extra.notice} />,
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
 * Find the rendered element carrying `attr={value}`, expanding function
 * components by calling them. The panel is a pure presentational component
 * with no hooks, so calling it is exactly what React would do.
 */
function findByData(root: unknown, attr: string, value: string): ElementLike | null {
  const queue: unknown[] = [root];
  let guard = 0;
  while (queue.length > 0 && guard++ < 10_000) {
    const node = queue.shift();
    if (Array.isArray(node)) {
      queue.push(...node);
      continue;
    }
    if (!isElement(node)) continue;
    if (node.props[attr] === value) return node;
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

function click(node: ElementLike | null, e: { preventDefault: () => void } = { preventDefault: noop }): void {
  expect(node).not.toBeNull();
  const handler = node?.props.onClick as ((e: unknown) => void) | undefined;
  expect(typeof handler).toBe("function");
  handler?.(e);
}

const RECEIPT_OPENED: ArtifactOpenReceipt = { state: "opened", summary: "Excel'de açıldı.", receiptId: "r1", windowTitle: "Bütçe 2026.xlsx - Excel" };

function fakeClient(overrides: Partial<ArtifactClient> = {}): ArtifactClient {
  return { open: vi.fn(async () => RECEIPT_OPENED), ...overrides };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: ArtifactClient, onSettled = vi.fn()) {
  let state: ArtifactOpenState = ARTIFACT_OPEN_IDLE;
  const writes: ArtifactOpenState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: ArtifactOpenState) => {
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

describe("the Üretilenler panel", () => {
  it("draws nothing at all when the list is empty and the bus said nothing (B24 req 714)", () => {
    expect(panel(ok([]))).toBe("");
    expect(panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/artifacts yok (HTTP 404)." })).toBe("");
  });

  it("says the bus told it nothing, when there are rows to show anyway", () => {
    const rows = panel(ok([row()]));
    expect(rows).toContain('data-artifact-activity="untold"');
    expect(rows).toContain("Üretim etkinliği bildirilmedi.");
  });

  it("keeps every word when the bus is telling us something the list cannot show", () => {
    const html = panel(ok([]), [ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")]);
    expect(html).toContain('data-panel="artifacts"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Henüz bir şey üretilmedi.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain(">Üretilenler<");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("data-artifact-outcome");
    // What the links and the chip do is said in words on every render.
    expect(html).toContain("sahip oturumundan geçer");
    expect(html).toContain("Doğrulanmamış bir çıktı açılmaz");
    expect(html).toContain("Bu ekran dosya üretmez, doğrulamaz, cihaza ulaşmaz.");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Henüz bir şey üretilmedi");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Henüz bir şey üretilmedi");

    // A route this Cloud Core does not serve still says so WHILE the bus is talking:
    // "the list is not here" and "nothing is happening" are different facts.
    const absent = panel(
      { kind: "absent", detail: "Bu Cloud Core sürümünde /v1/artifacts yok (HTTP 404)." },
      [ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")],
    );
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/artifacts yok (HTTP 404).");
    expect(absent).not.toContain("Henüz bir şey üretilmedi");
    for (const html of [loading, failed, absent]) {
      expect(html).not.toContain("<button");
      expect(html).not.toContain("<a ");
    }
  });

  it("lists an artifact with its valid renders as links on the owner-session-gated render route, and 'Aç' enabled", () => {
    const html = panel(ok([row({ renders: [render(), render({ format: "csv", size_bytes: 900 })] })]));
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).toContain('data-artifact="a1"');
    expect(html).toContain('data-artifact-state="READY"');
    expect(html).toContain('data-artifact-kind="spreadsheet"');
    expect(html).toContain('data-artifact-renders="2"');
    expect(html).toContain('data-artifact-valid-renders="2"');
    expect(html).toContain(">Bütçe 2026</span>");
    expect(html).toContain("tablo · 30 sn önce");
    // Each render on its line with the verdict its row carries.
    expect(html).toContain('data-artifact-render="xlsx" data-render-state="valid" data-render-failing-ref="" data-render-valid="yes"');
    expect(html).toContain("XLSX · 12 KB · doğrulandı");
    expect(html).toContain("CSV · 1 KB · doğrulandı");
    // A link per valid render: the real address, the format as a segment, no referrer.
    expect(html).toContain('href="http://127.0.0.1:8001/v1/artifacts/a1/renders/xlsx"');
    expect(html).toContain('href="http://127.0.0.1:8001/v1/artifacts/a1/renders/csv"');
    expect(html).toContain('rel="noreferrer"');
    expect(html).toContain('data-artifact-download="xlsx" data-artifact-download-target="a1"');
    expect(html).toContain(">İndir</a>");
    expect((html.match(/data-artifact-download=/g) ?? []).length).toBe(2);
    // "Aç": present, enabled, no reason.
    expect(html).toContain('data-artifact-open="a1"');
    expect(html).toContain('data-artifact-open-enabled="yes"');
    expect(html).toContain('data-artifact-open-in-flight="no"');
    expect(html).toContain('data-artifact-action="open" data-artifact-target="a1">Aç</button>');
    expect(html).not.toContain('disabled=""');
    expect(html).not.toContain("data-artifact-open-reason");
  });

  it("names an invalid render with the ref that failed, gives it no link, and disables 'Aç' with the reason when nothing passed", () => {
    const html = panel(ok([row({ renders: [INVALID_PDF()] })]));
    expect(html).toContain('data-artifact-valid-renders="0"');
    expect(html).toContain('data-artifact-render="pdf" data-render-state="invalid" data-render-failing-ref="sheet:Ozet!B5" data-render-valid="no"');
    expect(html).toContain("PDF · 29 KB · doğrulanamadı · yer: sheet:Ozet!B5");
    expect(html).not.toContain("doğrulandı");
    expect(html).not.toContain("data-artifact-download");
    expect(html).not.toContain("<a ");
    expect(html).toContain('data-artifact-open-enabled="no"');
    expect(html).toContain('data-artifact-action="open" data-artifact-target="a1" disabled=""');
    expect(html).toContain('data-artifact-open-reason="no_valid_render"');
    expect(html).toContain(ARTIFACT_OPEN_REASON_NO_VALID_RENDER);
  });

  it("says a render nobody validated is unvalidated — not passed, not failed — and gives it no link; a valid sibling still opens the artifact", () => {
    const html = panel(ok([row({ renders: [UNVALIDATED_DOCX(), INVALID_PDF(), render()] })]));
    expect(html).toContain('data-artifact-render="docx" data-render-state="" data-render-failing-ref="" data-render-valid="no"');
    expect(html).toContain("DOCX · 8 KB · doğrulama bildirilmedi");
    expect(html).toContain('data-artifact-valid-renders="1"');
    expect((html.match(/data-artifact-download=/g) ?? []).length).toBe(1);
    expect(html).toContain('data-artifact-download="xlsx"');
    expect(html).toContain('data-artifact-open-enabled="yes"');
  });

  it("says what a row did not report rather than filling it in", () => {
    const html = panel(ok([row({ title: null, kind: null, state: null, updated_at: null, created_at: null, renders: [] })]));
    expect(html).toContain(">başlık bildirilmedi</span>");
    expect(html).toContain('data-artifact-state=""');
    expect(html).toContain('data-artifact-kind=""');
    expect(html).toContain("çıktı bildirilmedi");
    expect(html).toContain('data-artifact-renders="0"');
    expect(html).toContain('data-artifact-open-enabled="no"');
    expect(html).toContain('data-artifact-open-reason="no_valid_render"');
  });

  it("shows the last artifacts as the route orders them, bounded", () => {
    const rows = Array.from({ length: ARTIFACT_ROWS_SHOWN + 3 }, (_, i) => row({ artifact_id: `a${i}`, title: `Dosya ${i}` }));
    const html = panel(ok(rows));
    expect(html).toContain(`data-panel-badge="true">${ARTIFACT_ROWS_SHOWN + 3}<`);
    expect((html.match(/data-artifact="a\d+"/g) ?? []).length).toBe(ARTIFACT_ROWS_SHOWN);
    expect(html).toContain('data-artifact="a0"');
    expect(html).not.toContain(`data-artifact="a${ARTIFACT_ROWS_SHOWN}"`);
  });

  it("the 'Aç' press asks for that artifact exactly once, and a download press asks for that render exactly once", () => {
    const onOpen = vi.fn();
    const onDownload = vi.fn();
    const preventDefault = vi.fn();
    const rows = [row({ renders: [render(), INVALID_PDF()] }), row({ artifact_id: "a2", title: "Sunum", renders: [render({ format: "pptx" })] })];
    const tree = <ArtifactsPanel artifacts={ok(rows)} truth={truthOf([AGENT_IDLE()])} now={T0} open={openOf({ onOpen })} onDownload={onDownload} />;

    click(findByData(tree, "data-artifact-target", "a2"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith("a2");
    expect(onDownload).not.toHaveBeenCalled();

    click(findByData(tree, "data-artifact-download-target", "a1"), { preventDefault });
    expect(onDownload).toHaveBeenCalledTimes(1);
    expect(onDownload).toHaveBeenCalledWith("a1", "xlsx");
    // The bare navigation is prevented: the bytes go through the session, not the address bar.
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("without a download handler the link is still the honest address, and no handler is attached", () => {
    const tree = <ArtifactsPanel artifacts={ok([row()])} truth={truthOf([AGENT_IDLE()])} now={T0} open={openOf()} />;
    const link = findByData(tree, "data-artifact-download", "xlsx");
    expect(link).not.toBeNull();
    expect(link?.props.href).toBe("http://127.0.0.1:8001/v1/artifacts/a1/renders/xlsx");
    expect(link?.props.onClick).toBeUndefined();
  });

  it("disables every 'Aç' while one is in flight, marking the one that is, with the reason", () => {
    const rows = [row(), row({ artifact_id: "a2", title: "Sunum", renders: [render({ format: "pptx" })] })];
    const html = panel(ok(rows), [AGENT_IDLE()], openOf({ busy: "a1" }));
    expect(html).toContain('data-artifact-open="a1" data-artifact-open-enabled="no" data-artifact-open-in-flight="yes"');
    expect(html).toContain('data-artifact-open="a2" data-artifact-open-enabled="no" data-artifact-open-in-flight="no"');
    expect(html).toContain('data-artifact-action="open" data-artifact-target="a1" disabled=""');
    expect(html).toContain('data-artifact-action="open" data-artifact-target="a2" disabled=""');
    expect(html).toContain('data-artifact-open-reason="busy"');
    expect(html).toContain(ARTIFACT_OPEN_REASON_BUSY);
    // The links are not gated by the chip: a download is this page's own fetch.
    expect((html.match(/data-artifact-download=/g) ?? []).length).toBe(2);
  });

  it("prints the last open's answer, dated, with the artifact it was about", () => {
    const outcome = { artifactId: "a1", ok: true, text: "Açıldı · pencere: Bütçe 2026.xlsx - Excel", at: T0 - 5_000 };
    const html = panel(ok([row()]), [AGENT_IDLE()], openOf({ outcome }));
    expect(html).toContain('data-artifact-outcome="true"');
    expect(html).toContain('data-artifact-ok="yes"');
    expect(html).toContain('data-artifact-target="a1"');
    expect(html).toContain("Açıldı · pencere: Bütçe 2026.xlsx - Excel · 5 sn önce");

    const refused = panel(ok([row()]), [AGENT_IDLE()], openOf({ outcome: { ...outcome, ok: false, text: "Cihaz çevrimdışı; açılmadı." } }));
    expect(refused).toContain('data-artifact-ok="no"');
    expect(refused).toContain("panel-unknown");
    expect(refused).toContain("Cihaz çevrimdışı; açılmadı.");
  });

  it("prints a download that could not be fetched, in words, and keeps the link", () => {
    const html = panel(ok([row()]), [AGENT_IDLE()], openOf(), { notice: "İndirilemedi (XLSX): HTTP 503" });
    expect(html).toContain('data-artifact-notice="true">İndirilemedi (XLSX): HTTP 503</p>');
    expect(html).toContain('data-artifact-download="xlsx"');
    expect(panel(ok([row()]))).not.toContain("data-artifact-notice");
  });

  it("states the bus activity in the spec's words with its age, and last-known once it aged out", () => {
    const live = panel(ok([]), [ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")], openOf(), { now: T0 + 3_000 });
    expect(live).toContain('data-artifact-stage="making"');
    expect(live).toContain('data-artifact-activity="making"');
    expect(live).toContain('data-artifact-caption="Bütçe 2026 · XLSX üretiliyor"');
    expect(live).toContain("Bütçe 2026 · XLSX üretiliyor · 3 sn önce");
    expect(live).not.toContain("Son bilinen");
    // The list is the list: the bus making something does not put a row on it.
    expect(live).toContain("Henüz bir şey üretilmedi.");

    const checked = panel(ok([]), [ARTIFACT_FACTORY("Bütçe 2026", "pdf", "invalid", "sheet:Ozet!B5")], openOf(), { now: T0 + 3_000 });
    expect(checked).toContain("Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5) · 3 sn önce");

    const stale = panel(ok([]), [ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "valid")], openOf(), { now: T0 + ARTIFACT_FACTORY_TTL_MS + 1_000 });
    expect(stale).toContain('data-artifact-stage="none"');
    expect(stale).toContain('data-artifact-last-known="making"');
    expect(stale).toContain("Son bilinen: Bütçe 2026 · XLSX · doğrulandı · 46 sn önce");

    // A document or mail event is not a factory event. Rows are present so the panel
    // renders at all: no rows AND no factory event is a quiet family now (req 714).
    expect(panel(ok([row()]), [DOCUMENT_ANALYSIS()])).toContain('data-artifact-activity="untold"');
    expect(panel(ok([row()]), [MAIL_ACTIVITY()])).toContain('data-artifact-activity="untold"');
  });
});

// ------------------------------------------------------------ the runner

describe("the open runner", () => {
  it("makes exactly one call with the artifact's id, writes busy then the receipt's outcome, and reloads the list", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runArtifactOpen(ports, "a1")).toBe(true);
    expect(client.open).toHaveBeenCalledTimes(1);
    expect(client.open).toHaveBeenCalledWith("a1");
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: "a1", outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      artifactId: "a1",
      ok: true,
      text: "Açıldı · pencere: Bütçe 2026.xlsx - Excel · Excel'de açıldı.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    const deferred: { release: ((receipt: ArtifactOpenReceipt) => void) | null } = { release: null };
    const open = vi.fn(async () => RECEIPT_OPENED);
    open.mockImplementationOnce(
      () =>
        new Promise<ArtifactOpenReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ open });
    const { ports, current } = portsOf(client);

    const first = runArtifactOpen(ports, "a1");
    expect(current().busy).toBe("a1");
    expect(await runArtifactOpen(ports, "a1")).toBe(false);
    expect(await runArtifactOpen(ports, "a2")).toBe(false);
    expect(open).toHaveBeenCalledTimes(1);

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_OPENED);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second open is the Cloud Core's to refuse, not this page's to hide.
    expect(await runArtifactOpen(ports, "a2")).toBe(true);
    expect(open).toHaveBeenCalledTimes(2);
    expect(open).toHaveBeenLastCalledWith("a2");
  });

  it("turns the Cloud Core's refusal — and a route not there yet — into the owner's words, says nothing opened, and still reloads", async () => {
    const client = fakeClient({
      open: vi.fn(async () => {
        throw new ArtifactOpenError(422, "capability_missing", "file.fetch unsupported by agent 0.1.0");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runArtifactOpen(ports, "a1")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toContain("file.fetch yok");
    expect(outcome?.text).toContain("Açılmadı");
    expect(outcome?.text).not.toContain("Açıldı ·");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    const absent = fakeClient({
      open: vi.fn(async () => {
        throw new ArtifactOpenError(404, OPEN_ROUTE_ABSENT, "Bu Cloud Core sürümünde /v1/artifacts/a1/open yok (HTTP 404).");
      }),
    });
    const second = portsOf(absent);
    await runArtifactOpen(second.ports, "a1");
    expect(second.current().outcome?.ok).toBe(false);
    expect(second.current().outcome?.text).toBe("Bu Cloud Core sürümünde /v1/artifacts/a1/open yok (HTTP 404). Açılmadı.");
  });

  it("never says 'açıldı' on the strength of a 2xx alone", () => {
    const none: ArtifactOpenReceipt = { state: null, summary: null, receiptId: "r1", windowTitle: null };
    expect(openOutcomeText(none)).toBe(OPEN_OUTCOME_NO_STATE_TR);
    expect(openOutcomeText(none)).not.toContain("Açıldı");
    expect(openOutcomeText({ state: "opened", summary: null, receiptId: null, windowTitle: null })).toBe("Açıldı");
    expect(openOutcomeText({ state: "fetched", summary: null, receiptId: null, windowTitle: null })).toBe("Cihaza getirildi; açıldığı bildirilmedi");
    expect(openOutcomeText({ state: "queued", summary: "sırada", receiptId: null, windowTitle: null })).toBe("Sıraya alındı; cihaz bekleniyor · sırada");
    expect(openOutcomeText({ state: "odd", summary: null, receiptId: null, windowTitle: "X" })).toBe("durum: odd · pencere: X");
    // The window is the companion's observation, and is said only when it was sent.
    expect(openOutcomeText({ state: "opened", summary: "Excel'de açıldı.", receiptId: null, windowTitle: null })).toBe("Açıldı · Excel'de açıldı.");
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for the factory", () => {
  it("headlines the making posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([ARTIFACT_FACTORY("Bütçe 2026", "pdf", "invalid", "sheet:Ozet!B5")]);
    expect(html).toContain('data-core-kind="artifact_factory"');
    expect(html).toContain('data-core-state="artifact.factory"');
    expect(html).toContain('data-core-subsystem="artifacts"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain("Dosya üretiliyor");
    expect(html).toContain("Üretim");
    expect(html).toContain('data-label="true">Bütçe 2026 · PDF · doğrulanamadı (sheet:Ozet!B5)</p>');
    expect(html).not.toContain("data-caption");
    expect(html).toContain("data-artifact-facts");
    expect(html).toContain('data-artifact-title="Bütçe 2026"');
    expect(html).toContain('data-artifact-format="pdf"');
    expect(html).toContain('data-artifact-verdict="invalid"');
    expect(html).toContain('data-artifact-failing-ref="sheet:Ozet!B5"');
    expect(html).toContain("başlık: Bütçe 2026 · biçim: PDF · sonuç: doğrulanamadı · yer: sheet:Ozet!B5");
    expect(html).toContain("Doğrulanmamış bir çıktı bitmiş sayılmaz.");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-document-facts");
    expect(html).not.toContain("data-mail-facts");
    expect(html).not.toContain("data-calendar-facts");
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([ARTIFACT_FACTORY_BARE()]);
    expect(html).toContain('data-label="true">Dosya üretiliyor</p>');
    expect(html).toContain("başlık bildirilmedi · biçim bildirilmedi · sonuç bildirilmedi");
    expect(html).toContain('data-artifact-title=""');
    expect(html).toContain('data-artifact-verdict=""');
  });

  it("keeps the caption in the compact form and drops the long line", () => {
    const html = readout([ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "valid")], true);
    expect(html).toContain("Bütçe 2026 · XLSX · doğrulandı");
    expect(html).not.toContain("data-artifact-facts");
  });

  it("names the aged-out render as last-known rather than as making, with its facts", () => {
    const html = readout([ARTIFACT_FACTORY("Bütçe 2026", "xlsx", "rendering")], false, T0 + ARTIFACT_FACTORY_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="artifact.factory"');
    expect(html).toContain("Dosya üretiliyor");
    expect(html).toContain("data-artifact-facts");
    expect(html).toContain("başlık: Bütçe 2026");
    expect(html).not.toContain("doğrulandı");
  });

  it("prints no factory line for any other kind", () => {
    expect(readout([AGENT_IDLE()])).not.toContain("data-artifact-facts");
    expect(readout([DOCUMENT_ANALYSIS()])).not.toContain("data-artifact-facts");
    expect(readout([MAIL_ACTIVITY()])).not.toContain("data-artifact-facts");
  });
});
