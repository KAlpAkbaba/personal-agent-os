import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// The lead's wiring at merge (office-01): the Kokpit links to the Ofis page. A page nothing
// links to is a page the owner never finds.
describe("the Kokpit's link to the Ofis page", () => {
  it("is in the core controls, beside the Onay Merkezi's", () => {
    const source = readFileSync(join(__dirname, "..", "..", "app", "core", "CoreControls.tsx"), "utf8");
    expect(source).toContain('href="/core/office"');
    expect(source.indexOf('href="/core/approvals"')).toBeLessThan(source.indexOf('href="/core/office"'));
  });
});
