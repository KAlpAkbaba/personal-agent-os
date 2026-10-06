/**
 * The proposal parser against what the inspector broke it with (cycle d20261001): the shape
 * the six files under `team/proposals/` really have, a benefit pair written on one line, and
 * a line long enough to make a backtracking expression stall the page.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { parseInlines, parseProposal, type Inline } from "../../app/core/approvals/proposalSections";
import { FEED_SHAPE, OLD_FEED_SHAPE, ONE_LINE_PAIRS, REAL_SHAPE } from "./fixtures";

function plain(inlines: Inline[]): string {
  return inlines.map((inline) => inline.text).join("");
}

/**
 * The rule every file under team/proposals must keep, the files the feeder writes included: '## '
 * sections read back as written, one "what" section, more than five inlines, no stray '**' and
 * http links only. A file that breaks it is a red gate.
 */
function expectProposalShape(text: string, file: string): void {
  const parsed = parseProposal(text);
  // The expected titles are read from the file by a rule that is not the parser's.
  const headings = text
    .split(/\r?\n/)
    .filter((line) => line.startsWith("## "))
    .map((line) => line.slice(3).trim());
  expect(headings.length, file).toBeGreaterThanOrEqual(3);
  expect(
    parsed.sections.map((section) => section.title).filter((title) => title !== ""),
    file,
  ).toEqual(headings);
  expect(parsed.title, file).toMatch(/\S/);
  expect(
    parsed.sections.filter((section) => section.role === "what"),
    file,
  ).toHaveLength(1);
  const inlines: Inline[] = parsed.sections.flatMap((section) =>
    section.blocks.flatMap((block) => (block.kind === "paragraph" ? block.inlines : block.items.flat())),
  );
  expect(inlines.length, file).toBeGreaterThan(5);
  for (const inline of inlines) {
    expect(inline.text, file).not.toContain("**");
    if (inline.kind === "link") expect(inline.href, file).toMatch(/^https?:\/\//);
  }
  // A proposal with the section has its examples as pairs, both halves written.
  if (parsed.benefit) {
    expect(parsed.benefit.pairs.length, file).toBeGreaterThanOrEqual(1);
    for (const pair of parsed.benefit.pairs) {
      expect(plain(pair.today), file).toMatch(/\S/);
      expect(plain(pair.withIt), file).toMatch(/\S/);
    }
  }
}

describe("the shape the researcher really writes", () => {
  it("never cuts a section at a bullet's bold lead, nor at a '**Name**:' line of a '## ' proposal", () => {
    const parsed = parseProposal(REAL_SHAPE);
    expect(parsed.sections.map((section) => section.title)).toEqual([
      "",
      "Ne",
      "Nasıl",
      "Maliyet/risk",
      "Kanıt planı",
      "Karar",
    ]);
    const cost = parsed.sections[3];
    expect(cost.blocks).toHaveLength(1);
    expect(cost.blocks[0].kind === "list" && cost.blocks[0].items.map(plain)).toEqual([
      "Efor: orta (toplayıcı+denetçi kod).",
      "Maliyet: 5 USD/ay.",
      "Karar: model seçimi sonraya kalır. Karar: bu satır da bölümün metnidir.",
    ]);
    // "## Ne" inside a code fence is the fence's text, not a second "Ne".
    expect(parsed.sections.filter((section) => section.role === "what")).toHaveLength(1);
    expect(JSON.stringify(parsed.sections[4].blocks)).toContain("pytest tests/narrative");
  });

  it("does not cut at a bulleted '- **Karar**:' in the older shape either", () => {
    const parsed = parseProposal("**Ne**: bir cümle.\n- **Karar**: bu bir liste öğesi.\n\n**Karar**: Yapalım mı?\n");
    expect(parsed.sections.map((section) => section.title)).toEqual(["Ne", "Karar"]);
    expect(parsed.sections[0].blocks.map((block) => block.kind)).toEqual(["paragraph", "list"]);
  });

  it("keeps a numbered list numbered and a bulleted one bulleted", () => {
    const parsed = parseProposal(REAL_SHAPE);
    const [how] = parsed.sections[2].blocks;
    expect(how.kind === "list" && how.ordered).toBe(true);
    expect(how.kind === "list" && how.items.map(plain)).toEqual(["Toplayıcı yazılır.", "Denetçi eklenir."]);
    const [cost] = parsed.sections[3].blocks;
    expect(cost.kind === "list" && cost.ordered).toBe(false);
  });

  it("reads every file under team/proposals as its '## ' sections, bold-free, http links only", () => {
    const folder = join(process.cwd(), "..", "..", "team", "proposals");
    const files = readdirSync(folder).filter((name) => name.endsWith(".md"));
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) expectProposalShape(readFileSync(join(folder, file), "utf8"), file);
  });

  it("passes the same rule with the owner item the roadmap feeder writes (FEED_SHAPE)", () => {
    expectProposalShape(FEED_SHAPE, "FEED_SHAPE");
    const parsed = parseProposal(FEED_SHAPE);
    expect(parsed.title).toBe("Radicale takvim sunucusu");
    expect(parsed.sections.map((section) => section.title).filter((title) => title !== "")).toEqual([
      "Ne",
      "Roadmap satırı",
      "Hedef",
      "Kabul",
      "Karar",
    ]);
    const what = parsed.sections.find((section) => section.role === "what");
    expect(JSON.stringify(what?.blocks)).toContain("Radicale'yi ev PC'sine kuralım mı?");
    // The feeder copies the owner's sentence as it was written: bold and a link in it are the
    // parser's to read, and the file still keeps the rule.
    expectProposalShape(
      FEED_SHAPE.replace("Radicale'yi ev", "**Radicale**'yi [ev](https://radicale.org/) "),
      "FEED_SHAPE with bold and a link",
    );
  });

  it("refuses the feeder's old shape: '## Sahibe sorulan' and no 'Ne' is what turned the gate red", () => {
    expect(parseProposal(OLD_FEED_SHAPE).sections.filter((section) => section.role === "what")).toHaveLength(0);
    expect(() => expectProposalShape(OLD_FEED_SHAPE, "OLD_FEED_SHAPE")).toThrow(/OLD_FEED_SHAPE/);
    // The old shape is also one inline short; with a '## Karar' it is not, and only the one-"what"
    // rule is left to refuse it (cycle d20261006: loosening that rule kept this case green).
    const withDecision = `${OLD_FEED_SHAPE}\n## Karar\n\nSahip: evet / hayır / ertele.\n`;
    expect(() => expectProposalShape(withDecision, "OLD_FEED_SHAPE + Karar")).toThrow(
      /^OLD_FEED_SHAPE \+ Karar: expected \[\] to have a length of 1/,
    );
    // The heading renamed is all it takes to pass: the refusal is the 'Ne' rule's.
    expectProposalShape(withDecision.replace("## Sahibe sorulan", "## Ne"), "OLD_FEED_SHAPE + Karar, Ne");
  });
});

