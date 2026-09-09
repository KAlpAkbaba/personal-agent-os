/**
 * A panel row's separate facts must READ as separate facts.
 *
 * Found while building the M27 "Yaratıcı" panel, by rendering the existing panels beside
 * it and looking at them: `.panel li` is a plain block and every row's body is a sequence
 * of sibling `<span>`s, so inline layout runs them together. The M25 "3B Sahne" panel
 * rendered
 *
 *     "doğrulandı (3 nesne)Kure, Kamera, Gunes"
 *     "yapılamadıNo valid Unity Editor license found."
 *
 * — a step and an object list, a refusal and the licensing client's own words, each pair
 * presented as one string. These are separately published facts. Running them together is
 * not a cosmetic problem: "yapılamadı" followed immediately by a vendor sentence reads as
 * a single garbled claim, and the whole point of these panels is that the owner can see
 * what was actually said.
 *
 * It affects every panel built on this row shape (M23 apps, M24 genesis, M25 scenes, M26
 * executive), all of them released. The fix is one rule in `core.css`; this test is what
 * keeps it, and it holds the MARKUP and the STYLESHEET to each other rather than trusting
 * either alone — the same discipline the contract-halves guards use.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(join(process.cwd(), "app/core/core.css"), "utf8");
const PANELS = readFileSync(join(process.cwd(), "app/core/panels/CockpitPanels.tsx"), "utf8");

/** The rule that makes each direct-child span of a panel row its own line. */
const RULE = /\.panel\s+li\s*>\s*span\s*\{[^}]*\bdisplay:\s*block\b[^}]*\}/;

describe("a panel row's facts are separate lines", () => {
  it("core.css gives every direct-child span of a panel row its own line", () => {
    expect(CSS).toMatch(RULE);
  });

  it("the rule is not blinded by a later override", () => {
    // A `display: inline` on the same selector further down would silently undo it, and
    // nothing else in this file would notice.
    const overrides = CSS.match(/\.panel\s+li\s*>\s*span\s*\{[^}]*\}/g) ?? [];
    expect(overrides.length).toBeGreaterThan(0);
    for (const block of overrides) {
      expect(block).not.toMatch(/display:\s*(inline|inline-block|contents)\b/);
    }
  });

  it("the panels really do stack sibling spans in a row, which is why the rule is needed", () => {
    // If someone rewrites the rows as <div>s or adds explicit separators, this guard should
    // be revisited rather than left asserting a rule nothing depends on any more.
    const rowSpans = PANELS.match(/<span className="muted" data-[a-z-]+/g) ?? [];
    expect(rowSpans.length).toBeGreaterThan(5);
  });

  it("the detector would catch the defect it was written for", () => {
    // Proving the regex bites, so this test cannot silently pass against a stylesheet that
    // lost the rule - the vacuous-guard failure this repo has shipped twice.
    const withoutRule = CSS.replace(RULE, "/* removed */");
    expect(withoutRule).not.toMatch(RULE);
  });
});
