/**
 * The apps client (M23 spec §6): four routes, exactly, through the owner
 * session — and the rows and receipts read from whatever shape the Cloud
 * Core answers with, never filled in.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  APPS_PATH,
  APP_ACTIONS,
  APP_ACTION_REFUSAL_TR,
  APP_ROUTE_ABSENT,
  AppActionError,
  appAction,
  appActionErrorText,
  appActionPath,
  appsClient,
  appsUrl,
  fetchApps,
  parseAppReceipt,
  parseAppRow,
  testCountsOf,
} from "../../app/lib/cockpit/apps";
import {
  APP_ACTION_LABEL,
  APP_KIND_LABEL,
  APP_REASON_ALREADY_RUNNING,
  APP_REASON_BUSY,
  APP_REASON_NOT_RUNNING,
  APP_REASON_NOT_SCAFFOLDED,
  appActionGate,
  appKindLabel,
  appRowLine,
  appRowUrl,
  rowIsRunning,
} from "../../app/lib/cockpit/app-rows";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

const ROW = {
  id: "p1",
  name: "Görev Takip",
  kind: "web_static",
  template: "task-tracker",
  spec_json: { name: "Görev Takip", kind: "web_static", entities: [{ name: "task", fields: ["title", "done"] }] },
  device_id: "dev-1",
  root_path: "C:\\Users\\alpak\\Documents\\PagentOS Projects\\gorev-takip",
  state: "running",
  run_port: 8123,
  last_run_log_ref: "logs/run-1.log",
  test_report_json: { exit_code: 0, passed: 12, failed: 0, report_tail: "12 passing" },
  created_at: "2026-09-08T09:00:00Z",
  updated_at: "2026-09-08T09:01:00Z",
};

describe("the four routes, exactly", () => {
  it("names them once", () => {
    expect(APPS_PATH).toBe("/v1/apps");
    expect(APP_ACTIONS).toEqual(["run", "stop", "test"]);
    expect(appActionPath("p1", "run")).toBe("/v1/apps/p1/run");
    expect(appActionPath("p1", "stop")).toBe("/v1/apps/p1/stop");
    expect(appActionPath("p1", "test")).toBe("/v1/apps/p1/test");
    expect(appsUrl()).toBe("http://core.test:8001/v1/apps");
    // An id is a path segment, never a path.
    expect(appActionPath("p 1/../x", "run")).toBe("/v1/apps/p%201%2F..%2Fx/run");
  });

  it("GET /v1/apps, and reads the rows from the shape the route answers with", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { apps: [ROW, { no_id: true }] }));
    const loaded = await fetchApps();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit | undefined];
    expect(path).toBe("/v1/apps");
    expect(init?.method ?? "GET").toBe("GET");
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(1); // a row with no id is not a project
    expect(loaded.value[0]).toEqual({
      app_id: "p1",
      name: "Görev Takip",
      kind: "web_static",
      template: "task-tracker",
      state: "running",
      port: 8123,
      // The counts come from the test report the row carries — the report's own two figures, nothing else of it.
      tests: { passed: 12, failed: 0 },
      root_path: "C:\\Users\\alpak\\Documents\\PagentOS Projects\\gorev-takip",
      created_at: "2026-09-08T09:00:00Z",
      updated_at: "2026-09-08T09:01:00Z",
    });

    // The body as a bare list, ids and the port under their other names, the report as its JSON text.
    apiFetch.mockResolvedValueOnce(json(200, [{ app_id: "p2", port: 8200, test_report_json: '{"passed": 3, "failed": 1}' }]));
    const bare = await fetchApps();
    expect(bare.kind).toBe("ok");
    if (bare.kind !== "ok") return;
    expect(bare.value[0]).toEqual({
      app_id: "p2",
      name: null,
      kind: null,
      template: null,
      state: null,
      port: 8200,
      tests: { passed: 3, failed: 1 },
      root_path: null,
      created_at: null,
      updated_at: null,
    });

    apiFetch.mockResolvedValueOnce(json(200, { projects: [{ project_id: "p3", state: "planned" }] }));
    const projects = await fetchApps();
    if (projects.kind !== "ok") throw new Error("expected ok");
    expect(projects.value[0].app_id).toBe("p3");
    expect(projects.value[0].state).toBe("planned");
  });

  it("answers absent for a list route this Cloud Core does not have, and failed for a broken one", async () => {
    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const absent = await fetchApps();
    expect(absent.kind).toBe("absent");
    if (absent.kind === "absent") expect(absent.detail).toContain("/v1/apps");
    apiFetch.mockResolvedValueOnce(new Response("", { status: 500 }));
    expect(await fetchApps()).toEqual({ kind: "failed", error: "HTTP 500" });
  });

  it("POST /v1/apps/{id}/run, /stop and /test, once each, with the id as a segment and no body", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { state: "running", port: 8123 }));
    const running = await appsClient.run("p 1");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    let [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/apps/p%201/run");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined(); // which command key, on which port, in which job, is the Cloud Core's
    expect(running).toEqual({ state: "running", port: 8123, tests: null, summary: null, receiptId: null });

    apiFetch.mockResolvedValueOnce(json(200, { stopped: true, state: "stopped" }));
    const stopped = await appsClient.stop("p1");
    [path, init] = apiFetch.mock.calls[1] as [string, RequestInit];
    expect(path).toBe("/v1/apps/p1/stop");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(stopped.state).toBe("stopped");

    apiFetch.mockResolvedValueOnce(json(200, { state: "tested", result: { exit_code: 0, passed: 12, failed: 0, report_tail: "12 passing" } }));
    const tested = await appsClient.test("p1");
    [path, init] = apiFetch.mock.calls[2] as [string, RequestInit];
    expect(path).toBe("/v1/apps/p1/test");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(tested).toEqual({ state: "tested", port: null, tests: { passed: 12, failed: 0 }, summary: "12 passing", receiptId: null });

    expect(apiFetch).toHaveBeenCalledTimes(3);
    // The client object the page hands the hook is these three calls and no other.
    expect(Object.keys(appsClient)).toEqual(["run", "stop", "test"]);
  });

  it("reads the receipt from any of its shapes, and never invents a state, a port or a count", async () => {
    expect(
      parseAppReceipt({
        state: "running",
        receipt: { receipt_id: "r1", factual_summary: "Görev Takip 8123 portunda çalışıyor." },
        result: { pid: 4242, port: 8123, url: "http://127.0.0.1:8123/" },
      }),
    ).toEqual({ state: "running", port: 8123, tests: null, summary: "Görev Takip 8123 portunda çalışıyor.", receiptId: "r1" });
    expect(parseAppReceipt({ receipt: { id: "r2", status: "stopped", speech: "Durdurdum." } })).toEqual({
      state: "stopped",
      port: null,
      tests: null,
      summary: "Durdurdum.",
      receiptId: "r2",
    });
    // The project row itself as the answer.
    expect(parseAppReceipt({ project: { ...ROW, state: "tested", run_port: null } })).toEqual({
      state: "tested",
      port: null,
      tests: { passed: 12, failed: 0 },
      summary: null,
      receiptId: null,
    });
    // The flat pair a flattening publisher sends.
    expect(parseAppReceipt({ state: "tested", tests_passed: 5, tests_failed: 1 }).tests).toEqual({ passed: 5, failed: 1 });
    // The device's own `project.test` result as the body: the report is the answer (regression: the
    // first parser read `tests` and the row's report column but never the result's own two counts).
    expect(parseAppReceipt({ exit_code: 1, passed: 10, failed: 2, report_tail: "2 failing" })).toEqual({
      state: null,
      port: null,
      tests: { passed: 10, failed: 2 },
      summary: null,
      receiptId: null,
    });
    // A port that is not one, a count that is half: nothing.
    expect(parseAppReceipt({ state: "running", port: 0 }).port).toBeNull();
    expect(parseAppReceipt({ state: "tested", result: { passed: 12 } }).tests).toBeNull();
    expect(parseAppReceipt({ message: "İletildi." })).toEqual({ state: null, port: null, tests: null, summary: "İletildi.", receiptId: null });
    expect(parseAppReceipt(null)).toEqual({ state: null, port: null, tests: null, summary: null, receiptId: null });
    expect(parseAppReceipt("running")).toEqual({ state: null, port: null, tests: null, summary: null, receiptId: null });

    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await appAction("p1", "run")).toEqual({ state: null, port: null, tests: null, summary: null, receiptId: null });
  });

  it("turns a refusal into a typed error with the Cloud Core's code, and into the owner's words", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: { code: "too_many_running", message: "2 projects already running" } }));
    const err = await appAction("p1", "run").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AppActionError);
    const typed = err as AppActionError;
    expect(typed.status).toBe(409);
    expect(typed.code).toBe("too_many_running");
    expect(typed.detail).toBe("2 projects already running");
    expect(appActionErrorText(typed)).toBe(APP_ACTION_REFUSAL_TR.too_many_running);
    expect(appActionErrorText(typed)).toContain("başlatılmadı");

    apiFetch.mockResolvedValueOnce(json(422, { error_class: "command_not_allowed" }));
    const flat = (await appAction("p1", "run").catch((e: unknown) => e)) as AppActionError;
    expect(flat.code).toBe("command_not_allowed");
    expect(appActionErrorText(flat)).toBe(APP_ACTION_REFUSAL_TR.command_not_allowed);

    apiFetch.mockResolvedValueOnce(json(400, { detail: { code: "strange", message: "açıklama" } }));
    const strange = (await appAction("p1", "test").catch((e: unknown) => e)) as AppActionError;
    expect(appActionErrorText(strange)).toBe("strange: açıklama");

    apiFetch.mockResolvedValueOnce(new Response("not json", { status: 503 }));
    const opaque = (await appAction("p1", "stop").catch((e: unknown) => e)) as AppActionError;
    expect(opaque.code).toBeNull();
    expect(appActionErrorText(opaque)).toBe("HTTP 503");
    expect(appActionErrorText(new Error("ağ koptu"))).toBe("ağ koptu");
    // Every refusal sentence says what did NOT happen, and none says it is running.
    for (const [code, text] of Object.entries(APP_ACTION_REFUSAL_TR)) {
      expect(text, code).toMatch(/yapılmadı|başlatılmadı|başlatılamadı|durdurulamadı|çalıştırılmadı|çalıştırılamadı|bilinmiyor|yok/i);
      expect(text, code).not.toMatch(/(^|[^a-zçğıöşü])çalışıyor[^;]*$/i);
    }
  });

  it("tells a route this Cloud Core does not have apart from a project it does not know", async () => {
    // FastAPI's bare 404 for a missing route: the route is not there yet.
    apiFetch.mockResolvedValueOnce(json(404, { detail: "Not Found" }));
    const absent = (await appAction("p1", "run").catch((e: unknown) => e)) as AppActionError;
    expect(absent.code).toBe(APP_ROUTE_ABSENT);
    expect(appActionErrorText(absent)).toBe("Bu Cloud Core sürümünde /v1/apps/p1/run yok (HTTP 404). Yapılmadı.");

    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const empty = (await appAction("p1", "stop").catch((e: unknown) => e)) as AppActionError;
    expect(empty.code).toBe(APP_ROUTE_ABSENT);

    // The route answering 404 in its own words: the project is unknown, the route exists.
    apiFetch.mockResolvedValueOnce(json(404, { detail: "unknown project" }));
    const unknown = (await appAction("p9", "run").catch((e: unknown) => e)) as AppActionError;
    expect(unknown.code).toBeNull();
    expect(appActionErrorText(unknown)).toBe("unknown project");

    apiFetch.mockResolvedValueOnce(json(404, { detail: { code: "not_found", message: "no such project" } }));
    const coded = (await appAction("p9", "run").catch((e: unknown) => e)) as AppActionError;
    expect(coded.code).toBe("not_found");
    expect(appActionErrorText(coded)).toBe(APP_ACTION_REFUSAL_TR.not_found);
  });

  it("parses rows defensively: no id is no project, a port that is not one is none, counts need both figures", () => {
    expect(parseAppRow(null)).toBeNull();
    expect(parseAppRow("p1")).toBeNull();
    expect(parseAppRow({ name: "x" })).toBeNull();
    expect(parseAppRow({ id: "p1", run_port: "8123", test_report_json: "not json" })).toEqual({
      app_id: "p1",
      name: null,
      kind: null,
      template: null,
      state: null,
      port: null,
      tests: null,
      root_path: null,
      created_at: null,
      updated_at: null,
    });
    expect(parseAppRow({ id: "p1", run_port: 70_000 })?.port).toBeNull();
    expect(parseAppRow({ id: "p1", run_port: 0 })?.port).toBeNull();
    expect(parseAppRow({ id: "p1", test_report_json: { passed: 12 } })?.tests).toBeNull();
    expect(parseAppRow({ id: "p1", tests: { passed: 12, failed: 0 } })?.tests).toEqual({ passed: 12, failed: 0 });
    expect(parseAppRow({ id: "p1", tests_passed: 2, tests_failed: 0 })?.tests).toEqual({ passed: 2, failed: 0 });
    // The contract's shape wins over the report, the report over the flat pair.
    expect(testCountsOf({ tests: { passed: 1, failed: 0 }, test_report_json: { passed: 9, failed: 9 } })).toEqual({ passed: 1, failed: 0 });
    expect(testCountsOf({ test_report_json: { passed: 9, failed: 9 }, tests_passed: 1, tests_failed: 1 })).toEqual({ passed: 9, failed: 9 });
    expect(testCountsOf({})).toBeNull();
  });
});

describe("the rows", () => {
  it("a project is running because its row says so, and only then does it get a link — on the loopback, from its port", () => {
    const running = parseAppRow(ROW);
    expect(running).not.toBeNull();
    if (!running) return;
    expect(rowIsRunning(running)).toBe(true);
    expect(appRowUrl(running)).toBe("http://127.0.0.1:8123/");
    expect(appRowLine(running)).toBe("çalışıyor · port: 8123 · testler: 12 geçti / 0 başarısız");
    // Stopped with its last port: no link, nothing is listening.
    expect(appRowUrl({ state: "stopped", port: 8123 })).toBeNull();
    // Running with no port: no link, nothing to point at.
    expect(appRowUrl({ state: "running", port: null })).toBeNull();
    // A newer server's word buys no link.
    expect(appRowUrl({ state: "serving", port: 8123 })).toBeNull();
    expect(rowIsRunning({ state: "RUNNING" })).toBe(false);
  });

  it("lines each row with its state, the port beside running, the counts beside a result", () => {
    const base = parseAppRow({ id: "p1" });
    if (!base) throw new Error("fixture");
    expect(appRowLine({ ...base, state: "scaffolded" })).toBe("iskeleti kuruluyor");
    expect(appRowLine({ ...base, state: "running" })).toBe("çalışıyor · port bildirilmedi");
    expect(appRowLine({ ...base, state: "tested" })).toBe("test edildi · test sayısı bildirilmedi");
    expect(appRowLine({ ...base, state: "tested", tests: { passed: 12, failed: 0 } })).toBe("test edildi · testler: 12 geçti / 0 başarısız");
    expect(appRowLine({ ...base, state: "failed", tests: { passed: 10, failed: 2 } })).toBe("başarısız · testler: 10 geçti / 2 başarısız");
    expect(appRowLine({ ...base, state: "stopped", port: 8123 })).toBe("durduruldu · port: 8123");
    expect(appRowLine({ ...base, state: null })).toBe("durum bildirilmedi");
    expect(appRowLine({ ...base, state: "built" })).toBe("built");
  });

  it("names the spec's kinds in the owner's words and any other verbatim, and the chips in the spec's order", () => {
    expect(APP_KIND_LABEL).toEqual({ web_static: "statik web", web_api: "web API", cli: "komut satırı" });
    expect(appKindLabel("cli")).toBe("komut satırı");
    expect(appKindLabel("desktop")).toBe("desktop");
    expect(appKindLabel(null)).toBeNull();
    expect(APP_ACTION_LABEL).toEqual({ run: "Çalıştır", stop: "Durdur", test: "Testleri çalıştır" });
  });

  it("the gate opens each chip only for a row the Cloud Core would not refuse, while nothing is in flight", () => {
    const busy = { action: "run" as const, id: "p9" };
    for (const state of ["planned", "scaffolded", "running", "tested", "failed", "stopped", null, "built"]) {
      for (const action of APP_ACTIONS) {
        expect(appActionGate({ state }, action, busy), `${state}/${action}`).toEqual({ enabled: false, reason: APP_REASON_BUSY, reasonKind: "busy" });
      }
    }
    const open = { enabled: true, reason: null, reasonKind: null };
    // run
    expect(appActionGate({ state: "scaffolded" }, "run", null)).toEqual(open);
    expect(appActionGate({ state: "tested" }, "run", null)).toEqual(open);
    expect(appActionGate({ state: "failed" }, "run", null)).toEqual(open);
    expect(appActionGate({ state: "stopped" }, "run", null)).toEqual(open);
    expect(appActionGate({ state: null }, "run", null)).toEqual(open);
    expect(appActionGate({ state: "built" }, "run", null)).toEqual(open);
    expect(appActionGate({ state: "running" }, "run", null)).toEqual({ enabled: false, reason: APP_REASON_ALREADY_RUNNING, reasonKind: "already_running" });
    expect(appActionGate({ state: "planned" }, "run", null)).toEqual({ enabled: false, reason: APP_REASON_NOT_SCAFFOLDED, reasonKind: "not_scaffolded" });
    // stop
    expect(appActionGate({ state: "running" }, "stop", null)).toEqual(open);
    for (const state of ["planned", "scaffolded", "tested", "failed", "stopped", null, "built"]) {
      expect(appActionGate({ state }, "stop", null), `${state}/stop`).toEqual({ enabled: false, reason: APP_REASON_NOT_RUNNING, reasonKind: "not_running" });
    }
    // test
    for (const state of ["scaffolded", "running", "tested", "failed", "stopped", null, "built"]) {
      expect(appActionGate({ state }, "test", null), `${state}/test`).toEqual(open);
    }
    expect(appActionGate({ state: "planned" }, "test", null)).toEqual({ enabled: false, reason: APP_REASON_NOT_SCAFFOLDED, reasonKind: "not_scaffolded" });
  });
});
