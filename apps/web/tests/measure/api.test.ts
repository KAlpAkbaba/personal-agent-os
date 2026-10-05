/**
 * The measurement client: the CONTRACT's GET, PUT, DELETE one and DELETE all, through
 * apiFetch, and a refusal kept as a refusal with the server's own sentence.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import {
  deleteAllRecordings,
  deleteRecording,
  fetchMeasurement,
  putRecording,
} from "../../app/lib/voice/measure/api";
import { item, measurement } from "./fixtures";

const fetchMock = vi.mocked(apiFetch);

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function calls(): Array<{ path: string; method: string; body: unknown }> {
  return fetchMock.mock.calls.map(([path, init]) => ({
    path,
    method: init?.method ?? "GET",
    body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
  }));
}

const BODY = {
  audio_wav_base64: "UklGRg==",
  browser_transcript: null,
  browser_engine: null,
  capture: { echoCancellation: true },
};

beforeEach(() => {
  fetchMock.mockReset();
});

describe("the four calls", () => {
  it("GET reads the whole measurement", async () => {
    fetchMock.mockResolvedValueOnce(json(200, measurement()));
    const result = await fetchMeasurement();
    expect(calls()).toEqual([{ path: "/v1/voice/measurement", method: "GET", body: undefined }]);
    expect(result.ok && result.measurement.sentences).toHaveLength(20);
  });

  it("PUT sends exactly the four CONTRACT keys to /recordings/{place}/{index}", async () => {
    fetchMock.mockResolvedValueOnce(json(200, item("ofis", 7)));
    const result = await putRecording("ofis", 7, BODY);
    const [call] = calls();
    expect(call.path).toBe("/v1/voice/measurement/recordings/ofis/7");
    expect(call.method).toBe("PUT");
    expect(Object.keys(call.body as object).toSorted()).toEqual(
      ["audio_wav_base64", "browser_engine", "browser_transcript", "capture"],
    );
    expect(call.body).toEqual(BODY);
    expect(result).toEqual({ ok: true, item: item("ofis", 7) });
  });

  it("DELETE one and DELETE all use their own paths and report the count", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { deleted: 1 })).mockResolvedValueOnce(json(200, { deleted: 23 }));
    expect(await deleteRecording("ev", 2)).toEqual({ ok: true, deleted: 1 });
    expect(await deleteAllRecordings()).toEqual({ ok: true, deleted: 23 });
    expect(calls()).toEqual([
      { path: "/v1/voice/measurement/recordings/ev/2", method: "DELETE", body: undefined },
      { path: "/v1/voice/measurement/recordings", method: "DELETE", body: undefined },
    ]);
  });
});

describe("a refusal", () => {
  it("422 comes back with the server's code and sentence", async () => {
    fetchMock.mockResolvedValueOnce(
      json(422, { detail: { code: "wav_empty", message: "Kayıt boş; cümleyi yeniden oku." } }),
    );
    expect(await putRecording("ev", 1, BODY)).toEqual({
      ok: false,
      code: "wav_empty",
      message: "Kayıt boş; cümleyi yeniden oku.",
    });
  });

  it("503 comes back with the server's code and sentence", async () => {
    fetchMock.mockResolvedValueOnce(
      json(503, {
        detail: { code: "store_unavailable", message: "Kayıt deposuna şu an ulaşılamıyor; biraz sonra yeniden dene." },
      }),
    );
    expect(await fetchMeasurement()).toEqual({
      ok: false,
      code: "store_unavailable",
      message: "Kayıt deposuna şu an ulaşılamıyor; biraz sonra yeniden dene.",
    });
  });

  it("a network failure is a refusal, never a success", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const result = await deleteAllRecordings();
    expect(result.ok).toBe(false);
  });
});
