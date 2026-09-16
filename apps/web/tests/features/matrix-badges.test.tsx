/**
 * B24 req 700/712/713: the badges speak the matrix's own vocabulary, and cannot drift.
 *
 * 713's note is the requirement: *"Bu matrisi ürüne bağlar."* Until this batch, the record
 * of what this system has, what state each piece is in and — the column nothing outside the
 * document ever read — HOW each claim was proven lived in a markdown file only the person
 * building the system opened. The owner could not tell a feature proven on their own machine
 * from one proven by a test from one nobody has checked.
 *
 * Two failures are possible once a document becomes a product surface, and this file is
 * about both:
 *
 *  1. **The copy goes stale.** So the copy is generated, and the first test regenerates it
 *     from the document and compares — the same thing `node scripts/web/sync-feature-matrix.mjs
 *     --check` does, run here so the web gate carries it.
 *  2. **The badges invent their own words.** So nothing restates the classes: the proof
 *     abbreviations are parsed out of the document's own LEGEND table, and the tests below
 *     read the markdown a SECOND time, independently of the generator, and compare against
 *     what the badge components actually render.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ImplBadge, ProofBadge } from "../../app/components/FeatureBadge";
import {
  IMPL_LABEL,
  IMPL_TONE,
  PROOF_LABEL,
  PROOF_TONE,
} from "../../app/lib/features/badges";
import {
  IMPL_CLASSES,
  type ImplClass,
  MATRIX_VERSION,
  PROOF_CLASSES,
  PROOF_EXPANSION,
  type ProofClass,
  ROWS,
  SECTIONS,
} from "../../app/lib/features/matrix.generated";

const MATRIX_PATH = fileURLToPath(
  new URL("../../../../docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md", import.meta.url),
);
const GENERATED_PATH = fileURLToPath(
  new URL("../../app/lib/features/matrix.generated.ts", import.meta.url),
);

const markdown = readFileSync(MATRIX_PATH, "utf8");

/**
 * The document's rows, read here and not through the generator.
 *
 * A second reader on purpose: comparing the generated module against a parse the generator
 * itself produced would compare a thing with itself. This one is deliberately naive — split
 * the pipes, take the columns the LEGEND names — and if the two disagree, one of them is
 * wrong about the document, which is exactly what should fail.
 */
function rowsFromDocument(): { id: number; impl: string; proof: string }[] {
  const out: { id: number; impl: string; proof: string }[] = [];
  for (const line of markdown.split(/\r?\n/)) {
    if (!/^\|\s*\d+\s*\|/.test(line)) continue;
    const cells = line
      .trim()
      .slice(1, -1)
      .split("|")
      .map((cell) => cell.trim());
    out.push({ id: Number(cells[0]), impl: cells[4], proof: cells[5] });
  }
  return out;
}

/** The `| `PR` | PROVEN_REAL |` rows of the LEGEND table. */
function legendFromDocument(): Record<string, string> {
  const legend: Record<string, string> = {};
  let inLegend = false;
  for (const line of markdown.split(/\r?\n/)) {
    if (line.startsWith("## LEGEND")) {
      inLegend = true;
      continue;
    }
    if (inLegend && line.startsWith("---")) break;
    const match = /^\|\s*`([A-Z]+)`\s*\|\s*([A-Z_]+)\s*\|$/.exec(line.trim());
    if (inLegend && match) legend[match[1]] = match[2];
  }
  return legend;
}

describe("the generated copy is the document", () => {
  it("regenerating from the markdown produces exactly the file on disk", async () => {
    // What `--check` does, run where the web gate will see it. A row flipped to DONE in
    // the document and never synced here would leave the owner reading a stale badge.
    const { render } = (await import("../../../../scripts/web/sync-feature-matrix.mjs")) as {
      render: (markdown: string) => string;
    };
    expect(render(markdown)).toBe(readFileSync(GENERATED_PATH, "utf8"));
  });

  it("carries every numbered row, once, under the heading it appears below", () => {
    const document = rowsFromDocument();
    expect(ROWS).toHaveLength(document.length);
    expect(ROWS.map((row) => row.id)).toEqual(document.map((row) => row.id));
    expect(new Set(ROWS.map((row) => row.id)).size).toBe(ROWS.length);

    // The section letters are the document's own headings, and every row has one.
    const letters = new Set(SECTIONS.map((section) => section.key));
    for (const row of ROWS) expect(letters.has(row.section), `${row.id}`).toBe(true);
  });

  it("says what each row's status and proof are, agreeing with a second reader", () => {
    const byId = new Map(rowsFromDocument().map((row) => [row.id, row]));
    for (const row of ROWS) {
      const source = byId.get(row.id);
      expect(source, `${row.id}`).toBeDefined();
      expect(row.impl, `${row.id} impl`).toBe(source?.impl);
      expect(row.proof, `${row.id} proof`).toBe(source?.proof);
    }
  });

  it("names the version of the record it copied", () => {
    expect(MATRIX_VERSION).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(markdown).toContain(`**Sürüm:** ${MATRIX_VERSION}`);
  });
});

