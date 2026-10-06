/**
 * The phone alarm's client: the two calls through apiFetch, a refusal kept as a refusal with
 * the server's own sentence, and the 'görüldü HH:MM' line read from the status.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import {
  fetchStatus,
  lastSentence,
  sendTest,
  type UrgentAlertStatus,
} from "../../app/core/urgent-alert/urgentAlertApi";

const fetchMock = vi.mocked(apiFetch);

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SEEN: UrgentAlertStatus = {
  configured: true,
  open_receipts: 0,
  last_seen_at: "2026-10-07T12:01:00Z",
  last_outcome: "seen",
};

beforeEach(() => {
  fetchMock.mockReset();
});

describe("the two calls", () => {
  it("GET reads the status", async () => {
    fetchMock.mockResolvedValueOnce(json(200, SEEN));
    const result = await fetchStatus();
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/urgent-alert/status");
    expect(result).toEqual({ ok: true, status: SEEN });
  });

  it("POST sends the test", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { id: "n1", kind: "urgent_alert.test" }));
    const result = await sendTest();
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/urgent-alert/test");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(result).toEqual({ ok: true, id: "n1" });
  });

  it("the fourth test in an hour is the server's 429 sentence, not a success", async () => {
    fetchMock.mockResolvedValueOnce(
      json(429, { detail: "Saatte en çok 3 deneme gönderilebilir." }),
    );
    expect(await sendTest()).toEqual({
      ok: false,
      code: "http_429",
      message: "Saatte en çok 3 deneme gönderilebilir.",
    });
  });
});

describe("the last alarm's line", () => {
  it("is 'görüldü HH:MM' on the owner's clock", () => {
    expect(lastSentence(SEEN, "Europe/Istanbul")).toBe("görüldü 15:01");
  });

  it("is 'görüldü' also when he read it in the inbox first", () => {
    expect(lastSentence({ ...SEEN, last_outcome: "cancelled" }, "Europe/Istanbul")).toBe(
      "görüldü 15:01",
    );
  });

  it("is 'görülmedi' when it rang out", () => {
    expect(lastSentence({ ...SEEN, last_seen_at: null, last_outcome: "unseen" })).toBe("görülmedi");
  });

  it("is nothing before the first alarm", () => {
    expect(lastSentence({ ...SEEN, last_seen_at: null, last_outcome: null })).toBeNull();
  });
});

describe("the contract with the Cloud Core", () => {
  it("names exactly the keys routes.py returns", () => {
    const routes = readFileSync(
      join(__dirname, "..", "..", "..", "..", "services", "api", "app", "urgent_alert", "routes.py"),
      "utf8",
    );
    for (const key of Object.keys(SEEN)) {
      expect(routes).toContain(`"${key}"`);
    }
  });
});
