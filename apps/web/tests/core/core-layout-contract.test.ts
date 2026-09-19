/**
 * Three things on the Core that only a rendered layout shows, held here at the level jsdom
 * CAN see: the declarations two stylesheets and the page make about the same pixels.
 *
 * Owner report 2026-09-19, with a screenshot of /core on a 3840px-wide window:
 *
 * 1. "● canlı" was drawn through the nav's "Kokpit" and "Ses". The quiet site nav is fixed at
 *    the top and CENTRES its link rows; the connection dot was absolutely positioned at
 *    `top: 1rem; left: 50%`. Two centred things at the same top edge. Each file was fine on
 *    its own, so this reads BOTH: while the nav centres its lists, the dot may not be centred.
 * 2. Every ambient cell was cut mid-sentence ("Bu ekran yalnızca gösteric"): the strip's
 *    cells carried `max-height` + `overflow: hidden`, and the band inside kept its own 60rem
 *    cap, so five cells got ~190px each. Those cells carry the camera and presence
 *    assurances; a clipped assurance is not one.
 * 3. The caption sat at a typed-in `bottom: 6.2rem` above a strip at `bottom: 0.6rem` - true
 *    only while the strip was one clipped row. They are one flow column now; the page must
 *    keep them inside it, in that order.
 *
 * The geometry itself was measured in a real browser at 2000px and 3840px when this was
 * fixed (docs/evidence/core-layout-2026-09-19.json); this test keeps the declarations that
 * produced it from drifting apart again.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const APP = join(__dirname, "..", "..", "app");
const GLOBALS = readFileSync(join(APP, "globals.css"), "utf8");
const CORE = readFileSync(join(APP, "core", "core.css"), "utf8");
const PAGE = readFileSync(join(APP, "core", "page.tsx"), "utf8");

/** The declarations of the FIRST rule whose selector is exactly `selector`. */
function declarations(css: string, selector: string): Map<string, string> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`, "m").exec(withoutComments);
  const out = new Map<string, string>();
  if (!match) return out;
  for (const part of match[1].split(";")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    out.set(part.slice(0, i).trim(), part.slice(i + 1).trim());
  }
  return out;
}

describe("the Core's fixed furniture does not share pixels", () => {
  it("the quiet nav still centres its link rows at the top (the premise)", () => {
    const nav = declarations(GLOBALS, ".site-nav-quiet");
    const list = declarations(GLOBALS, ".site-nav-quiet .site-nav-list");
    expect(nav.get("position")).toBe("fixed");
    expect(nav.get("top")).toBe("0");
    expect(list.get("justify-content")).toBe("center");
  });

  it("while the nav is centred at the top, the connection dot is not", () => {
    const dot = declarations(CORE, ".core-connection-dot");
    expect(dot.get("position")).toBe("absolute");
    expect(dot.has("top")).toBe(true);
    // Centred means left:50% (with or without the translate). Pinned to a corner it is.
    expect(dot.get("left")).not.toBe("50%");
    expect(dot.get("transform") ?? "").not.toContain("translateX(-50%)");
    expect(dot.get("left") ?? dot.get("right")).toMatch(/^\d*\.?\d+rem$/);
  });

  it("an ambient cell on the strip is never clipped", () => {
    const cell = declarations(CORE, ".core-strip .ambient-cell");
    expect(cell.size).toBeGreaterThan(0);
    expect(cell.has("max-height")).toBe(false);
    expect(cell.get("overflow") ?? "visible").toBe("visible");
  });

  it("the band fills the strip instead of keeping its own narrower cap", () => {
    expect(declarations(CORE, ".core-strip .ambient-band").get("width")).toBe("100%");
  });

  it("the caption and the strip are one flow column anchored to the bottom", () => {
    const bottom = declarations(CORE, ".core-bottom");
    expect(bottom.get("position")).toBe("absolute");
    expect(bottom.get("display")).toBe("flex");
    expect(bottom.get("flex-direction")).toBe("column");
    // Neither child is positioned on its own any more - that is what let them collide.
    expect(declarations(CORE, ".core-caption").has("position")).toBe(false);
    expect(declarations(CORE, ".core-caption").has("bottom")).toBe(false);
    expect(declarations(CORE, ".core-strip").has("position")).toBe(false);
    // And the page really nests them, caption first.
    const column = PAGE.indexOf('className="core-bottom"');
    const caption = PAGE.indexOf('className="core-caption"');
    const strip = PAGE.indexOf('className="core-strip"');
    expect(column).toBeGreaterThan(-1);
    expect(caption).toBeGreaterThan(column);
    expect(strip).toBeGreaterThan(caption);
  });

  it("the root grows with a large window on this page only", () => {
    const root = declarations(CORE, "html:has(.core-shell)");
    expect(root.get("font-size")).toMatch(/^clamp\(100%,/);
  });
});
