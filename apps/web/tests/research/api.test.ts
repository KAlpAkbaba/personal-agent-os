import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  NO_CAPABLE_DEVICE,
  NO_CAPABLE_DEVICE_HINT,
  ResearchApiError,
  cancelResearch,
  explainError,
  fetchReportJson,
  getResearchTask,
  listDevices,
  listResearchTasks,
  pauseResearch,
  previewSelection,
  resumeResearch,
  startResearch,
} from "../../app/lib/research/api";
import { DEVICES, REPORT, taskAt } from "./fixtures";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("startResearch", () => {
  it("posts the spec §4 body and returns the 202 payload", async () => {
    apiFetch.mockResolvedValueOnce(
      json(202, { task_id: "t1", workflow_id: "research-browser-t1", status: "planned", device: { device_id: "dev-home", name: "Ev" } }),
    );
    const started = await startResearch({ input: "konu", target_device: "ev", recency_days: 3, max_sources: 12 });
    expect(started.task_id).toBe("t1");
    expect(started.device?.name).toBe("Ev");
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/research");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ input: "konu", target_device: "ev", recency_days: 3, max_sources: 12 });
  });

  it("omits the target when automatic selection is chosen", async () => {
    apiFetch.mockResolvedValueOnce(json(202, { task_id: "t1", status: "planned", device: null }));
    await startResearch({ input: "konu", target_device: null });
    const [, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ input: "konu" });
  });

  it("sends the research mode only when the owner chose one (B31 req 192)", async () => {
    apiFetch.mockResolvedValueOnce(json(202, { task_id: "t1", status: "planned", device: null }));
    await startResearch({ input: "konu", research_mode: "deep" });
    const [, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ input: "konu", research_mode: "deep" });
  });

  it("pauses and resumes a run through the two B31 routes (req 203/204)", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { task_id: "t1", status: "paused" }));
    await pauseResearch("t 1");
    expect(apiFetch.mock.calls[0]).toEqual(["/v1/research/t%201/pause", { method: "POST" }]);
    apiFetch.mockResolvedValueOnce(json(200, { task_id: "t1", status: "resumed" }));
    await resumeResearch("t1");
    expect(apiFetch.mock.calls[1]).toEqual(["/v1/research/t1/resume", { method: "POST" }]);
    apiFetch.mockResolvedValueOnce(json(409, { detail: "research is not paused" }));
    await expect(resumeResearch("t1")).rejects.toThrow("research is not paused");
  });

  it("sends interactive (owner-handoff mode) only when explicitly set", async () => {
    apiFetch.mockResolvedValueOnce(json(202, { task_id: "t1", status: "planned", device: null }));
    await startResearch({ input: "konu", interactive: true, interactive_wait_s: 120 });
    const [, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      input: "konu", interactive: true, interactive_wait_s: 120,
    });
  });

  it("turns a 409 no_capable_device into a typed error with the Turkish detail explained", async () => {
    apiFetch.mockResolvedValueOnce(
      json(409, { detail: "browser.chrome yeteneğine sahip çevrimiçi cihaz yok." }),
    );
    const err = await startResearch({ input: "konu" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ResearchApiError);
    const apiErr = err as ResearchApiError;
    expect(apiErr.status).toBe(409);
    expect(apiErr.code).toBe(NO_CAPABLE_DEVICE);
    expect(apiErr.detail).toBe("browser.chrome yeteneğine sahip çevrimiçi cihaz yok.");
    const text = explainError(err);
    expect(text).toContain("browser.chrome yeteneğine sahip çevrimiçi cihaz yok.");
    expect(text).toContain(NO_CAPABLE_DEVICE_HINT);
    expect(text).toContain("Windows ajanının çalıştığından");
  });

  it("reads a structured detail body too", async () => {
    apiFetch.mockResolvedValueOnce(
      json(409, { detail: { code: "no_capable_device", message: "Uygun cihaz yok." } }),
    );
    const err = (await startResearch({ input: "konu" }).catch((e: unknown) => e)) as ResearchApiError;
    expect(err.code).toBe("no_capable_device");
    expect(err.detail).toBe("Uygun cihaz yok.");
  });

  it("keeps other errors as their own Turkish detail", async () => {
    apiFetch.mockResolvedValueOnce(json(422, { detail: "Konu boş olamaz." }));
    const err = (await startResearch({ input: "" }).catch((e: unknown) => e)) as ResearchApiError;
    expect(err.code).toBeNull();
    expect(explainError(err)).toBe("Konu boş olamaz.");
    apiFetch.mockResolvedValueOnce(new Response("", { status: 500 }));
    const err2 = await startResearch({ input: "x" }).catch((e: unknown) => e);
    expect(explainError(err2)).toBe("API hatası (HTTP 500)");
  });
});

