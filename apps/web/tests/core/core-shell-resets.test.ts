/**
 * The Core's full-viewport shell must undo every box rule the global `main` sets.
 *
 * Owner report 2026-09-18, with a screenshot: /core rendered as a 720px column centred in
 * a 2000px window — the site nav on top of the Core's own controls, the ambient strip cut
 * off at both sides. B25 req 724 (2026-09-16) had turned the shell's root from a `<div>`
 * into a `<main>` landmark so a screen reader could enter the page, which was right, and
 * the element silently inherited `globals.css`'s `main { max-width: 720px; margin: 0 auto;
 * padding: ... }`. `position: fixed; inset: 0` does not beat `max-width`: a fixed box with
 * both left and right pinned still stops at its max-width, and `margin: auto` then centres
 * it. Every test that rendered the page was green, because jsdom computes no layout.
 *
 * So this holds the two stylesheets to each other, not to a list typed here: whatever box
 * property the global `main` rule sets TODAY, `.core-shell` must set too. A property added
 * to the global rule later fails this test instead of shrinking the Core a second time.
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

/** Box properties that can move or shrink a fixed, inset:0 element. */
const BOX = /^(max-width|max-height|width|height|margin|margin-(top|right|bottom|left|inline|block)|padding|padding-(top|right|bottom|left|inline|block))$/;

describe("the Core shell is the whole viewport", () => {
  it("the page's root element is the <main> the global rule reaches", () => {
    // If the shell stops being a <main>, this test's premise changes - say so loudly.
    expect(PAGE).toMatch(/<main\s+className="core-shell"/);
  });

  it("the global main rule still sets box properties (or there is nothing to undo)", () => {
    const main = declarations(GLOBALS, "main");
    expect([...main.keys()].filter((k) => BOX.test(k))).not.toHaveLength(0);
  });

  it("every box property the global main sets is overridden by .core-shell", () => {
    const main = declarations(GLOBALS, "main");
    const shell = declarations(CORE, ".core-shell");
    const missing = [...main.keys()].filter((k) => BOX.test(k) && !shell.has(k));
    expect(missing, `.core-shell inherits ${missing.join(", ")} from the global main rule`).toEqual([]);
  });

  it("and overrides them to values that leave the viewport alone", () => {
    const shell = declarations(CORE, ".core-shell");
    expect(shell.get("position")).toBe("fixed");
    expect(shell.get("inset")).toBe("0");
    expect(shell.get("max-width")).toBe("none");
    expect(shell.get("margin")).toBe("0");
    expect(shell.get("padding")).toBe("0");
  });
});
