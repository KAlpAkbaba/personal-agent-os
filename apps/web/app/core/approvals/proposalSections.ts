/**
 * A researcher's proposal (`.claude/agents/researcher.md`), split for the Onay Merkezi's
 * "Detay" panel.
 *
 * Pure, and deliberately not a Markdown renderer: it knows sections, paragraphs, lists and
 * links, and everything else in the text stays the text the researcher wrote. Nothing here
 * produces markup - the panel renders these values as text nodes - and a link exists only
 * for an http/https address; any other scheme is left in the sentence as written.
 *
 * Sections are cut at `## ` headings (any title, unknown ones kept) and at the older
 * `**Heading**:` lines - those only for the section names the rule knows, because a bold
 * lead ("**Efor**: orta") is how the same proposals write ordinary sentences.
 */

export type Inline = { kind: "text"; text: string } | { kind: "link"; text: string; href: string };

export type Block = { kind: "paragraph"; inlines: Inline[] } | { kind: "list"; items: Inline[][] };

/** `benefit`: "Faydası — örneklerle". `what`: "Ne". The panel shows those two first. */
export type SectionRole = "benefit" | "what" | "other";

/** `title` is "" for what stands between the proposal's title and its first section. */
export type ProposalSection = { title: string; role: SectionRole; blocks: Block[] };

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

const TITLE = /^#\s+(.+?)\s*$/;
const HASH_HEADING = /^##\s+(.+?)[\s#]*$/;
const BOLD_HEADING = /^(?:[-*]\s+)?\*\*([^*]+?)(?::\*\*|\*\*\s*:)\s*(.*)$/;
const SUB_HEADING = /^\s*#{1,6}\s+(.*)$/;
const LIST_ITEM = /^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$/;
const BENEFIT_LABEL =
  /^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*)?(Bugün|Bununla|Kazanç|Kazanmadığımız)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$/i;
const LINK = /\[([^\]\n]+)\]\(([^()\s]+)\)/g;
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

function headingTitle(raw: string): string {
  return raw.replace(EMPHASIS, "$1").trim().replace(/\s*:$/, "");
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
  let pending = "";
  let at = 0;
  for (const match of text.matchAll(LINK)) {
    pending += text.slice(at, match.index);
    at = match.index + match[0].length;
    const href = safeHref(match[2]);
    if (href === null) {
      pending += match[0];
      continue;
    }
    if (pending !== "") inlines.push({ kind: "text", text: pending });
    pending = "";
    inlines.push({ kind: "link", text: match[1], href });
  }
  pending += text.slice(at);
  if (pending !== "") inlines.push({ kind: "text", text: pending });
  return inlines;
}

function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let items: string[][] | null = null;
  const flush = () => {
    if (paragraph.length > 0) blocks.push({ kind: "paragraph", inlines: parseInlines(paragraph.join(" ")) });
    if (items) blocks.push({ kind: "list", items: items.map((item) => parseInlines(item.join(" "))) });
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
      if (!items) flush();
      items ??= [];
      items.push([item[1].trim()]);
    } else if (items) {
      items[items.length - 1].push(line.trim());
    } else {
      paragraph.push(line.trim());
    }
  }
  flush();
  return blocks;
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
      if (label === "bugün") {
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
      current.push(line.trim());
      continue;
    }
    current = null;
    rest.push(line);
  }
  const inline = (parts: string[] | null) => (parts === null ? null : parseInlines(parts.join(" ")));
  return {
    title,
    pairs: pairs.map((pair) => ({
      today: parseInlines(pair.today.join(" ")),
      withIt: parseInlines(pair.withIt.join(" ")),
    })),
    gain: inline(gain),
    notGained: inline(notGained),
    rest: parseBlocks(rest),
  };
}

export function parseProposal(text: string): ParsedProposal {
  let title: string | null = null;
  const raw: { title: string; lines: string[] }[] = [{ title: "", lines: [] }];
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const hash = HASH_HEADING.exec(line);
    if (hash) {
      raw.push({ title: headingTitle(hash[1]), lines: [] });
      continue;
    }
    const bold = BOLD_HEADING.exec(line);
    if (bold && KNOWN_NAMES.some((name) => name.test(fold(bold[1])))) {
      raw.push({ title: bold[1].trim(), lines: bold[2].trim() === "" ? [] : [bold[2]] });
      continue;
    }
    const current = raw[raw.length - 1];
    if (title === null && raw.length === 1 && current.lines.every((seen) => seen.trim() === "")) {
      const heading = TITLE.exec(line);
      if (heading) {
        title = heading[1];
        continue;
      }
    }
    current.lines.push(line);
  }
  const sections: ProposalSection[] = [];
  let benefit: Benefit | null = null;
  for (const { title: name, lines } of raw) {
    const section: ProposalSection = { title: name, role: name === "" ? "other" : roleOf(name), blocks: parseBlocks(lines) };
    // The untitled lead exists only when something was written there.
    if (name === "" && section.blocks.length === 0) continue;
    sections.push(section);
    if (section.role === "benefit" && benefit === null) benefit = parseBenefit(name, lines);
  }
  return { title, sections, benefit };
}
