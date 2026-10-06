/**
 * Card verify-mode: the owner's list of verified claims (/research/verify).
 *
 * What is held here:
 *
 * · the list asks the server with the owner's words and window, and nothing else;
 * · a settled row shows the verdict, its confidence, every source with the quote
 *   that decides, and the counter-argument - exactly what the server sent;
 * · a BELİRSİZ with no source says no source decided, never a guessed verdict;
 * · a pending row says it is waiting, never a verdict;
 * · an empty list says so.
 *
 * `react-dom/server` in Node, no browser.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import {
  listVerifications,
  sinceForDays,
  type Verification,
  verificationsPath,
} from "../../app/research/verify/api";
import VerificationList from "../../app/research/verify/VerificationList";

const SETTLED: Verification = {
  verification_id: "v-1",
  created_at: "2026-10-05T09:00:00Z",
  said: "bunu doğrula: Everest'in 8849 metre olduğu",
  claim: "Everest'in 8849 metre olduğu",
  status: "settled",
  verdict: "kismen",
  verdict_label: "KISMEN",
  confidence: 0.45,
  sources: [
    {
      url: "https://olcum.example/everest",
      title: "Ölçüm Raporu",
      published_at: "2026-08-01T00:00:00Z",
      quote: "Everest'in yüksekliği 8849 metre olarak ölçüldü.",
      stance: "supports",
    },
    {
      url: "https://eski.example/everest",
      title: "Eski Atlas",
      published_at: null,
      quote: "Everest'in yüksekliği 8848 metre.",
      stance: "refutes",
    },
  ],
  counter_argument: {
    url: "https://eski.example/everest",
    title: "Eski Atlas",
    published_at: null,
    quote: "Everest'in yüksekliği 8848 metre.",
    stance: "refutes",
  },
  spoken: "Hüküm: kısmen doğru (güven yüzde 45). Kaynak: Ölçüm Raporu, 1 Ağustos 2026.",
};

const UNKNOWN: Verification = {
  ...SETTLED,
  verification_id: "v-2",
  claim: "Mars'ta göl var",
  verdict: "belirsiz",
  verdict_label: "BELİRSİZ",
  confidence: 0,
  sources: [],
  counter_argument: null,
  spoken: "Bunu doğrulayacak bir kaynak bulamadım; hüküm belirsiz.",
};

const PENDING: Verification = {
  ...SETTLED,
  verification_id: "v-3",
  claim: "Ay'da su buzu var",
  status: "pending",
  verdict: null,
  verdict_label: null,
  confidence: null,
  sources: [],
  counter_argument: null,
  spoken: null,
};

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200 });
}

describe("verify list - api", () => {
  beforeEach(() => apiFetch.mockReset());

  it("asks with the owner's words and window only", async () => {
    apiFetch.mockResolvedValue(ok({ items: [SETTLED], count: 1 }));
    const items = await listVerifications({ q: "  everest ", since: "2026-09-29T00:00:00.000Z" });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe(
      "/v1/research/verifications?q=everest&since=2026-09-29T00%3A00%3A00.000Z",
    );
    expect(items).toEqual([SETTLED]);
    expect(verificationsPath()).toBe("/v1/research/verifications");
  });

  it("says a failed read, never an empty list", async () => {
    apiFetch.mockResolvedValue(new Response("{}", { status: 500 }));
    await expect(listVerifications()).rejects.toThrow("HTTP 500");
  });

  it("turns a range into a start instant", () => {
    const now = Date.parse("2026-10-06T09:00:00Z");
    expect(sinceForDays(7, now)).toBe("2026-09-29T09:00:00.000Z");
    expect(sinceForDays(null, now)).toBeUndefined();
  });
});

describe("verify list - rows", () => {
  it("shows the verdict, its confidence, the sources with their quotes and the counter-argument", () => {
    const html = renderToStaticMarkup(<VerificationList items={[SETTLED]} />);
    expect(html).toContain('data-verdict="kismen"');
    expect(html).toContain("KISMEN · %45");
    expect(html).toContain("Everest&#x27;in 8849 metre olduğu");
    expect(html).toContain('href="https://olcum.example/everest"');
    expect(html).toContain("Everest&#x27;in yüksekliği 8849 metre olarak ölçüldü.");
    expect(html).toContain("1 Ağustos 2026");
    expect(html).toContain("çürütüyor");
    expect(html).toContain("En güçlü karşı argüman");
    expect((html.match(/<li data-source/g) ?? []).length).toBe(3);
  });

  it("says no source decided a BELİRSİZ, and never shows a guessed verdict", () => {
    const html = renderToStaticMarkup(<VerificationList items={[UNKNOWN]} />);
    expect(html).toContain("BELİRSİZ · %0");
    expect(html).toContain("data-no-source");
    expect(html).not.toContain("data-sources");
    expect(html).not.toContain("data-counter");
  });

  it("shows a pending check as waiting, without a verdict", () => {
    const html = renderToStaticMarkup(<VerificationList items={[PENDING]} />);
    expect(html).toContain('data-verdict="pending"');
    expect(html).toContain("Bekliyor");
    expect(html).not.toContain("data-no-source");
  });

  it("says an empty window is empty", () => {
    const html = renderToStaticMarkup(<VerificationList items={[]} />);
    expect(html).toContain("doğrulanan bir iddia yok");
  });
});
