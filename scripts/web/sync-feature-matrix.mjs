#!/usr/bin/env node
/**
 * B24 req 700/712/713: bind the FEATURE MATRIX to the product.
 *
 * `docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md` is the authoritative record of what
 * this system has, what state each feature is in and — the column nothing outside the
 * document had ever read — how each claim was PROVEN. Requirement 713's whole note is
 * "Bu matrisi ürüne bağlar": the owner should be able to see that record without opening
 * a markdown file in an editor.
 *
 * Copying 750 rows into TypeScript by hand would produce a second source of truth that
 * drifts silently, which is the failure this repository has hit more than any other. So
 * the copy is GENERATED, and `--check` (which the web test suite runs) fails when the
 * generated module no longer matches the document. One command puts it right:
 *
 *     node scripts/web/sync-feature-matrix.mjs
 *
 * What is deliberately NOT taken from here: whether a family is working RIGHT NOW. That
 * is requirement 78's rule — an answer read from a hand-maintained document is only as
 * true as the document — so the availability page measures the live families itself and
 * shows the record beside the measurement rather than instead of it.
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const SOURCE = join(REPO, "docs", "product", "PERSONALAGENTOS_V1_FEATURE_MATRIX.md");
const TARGET = join(REPO, "apps", "web", "app", "lib", "features", "matrix.generated.ts");

/** `| a | b | c |` -> `["a", "b", "c"]`. */
function cells(line) {
  const trimmed = line.trim();
  return trimmed
    .slice(1, trimmed.length - 1)
    .split("|")
    .map((cell) => cell.trim());
}

/**
 * The proof vocabulary, read from the document's own LEGEND table.
 *
 * Read rather than restated: "rozetlerin FEATURE_MATRIX ile aynı sınıf sözlüğünü
 * kullandığı" (B24 TEST_PLAN) cannot be shown by a badge module that spells the classes
 * out a second time.
 */
export function parseLegend(markdown) {
  const legend = {};
  const lines = markdown.split(/\r?\n/);
  let inLegend = false;
  for (const line of lines) {
    if (line.startsWith("## LEGEND")) {
      inLegend = true;
      continue;
    }
    if (inLegend && line.startsWith("---")) break;
    if (!inLegend) continue;
    const match = /^\|\s*`([A-Z]+)`\s*\|\s*([A-Z_]+)\s*\|$/.exec(line.trim());
    if (match) legend[match[1]] = match[2];
  }
  return legend;
}

/** Every `## X. TITLE (a–b)` heading, in document order. */
export function parseSections(markdown) {
  const sections = [];
  for (const line of markdown.split(/\r?\n/)) {
    // The dash in the range is an en dash in the document, not a hyphen.
    const match = /^##\s+([A-Z])\.\s+(.+?)\s+\((\d+)[\u2013-](\d+)\)\s*$/.exec(line);
    if (match) {
      sections.push({
        key: match[1],
        title: match[2],
        from: Number(match[3]),
        to: Number(match[4]),
      });
    }
  }
  return sections;
}

/**
 * Every numbered row, with the six fields the product surfaces.
 *
 * The section a row belongs to is decided by the heading it appears UNDER, not by its id
 * falling inside the heading's range: the ranges are the document's own promise and a row
 * filed in the wrong place should show up as itself rather than be silently re-filed.
 */
export function parseRows(markdown) {
  const rows = [];
  let section = "";
  for (const line of markdown.split(/\r?\n/)) {
    const heading = /^##\s+([A-Z])\.\s+/.exec(line);
    if (heading) {
      section = heading[1];
      continue;
    }
    if (!/^\|\s*\d+\s*\|/.test(line)) continue;
    const cell = cells(line);
    if (cell.length !== 14) {
      throw new Error(`row with ${cell.length} columns, expected 14: ${line.slice(0, 80)}`);
    }
    rows.push({
      id: Number(cell[0]),
      section,
      feature: cell[1],
      impl: cell[4],
      proof: cell[5],
      batch: cell[8],
      owner: cell[12] === "yes",
    });
  }
  return rows;
}