describe("devices", () => {
  it("lists devices and previews selection with the browser capability", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { devices: DEVICES }));
    const devices = await listDevices();
    expect(devices.map((d) => d.device_id)).toEqual(["dev-home", "dev-laptop", "dev-old"]);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/devices");

    apiFetch.mockResolvedValueOnce(json(200, { device: { device_id: "dev-home", name: "Ev bilgisayarı" }, reason: "healthiest" }));
    const auto = await previewSelection(null);
    expect(auto).toEqual({ kind: "device", device_id: "dev-home", name: "Ev bilgisayarı", reason: "healthiest" });
    const [path, init] = apiFetch.mock.calls[1] as [string, RequestInit];
    expect(path).toBe("/v1/devices/select");
    expect(JSON.parse(init.body as string)).toEqual({ capability: "browser.chrome" });

    apiFetch.mockResolvedValueOnce(json(200, { device: { device_id: "dev-home", name: "Ev bilgisayarı" } }));
    await previewSelection("ev");
    const [, init2] = apiFetch.mock.calls[2] as [string, RequestInit];
    expect(JSON.parse(init2.body as string)).toEqual({ capability: "browser.chrome", target: "ev" });
  });

  it("treats a 409 on preview as an answer, not a failure", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: "Uygun cihaz yok." }));
    const preview = await previewSelection("dev-laptop");
    expect(preview.kind).toBe("none");
    if (preview.kind === "none") {
      expect(preview.detail).toContain("Uygun cihaz yok.");
      expect(preview.detail).toContain(NO_CAPABLE_DEVICE_HINT);
    }
  });
});

describe("tasks", () => {
  it("lists, reads, cancels and fetches the report JSON", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { tasks: [{ task_id: "t2", topic: "b", status: "READY", stage: "ready", device: null }] }));
    expect((await listResearchTasks()).map((t) => t.task_id)).toEqual(["t2"]);

    apiFetch.mockResolvedValueOnce(json(200, taskAt("fetching")));
    const task = await getResearchTask("task 1");
    expect(task.stage).toBe("fetching");
    expect(apiFetch.mock.calls[1][0]).toBe("/v1/research/task%201");

    apiFetch.mockResolvedValueOnce(json(200, { ok: true }));
    await cancelResearch("task-1");
    expect(apiFetch.mock.calls[2]).toEqual(["/v1/research/task-1/cancel", { method: "POST" }]);

    apiFetch.mockResolvedValueOnce(json(200, REPORT));
    const text = await fetchReportJson("task-1");
    expect(apiFetch.mock.calls[3][0]).toBe("/v1/research/task-1/report");
    expect(JSON.parse(text)).toEqual(REPORT);
    expect(text).toContain("\n  "); // pretty-printed for the clipboard

    apiFetch.mockResolvedValueOnce(json(404, { detail: "Rapor henüz hazır değil." }));
    await expect(fetchReportJson("task-1")).rejects.toThrow("Rapor henüz hazır değil.");
  });
});
