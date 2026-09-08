/**
 * The genesis client (M24 spec §8): three routes, exactly, through the
 * owner session — and the rows and receipts read from whatever shape the
 * Cloud Core answers with, never filled in.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  GENESIS_ACTIONS,
  GENESIS_ACTION_REFUSAL_TR,
  GENESIS_ROUTE_ABSENT,
  GENESIS_RUNS_PATH,
  GenesisActionError,
  fetchGenesisRuns,
  genesisAction,
  genesisActionErrorText,
  genesisActionPath,
  genesisClient,
  genesisRunsUrl,
  parseGenesisReceipt,
  parseGenesisRow,
} from "../../app/lib/cockpit/genesis";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

/** One `genesis_runs` row as spec §5 lists its columns. */
const ROW = {
  id: "g1",
  capability_id: "counterbox.increment",
  gap_id: "gap-1",
  state: "awaiting_approval",
  interface_json: { name: "counterbox", base_url: "http://127.0.0.1:8765", operations: [] },
  authority_class: "mutating_unauthorized",
  side_effect_class: "mutate_external",
  approval_required: true,
  approval_ref: null,
  skill_version_id: null,
  evidence_json: null,
  error_class: null,
  error_message: null,
  created_at: "2026-09-08T09:00:00Z",
  updated_at: "2026-09-08T09:01:00Z",
  session_id: "sess-1",
};

