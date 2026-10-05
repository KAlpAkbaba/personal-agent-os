import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// A page nothing links to is a page the owner never finds.
describe("the core controls' link to the misheard notebook", () => {
  it("comes after the Ofis link, same class, data-control='misheard'", () => {
    const source = readFileSync(join(__dirname, "..", "..", "app", "core", "CoreControls.tsx"), "utf8");
    const office = source.indexOf('href="/core/office"');
    const misheard = source.indexOf('href="/core/misheard"');
    expect(office).toBeGreaterThan(-1);
    expect(misheard).toBeGreaterThan(office);
    const tag = source.slice(source.lastIndexOf("<Link", misheard), source.indexOf(">", misheard));
    expect(tag).toContain('className="core-controls-link"');
    expect(tag).toContain('data-control="misheard"');
  });
});
