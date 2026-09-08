/**
 * The Uygulamalar panel, its three chips and the Core's readout for
 * `app.factory` (M23 spec §6): sentences about the rows the list route
 * holds and the tokens the Cloud Core published, a link for a RUNNING
 * project and nothing else, three chips that ask the Cloud Core exactly
 * once each, and never a process this page started.
 *
 * Rendered with `react-dom/server` like the rest of this suite. The click
 * is proven the way `artifacts-panel.test.tsx` proves it: the panel is
 * hook-free, so the element tree is walked to the control and its handler
 * invoked — exactly what React would do.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AppsPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import {
  APP_REASON_ALREADY_RUNNING,
  APP_REASON_BUSY,
  APP_REASON_NOT_RUNNING,
  APP_REASON_NOT_SCAFFOLDED,
  APP_ROWS_SHOWN,
} from "../../app/lib/cockpit/app-rows";
import {
  APPS_CONTROL_IDLE,
  APP_ROUTE_ABSENT,
  type AppActionReceipt,
  type AppProjectRow,
  type AppsClient,
  type AppsControlProps,
  type AppsControlState,
  AppActionError,
} from "../../app/lib/cockpit/apps";
import { APP_OUTCOME_NO_STATE_TR, appOutcomeText, runAppAction } from "../../app/lib/cockpit/useAppsControl";
import { APP_FACTORY_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  APP_FACTORY,
  APP_FACTORY_BARE,
  ARTIFACT_FACTORY,
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

function row(overrides: Partial<AppProjectRow> = {}): AppProjectRow {
  return {
    app_id: "p1",
    name: "Görev Takip",
    kind: "web_static",
    template: "task-tracker",
    state: "scaffolded",
    port: null,
    tests: null,
    root_path: null,
    created_at: iso(-90_000),
    updated_at: iso(-30_000),
    ...overrides,
  };
}

const RUNNING = () => row({ state: "running", port: 8123 });
const TESTED = () => row({ app_id: "p2", name: "Notlarım", template: "static-page", state: "tested", tests: { passed: 12, failed: 0 } });
const FAILED = () => row({ app_id: "p3", name: "Hesap", kind: "cli", template: "cli-tool", state: "failed", tests: { passed: 10, failed: 2 } });

const ok = (rows: AppProjectRow[]) => ({ kind: "ok" as const, value: rows, at: T0 });
const noop = () => {};

function controlOf(overrides: Partial<AppsControlProps> = {}): AppsControlProps {
  return { ...APPS_CONTROL_IDLE, onRun: noop, onStop: noop, onTest: noop, ...overrides };
}

function panel(
  apps: Parameters<typeof AppsPanel>[0]["apps"],
  events: ReturnType<typeof event>[] = [AGENT_IDLE()],
  control: AppsControlProps = controlOf(),
  now = T0,
) {
  return renderToStaticMarkup(<AppsPanel apps={apps} truth={truthOf(events)} now={now} control={control} />);
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

const RECEIPT_RUNNING: AppActionReceipt = { state: "running", port: 8123, tests: null, summary: "Görev Takip çalışıyor.", receiptId: "r1" };
const RECEIPT_STOPPED: AppActionReceipt = { state: "stopped", port: null, tests: null, summary: null, receiptId: "r2" };
const RECEIPT_TESTED: AppActionReceipt = { state: "tested", port: null, tests: { passed: 12, failed: 0 }, summary: null, receiptId: "r3" };

function fakeClient(overrides: Partial<AppsClient> = {}): AppsClient {
  return {
    run: vi.fn(async () => RECEIPT_RUNNING),
    stop: vi.fn(async () => RECEIPT_STOPPED),
    test: vi.fn(async () => RECEIPT_TESTED),
    ...overrides,
  };
}

/** Plain ports over a local state cell, recording every write. */
function portsOf(client: AppsClient, onSettled = vi.fn()) {
  let state: AppsControlState = APPS_CONTROL_IDLE;
  const writes: AppsControlState[] = [];
  return {
    ports: {
      client,
      read: () => state,
      write: (next: AppsControlState) => {
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

describe("the Uygulamalar panel", () => {
  it("is empty, in words, when the list route answered with no project and the bus said nothing", () => {
    const html = panel(ok([]));
    expect(html).toContain('data-panel="apps"');
    expect(html).toContain('data-panel-state="ok"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain("Henüz bir uygulama yapılmadı");
    expect(html).toContain('data-app-activity="untold"');
    expect(html).toContain("Uygulama etkinliği bildirilmedi.");
    expect(html).toContain('data-panel-badge="true">0<');
    expect(html).toContain('data-apps-running="0"');
    expect(html).toContain(">Uygulamalar<");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("data-app-outcome");
    expect(html).not.toContain("attention");
    // What the link and the chips do is said in words on every render.
    expect(html).toContain("yalnızca çalışan bir uygulama için gösterilir");
    expect(html).toContain("bu sayfa ona ulaşmaz");
    expect(html).toContain("sahibin hiçbir süreci durdurulmaz");
    expect(html).toContain("Bu ekran süreç başlatmaz, durdurmaz, dosya yazmaz.");
  });

  it("never renders the empty sentence for a route that is loading, failed or absent", () => {
    const loading = panel({ kind: "loading" });
    expect(loading).toContain("data-panel-loading");
    expect(loading).toContain("yükleniyor…");
    expect(loading).toContain('data-panel-empty=""');
    expect(loading).not.toContain("Henüz bir uygulama yapılmadı");
    expect(loading).not.toContain("data-panel-badge");

    const failed = panel({ kind: "failed", error: "HTTP 503" });
    expect(failed).toContain("Alınamadı: HTTP 503");
    expect(failed).not.toContain("Henüz bir uygulama yapılmadı");

    const absent = panel({ kind: "absent", detail: "Bu Cloud Core sürümünde /v1/apps yok (HTTP 404)." });
    expect(absent).toContain("data-panel-absent");
    expect(absent).toContain("Henüz yok. Bu Cloud Core sürümünde /v1/apps yok (HTTP 404).");
    expect(absent).not.toContain("Henüz bir uygulama yapılmadı");
    for (const html of [loading, failed, absent]) {
      expect(html).not.toContain("<button");
      expect(html).not.toContain("<a ");
    }
  });

  it("lists a running project with its link to the owner's loopback, 'Durdur' enabled and 'Çalıştır' refused with the reason", () => {
    const html = panel(ok([RUNNING()]));
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-panel-badge="true">1 çalışıyor / 1<');
    expect(html).toContain('data-apps-running="1"');
    expect(html).toContain('data-app="p1"');
    expect(html).toContain('data-app-state="running"');
    expect(html).toContain('data-app-kind="web_static"');
    expect(html).toContain('data-app-template="task-tracker"');
    expect(html).toContain('data-app-port="8123"');
    expect(html).toContain('data-app-running="yes"');
    expect(html).toContain("Görev Takip");
    expect(html).toContain("statik web · 30 sn önce");
    expect(html).toContain('data-app-line="true">çalışıyor · port: 8123</span>');
    // The link: the loopback address on the row's port, a new tab, no referrer — for the owner's browser.
    expect(html).toContain('href="http://127.0.0.1:8123/"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noreferrer noopener"');
    expect(html).toContain('data-app-link="p1"');
    expect(html).toContain('data-app-url="http://127.0.0.1:8123/"');
    expect(html).toContain(">http://127.0.0.1:8123/</a>");
    expect((html.match(/data-app-link=/g) ?? []).length).toBe(1);
    // The chips: stop and test enabled, run refused because it already runs.
    expect(html).toContain('data-app-controls="p1"');
    expect(html).toContain('data-app-in-flight="no"');
    expect(html).toContain('data-app-action="run" data-app-target="p1" data-app-enabled="no" disabled=""');
    expect(html).toContain('data-app-action="stop" data-app-target="p1" data-app-enabled="yes">Durdur</button>');
    expect(html).toContain('data-app-action="test" data-app-target="p1" data-app-enabled="yes">Testleri çalıştır</button>');
    expect(html).toContain('data-app-reason="already_running" data-app-reason-for="run"');
    expect(html).toContain(`Çalıştır: ${APP_REASON_ALREADY_RUNNING}`);
    expect(html).not.toContain("attention");
  });

  it("lists a tested project with its counts and no link, 'Çalıştır' and 'Testleri çalıştır' enabled, 'Durdur' refused", () => {
    const html = panel(ok([TESTED()]));
    expect(html).toContain('data-app="p2"');
    expect(html).toContain('data-app-state="tested"');
    expect(html).toContain('data-app-running="no"');
    expect(html).toContain('data-app-tests-passed="12"');
    expect(html).toContain('data-app-tests-failed="0"');
    expect(html).toContain("Notlarım");
    expect(html).toContain('data-app-line="true">test edildi · testler: 12 geçti / 0 başarısız</span>');
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("data-app-link");
    expect(html).toContain('data-app-action="run" data-app-target="p2" data-app-enabled="yes">Çalıştır</button>');
    expect(html).toContain('data-app-action="stop" data-app-target="p2" data-app-enabled="no" disabled=""');
    expect(html).toContain('data-app-action="test" data-app-target="p2" data-app-enabled="yes">Testleri çalıştır</button>');
    expect(html).toContain('data-app-reason="not_running" data-app-reason-for="stop"');
    expect(html).toContain(`Durdur: ${APP_REASON_NOT_RUNNING}`);
    expect(html).toContain('data-panel-badge="true">1<');
    expect(html).toContain('data-apps-running="0"');
  });

  it("lists a failed project with its failing count, draws attention to it, and never dresses it as done", () => {
    const html = panel(ok([FAILED()]));
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-app="p3"');
    expect(html).toContain('data-app-state="failed"');
    expect(html).toContain('data-app-kind="cli"');
    expect(html).toContain('data-app-tests-passed="10"');
    expect(html).toContain('data-app-tests-failed="2"');
    expect(html).toContain("komut satırı");
    expect(html).toContain('data-app-line="true">başarısız · testler: 10 geçti / 2 başarısız</span>');
    expect(html).not.toContain("geçti<");
    expect(html).not.toContain("test edildi");
    expect(html).not.toContain("data-app-link");
    // A failed project can be run again and tested again; it has no process to stop.
    expect(html).toContain('data-app-action="run" data-app-target="p3" data-app-enabled="yes"');
    expect(html).toContain('data-app-action="test" data-app-target="p3" data-app-enabled="yes"');
    expect(html).toContain('data-app-action="stop" data-app-target="p3" data-app-enabled="no"');
  });

  it("gives a stopped project that still carries its last port no link: nothing is listening", () => {
    const html = panel(ok([row({ state: "stopped", port: 8123 })]));
    expect(html).toContain('data-app-state="stopped"');
    expect(html).toContain('data-app-port="8123"');
    expect(html).toContain('data-app-running="no"');
    expect(html).toContain('data-app-line="true">durduruldu · port: 8123</span>');
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("127.0.0.1");
  });

  it("gives a running project with no port no link, and says the port was not reported", () => {
    const html = panel(ok([row({ state: "running", port: null })]));
    expect(html).toContain('data-app-running="yes"');
    expect(html).toContain('data-app-line="true">çalışıyor · port bildirilmedi</span>');
    expect(html).not.toContain("<a ");
    expect(html).toContain('data-app-action="stop" data-app-target="p1" data-app-enabled="yes"');
  });

  it("refuses 'Çalıştır' and 'Testleri çalıştır' for a planned project, whose files are not on the device yet", () => {
    const html = panel(ok([row({ state: "planned" })]));
    expect(html).toContain('data-app-line="true">planlandı</span>');
    expect(html).toContain('data-app-action="run" data-app-target="p1" data-app-enabled="no"');
    expect(html).toContain('data-app-action="test" data-app-target="p1" data-app-enabled="no"');
    expect(html).toContain('data-app-action="stop" data-app-target="p1" data-app-enabled="no"');
    expect(html).toContain('data-app-reason="not_scaffolded" data-app-reason-for="run,test"');
    expect(html).toContain('data-app-reason="not_running" data-app-reason-for="stop"');
    // One sentence per distinct reason, not one per chip — and it names every chip it refuses
    // (review finding: the first cut named only the last of them).
    expect(html).toContain(`Çalıştır, Testleri çalıştır: ${APP_REASON_NOT_SCAFFOLDED}`);
    expect(html).toContain(`Durdur: ${APP_REASON_NOT_RUNNING}`);
    expect((html.match(new RegExp(APP_REASON_NOT_SCAFFOLDED.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) ?? []).length).toBe(1);
  });

  it("says what a row did not report rather than filling it in", () => {
    const html = panel(ok([row({ name: null, kind: null, template: null, state: null, updated_at: null, created_at: null })]));
    expect(html).toContain(">ad bildirilmedi");
    expect(html).toContain('data-app-state=""');
    expect(html).toContain('data-app-kind=""');
    expect(html).toContain('data-app-line="true">durum bildirilmedi</span>');
    // A state this build cannot read gates nothing: the Cloud Core refuses on its own terms.
    expect(html).toContain('data-app-action="run" data-app-target="p1" data-app-enabled="yes"');
    expect(html).toContain('data-app-action="test" data-app-target="p1" data-app-enabled="yes"');
    expect(html).toContain('data-app-action="stop" data-app-target="p1" data-app-enabled="no"');
    // A newer server's word is printed verbatim: still a published fact.
    expect(panel(ok([row({ state: "built" })]))).toContain('data-app-line="true">built</span>');
  });

  it("shows the root path only when the row named one", () => {
    expect(panel(ok([row({ root_path: "C:\\Users\\alpak\\Documents\\PagentOS Projects\\gorev-takip" })]))).toContain(
      'data-app-root-path="true">C:\\Users\\alpak\\Documents\\PagentOS Projects\\gorev-takip</span>',
    );
    expect(panel(ok([row()]))).not.toContain("data-app-root-path");
  });

  it("shows the last projects as the route orders them, bounded, and counts the running ones in the badge", () => {
    const rows = Array.from({ length: APP_ROWS_SHOWN + 3 }, (_, i) => row({ app_id: `p${i}`, name: `Uygulama ${i}`, state: i < 2 ? "running" : "stopped", port: i < 2 ? 8100 + i : null }));
    const html = panel(ok(rows));
    expect(html).toContain(`data-panel-badge="true">2 çalışıyor / ${APP_ROWS_SHOWN + 3}<`);
    expect(html).toContain('data-apps-running="2"');
    expect((html.match(/data-app="p\d+"/g) ?? []).length).toBe(APP_ROWS_SHOWN);
    expect(html).toContain('data-app="p0"');
    expect(html).not.toContain(`data-app="p${APP_ROWS_SHOWN}"`);
    expect((html.match(/data-app-link=/g) ?? []).length).toBe(2);
  });

  it("each chip asks for that project exactly once, and only through its own handler", () => {
    const onRun = vi.fn();
    const onStop = vi.fn();
    const onTest = vi.fn();
    const rows = [RUNNING(), TESTED(), FAILED()];
    const tree = <AppsPanel apps={ok(rows)} truth={truthOf([AGENT_IDLE()])} now={T0} control={controlOf({ onRun, onStop, onTest })} />;

    click(findByData(tree, { "data-app-action": "stop", "data-app-target": "p1" }));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onStop).toHaveBeenCalledWith("p1");
    expect(onRun).not.toHaveBeenCalled();
    expect(onTest).not.toHaveBeenCalled();

    click(findByData(tree, { "data-app-action": "run", "data-app-target": "p2" }));
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(onRun).toHaveBeenCalledWith("p2");

    click(findByData(tree, { "data-app-action": "test", "data-app-target": "p3" }));
    expect(onTest).toHaveBeenCalledTimes(1);
    expect(onTest).toHaveBeenCalledWith("p3");

    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onRun).toHaveBeenCalledTimes(1);
    // The link is a plain anchor for the browser: no handler of this page's is on it.
    const link = findByData(tree, { "data-app-link": "p1" });
    expect(link).not.toBeNull();
    expect(link?.props.href).toBe("http://127.0.0.1:8123/");
    expect(link?.props.onClick).toBeUndefined();
  });

  it("disables every chip while one call is in flight, marking the project and the action it is, with the reason", () => {
    const html = panel(ok([RUNNING(), TESTED()]), [AGENT_IDLE()], controlOf({ busy: { action: "test", id: "p1" } }));
    expect(html).toContain('data-app-controls="p1" data-app-in-flight="yes" data-app-in-flight-action="test"');
    expect(html).toContain('data-app-controls="p2" data-app-in-flight="no" data-app-in-flight-action=""');
    for (const id of ["p1", "p2"]) {
      for (const action of ["run", "stop", "test"]) {
        expect(html).toContain(`data-app-action="${action}" data-app-target="${id}" data-app-enabled="no" disabled=""`);
      }
    }
    expect(html).toContain('data-app-reason="busy" data-app-reason-for="run,stop,test"');
    expect(html).toContain(APP_REASON_BUSY);
    expect(html).not.toContain(`Durdur: ${APP_REASON_BUSY}`);
    expect(html).not.toContain(`Çalıştır, Durdur, Testleri çalıştır: ${APP_REASON_BUSY}`);
    // Said once per project, not once per chip.
    expect((html.match(/data-app-reason="busy"/g) ?? []).length).toBe(2);
    // The link is not gated by the chips: a running app is still running.
    expect(html).toContain('data-app-link="p1"');
  });

  it("prints the last call's answer, dated, with the project and the action it was about", () => {
    const outcome = { action: "run" as const, id: "p1", ok: true, text: "Çalışıyor · 127.0.0.1:8123 · Görev Takip çalışıyor.", at: T0 - 5_000 };
    const html = panel(ok([RUNNING()]), [AGENT_IDLE()], controlOf({ outcome }));
    expect(html).toContain('data-app-outcome="run"');
    expect(html).toContain('data-app-ok="yes"');
    expect(html).toContain('data-app-target="p1"');
    expect(html).toContain("Çalışıyor · 127.0.0.1:8123 · Görev Takip çalışıyor. · 5 sn önce");

    const refused = panel(ok([RUNNING()]), [AGENT_IDLE()], controlOf({ outcome: { ...outcome, ok: false, text: "Cihaz çevrimdışı; yapılmadı." } }));
    expect(refused).toContain('data-app-ok="no"');
    expect(refused).toContain("panel-unknown");
    expect(refused).toContain("Cihaz çevrimdışı; yapılmadı.");
  });

  it("states the bus activity in the spec's words with its age, and last-known once it aged out", () => {
    const live = panel(ok([]), [APP_FACTORY("Görev Takip", "scaffolded")], controlOf(), T0 + 3_000);
    expect(live).toContain('data-app-stage="active"');
    expect(live).toContain('data-app-activity="active"');
    expect(live).toContain('data-app-caption="Görev Takip iskeleti kuruluyor"');
    expect(live).toContain("Görev Takip iskeleti kuruluyor · 3 sn önce");
    expect(live).not.toContain("Son bilinen");
    // The list is the list: the bus building something does not put a row on it, nor a link.
    expect(live).toContain("Henüz bir uygulama yapılmadı.");
    expect(live).not.toContain("<a ");

    const running = panel(ok([]), [APP_FACTORY("Görev Takip", "running", 8123)], controlOf(), T0 + 3_000);
    expect(running).toContain("Görev Takip çalışıyor · 127.0.0.1:8123 · 3 sn önce");
    expect(running).not.toContain("<a ");

    const stale = panel(ok([]), [APP_FACTORY("Görev Takip", "tested", null, { passed: 12, failed: 0 })], controlOf(), T0 + APP_FACTORY_TTL_MS + 1_000);
    expect(stale).toContain('data-app-stage="none"');
    expect(stale).toContain('data-app-last-known="active"');
    expect(stale).toContain("Son bilinen: Görev Takip testleri geçti (12/12) · 46 sn önce");

    // A document, mail or artifact event is not an app event.
    expect(panel(ok([]), [DOCUMENT_ANALYSIS()])).toContain('data-app-activity="untold"');
    expect(panel(ok([]), [MAIL_ACTIVITY()])).toContain('data-app-activity="untold"');
    expect(panel(ok([]), [ARTIFACT_FACTORY()])).toContain('data-app-activity="untold"');
  });
});

// ------------------------------------------------------------ the runner

describe("the action runner", () => {
  it("makes exactly one call with the project's id, writes busy then the receipt's outcome, and reloads the list", async () => {
    const client = fakeClient();
    const { ports, writes, current, onSettled } = portsOf(client);
    expect(await runAppAction(ports, "run", "p1")).toBe(true);
    expect(client.run).toHaveBeenCalledTimes(1);
    expect(client.run).toHaveBeenCalledWith("p1");
    expect(client.stop).not.toHaveBeenCalled();
    expect(client.test).not.toHaveBeenCalled();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual({ busy: { action: "run", id: "p1" }, outcome: null });
    expect(current().busy).toBeNull();
    expect(current().outcome).toEqual({
      action: "run",
      id: "p1",
      ok: true,
      text: "Çalışıyor · 127.0.0.1:8123 · Görev Takip çalışıyor.",
      at: T0,
    });
    expect(onSettled).toHaveBeenCalledTimes(1);

    const second = portsOf(fakeClient());
    await runAppAction(second.ports, "stop", "p1");
    expect(second.ports.client.stop).toHaveBeenCalledTimes(1);
    expect(second.ports.client.run).not.toHaveBeenCalled();
    expect(second.current().outcome?.text).toBe("Durduruldu");

    const third = portsOf(fakeClient());
    await runAppAction(third.ports, "test", "p2");
    expect(third.ports.client.test).toHaveBeenCalledTimes(1);
    expect(third.ports.client.test).toHaveBeenCalledWith("p2");
    expect(third.current().outcome?.text).toBe("Test edildi · testler: 12 geçti / 0 başarısız");
  });

  it("refuses a second press while the first is in flight: the client is still called once", async () => {
    const deferred: { release: ((receipt: AppActionReceipt) => void) | null } = { release: null };
    const run = vi.fn(async () => RECEIPT_RUNNING);
    run.mockImplementationOnce(
      () =>
        new Promise<AppActionReceipt>((resolve) => {
          deferred.release = resolve;
        }),
    );
    const client = fakeClient({ run });
    const { ports, current } = portsOf(client);

    const first = runAppAction(ports, "run", "p1");
    expect(current().busy).toEqual({ action: "run", id: "p1" });
    expect(await runAppAction(ports, "run", "p1")).toBe(false);
    expect(await runAppAction(ports, "stop", "p1")).toBe(false);
    expect(await runAppAction(ports, "test", "p2")).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);
    expect(client.stop).not.toHaveBeenCalled();
    expect(client.test).not.toHaveBeenCalled();

    expect(deferred.release).not.toBeNull();
    deferred.release?.(RECEIPT_RUNNING);
    expect(await first).toBe(true);
    expect(current().busy).toBeNull();
    // Once settled, the next press goes through — a second run is the Cloud Core's to refuse, not this page's to hide.
    expect(await runAppAction(ports, "run", "p2")).toBe(true);
    expect(run).toHaveBeenCalledTimes(2);
    expect(run).toHaveBeenLastCalledWith("p2");
  });

  it("turns the Cloud Core's refusal — and a route not there yet — into the owner's words, says nothing ran, and still reloads", async () => {
    const client = fakeClient({
      run: vi.fn(async () => {
        throw new AppActionError(409, "too_many_running", "2 projects already running");
      }),
    });
    const { ports, current, onSettled } = portsOf(client);
    expect(await runAppAction(ports, "run", "p1")).toBe(true);
    const outcome = current().outcome;
    expect(outcome?.ok).toBe(false);
    expect(outcome?.text).toBe("Aynı anda en fazla iki proje çalışır; üçüncüsü başlatılmadı.");
    expect(outcome?.text).not.toContain("Çalışıyor");
    expect(current().busy).toBeNull();
    expect(onSettled).toHaveBeenCalledTimes(1);

    const absent = fakeClient({
      test: vi.fn(async () => {
        throw new AppActionError(404, APP_ROUTE_ABSENT, "Bu Cloud Core sürümünde /v1/apps/p1/test yok (HTTP 404).");
      }),
    });
    const second = portsOf(absent);
    await runAppAction(second.ports, "test", "p1");
    expect(second.current().outcome?.ok).toBe(false);
    expect(second.current().outcome?.text).toBe("Bu Cloud Core sürümünde /v1/apps/p1/test yok (HTTP 404). Yapılmadı.");
  });

  it("never says 'çalışıyor' on the strength of a 2xx alone", () => {
    const none: AppActionReceipt = { state: null, port: null, tests: null, summary: null, receiptId: "r1" };
    expect(appOutcomeText("run", none)).toBe(APP_OUTCOME_NO_STATE_TR.run);
    expect(appOutcomeText("run", none)).not.toContain("alışıyor");
    expect(appOutcomeText("stop", none)).toBe(APP_OUTCOME_NO_STATE_TR.stop);
    expect(appOutcomeText("test", none)).toBe(APP_OUTCOME_NO_STATE_TR.test);
    expect(appOutcomeText("run", { ...none, state: "running" })).toBe("Çalışıyor");
    expect(appOutcomeText("run", { ...none, state: "running", port: 8123 })).toBe("Çalışıyor · 127.0.0.1:8123");
    expect(appOutcomeText("run", { ...none, state: "scaffolded" })).toBe("İskeleti kuruluyor");
    expect(appOutcomeText("test", { ...none, state: "failed", tests: { passed: 10, failed: 2 } })).toBe("Başarısız · testler: 10 geçti / 2 başarısız");
    // A state this build cannot read is printed as the token, never as one of the six.
    expect(appOutcomeText("run", { ...none, state: "queued", summary: "sırada" })).toBe("durum: queued · sırada");
    // A port with no state is a fact beside the no-state sentence, not a running app.
    expect(appOutcomeText("run", { ...none, port: 8123 })).toBe(`${APP_OUTCOME_NO_STATE_TR.run} · 127.0.0.1:8123`);
  });
});

// ------------------------------------------------------------- the readout

describe("the Core's readout for the App Factory", () => {
  it("headlines the building posture with the caption and the facts beneath, and no bar", () => {
    const html = readout([APP_FACTORY("Görev Takip", "scaffolded")]);
    expect(html).toContain('data-core-kind="app_factory"');
    expect(html).toContain('data-core-state="app.factory"');
    expect(html).toContain('data-core-subsystem="apps"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain("Uygulama yapılıyor");
    expect(html).toContain("Uygulamalar");
    expect(html).toContain('data-label="true">Görev Takip iskeleti kuruluyor</p>');
    expect(html).not.toContain("data-caption");
    expect(html).toContain("data-app-facts");
    expect(html).toContain('data-app-project="Görev Takip"');
    expect(html).toContain('data-app-state="scaffolded"');
    expect(html).toContain('data-app-port=""');
    expect(html).toContain('data-app-serving="no"');
    expect(html).toContain("proje: Görev Takip · durum: iskeleti kuruluyor");
    expect(html).toContain("Testleri geçmemiş bir uygulama bitmiş sayılmaz.");
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
    expect(html).not.toContain("data-artifact-facts");
    expect(html).not.toContain("data-document-facts");
    expect(html).not.toContain("data-mail-facts");
    expect(html).not.toContain("data-calendar-facts");
    // No link on the Core: the link is the Cockpit's, from the row.
    expect(html).not.toContain("<a ");
  });

  it("marks the running posture as serving only when a port was published, and never draws progress for it", () => {
    const served = readout([APP_FACTORY("Görev Takip", "running", 8123)]);
    expect(served).toContain('data-label="true">Görev Takip çalışıyor · 127.0.0.1:8123</p>');
    expect(served).toContain('data-app-state="running"');
    expect(served).toContain('data-app-port="8123"');
    expect(served).toContain('data-app-serving="yes"');
    expect(served).toContain("proje: Görev Takip · durum: çalışıyor · port: 8123");
    expect(served).not.toContain("core-progress-fill");
    expect(served).not.toContain("<a ");

    const unaddressed = readout([APP_FACTORY("Görev Takip", "running")]);
    expect(unaddressed).toContain('data-label="true">Görev Takip çalışıyor</p>');
    expect(unaddressed).toContain('data-app-serving="no"');
    expect(unaddressed).toContain("port bildirilmedi");
  });

  it("prints the counts beside a result, and the failure with its count", () => {
    const tested = readout([APP_FACTORY("Görev Takip", "tested", null, { passed: 12, failed: 0 })]);
    expect(tested).toContain('data-label="true">Görev Takip testleri geçti (12/12)</p>');
    expect(tested).toContain('data-app-tests-passed="12"');
    expect(tested).toContain('data-app-tests-failed="0"');
    expect(tested).toContain("testler: 12 geçti / 0 başarısız");

    const failed = readout([APP_FACTORY("Görev Takip", "failed", null, { passed: 10, failed: 2 })]);
    expect(failed).toContain('data-label="true">Görev Takip başarısız — 2 test</p>');
    expect(failed).toContain("testler: 10 geçti / 2 başarısız");
    expect(failed).not.toContain("geçti (");
  });

  it("says what was not reported when the publisher named nothing", () => {
    const html = readout([APP_FACTORY_BARE()]);
    expect(html).toContain('data-label="true">Uygulama yapılıyor</p>');
    expect(html).toContain("proje bildirilmedi · durum bildirilmedi");
    expect(html).toContain('data-app-project=""');
    expect(html).toContain('data-app-state=""');
    expect(html).toContain('data-app-serving="no"');
  });

  it("keeps the caption in the compact form and drops the long line", () => {
    const html = readout([APP_FACTORY("Görev Takip", "running", 8123)], true);
    expect(html).toContain("Görev Takip çalışıyor · 127.0.0.1:8123");
    expect(html).not.toContain("data-app-facts");
  });

  it("names the aged-out build as last-known rather than as building, with its facts and not serving", () => {
    const html = readout([APP_FACTORY("Görev Takip", "running", 8123)], false, T0 + APP_FACTORY_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="app.factory"');
    expect(html).toContain("Uygulama yapılıyor");
    expect(html).toContain("data-app-facts");
    expect(html).toContain("proje: Görev Takip");
    // The facts line still names the port it WAS on; the serving mark follows the facts, not the life of the claim.
    expect(html).toContain('data-app-port="8123"');
  });

  it("prints no app line for any other kind", () => {
    expect(readout([AGENT_IDLE()])).not.toContain("data-app-facts");
    expect(readout([DOCUMENT_ANALYSIS()])).not.toContain("data-app-facts");
    expect(readout([MAIL_ACTIVITY()])).not.toContain("data-app-facts");
    expect(readout([ARTIFACT_FACTORY()])).not.toContain("data-app-facts");
  });
});
