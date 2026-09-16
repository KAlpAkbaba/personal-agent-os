/**
 * B36 (req 562/563/576): the interfaces the owner registered for Capability Genesis,
 * read from `/v1/genesis/catalogue` and drawn on /selfdev - names, spoken phrases,
 * operation verbs, and whether an entry is still enabled. Read-only: registration is
 * the Cloud Core's route, never something this page invents.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { GenesisCataloguePanel } from "../../app/core/panels/CockpitPanels";
import {
  type CatalogueEntryRow,
  GENESIS_CATALOGUE_PATH,
  fetchGenesisCatalogue,
  parseCatalogueEntry,
} from "../../app/lib/cockpit/genesis";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

const ROW = {
  name: "counterbox",
  url: "http://127.0.0.1:54321/spec",
  target_phrases: ["sayaç kutusu", "sayaç"],
  operations: [
    { operation_id: "read", verbs: ["kaç"] },
    { operation_id: "increment", verbs: ["artır", "arttır"] },
  ],
  source: "owner_rest",
  enabled: true,
  updated_at: "2026-09-15T10:00:00Z",
};

describe("the genesis catalogue", () => {
  it("is read from the route's own shape, and a row with no name is not an entry", async () => {
    expect(GENESIS_CATALOGUE_PATH).toBe("/v1/genesis/catalogue");
    apiFetch.mockResolvedValueOnce(json(200, { entries: [ROW, { url: "x" }] }));
    const loaded = await fetchGenesisCatalogue();
    expect((apiFetch.mock.calls[0] as unknown[])[0]).toBe(GENESIS_CATALOGUE_PATH);
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value).toHaveLength(1);
    expect(loaded.value[0].operations[1].verbs).toEqual(["artır", "arttır"]);
    expect(parseCatalogueEntry(null)).toBeNull();
    expect(parseCatalogueEntry({ name: "x", operations: [{ verbs: ["a"] }], target_phrases: "no" })).toEqual({
      name: "x",
      url: null,
      target_phrases: [],
      operations: [],
      source: null,
      enabled: null,
      updated_at: null,
    });
  });

  it("draws each entry with its host, phrases and verbs, and says when one is disabled", () => {
    const value = [parseCatalogueEntry(ROW) as CatalogueEntryRow, parseCatalogueEntry({ ...ROW, name: "lampbox", enabled: false, operations: [] }) as CatalogueEntryRow];
    const html = renderToStaticMarkup(<GenesisCataloguePanel state={{ kind: "ok", value, at: 0 }} always />);
    expect(html).toContain('id="genesis-catalogue"');
    expect(html).toContain("1 açık / 2");
    expect(html).toContain('data-catalogue-entry="counterbox"');
    expect(html).toContain("127.0.0.1:54321");
    expect(html).toContain("sayaç kutusu, sayaç");
    expect(html).toContain("increment: artır/arttır");
    expect(html).toContain('data-catalogue-enabled="no"');
    expect(html).toContain("devre dışı");
    expect(html).toContain("hedef çözülür, fiil çözülmez");
  });

  it("says there is nothing registered, in as many words", () => {
    const html = renderToStaticMarkup(<GenesisCataloguePanel state={{ kind: "ok", value: [], at: 0 }} always />);
    expect(html).toContain("Kayıtlı arayüz yok");
  });
});