describe("the badges use the matrix's class vocabulary (req 712/713)", () => {
  it("the proof classes are the LEGEND's, expanded in the LEGEND's words", () => {
    const legend = legendFromDocument();
    expect(Object.keys(legend).length).toBeGreaterThanOrEqual(5);
    expect([...PROOF_CLASSES].sort()).toEqual(Object.keys(legend).sort());
    for (const [abbrev, expansion] of Object.entries(legend)) {
      expect(PROOF_EXPANSION[abbrev as ProofClass], abbrev).toBe(expansion);
    }
  });

  it("the status classes are the ones the document actually uses", () => {
    const used = new Set(rowsFromDocument().map((row) => row.impl));
    expect([...IMPL_CLASSES].sort()).toEqual([...used].sort());
  });

  it("every class has Turkish and a tone — a new one is a type error, not a blank badge", () => {
    for (const impl of IMPL_CLASSES) {
      expect(IMPL_LABEL[impl], impl).toBeTruthy();
      expect(["good", "warn", "bad", "wait"]).toContain(IMPL_TONE[impl]);
    }
    for (const proof of PROOF_CLASSES) {
      expect(PROOF_LABEL[proof], proof).toBeTruthy();
      expect(["good", "warn", "bad", "wait"]).toContain(PROOF_TONE[proof]);
    }
    // The distinction the owner most needs is the one the abbreviations carry: a test
    // said so, a person watched it happen, or nobody has checked.
    expect(PROOF_LABEL.PA).not.toBe(PROOF_LABEL.PR);
    expect(PROOF_LABEL.NYP).not.toBe(PROOF_LABEL.PA);
  });

  it("a rendered badge carries the raw class, so the product's word IS the document's", () => {
    for (const impl of IMPL_CLASSES) {
      const html = renderToStaticMarkup(<ImplBadge impl={impl} />);
      expect(html).toContain(`data-badge-class="${impl}"`);
      expect(html).toContain(IMPL_LABEL[impl]);
      expect(html).toContain(`tone-${IMPL_TONE[impl]}`);
    }
    for (const proof of PROOF_CLASSES) {
      const html = renderToStaticMarkup(<ProofBadge proof={proof} />);
      expect(html).toContain(`data-badge-class="${proof}"`);
      expect(html).toContain(PROOF_LABEL[proof]);
      // The expansion is the tooltip, so hovering says the document's own term.
      expect(html).toContain(PROOF_EXPANSION[proof]);
    }
  });

  it("no badge invents a class the document does not have", () => {
    const known: Set<string> = new Set<string>([...IMPL_CLASSES, ...PROOF_CLASSES]);
    const rendered = [
      ...IMPL_CLASSES.map((impl: ImplClass) => renderToStaticMarkup(<ImplBadge impl={impl} />)),
      ...PROOF_CLASSES.map((proof: ProofClass) =>
        renderToStaticMarkup(<ProofBadge proof={proof} />),
      ),
    ].join("");
    for (const [, value] of rendered.matchAll(/data-badge-class="([^"]+)"/g)) {
      expect(known.has(value), value).toBe(true);
    }
  });
});

describe("the availability page (req 700)", () => {
  const page = readFileSync(
    fileURLToPath(new URL("../../app/availability/page.tsx", import.meta.url)),
    "utf8",
  );

  it("measures the live families itself rather than answering from the record", () => {
    // req 78's rule, applied to a page: an answer read from a hand-maintained document is
    // only as true as the document. So "is this family working right now" comes from the
    // SAME loader the cockpit's panels read, and the record sits beside it, not instead.
    expect(page).toContain("useCockpitData");
    expect(page).toContain("familyQualities");
    expect(page).toContain("QUALITY_LABEL");
  });

  it("shows the record with both badges and the version it was copied from", () => {
    expect(page).toContain("<ImplBadge");
    expect(page).toContain("<ProofBadge");
    expect(page).toContain("MATRIX_VERSION");
    expect(page).toContain("MATRIX_BASE");
  });

  it("is where the cockpit's hidden families point", async () => {
    const panel = readFileSync(
      fileURLToPath(new URL("../../app/core/panels/Panel.tsx", import.meta.url)),
      "utf8",
    );
    expect(panel).toContain('href="/availability"');
    const nav = await import("../../app/components/SiteNav");
    expect(nav.NAV.map((item) => item.href)).toContain("/availability");
  });
});
