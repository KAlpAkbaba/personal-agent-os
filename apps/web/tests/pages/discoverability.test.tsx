/**
 * B25 req 660/701: the kill switch you can find, and the list of what you may say.
 *
 * Both requirements are the same defect in different clothes — a thing this system has and
 * the owner cannot get at. `POST /v1/identity/panic` has existed since B05 and the matrix's
 * whole note on it is three words: *Arayüzde görünmüyor*. The capability list has existed
 * since there were tools, and its only reader was the model.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import PanicControl, { PANIC_PATH } from "../../app/security/PanicControl";
import {
  type CapabilityList,
  byFamily,
  capabilityAnchor,
  fetchCapabilities,
  parseCapabilities,
} from "../../app/lib/pages/capabilities";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  apiFetch.mockReset();
});

// ------------------------------------------------------------ req 660: the kill switch

describe("the panic control", () => {
  it("asks the route the API actually serves", () => {
    expect(PANIC_PATH).toBe("/v1/identity/panic");
  });

  it("arms before it fires, so one stray click cannot revoke everything", () => {
    const html = renderToStaticMarkup(<PanicControl />);
    expect(html).toContain('data-panic="arm"');
    // The irreversible press is not in the document until the owner has asked for it.
    expect(html).not.toContain('data-panic="fire"');
  });

  it("says what it does, and — the part that matters — what it does NOT do", () => {
    const html = renderToStaticMarkup(<PanicControl />);
    // What it does: every session, this one included.
    expect(html).toContain("bütün oturumları");
    expect(html).toContain("bu tarayıcınınki dahil");
    // What it does not: the owner is not locked out of their own system. A control people
    // are afraid of is a control they do not press when they should.
    expect(html).toContain("Sahip kimlik bilgisi geçerli kalır");
    expect(html).toContain("recover --rotate");
  });

  it("is drawn as the serious thing it is, and is addressable", () => {
    const html = renderToStaticMarkup(<PanicControl />);
    expect(html).toContain('data-panel="panic"');
    expect(html).toContain('id="panic"');
    expect(html).toContain("attention");
  });

  it("is on the security page, first", async () => {
    const fs = await import("node:fs/promises");
    const page = await fs.readFile(new URL("../../app/security/page.tsx", import.meta.url), "utf8");
    expect(page).toContain("<PanicControl />");
    // Before the four lists: the one moment it exists for is not a moment for scrolling.
    expect(page.indexOf("<PanicControl />")).toBeLessThan(page.indexOf('id="security-assets"'));
  });

  it("is findable from anywhere, by the words somebody would actually type", async () => {
    // req 660's target is one word: *bulunabilir*. A kill switch reachable only by
    // remembering which page it is on is not findable, and the moment it exists for is not
    // a moment for remembering.
    const { paletteItems, search } = await import("../../app/components/CommandPalette");
    const items = paletteItems(null);
    for (const word of ["acil", "panik", "oturum", "güvenlik", "PANİK"]) {
      const results = search(items, word);
      expect(
        results.some((item) => item.href === "/security#panic" || item.href === "/security"),
        word,
      ).toBe(true);
    }
    expect(search(items, "acil").some((item) => item.href === "/security#panic")).toBe(true);
  });
});

// --------------------------------------------------------- req 701: what you may say

const BODY = {
  capabilities: [
    {
      name: "alarm.snooze",
      family: "alarm",
      family_tr: "Alarmlar",
      summary: "Çalan alarmı ERTELER",
      phrases: ["beş dakika ertele", "on dakika ertele"],
    },
    {
      name: "alarm.stop",
      family: "alarm",
      family_tr: "Alarmlar",
      summary: "Çalan alarmı DURDURUR",
      phrases: ["alarmı kapat"],
    },
    {
      name: "eye.disable",
      family: "eye",
      family_tr: "Kamera",
      summary: "Active Eye'ı KAPATIR",
      phrases: ["gözünü kapat"],
    },
  ],
  families: [
    { family: "alarm", family_tr: "Alarmlar", count: 2 },
    { family: "eye", family_tr: "Kamera", count: 1 },
  ],
  speech: "Efendim, 3 şey yapabiliyorum.",
  count: 3,
};

describe("the capability list the product reads", () => {
  it("reads the server's derivation, phrases and all", async () => {
    apiFetch.mockResolvedValue(json(200, BODY));
    const loaded = await fetchCapabilities();
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value.rows).toHaveLength(3);
    expect(loaded.value.rows[0].phrases).toEqual(["beş dakika ertele", "on dakika ertele"]);
    expect(loaded.value.speech).toBe("Efendim, 3 şey yapabiliyorum.");
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/voice/capabilities");
  });

  it("a Cloud Core without the route says so rather than showing an empty list", async () => {
    apiFetch.mockResolvedValue(json(404, {}));
    expect((await fetchCapabilities()).kind).toBe("absent");
  });

  it("drops a row with no name rather than drawing an unaddressable one", () => {
    const list = parseCapabilities({ capabilities: [{ summary: "bir şey" }, BODY.capabilities[0]] });
    expect(list.rows.map((row) => row.name)).toEqual(["alarm.snooze"]);
  });

  it("falls back to the family key when the server named no Turkish for it", () => {
    const list = parseCapabilities({
      capabilities: [{ name: "teapot.brew", family: "teapot", summary: "" }],
      families: [{ family: "teapot", count: 1 }],
    });
    expect(list.rows[0].familyTr).toBe("teapot");
    expect(list.families[0].familyTr).toBe("teapot");
  });

  it("groups in the server's order, which is the order a Turkish reader scans", () => {
    const list: CapabilityList = parseCapabilities(BODY);
    const groups = byFamily(list);
    expect(groups.map((group) => group.family.family)).toEqual(["alarm", "eye"]);
    expect(groups[0].rows.map((row) => row.name)).toEqual(["alarm.snooze", "alarm.stop"]);
  });

  it("drops a family the rows do not actually cover", () => {
    const list = parseCapabilities({
      ...BODY,
      families: [...BODY.families, { family: "ghost", family_tr: "Hayalet", count: 4 }],
    });
    expect(byFamily(list).map((group) => group.family.family)).toEqual(["alarm", "eye"]);
  });

  it("gives every capability an anchor a link can reach", () => {
    expect(capabilityAnchor("alarm.snooze")).toBe("cap-alarm-snooze");
    // The anchor and the palette's link have to agree, or every "say" result scrolls
    // nowhere.
    expect(`/voice#${capabilityAnchor("eye.disable")}`).toBe("/voice#cap-eye-disable");
  });
});

describe("the page that shows them", () => {
  it("is on /voice, above the diagnostics", async () => {
    const fs = await import("node:fs/promises");
    const page = await fs.readFile(new URL("../../app/voice/page.tsx", import.meta.url), "utf8");
    expect(page).toContain("<Capabilities />");
    expect(page.indexOf("<Capabilities />")).toBeLessThan(page.indexOf("Yan kanal"));
  });

  it("shows the owner's own sentences as sentences, and never invents one", async () => {
    const fs = await import("node:fs/promises");
    const source = await fs.readFile(
      new URL("../../app/voice/Capabilities.tsx", import.meta.url),
      "utf8",
    );
    expect(source).toContain("data-capability-phrase");
    // The no-example branch shows the SUMMARY. Writing a phrase here would put words in
    // the owner's mouth that the assistant was never told to listen for.
    expect(source).toContain("data-capability-noexample");
    expect(source).toContain("row.summary");
    // Nothing is written down: the list comes from the route.
    expect(source).toContain("fetchCapabilities");
  });
});
