/**
 * B24 req 712/713: the badges, in the matrix's own vocabulary.
 *
 * Both requirements are about the same thing from two sides. 712 asks for a status badge
 * per feature; 713 asks for a proof badge, and its note is the whole point — *"Bu matrisi
 * ürüne bağlar."* Until now the record of what this system has, what state each piece is
 * in and how each claim was proven lived in a markdown file that only the person building
 * the system ever opened. The owner had no way to tell a feature proven on their own
 * machine from one proven by a test from one nobody has proven at all.
 *
 * The classes are NOT restated here. `IMPL_CLASSES` and `PROOF_CLASSES` come from
 * `matrix.generated.ts`, which a script derives from the document — including the proof
 * abbreviations, which are read from the document's own LEGEND table. What this file adds
 * is the only thing the document cannot supply: Turkish for a reader who is not the
 * person maintaining it.
 *
 * The two `Record<…Class, string>` types are the guard. A class that appears in the
 * document and has no Turkish word here is a type error, not a blank badge.
 *
 * The implementation vocabulary is closed while its use is not: when B45 closed the last
 * `BROKEN` row the generated union lost `BROKEN`, and a word for it became a type error.
 * `ImplVocabulary` still demands a word for every class in use and keeps the words for
 * classes the document may use again, so a reopened row never renders blank.
 */

import {
  type ImplClass,
  type ProofClass,
  PROOF_EXPANSION,
} from "./matrix.generated";

type ImplVocabulary<T> = Record<ImplClass, T> & { readonly [status: string]: T };

/** What each implementation status means to the owner, not to the person building it. */
export const IMPL_LABEL: ImplVocabulary<string> = {
  DONE: "tamam",
  PARTIAL: "kısmi",
  MISSING: "yok",
  BROKEN: "bozuk",
  DEFERRED: "ertelendi",
  BLOCKED_OWNER: "sizi bekliyor",
  BLOCKED_PROVIDER: "sağlayıcı bekliyor",
};

/**
 * How the claim was proven, in words.
 *
 * The distinction the abbreviations carry is the one the owner most needs and would never
 * guess: `PA` means a test says so, `PR` means it was watched happening on a real machine,
 * and `NYP` means nobody has checked. A product that shows only "tamam" for all three is
 * hiding exactly the difference that matters.
 */
export const PROOF_LABEL: Record<ProofClass, string> = {
  PR: "gerçekte görüldü",
  PA: "testle kanıtlandı",
  PX: "vekille kanıtlandı",
  NYP: "henüz kanıtlanmadı",
  BLK: "engelli",
  PU: "sağlayıcı yok",
};

/** The document's own expansion, for the badge's tooltip. */
export function proofTitle(proof: ProofClass): string {
  return `${PROOF_EXPANSION[proof]} — ${PROOF_LABEL[proof]}`;
}

/**
 * Which of four tones a status badge takes.
 *
 * Kept separate from the label so the colour is a property of the CLASS rather than of a
 * sentence somebody might reword: `good` is finished, `warn` is started and not finished,
 * `bad` is known broken or absent, and `wait` is a thing this project cannot finish on its
 * own. Nothing in the product decides a tone by reading Turkish.
 */
export type BadgeTone = "good" | "warn" | "bad" | "wait";

export const IMPL_TONE: ImplVocabulary<BadgeTone> = {
  DONE: "good",
  PARTIAL: "warn",
  MISSING: "bad",
  BROKEN: "bad",
  DEFERRED: "wait",
  BLOCKED_OWNER: "wait",
  BLOCKED_PROVIDER: "wait",
};

export const PROOF_TONE: Record<ProofClass, BadgeTone> = {
  PR: "good",
  PA: "good",
  PX: "warn",
  NYP: "warn",
  BLK: "wait",
  PU: "wait",
};
