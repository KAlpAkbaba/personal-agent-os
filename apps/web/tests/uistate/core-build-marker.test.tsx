/**
 * The Living Core build marker (M18.3 §12, qualification A).
 *
 * The owner harness `scripts/core/owner-m18-3-core.ps1` proves the served build by reading
 * `<meta name="pagentos-core-build">` off `/core`'s server-rendered head. Three things must
 * agree for that proof to mean anything: the constant, the layout's metadata, and the
 * harness's own expectation - so all three are read here, and the harness file is read as
 * text rather than trusted from memory.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import CoreLayout, { metadata } from "../../app/core/layout";
import { CORE_BUILD_ID } from "../../app/lib/uistate/contract";

describe("the Living Core build marker", () => {
  it("is rendered into the /core document head by the route layout, before any sign-in", () => {
    const other = (metadata.other ?? {}) as Record<string, string | number | (string | number)[]>;
    expect(other["pagentos-core-build"]).toBe(CORE_BUILD_ID);
    expect(CORE_BUILD_ID).toMatch(/^living-core-\d+$/);
  });

  it("passes its children through untouched (the gate and the Core stay client-side)", () => {
    const child = <span>x</span>;
    expect(CoreLayout({ children: child })).toBe(child);
  });

  it("is exactly what the owner harness expects, read from the harness file itself", () => {
    const harness = readFileSync(
      resolve(__dirname, "../../../../scripts/core/owner-m18-3-core.ps1"),
      "utf8",
    );
    const match = /\$expectedBuild\s*=\s*"([^"]+)"/.exec(harness);
    expect(match?.[1]).toBe(CORE_BUILD_ID);
  });
});