describe("the three routes, exactly", () => {
  it("names them once", () => {
    expect(GENESIS_RUNS_PATH).toBe("/v1/genesis/runs");
    expect(GENESIS_ACTIONS).toEqual(["approve", "cancel"]);
    expect(genesisActionPath("g1", "approve")).toBe("/v1/genesis/runs/g1/approve");
    expect(genesisActionPath("g1", "cancel")).toBe("/v1/genesis/runs/g1/cancel");
    expect(genesisRunsUrl()).toBe("http://core.test:8001/v1/genesis/runs");
    // An id is a path segment, never a path.
    expect(genesisActionPath("g 1/../x", "approve")).toBe("/v1/genesis/runs/g%201%2F..%2Fx/approve");
  });

  it("GET /v1/genesis/runs, and reads the rows from the shape the route answers with — the columns, never the interface", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { runs: [ROW, { no_id: true }] }));
    const loaded = await fetchGenesisRuns();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit | undefined];
    expect(path).toBe("/v1/genesis/runs");
    expect(init?.method ?? "GET").toBe("GET");
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(1); // a row with no id is not a run
    expect(loaded.value[0]).toEqual({
      run_id: "g1",
      capability: "counterbox.increment",
      state: "awaiting_approval",
      approval_required: true,
      authority_class: "mutating_unauthorized",
      side_effect_class: "mutate_external",
      error_class: null,
      error_message: null,
      created_at: "2026-09-08T09:00:00Z",
      updated_at: "2026-09-08T09:01:00Z",
    });

    // The body as a bare list, the id and the capability under their other names, a failure with its message.
    apiFetch.mockResolvedValueOnce(
      json(200, [{ run_id: "g2", capability: "lampbox.set", state: "failed", error_class: "dependency_unavailable", error_message: "no answer" }]),
    );
    const bare = await fetchGenesisRuns();
    expect(bare.kind).toBe("ok");
    if (bare.kind !== "ok") return;
    expect(bare.value[0]).toEqual({
      run_id: "g2",
      capability: "lampbox.set",
      state: "failed",
      approval_required: null,
      authority_class: null,
      side_effect_class: null,
      error_class: "dependency_unavailable",
      error_message: "no answer",
      created_at: null,
      updated_at: null,
    });

    apiFetch.mockResolvedValueOnce(json(200, { items: [{ id: "g3", state: "verified" }] }));
    const items = await fetchGenesisRuns();
    if (items.kind !== "ok") throw new Error("expected ok");
    expect(items.value[0].run_id).toBe("g3");
    expect(items.value[0].state).toBe("verified");
  });

  it("answers absent for a list route this Cloud Core does not have, and failed for a broken one", async () => {
    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const absent = await fetchGenesisRuns();
    expect(absent.kind).toBe("absent");
    if (absent.kind === "absent") expect(absent.detail).toContain("/v1/genesis/runs");
    apiFetch.mockResolvedValueOnce(new Response("", { status: 500 }));
    expect(await fetchGenesisRuns()).toEqual({ kind: "failed", error: "HTTP 500" });
  });

  it("POST /v1/genesis/runs/{id}/approve and /cancel, once each, with the id as a segment and no body", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { state: "rolling_out", receipt: { receipt_id: "r1", factual_summary: "Yetkilendirme kaydedildi." } }));
    const approved = await genesisClient.approve("g 1");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    let [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/genesis/runs/g%201/approve");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined(); // which asset, bound to which session and turn, is the Cloud Core's
    expect(approved).toEqual({ state: "rolling_out", errorClass: null, summary: "Yetkilendirme kaydedildi.", receiptId: "r1" });

    apiFetch.mockResolvedValueOnce(json(200, { cancelled: true, run: { state: "failed", error_class: "cancelled" } }));
    const cancelled = await genesisClient.cancel("g1");
    [path, init] = apiFetch.mock.calls[1] as [string, RequestInit];
    expect(path).toBe("/v1/genesis/runs/g1/cancel");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(cancelled).toEqual({ state: "failed", errorClass: "cancelled", summary: null, receiptId: null });

    expect(apiFetch).toHaveBeenCalledTimes(2);
    // The client object the page hands the hook is these two calls and no other.
    expect(Object.keys(genesisClient)).toEqual(["approve", "cancel"]);
  });

  it("reads the receipt from any of its shapes, and never invents a state", async () => {
    expect(parseGenesisReceipt({ receipt: { id: "r2", status: "registering", speech: "Kaydediyorum." } })).toEqual({
      state: "registering",
      errorClass: null,
      summary: "Kaydediyorum.",
      receiptId: "r2",
    });
    // The run row itself as the answer.
    expect(parseGenesisReceipt({ run: { ...ROW, state: "failed", error_class: "timeout", error_message: "sandbox timed out" } })).toEqual({
      state: "failed",
      errorClass: "timeout",
      summary: "sandbox timed out",
      receiptId: null,
    });
    expect(parseGenesisReceipt({ message: "İletildi." })).toEqual({ state: null, errorClass: null, summary: "İletildi.", receiptId: null });
    expect(parseGenesisReceipt(null)).toEqual({ state: null, errorClass: null, summary: null, receiptId: null });
    expect(parseGenesisReceipt("approved")).toEqual({ state: null, errorClass: null, summary: null, receiptId: null });

    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await genesisAction("g1", "approve")).toEqual({ state: null, errorClass: null, summary: null, receiptId: null });
  });

  it("turns a refusal into a typed error with the Cloud Core's code, and into the owner's words", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: { code: "not_awaiting_approval", message: "run is in state rolling_out" } }));
    const err = await genesisAction("g1", "approve").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(GenesisActionError);
    const typed = err as GenesisActionError;
    expect(typed.status).toBe(409);
    expect(typed.code).toBe("not_awaiting_approval");
    expect(typed.detail).toBe("run is in state rolling_out");
    expect(genesisActionErrorText(typed)).toBe(GENESIS_ACTION_REFUSAL_TR.not_awaiting_approval);
    expect(genesisActionErrorText(typed)).toContain("onaylanmadı");

    apiFetch.mockResolvedValueOnce(json(403, { error_class: "confirmation_required" }));
    const flat = (await genesisAction("g1", "approve").catch((e: unknown) => e)) as GenesisActionError;
    expect(flat.code).toBe("confirmation_required");
    expect(genesisActionErrorText(flat)).toBe(GENESIS_ACTION_REFUSAL_TR.confirmation_required);

    apiFetch.mockResolvedValueOnce(json(400, { detail: { code: "strange", message: "açıklama" } }));
    const strange = (await genesisAction("g1", "cancel").catch((e: unknown) => e)) as GenesisActionError;
    expect(genesisActionErrorText(strange)).toBe("strange: açıklama");

    apiFetch.mockResolvedValueOnce(new Response("not json", { status: 503 }));
    const opaque = (await genesisAction("g1", "cancel").catch((e: unknown) => e)) as GenesisActionError;
    expect(opaque.code).toBeNull();
    expect(genesisActionErrorText(opaque)).toBe("HTTP 503");
    expect(genesisActionErrorText(new Error("ağ koptu"))).toBe("ağ koptu");
    // Every refusal sentence says what did NOT happen, and none says the capability exists.
    for (const [code, text] of Object.entries(GENESIS_ACTION_REFUSAL_TR)) {
      expect(text, code).toMatch(/onaylanmadı|vazgeçilmedi|kaydedilmedi|sürmedi|bilinmiyor|yok/i);
      expect(text, code).not.toMatch(/kullanılabilir|doğrulandı/);
    }
  });

  it("tells a route this Cloud Core does not have apart from a run it does not know", async () => {
    // FastAPI's bare 404 for a missing route: the route is not there yet.
    apiFetch.mockResolvedValueOnce(json(404, { detail: "Not Found" }));
    const absent = (await genesisAction("g1", "approve").catch((e: unknown) => e)) as GenesisActionError;
    expect(absent.code).toBe(GENESIS_ROUTE_ABSENT);
    expect(genesisActionErrorText(absent)).toBe("Bu Cloud Core sürümünde /v1/genesis/runs/g1/approve yok (HTTP 404). Yapılmadı.");

    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const empty = (await genesisAction("g1", "cancel").catch((e: unknown) => e)) as GenesisActionError;
    expect(empty.code).toBe(GENESIS_ROUTE_ABSENT);

    // The route answering 404 in its own words: the run is unknown, the route exists.
    apiFetch.mockResolvedValueOnce(json(404, { detail: "unknown run" }));
    const unknown = (await genesisAction("g9", "approve").catch((e: unknown) => e)) as GenesisActionError;
    expect(unknown.code).toBeNull();
    expect(genesisActionErrorText(unknown)).toBe("unknown run");

    apiFetch.mockResolvedValueOnce(json(404, { detail: { code: "not_found", message: "no such run" } }));
    const coded = (await genesisAction("g9", "approve").catch((e: unknown) => e)) as GenesisActionError;
    expect(coded.code).toBe("not_found");
    expect(genesisActionErrorText(coded)).toBe(GENESIS_ACTION_REFUSAL_TR.not_found);
  });

  it("parses rows defensively: no id is no run, a flag that is not one is none, nothing content-shaped rides along", () => {
    expect(parseGenesisRow(null)).toBeNull();
    expect(parseGenesisRow("g1")).toBeNull();
    expect(parseGenesisRow({ state: "building" })).toBeNull();
    expect(parseGenesisRow({ id: "g1", approval_required: "true", interface_json: { operations: [] }, evidence_json: { read_back: 4 } })).toEqual({
      run_id: "g1",
      capability: null,
      state: null,
      approval_required: null,
      authority_class: null,
      side_effect_class: null,
      error_class: null,
      error_message: null,
      created_at: null,
      updated_at: null,
    });
    expect(parseGenesisRow({ id: "g1", approval_required: false })?.approval_required).toBe(false);
    // `capability_id` wins over `capability` when both are sent: it is the column's name.
    expect(parseGenesisRow({ id: "g1", capability_id: "a.b", capability: "c.d" })?.capability).toBe("a.b");
  });
});