describe("a pair on one line", () => {
  it("splits 'Bugün: … / Bununla: …' and '… → Bununla: …' into the two halves", () => {
    const { benefit } = parseProposal(ONE_LINE_PAIRS);
    expect(benefit?.pairs.map((pair) => [plain(pair.today), plain(pair.withIt)])).toEqual([
      ["fiyata elle bakıyorum.", "ölçümle birlikte gelir."],
      ["tahmin ediyoruz", "tablo raporda durur."],
      ["komut düşüyor ve yeniden söylüyorum", "ilk seferde çalışır, gürültüde de."],
      ["", "yalnızca sonucu yazılmış."],
      ["yalnızca bugünü yazılmış.", ""],
    ]);
    expect(benefit?.pairs[1].withIt[0]).toEqual({ kind: "link", text: "tablo", href: "https://example.com/t" });
    // The closing lines are read in capitals too (to an expression, dotless ı is not ASCII's I).
    expect(benefit?.gain && plain(benefit.gain)).toBe("sayıyla görülür.");
    expect(benefit?.notGained && plain(benefit.notGained)).toBe("TTS değişmez.");
    expect(benefit?.rest).toEqual([]);
  });

  it("leaves a sentence that merely says 'bununla' alone, and drops a pair with neither half", () => {
    const { benefit } = parseProposal(
      "## Faydası\nBugün: elle yapıyorum, bununla: uğraşıyorum.\nBununla: kendiliğinden olur.\nBugün:\n",
    );
    expect(benefit?.pairs.map((pair) => [plain(pair.today), plain(pair.withIt)])).toEqual([
      ["elle yapıyorum, bununla: uğraşıyorum.", "kendiliğinden olur."],
    ]);
  });
});

describe("a line of any length", () => {
  // The claim is the answer; the runner's timeout is only the hang guard. Each of these took
  // 8-10 s when the heading and the link were found by a backtracking expression.
  it("reads a heading of 100 KB of '# ' as one heading", () => {
    const parsed = parseProposal(`## ${"# ".repeat(50_000)}x\nmetin\n`);
    expect(parsed.sections).toHaveLength(1);
    expect(parsed.sections[0].title).toHaveLength(100_001);
    expect(parsed.sections[0].title.endsWith("# x")).toBe(true);
    const trailing = parseProposal(`## Ne ${"# ".repeat(50_000)}\nmetin\n`);
    expect(trailing.sections.map((section) => section.title)).toEqual(["Ne"]);
    const spaced = parseProposal(`# Öneri${" ".repeat(100_000)}x\n\n**Ne${" ".repeat(100_000)}x**: a\n`);
    expect(spaced.title).toHaveLength(100_006);
  });

  it("finds the one link after 500 KB of '[', and none in 500 KB of '[a]('", () => {
    const opened = parseInlines(`${"[".repeat(500_000)} [fiyat](https://soniox.com/pricing) son`);
    expect(opened.filter((inline) => inline.kind === "link")).toEqual([
      { kind: "link", text: "fiyat", href: "https://soniox.com/pricing" },
    ]);
    expect(plain(opened)).toHaveLength(500_000 + " fiyat son".length);
    const unclosed = "[a](x ".repeat(100_000);
    expect(parseInlines(unclosed)).toEqual([{ kind: "text", text: unclosed }]);
    const brackets = "[a] ".repeat(125_000);
    expect(parseInlines(brackets)).toEqual([{ kind: "text", text: brackets }]);
  });
});
