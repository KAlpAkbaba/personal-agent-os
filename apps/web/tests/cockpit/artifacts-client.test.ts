/**
 * The artifact client (M22 spec §4): three routes, exactly, through the
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
  ARTIFACTS_PATH,
  ARTIFACT_OPEN_REFUSAL_TR,
  ArtifactOpenError,
  OPEN_ROUTE_ABSENT,
  RENDER_URL_REVOKE_MS,
  artifactClient,
  artifactOpenErrorText,
  artifactOpenPath,
  artifactRenderPath,
  artifactRenderUrl,
  downloadRender,
  fetchArtifacts,
  fetchRenderBlob,
  openArtifact,
  parseArtifact,
  parseOpenReceipt,
  parseRender,
} from "../../app/lib/cockpit/artifacts";
import {
  ARTIFACT_OPEN_REASON_BUSY,
  ARTIFACT_OPEN_REASON_NO_VALID_RENDER,
  artifactKindLabel,
  artifactOpenGate,
  artifactRenderLine,
  renderSizeLabel,
  validRenders,
} from "../../app/lib/cockpit/artifact-rows";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

const XLSX = { format: "xlsx", mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", content_hash: "h1", size_bytes: 12_288, state: "valid" };
const PDF_INVALID = {
  format: "pdf",
  mime_type: "application/pdf",
  content_hash: "h2",
  size_bytes: 30_000,
  state: "invalid",
  validation_json: { ok: false, elements: [{ ref: "sheet:Ozet!B2", ok: true }, { ref: "sheet:Ozet!B5", expected: 65000, found: null, ok: false }] },
};
const DOCX_M13 = { format: "docx", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", content_hash: "h3", size_bytes: 8_000 };

const ROW = {
  artifact_id: "a1",
  task_id: null,
  title: "Bütçe 2026",
  kind: "spreadsheet",
  canonical_format: "markdown",
  state: "READY",
  current_version: 1,
  executive_summary: null,
  content_hash: "c1",
  available_renders: [XLSX, PDF_INVALID, DOCX_M13],
  created_at: "2026-09-08T09:00:00Z",
  updated_at: "2026-09-08T09:01:00Z",
};

describe("the three routes, exactly", () => {
  it("names them once", () => {
    expect(ARTIFACTS_PATH).toBe("/v1/artifacts");
    expect(artifactRenderPath("a1", "xlsx")).toBe("/v1/artifacts/a1/renders/xlsx");
    expect(artifactOpenPath("a1")).toBe("/v1/artifacts/a1/open");
    // The link's address is the API's origin and the gated path — M13's download, not a copy of it.
    expect(artifactRenderUrl("a1", "xlsx")).toBe("http://core.test:8001/v1/artifacts/a1/renders/xlsx");
    // An id and a format are path segments, never a path.
    expect(artifactRenderPath("a 1/../x", "x/y")).toBe("/v1/artifacts/a%201%2F..%2Fx/renders/x%2Fy");
    expect(artifactOpenPath("a 1")).toBe("/v1/artifacts/a%201/open");
  });

  it("GET /v1/artifacts, and reads the rows from the shape the route answers with", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { artifacts: [ROW, { no_id: true }] }));
    const loaded = await fetchArtifacts();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit | undefined];
    expect(path).toBe("/v1/artifacts");
    expect(init?.method ?? "GET").toBe("GET");
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(1); // a row with no id is not an artifact
    expect(loaded.value[0]).toEqual({
      artifact_id: "a1",
      title: "Bütçe 2026",
      kind: "spreadsheet",
      state: "READY",
      created_at: "2026-09-08T09:00:00Z",
      updated_at: "2026-09-08T09:01:00Z",
      renders: [
        { format: "xlsx", mime_type: XLSX.mime_type, size_bytes: 12_288, content_hash: "h1", state: "valid", failing_ref: null },
        // The failing ref is read from the report the row carries: the first element marked not ok.
        { format: "pdf", mime_type: "application/pdf", size_bytes: 30_000, content_hash: "h2", state: "invalid", failing_ref: "sheet:Ozet!B5" },
        // An M13 render the factory never validated: no state, and no verdict invented for it.
        { format: "docx", mime_type: DOCX_M13.mime_type, size_bytes: 8_000, content_hash: "h3", state: null, failing_ref: null },
      ],
    });

    // The body as a bare list, ids and renders under their other names.
    apiFetch.mockResolvedValueOnce(json(200, [{ id: "a2", renders: [{ format: "csv", validation_state: "valid" }] }]));
    const bare = await fetchArtifacts();
    expect(bare.kind).toBe("ok");
    if (bare.kind !== "ok") return;
    expect(bare.value[0].artifact_id).toBe("a2");
    expect(bare.value[0].title).toBeNull();
    expect(bare.value[0].renders).toEqual([{ format: "csv", mime_type: null, size_bytes: null, content_hash: null, state: "valid", failing_ref: null }]);
  });

  it("answers absent for a list route this Cloud Core does not have, and failed for a broken one", async () => {
    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const absent = await fetchArtifacts();
    expect(absent.kind).toBe("absent");
    if (absent.kind === "absent") expect(absent.detail).toContain("/v1/artifacts");
    apiFetch.mockResolvedValueOnce(new Response("", { status: 500 }));
    const failedState = await fetchArtifacts();
    expect(failedState).toMatchObject({ kind: "failed", error: "HTTP 500" });
    // B22 req 708-711: and it now carries WHICH failure this was, so a panel can tell
    // "try again" from "waiting on a key". A 500 with no class stays a plain failure.
    expect(failedState.kind === "failed" && failedState.failure?.kind).toBe("failed");
  });

  it("GET /v1/artifacts/{id}/renders/{fmt} for the bytes, through the session, and a non-2xx is an error, never an empty file", async () => {
    apiFetch.mockResolvedValueOnce(new Response(new Uint8Array([80, 75, 3, 4]), { status: 200, headers: { "Content-Type": XLSX.mime_type } }));
    const blob = await fetchRenderBlob("a1", "xlsx");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/artifacts/a1/renders/xlsx");
    expect(blob.size).toBe(4);

    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    await expect(fetchRenderBlob("a1", "pptx")).rejects.toThrow("HTTP 404");
  });

  it("the download hands the browser a blob URL of those bytes, and revokes it after its minute", async () => {
    vi.useFakeTimers();
    try {
      apiFetch.mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), { status: 200 }));
      const open = vi.fn();
      const revoke = vi.spyOn(URL, "revokeObjectURL");
      await downloadRender("a1", "xlsx", { open });
      expect(open).toHaveBeenCalledTimes(1);
      const url = open.mock.calls[0][0] as string;
      expect(url.startsWith("blob:")).toBe(true);
      // Never the gated route itself: a bare navigation there carries no bearer.
      expect(url).not.toContain("/v1/artifacts");
      expect(revoke).not.toHaveBeenCalled();
      vi.advanceTimersByTime(RENDER_URL_REVOKE_MS);
      expect(revoke).toHaveBeenCalledWith(url);
      revoke.mockRestore();
    } finally {
      vi.useRealTimers();
    }
  });

  it("POST /v1/artifacts/{id}/open, once, with the id as a segment and no body", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { state: "opened", window_title: "Bütçe 2026.xlsx - Excel" }));
    const receipt = await openArtifact("a 1");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/artifacts/a%201/open");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined(); // which render, from where, checked how, is the Cloud Core's
    expect(receipt).toEqual({ state: "opened", summary: null, receiptId: null, windowTitle: "Bütçe 2026.xlsx - Excel" });
    // The client object the page hands the hook is this one call and no other.
    expect(Object.keys(artifactClient)).toEqual(["open"]);
  });

  it("reads the receipt from either shape, and never invents a state", async () => {
    expect(parseOpenReceipt({ state: "opened", receipt: { receipt_id: "r1", factual_summary: "Excel'de açıldı." }, observed: { window_title: "Bütçe - Excel" } })).toEqual({
      state: "opened",
      summary: "Excel'de açıldı.",
      receiptId: "r1",
      windowTitle: "Bütçe - Excel",
    });
    expect(parseOpenReceipt({ receipt: { id: "r2", status: "fetched", speech: "Dosyayı indirdim.", observed: { title: "İndirilenler" } } })).toEqual({
      state: "fetched",
      summary: "Dosyayı indirdim.",
      receiptId: "r2",
      windowTitle: "İndirilenler",
    });
    expect(parseOpenReceipt({ message: "İletildi." })).toEqual({ state: null, summary: "İletildi.", receiptId: null, windowTitle: null });
    expect(parseOpenReceipt(null)).toEqual({ state: null, summary: null, receiptId: null, windowTitle: null });
    expect(parseOpenReceipt("opened")).toEqual({ state: null, summary: null, receiptId: null, windowTitle: null });

    apiFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await openArtifact("a1")).toEqual({ state: null, summary: null, receiptId: null, windowTitle: null });
  });

  it("turns a refusal into a typed error with the Cloud Core's code, and into the owner's words", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: { code: "invalid_render", message: "validation failed" } }));
    const err = await openArtifact("a1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ArtifactOpenError);
    const typed = err as ArtifactOpenError;
    expect(typed.status).toBe(409);
    expect(typed.code).toBe("invalid_render");
    expect(typed.detail).toBe("validation failed");
    expect(artifactOpenErrorText(typed)).toBe(ARTIFACT_OPEN_REFUSAL_TR.invalid_render);
    expect(artifactOpenErrorText(typed)).toContain("açılmaz");

    apiFetch.mockResolvedValueOnce(json(422, { error_class: "capability_missing" }));
    const flat = (await openArtifact("a1").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(flat.code).toBe("capability_missing");
    expect(artifactOpenErrorText(flat)).toBe(ARTIFACT_OPEN_REFUSAL_TR.capability_missing);
    expect(artifactOpenErrorText(flat)).toContain("Açılmadı");

    apiFetch.mockResolvedValueOnce(json(400, { detail: { code: "strange", message: "açıklama" } }));
    const strange = (await openArtifact("a1").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(artifactOpenErrorText(strange)).toBe("strange: açıklama");

    apiFetch.mockResolvedValueOnce(new Response("not json", { status: 503 }));
    const opaque = (await openArtifact("a1").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(opaque.code).toBeNull();
    expect(artifactOpenErrorText(opaque)).toBe("HTTP 503");
    expect(artifactOpenErrorText(new Error("ağ koptu"))).toBe("ağ koptu");
    // Every refusal sentence says what did NOT happen, and none says it opened.
    for (const [code, text] of Object.entries(ARTIFACT_OPEN_REFUSAL_TR)) {
      expect(text, code).toMatch(/açılmadı|açılamadı|açılmaz|yok/i);
      expect(text, code).not.toMatch(/(^|[^a-zçğıöşü])açıldı/i);
    }
  });

  it("tells a route this Cloud Core does not have apart from an artifact it does not know", async () => {
    // FastAPI's bare 404 for a missing route: the route is not there yet.
    apiFetch.mockResolvedValueOnce(json(404, { detail: "Not Found" }));
    const absent = (await openArtifact("a1").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(absent.code).toBe(OPEN_ROUTE_ABSENT);
    expect(artifactOpenErrorText(absent)).toBe("Bu Cloud Core sürümünde /v1/artifacts/a1/open yok (HTTP 404). Açılmadı.");

    apiFetch.mockResolvedValueOnce(new Response("", { status: 404 }));
    const empty = (await openArtifact("a1").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(empty.code).toBe(OPEN_ROUTE_ABSENT);

    // The route answering 404 in its own words: the artifact is unknown, the route exists.
    apiFetch.mockResolvedValueOnce(json(404, { detail: "unknown artifact" }));
    const unknown = (await openArtifact("a9").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(unknown.code).toBeNull();
    expect(artifactOpenErrorText(unknown)).toBe("unknown artifact");

    apiFetch.mockResolvedValueOnce(json(404, { detail: { code: "not_found", message: "no such artifact" } }));
    const coded = (await openArtifact("a9").catch((e: unknown) => e)) as ArtifactOpenError;
    expect(coded.code).toBe("not_found");
    expect(artifactOpenErrorText(coded)).toBe(ARTIFACT_OPEN_REFUSAL_TR.not_found);
  });

  it("parses rows defensively: no id is no artifact, no format is no render, everything else is null", () => {
    expect(parseArtifact(null)).toBeNull();
    expect(parseArtifact("a1")).toBeNull();
    expect(parseArtifact({ title: "x" })).toBeNull();
    expect(parseArtifact({ id: "a1", available_renders: "xlsx" })).toEqual({
      artifact_id: "a1",
      title: null,
      kind: null,
      state: null,
      created_at: null,
      updated_at: null,
      renders: [],
    });
    expect(parseRender(null)).toBeNull();
    expect(parseRender({ state: "valid" })).toBeNull();
    expect(parseRender({ format: "pdf", size_bytes: -1 })?.size_bytes).toBeNull();
    expect(parseRender({ format: "pdf", size_bytes: "12" })?.size_bytes).toBeNull();
    expect(parseRender({ format: "pdf", verdict: "invalid", failing_ref: "p3" })).toMatchObject({ state: "invalid", failing_ref: "p3" });
    // A report with no failing element names no ref, whatever the state says.
    expect(parseRender({ format: "pdf", state: "invalid", validation: { elements: [{ ref: "p1", ok: true }] } })?.failing_ref).toBeNull();
    expect(parseRender({ format: "pdf", state: "invalid", validation: { failing_ref: "s4" } })?.failing_ref).toBe("s4");
    expect(parseRender({ format: "pdf", state: "invalid", validation: "broken" })?.failing_ref).toBeNull();
  });
});

describe("the rows", () => {
  it("a render is valid because its row says so, and only then is it downloadable", () => {
    const row = parseArtifact(ROW);
    expect(row).not.toBeNull();
    if (!row) return;
    expect(validRenders(row).map((r) => r.format)).toEqual(["xlsx"]);
    expect(artifactRenderLine(row.renders[0])).toBe("XLSX · 12 KB · doğrulandı");
    expect(artifactRenderLine(row.renders[1])).toBe("PDF · 29 KB · doğrulanamadı · yer: sheet:Ozet!B5");
    expect(artifactRenderLine(row.renders[2])).toBe("DOCX · 8 KB · doğrulama bildirilmedi");
    expect(artifactRenderLine({ format: "pptx", mime_type: null, size_bytes: null, content_hash: null, state: "invalid", failing_ref: null })).toBe(
      "PPTX · boyut bildirilmedi · doğrulanamadı · yer bildirilmedi",
    );
    // A verdict word this build does not know is printed verbatim, and buys no link.
    const odd = { format: "csv", mime_type: null, size_bytes: 512, content_hash: null, state: "validated", failing_ref: null };
    expect(artifactRenderLine(odd)).toBe("CSV · 1 KB · validated");
    expect(validRenders({ renders: [odd] })).toEqual([]);
  });

  it("sizes as M13's inbox prints them, or the statement that none came", () => {
    expect(renderSizeLabel(12_288)).toBe("12 KB");
    expect(renderSizeLabel(10)).toBe("1 KB");
    expect(renderSizeLabel(0)).toBe("1 KB");
    expect(renderSizeLabel(null)).toBe("boyut bildirilmedi");
  });

  it("names the spec's kinds in the owner's words and any other verbatim", () => {
    expect(artifactKindLabel("spreadsheet")).toBe("tablo");
    expect(artifactKindLabel("document")).toBe("belge");
    expect(artifactKindLabel("presentation")).toBe("sunum");
    expect(artifactKindLabel("dataset")).toBe("veri kümesi");
    expect(artifactKindLabel("page")).toBe("sayfa");
    expect(artifactKindLabel("memo")).toBe("memo");
    expect(artifactKindLabel(null)).toBeNull();
  });

  it("the gate opens only for an artifact with a valid render while nothing is in flight", () => {
    const row = parseArtifact(ROW);
    if (!row) throw new Error("fixture");
    expect(artifactOpenGate(row, null)).toEqual({ enabled: true, reason: null, reasonKind: null });
    expect(artifactOpenGate(row, "a1")).toEqual({ enabled: false, reason: ARTIFACT_OPEN_REASON_BUSY, reasonKind: "busy" });
    expect(artifactOpenGate(row, "a7")).toEqual({ enabled: false, reason: ARTIFACT_OPEN_REASON_BUSY, reasonKind: "busy" });
    expect(artifactOpenGate({ renders: [row.renders[1], row.renders[2]] }, null)).toEqual({
      enabled: false,
      reason: ARTIFACT_OPEN_REASON_NO_VALID_RENDER,
      reasonKind: "no_valid_render",
    });
    expect(artifactOpenGate({ renders: [] }, null).reasonKind).toBe("no_valid_render");
  });
});
