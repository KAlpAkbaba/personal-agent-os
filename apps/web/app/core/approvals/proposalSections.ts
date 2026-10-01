/**
 * A researcher's proposal (`.claude/agents/researcher.md`), split for the Onay Merkezi's
 * "Detay" panel.
 *
 * Pure, and deliberately not a Markdown renderer: it knows sections, paragraphs, lists and
 * links, and everything else in the text stays the text the researcher wrote. Nothing here
 * produces markup - the panel renders these values as text nodes - and a link exists only
 * for an http/https address; any other scheme is left in the sentence as written.
 *
 * Sections are cut at `## ` headings (any title, unknown ones kept). A proposal with no `## `
 * heading at all is the older shape and is cut at its `**Heading**:` lines - only for the
 * section names the rule knows, and never at a list item, because a bold lead
 * ("- **Maliyet**: 5 USD/ay") is how the same proposals write ordinary sentences.
 *
 * Headings and links are found by scanning, not by a backtracking expression: the text comes
 * over the network, and one long line must not stall the page.
 */

export type Inline = { kind: "text"; text: string } | { kind: "link"; text: string; href: string };

export type Block =
  | { kind: "paragraph"; inlines: Inline[] }
  | { kind: "list"; ordered: boolean; items: Inline[][] };

/** `benefit`: "Faydası — örneklerle". `what`: "Ne". The panel shows those two first. */
export type SectionRole = "benefit" | "what" | "other";

/** `title` is "" for what stands between the proposal's title and its first section. */
export type ProposalSection = { title: string; role: SectionRole; blocks: Block[] };

/** One example. Either half may be empty (a half pair), never both. */
export type BenefitPair = { today: Inline[]; withIt: Inline[] };

export type Benefit = {
  title: string;
  pairs: BenefitPair[];
  gain: Inline[] | null;
  notGained: Inline[] | null;
  /** What the section says outside its pairs and its two closing lines. */
  rest: Block[];
};

export type ParsedProposal = {
  title: string | null;
  /** Every section in the file's order, the benefit section included. */
  sections: ProposalSection[];
  /** Read from the first benefit section; null when the proposal has none. */
  benefit: Benefit | null;
};

const BOLD_HEADING = /^\*\*([^*]+?)(?::\*\*|\*\*\s*:)\s*(.*)$/;
const SUB_HEADING = /^\s*#{1,6}\s+(.*)$/;
const LIST_ITEM = /^\s*(?:[-*+]\s+|(\d+)[.)]\s+)(.*)$/;
// "ı" is spelled both ways: to an expression's /i, dotless ı and ASCII's I are not one letter.
const BENEFIT_LABEL =
  /^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?(Bugün|Bununla|Kazanç|Kazanmad[ıI]ğ[ıI]m[ıI]z)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$/i;
