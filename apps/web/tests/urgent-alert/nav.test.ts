import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// A page nothing links to is a page the owner never finds.
describe("the core controls' link to the phone alarm page", () => {
  it("comes after the Ofis and misheard links, same class, data-control='urgent-alert'", () => {
    const source = readFileSync(join(__dirname, "..", "..", "app", "core", "CoreControls.tsx"), "utf8");
    const office = source.indexOf('href="/core/office"');
    const misheard = source.indexOf('href="/core/misheard"');
    const alarm = source.indexOf('href="/core/urgent-alert"');
    expect(office).toBeGreaterThan(-1);
    expect(misheard).toBeGreaterThan(office);
    expect(alarm).toBeGreaterThan(misheard);
    const tag = source.slice(source.lastIndexOf("<Link", alarm), source.indexOf(">", alarm));
    expect(tag).toContain('className="core-controls-link"');
    expect(tag).toContain('data-control="urgent-alert"');
  });
});
