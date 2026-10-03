/**
 * The misheard notebook's client: the CONTRACT's four calls, through apiFetch, and a refusal
 * kept as a refusal with the server's own sentence.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import {
  answerMeaning,
  fetchNotebook,
  forgetAll,
  forgetOne,
} from "../../app/core/misheard/misheardApi";
import { forgetNotebook, saveMeaning } from "../../app/core/misheard/misheardActions";
import { item, three } from "./fixtures";

const fetchMock = vi.mocked(apiFetch);

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function calls(): Array<{ path: string; method: string; body: unknown }> {
  return fetchMock.mock.calls.map(([path, init]) => ({
    path,
    method: init?.method ?? "GET",
    body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
  }));
}

const ID = "aaaaaaaa-0000-4000-8000-000000000001";

beforeEach(() => {
  fetchMock.mockReset();
});

describe("the four calls", () => {
  it("GET lists the notebook", async () => {
    fetchMock.mockResolvedValueOnce(json(200, three()));
    const result = await fetchNotebook();
    expect(calls()).toEqual([{ path: "/v1/voice/misheard", method: "GET", body: undefined }]);
    expect(result).toEqual({ ok: true, list: three() });
  });

  it("POST writes the meaning, letter for letter", async () => {
    const meant = "Işığı söndür, şömineyi değil; ğ ı ş ü ö ç";
    fetchMock.mockResolvedValueOnce(json(200, item({ id: ID, meant })));
    const result = await answerMeaning(ID, meant);
    expect(calls()).toEqual([
      { path: `/v1/voice/misheard/${ID}/meaning`, method: "POST", body: { meant } },
    ]);
    expect(result).toEqual({ ok: true, item: item({ id: ID, meant }) });
  });

  it("DELETE one forgets that row", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { deleted: 1 }));
    expect(await forgetOne(ID)).toEqual({ ok: true, deleted: 1 });
    expect(calls()).toEqual([{ path: `/v1/voice/misheard/${ID}`, method: "DELETE", body: undefined }]);
  });

  it("DELETE all forgets the notebook with ONE call to the collection", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { deleted: 5 }));
    expect(await forgetAll()).toEqual({ ok: true, deleted: 5 });
    expect(calls()).toEqual([{ path: "/v1/voice/misheard", method: "DELETE", body: undefined }]);
  });
});

describe("a refusal", () => {
  const cases: Array<[number, string, string]> = [
    [422, "meant_empty", "Ne demek istediğini yaz; anlam boş olamaz."],
    [404, "not_found", "Bu cümle defterde yok; silinmiş ya da süresi dolmuş."],
    [503, "store_unavailable", "Defter şu an açılamıyor."],
  ];

  for (const [status, code, message] of cases) {
    it(`${status} comes back as {ok: false} with the server's sentence, on every call`, async () => {
      const refusal = { detail: { code, message } };
      for (const call of [
        () => fetchNotebook(),
        () => answerMeaning(ID, "x"),
        () => forgetOne(ID),
        () => forgetAll(),
      ]) {
        fetchMock.mockResolvedValueOnce(json(status, refusal));
        expect(await call()).toEqual({ ok: false, code, message });
      }
    });
  }

  it("without a body still says which status it was", async () => {
    fetchMock.mockResolvedValueOnce(new Response("oops", { status: 500 }));
    expect(await forgetAll()).toEqual({ ok: false, code: "http_500", message: "HTTP 500" });
  });
});

describe("the actions the page runs", () => {
  it("saveMeaning sends the typed words unchanged and reports success", async () => {
    const meant = "Çiçekleri sula, ağaçları değil";
    fetchMock.mockResolvedValueOnce(json(200, item({ id: ID, meant })));
    const notice = await saveMeaning(ID, meant);
    expect(calls()[0].body).toEqual({ meant });
    expect(notice.ok).toBe(true);
  });

  it("forgetNotebook sends ONE DELETE and says how many were deleted", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { deleted: 4 }));
    const notice = await forgetNotebook();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(calls()[0]).toEqual({ path: "/v1/voice/misheard", method: "DELETE", body: undefined });
    expect(notice).toEqual({ ok: true, message: expect.stringContaining("4") });
  });

  it("a refused action shows the server's sentence and is never a success", async () => {
    fetchMock.mockResolvedValueOnce(
      json(503, { detail: { code: "store_unavailable", message: "Defter şu an açılamıyor." } }),
    );
    expect(await forgetNotebook()).toEqual({ ok: false, message: "Defter şu an açılamıyor." });
  });
});