// "Bununla:" after its "Bugün" on the same line. Capitalised only: "…, bununla: …" is a sentence.
const WITH_IT_INLINE = /(?:\*\*)?(?:Bununla|BUNUNLA)(?:\*\*)?\s*:\s*(?:\*\*)?\s*/;
const PAIR_SEPARATORS = ["->", "=>", "→", "/", "|", "—", "–"];
const FENCE = /^\s*(?:```|~~~)/;
const EMPHASIS = /\*\*([^*\n]+?)\*\*/g;

// Whole names, not stems: "Faydalanılan kaynaklar" is not the benefit section.
const BENEFIT_NAME = /^fayda(?:sı|si|lar|ları)?(?=$|[\s—–:-])/;
const KNOWN_NAMES = [
  /^ne$/,
  BENEFIT_NAME,
  /^neden şimdi(?=$|\s)/,
  /^nasıl$/,
  /^maliyet(?=$|[\s/])/,
  /^kanıt planı(?=$|\s)/,
  /^karar$/,
];

function fold(name: string): string {
  return name.trim().toLocaleLowerCase("tr-TR");
}

function roleOf(title: string): SectionRole {
  const name = fold(title);
  if (BENEFIT_NAME.test(name)) return "benefit";
  return name === "ne" ? "what" : "other";
}

function isSpace(char: string): boolean {
  return char.trim() === "";
}

function headingTitle(raw: string): string {
  const title = raw.replace(EMPHASIS, "$1").trim();
  return title.endsWith(":") ? title.slice(0, -1).trimEnd() : title;
}

/** The title of a `# ` (depth 1) or `## ` (depth 2) line, closing hashes dropped; else null. */
function hashTitle(line: string, depth: 1 | 2): string | null {
  if (!line.startsWith("#".repeat(depth)) || line.length <= depth || !isSpace(line[depth])) return null;
  let end = line.length;
  if (depth === 2) while (end > depth && (line[end - 1] === "#" || isSpace(line[end - 1]))) end -= 1;
  const title = line.slice(depth, end).trim();
  return title === "" ? null : title;
}

/** The address a link may carry, or null: http and https, and nothing else. */
export function safeHref(raw: string): string | null {
  if (!/^https?:\/\//i.test(raw)) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

/** Text and http(s) links. A refused link stays in the text exactly as it was written. */
export function parseInlines(source: string): Inline[] {
  const text = source.replace(EMPHASIS, "$1");
  const inlines: Inline[] = [];
  // Everything before `at` is already in `inlines`; `open` is the last "[" not yet closed.
  let at = 0;
  let open = -1;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "[") open = index;
    if (char !== "]" && char !== "\n") continue;
    const from = open;
    open = -1;
    if (char === "\n" || from < 0 || index === from + 1 || text[index + 1] !== "(") continue;
    // The address runs to its ")" and holds no bracket and no space: read once, never again.
    let end = index + 2;
    while (end < text.length && text[end] !== "(" && text[end] !== ")" && !isSpace(text[end])) end += 1;
    if (text[end] !== ")" || end === index + 2) continue;
    const href = safeHref(text.slice(index + 2, end));
    if (href !== null) {
      if (from > at) inlines.push({ kind: "text", text: text.slice(at, from) });
      inlines.push({ kind: "link", text: text.slice(from + 1, index), href });
      at = end + 1;
    }
    index = end;
  }
  if (at < text.length) inlines.push({ kind: "text", text: text.slice(at) });
  return inlines;
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let items: string[][] | null = null;
  // A list is numbered when its first item is.
  let ordered = false;
  const flush = () => {
    if (paragraph.length > 0) blocks.push({ kind: "paragraph", inlines: parseInlines(paragraph.join(" ")) });
    if (items) blocks.push({ kind: "list", ordered, items: items.map((item) => parseInlines(item.join(" "))) });
    paragraph = [];
    items = null;
  };
  for (const line of lines) {
    if (line.trim() === "") {
      flush();
      continue;
    }
    const sub = SUB_HEADING.exec(line);
    if (sub) {
      flush();
      paragraph = [sub[1].trim()];
      flush();
      continue;
    }
    const item = LIST_ITEM.exec(line);
    if (item) {
      if (!items) {
        flush();
        ordered = item[1] !== undefined;
      }
      items ??= [];
      items.push([item[2].trim()]);
    } else if (items) {
      items[items.length - 1].push(line.trim());
    } else {
      paragraph.push(line.trim());
    }
  }
  flush();
  return blocks;
}

/** "… / Bununla: …" (or "→", "->", "—", "|") cut in two; null when no second half is written there. */
function splitOneLinePair(text: string): { today: string; withIt: string } | null {
  const match = WITH_IT_INLINE.exec(text);
  if (!match) return null;
  if (match.index > 0 && /[\p{L}\p{N}]/u.test(text[match.index - 1])) return null;
  let today = text.slice(0, match.index).trimEnd();
  const separator = PAIR_SEPARATORS.find((candidate) => today.endsWith(candidate));
  if (separator) today = today.slice(0, -separator.length).trimEnd();
  return { today, withIt: text.slice(match.index + match[0].length).trim() };
}

function parseBenefit(title: string, lines: string[]): Benefit {
  const pairs: { today: string[]; withIt: string[]; closed: boolean }[] = [];
  let gain: string[] | null = null;
  let notGained: string[] | null = null;
  const rest: string[] = [];
  // The labelled line a following unlabelled line continues ("Bununla: …" wrapped in two).
  let current: string[] | null = null;
  for (const line of lines) {
    const labelled = BENEFIT_LABEL.exec(line);
    if (labelled) {
      const label = fold(labelled[1]);
      const body = labelled[2].trim() === "" ? [] : [labelled[2].trim()];
      const oneLine = label === "bugün" ? splitOneLinePair(labelled[2].trim()) : null;
      if (oneLine) {
        const pair = { today: oneLine.today === "" ? [] : [oneLine.today], withIt: [oneLine.withIt], closed: true };
        pairs.push(pair);
        current = pair.withIt;
      } else if (label === "bugün") {
        pairs.push({ today: body, withIt: [], closed: false });
        current = pairs[pairs.length - 1].today;
      } else if (label === "bununla") {
        if (pairs.length === 0 || pairs[pairs.length - 1].closed) {
          pairs.push({ today: [], withIt: [], closed: false });
        }
        const pair = pairs[pairs.length - 1];
        pair.withIt = body;
        pair.closed = true;
        current = pair.withIt;
      } else if (label === "kazanç") {
        gain = body;
        current = gain;
      } else {
        notGained = body;
        current = notGained;
      }
      continue;
    }
    const continues = current !== null && line.trim() !== "" && !LIST_ITEM.test(line) && !/^\s*(?:#|\*\*)/.test(line);
    if (continues && current) {
      // A wrapped "Bugün" may reach its "→ Bununla:" on a later line.
      const pair = pairs.at(-1);
      const oneLine = pair && !pair.closed && current === pair.today ? splitOneLinePair(line.trim()) : null;
      if (oneLine && pair) {
        if (oneLine.today !== "") pair.today.push(oneLine.today);
        pair.withIt = [oneLine.withIt];
        pair.closed = true;
        current = pair.withIt;
      } else {
        current.push(line.trim());
      }
      continue;
    }
    current = null;
    rest.push(line);
  }
  const inline = (parts: string[] | null) => (parts === null ? null : parseInlines(parts.join(" ")));
  return {
    title,
    // A label with nothing after it on either side is not an example.
    pairs: pairs
      .map((pair) => ({
        today: parseInlines(pair.today.join(" ")),
        withIt: parseInlines(pair.withIt.join(" ")),
      }))
      .filter((pair) => pair.today.length > 0 || pair.withIt.length > 0),
    gain: inline(gain),
    notGained: inline(notGained),
    rest: parseBlocks(rest),
  };
}

export function parseProposal(text: string): ParsedProposal {
  let title: string | null = null;
  const raw: { title: string; lines: string[] }[] = [{ title: "", lines: [] }];
  // What each line is - a code fence and the lines inside it being nothing but text.
  let fenced = false;
  const lines = text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => {
      const fence = FENCE.test(line);
      if (fence) fenced = !fenced;
      const literal = fence || fenced;
      return { line, literal, hash: literal ? null : hashTitle(line, 2) };
    });
  const olderShape = lines.every(({ hash }) => hash === null);
  for (const { line, literal, hash } of lines) {
    if (hash !== null) {
      raw.push({ title: headingTitle(hash), lines: [] });
      continue;
    }
    const bold = olderShape && !literal ? BOLD_HEADING.exec(line) : null;
    if (bold && KNOWN_NAMES.some((name) => name.test(fold(bold[1])))) {
      raw.push({ title: bold[1].trim(), lines: bold[2].trim() === "" ? [] : [bold[2]] });
      continue;
    }
    const current = raw[raw.length - 1];
    if (title === null && raw.length === 1 && !literal && current.lines.every((seen) => seen.trim() === "")) {
      const heading = hashTitle(line, 1);
      if (heading !== null) {
        title = heading;
        continue;
      }
    }
    current.lines.push(line);
  }
  const sections: ProposalSection[] = [];
  let benefit: Benefit | null = null;
  for (const { title: name, lines: body } of raw) {
    const section: ProposalSection = { title: name, role: name === "" ? "other" : roleOf(name), blocks: parseBlocks(body) };
    // The untitled lead exists only when something was written there.
    if (name === "" && section.blocks.length === 0) continue;
    sections.push(section);
    if (section.role === "benefit" && benefit === null) benefit = parseBenefit(name, body);
  }
  return { title, sections, benefit };
}