/** The document's own version line, so the page can say how old the record is. */
export function parseVersion(markdown) {
  const match = /^\*\*Sürüm:\*\*\s*([0-9-]+)\s*·\s*\*\*Taban:\*\*\s*`([^`]+)`\s*@\s*`([^`]+)`/m.exec(
    markdown,
  );
  return match ? { version: match[1], base: `${match[2]} @ ${match[3]}` } : { version: "", base: "" };
}

function quote(text) {
  return JSON.stringify(text);
}

export function render(markdown) {
  const legend = parseLegend(markdown);
  const sections = parseSections(markdown);
  const rows = parseRows(markdown);
  const { version, base } = parseVersion(markdown);

  const implClasses = [...new Set(rows.map((row) => row.impl))].sort();
  const proofClasses = Object.keys(legend).sort();
  const unknownProof = rows.filter((row) => !legend[row.proof]).map((row) => row.id);
  if (unknownProof.length > 0) {
    throw new Error(`rows use a proof class the LEGEND does not define: ${unknownProof}`);
  }

  const out = [];
  out.push("/**");
  out.push(" * GENERATED — do not edit.");
  out.push(" *");
  out.push(" * `node scripts/web/sync-feature-matrix.mjs` rewrites this file from");
  out.push(" * `docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md`, which is the record this");
  out.push(" * product is measured against. The web test suite runs the same script with");
  out.push(" * `--check`, so an edit to the document that never reached here fails the gate");
  out.push(" * rather than leaving the owner reading a stale badge.");
  out.push(" */");
  out.push("");
  out.push(`export const MATRIX_VERSION = ${quote(version)};`);
  out.push(`export const MATRIX_BASE = ${quote(base)};`);
  out.push("");
  out.push("/** The IMPLEMENTATION_STATUS values the document actually uses. */");
  out.push("export const IMPL_CLASSES = [");
  for (const value of implClasses) out.push(`  ${quote(value)},`);
  out.push("] as const;");
  out.push("export type ImplClass = (typeof IMPL_CLASSES)[number];");
  out.push("");
  out.push("/** The PROOF_STATUS abbreviations, from the document's own LEGEND table. */");
  out.push("export const PROOF_CLASSES = [");
  for (const value of proofClasses) out.push(`  ${quote(value)},`);
  out.push("] as const;");
  out.push("export type ProofClass = (typeof PROOF_CLASSES)[number];");
  out.push("");
  out.push("/** What each abbreviation stands for — again the LEGEND's own words. */");
  out.push("export const PROOF_EXPANSION: Record<ProofClass, string> = {");
  for (const key of proofClasses) out.push(`  ${key}: ${quote(legend[key])},`);
  out.push("};");
  out.push("");
  out.push("export type MatrixSection = {");
  out.push("  key: string;");
  out.push("  title: string;");
  out.push("  from: number;");
  out.push("  to: number;");
  out.push("};");
  out.push("");
  out.push("export const SECTIONS: readonly MatrixSection[] = [");
  for (const section of sections) {
    out.push(
      `  { key: ${quote(section.key)}, title: ${quote(section.title)}, from: ${section.from}, to: ${section.to} },`,
    );
  }
  out.push("];");
  out.push("");
  out.push("export type MatrixRow = {");
  out.push("  /** The permanent requirement id. Never renumbered. */");
  out.push("  id: number;");
  out.push("  /** The letter of the section heading the row appears under. */");
  out.push("  section: string;");
  out.push("  feature: string;");
  out.push("  impl: ImplClass;");
  out.push("  proof: ProofClass;");
  out.push("  /** The batch that owns the row, or `—`. */");
  out.push("  batch: string;");
  out.push("  /** True when the row cannot close without the owner doing something. */");
  out.push("  owner: boolean;");
  out.push("};");
  out.push("");
  out.push("export const ROWS: readonly MatrixRow[] = [");
  for (const row of rows) {
    out.push(
      `  { id: ${row.id}, section: ${quote(row.section)}, feature: ${quote(row.feature)}, ` +
        `impl: ${quote(row.impl)}, proof: ${quote(row.proof)}, batch: ${quote(row.batch)}, ` +
        `owner: ${row.owner} },`,
    );
  }
  out.push("];");
  out.push("");
  return out.join("\n");
}

function main() {
  const markdown = readFileSync(SOURCE, "utf8");
  const generated = render(markdown);
  const check = process.argv.includes("--check");
  let current = "";
  try {
    current = readFileSync(TARGET, "utf8");
  } catch {
    current = "";
  }
  if (current === generated) {
    if (!check) process.stdout.write(`unchanged: ${TARGET}\n`);
    return;
  }
  if (check) {
    process.stderr.write(
      "matrix.generated.ts is stale. Run: node scripts/web/sync-feature-matrix.mjs\n",
    );
    process.exit(1);
  }
  writeFileSync(TARGET, generated, "utf8");
  process.stdout.write(`wrote: ${TARGET}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
