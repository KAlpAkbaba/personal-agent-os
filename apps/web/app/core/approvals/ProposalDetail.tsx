/**
 * What "Detay" opens: one proposal as readable sections (owner, 2026-10-01: "bana örneklerle,
 * bunu yaparsak ne avantaj ve gelişmeler sağlar, onu söylesin").
 *
 * The benefit examples come first, each as its "Bugün → Bununla" pair, then "Ne", then the
 * rest in the file's order. Every word of the proposal is rendered as a text node: nothing in
 * it can become markup, and the only attribute it can reach is the `href` of an http/https
 * link (`proposalSections.ts`). The panel is never empty - a missing text and a missing
 * benefit section each have their sentence.
 */

import type { ReactNode } from "react";

import {
  parseProposal,
  type Benefit,
  type Block,
  type Inline,
  type ProposalSection,
} from "./proposalSections";

const NO_TEXT_TR = "Bu fikrin metni henüz sunucuya ulaşmadı.";
const NO_BENEFIT_TR =
  "Bu öneri fayda örnekleri olmadan yazılmış; araştırmacı bir sonraki koşuda ekleyecek.";

function Inlines({ inlines }: { inlines: Inline[] }) {
  return (
    <>
      {inlines.map((inline, index) =>
        inline.kind === "link" ? (
          <a key={index} href={inline.href} target="_blank" rel="noopener noreferrer">
            {inline.text}
          </a>
        ) : (
          inline.text
        ),
      )}
    </>
  );
}

function ListTag({ ordered, children }: { ordered: boolean; children: ReactNode }) {
  return ordered ? <ol>{children}</ol> : <ul>{children}</ul>;
}

function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((block, index) =>
        block.kind === "paragraph" ? (
          <p key={index}>
            <Inlines inlines={block.inlines} />
          </p>
        ) : (
          <ListTag key={index} ordered={block.ordered}>
            {block.items.map((item, at) => (
              <li key={at}>
                <Inlines inlines={item} />
              </li>
            ))}
          </ListTag>
        ),
      )}
    </>
  );
}

function BenefitSection({ benefit }: { benefit: Benefit }) {
  // A heading over nothing is not shown.
  if (benefit.pairs.length === 0 && !benefit.gain && !benefit.notGained && benefit.rest.length === 0) return null;
  return (
    <section data-detail-section="benefit">
      <h3>{benefit.title}</h3>
      {benefit.pairs.length > 0 && (
        <ol>
          {benefit.pairs.map((pair, index) => (
            <li key={index} data-benefit-pair>
              {/* A half pair shows the half it has: a label is never left with nothing after it. */}
              {pair.today.length > 0 && (
                <p>
                  <strong>Bugün:</strong> <Inlines inlines={pair.today} />
                </p>
              )}
              {pair.withIt.length > 0 && (
                <p>
                  <strong>→ Bununla:</strong> <Inlines inlines={pair.withIt} />
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
      {benefit.gain && (
        <p>
          <strong>Kazanç:</strong> <Inlines inlines={benefit.gain} />
        </p>
      )}
      {benefit.notGained && (
        <p>
          <strong>Kazanmadığımız:</strong> <Inlines inlines={benefit.notGained} />
        </p>
      )}
      <Blocks blocks={benefit.rest} />
    </section>
  );
}

function Section({ section }: { section: ProposalSection }) {
  return (
    <section data-detail-section={section.role}>
      {section.title !== "" && <h3>{section.title}</h3>}
      <Blocks blocks={section.blocks} />
    </section>
  );
}

export default function ProposalDetail({ text }: { text: string | null }) {
  if (text === null || text.trim() === "") return <p className="muted">{NO_TEXT_TR}</p>;
  const parsed = parseProposal(text);
  const shownAsBenefit = parsed.benefit ? parsed.sections.find((section) => section.role === "benefit") : undefined;
  const others = parsed.sections.filter((section) => section !== shownAsBenefit);
  const ordered = [
    ...others.filter((section) => section.role === "what"),
    ...others.filter((section) => section.role !== "what"),
  ];
  return (
    <>
      {/* A benefit section with no "Bugün / Bununla" example in it is still without examples. */}
      {(parsed.benefit?.pairs.length ?? 0) === 0 && <p className="muted">{NO_BENEFIT_TR}</p>}
      {parsed.benefit && <BenefitSection benefit={parsed.benefit} />}
      {ordered.map((section, index) => (
        <Section key={index} section={section} />
      ))}
    </>
  );
}
