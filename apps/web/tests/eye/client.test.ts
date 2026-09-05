import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { EyeApiError, disableEye, enableEye, explainEyeError, postObservation } from "../../app/lib/eye/client";
import type { EyeObservation } from "../../app/lib/eye/types";
import { UnauthorizedError } from "../../app/lib/session";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const OBS: EyeObservation = {
  person_present: true,
  presence_confidence: 0.62,
  activity_level: "medium",
  posture: "unknown",
  awake_state: "awake",
  observed_at: "2026-09-06T10:00:00.000Z",
  source: "camera",
};

beforeEach(() => {
  apiFetch.mockReset();
});

describe("postObservation", () => {
  it("posts exactly the seven fields to the presence endpoint", async () => {
    apiFetch.mockResolvedValueOnce(json(201, { observation: OBS, assertion: {}, changed: true }));
    const result = await postObservation(OBS);
    expect(result).toEqual({ status: "posted" });
    const [path, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/v1/presence/observations");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(OBS);
    expect(Object.keys(JSON.parse(init.body as string)).toSorted()).toEqual(
      [
        "activity_level",
        "awake_state",
        "observed_at",
        "person_present",
        "posture",
        "presence_confidence",
        "source",
      ].toSorted(),
    );
  });

  it("treats a 409 as the eye being disabled server-side, not a network failure", async () => {
    apiFetch.mockResolvedValueOnce(json(409, { detail: "the Active Eye is disabled" }));
    const result = await postObservation(OBS);
    expect(result).toEqual({ status: "eye_disabled" });
  });

  it("treats a 422 as a rejection with the server's Turkish/English detail, not a thrown error", async () => {
    apiFetch.mockResolvedValueOnce(json(422, { detail: "unknown observation field: 'frame'" }));
    const result = await postObservation(OBS);
    expect(result).toEqual({ status: "rejected", detail: "unknown observation field: 'frame'" });
  });

  it("throws a typed error for anything else non-2xx", async () => {
    apiFetch.mockResolvedValueOnce(json(500, {}));
    await expect(postObservation(OBS)).rejects.toBeInstanceOf(EyeApiError);
  });

  it("forwards an AbortSignal so a caller can cancel an in-flight post", async () => {
    apiFetch.mockResolvedValueOnce(json(201, {}));
    const controller = new AbortController();
    await postObservation(OBS, { signal: controller.signal });
    const [, init] = apiFetch.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
  });
});

describe("enableEye / disableEye", () => {
  it("posts to the owner action endpoints with an optional reason", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { eye_enabled: true }));
    await enableEye("owner_start");
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/presence/eye/enable");
    expect(JSON.parse((apiFetch.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      reason: "owner_start",
    });

    apiFetch.mockResolvedValueOnce(json(200, { eye_enabled: false }));
    await disableEye();
    expect(apiFetch.mock.calls[1][0]).toBe("/v1/presence/eye/disable");
    expect(JSON.parse((apiFetch.mock.calls[1][1] as RequestInit).body as string)).toEqual({});
  });

  it("throws a typed error on failure", async () => {
    apiFetch.mockResolvedValueOnce(json(403, { detail: "yasak" }));
    await expect(disableEye()).rejects.toBeInstanceOf(EyeApiError);
  });
});

describe("explainEyeError", () => {
  it("explains a typed error, an unauthorized error and a generic one", async () => {
    expect(explainEyeError(new UnauthorizedError("/v1/presence/observations"))).toBe(
      "Sahip oturumu reddedildi.",
    );
    expect(explainEyeError(new EyeApiError(500, "sunucu hatası"))).toBe("sunucu hatası");
    expect(explainEyeError(new Error("boom"))).toBe("boom");
    expect(explainEyeError("boom")).toBe("boom");
  });
});
