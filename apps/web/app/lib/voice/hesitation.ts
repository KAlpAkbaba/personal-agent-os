/**
 * Turkish hesitation guard — the client side of end-of-turn (M12 spec §5,
 * ADR-0036 §9).
 *
 * The provider's VAD reports `speech_stopped`. Before the client treats that
 * as the owner's end of turn it looks at the tail of what the owner just said:
 * a filler ("şey", "yani", "hani", "ııı", a trailing drawn-out vowel…) means
 * the owner is thinking, not done, so the client extends its trailing-silence
 * window. If speech resumes inside the window no end-of-turn is reported and
 * a response that the provider started prematurely is cancelled by the
 * controller — the false-barge case the harness measures.
 *
 * Pure and deterministic: no timers, no I/O. The controller owns the clock.
 */

/** Mirrors `app/voice/intents.py` FILLERS so both sides agree on the vocabulary. */
export const DEFAULT_FILLERS: ReadonlySet<string> = new Set([
  "şey",
  "yani",
  "hani",
  "işte",
  "böyle",
  "ya",
  "yaa",
  "ee",
  "eee",
  "ıı",
  "ııı",
  "ı",
  "hmm",
  "hm",
  "hımm",
  "hım",
  "ehm",
  "aa",
  "aaa",
  "of",
  "e",
  "mm",
  "mmm",
]);

const ELONGATED_FILLER = /^(?:ı{2,}|e{2,}|a{2,}|m{2,}|h[ıi]?m+|ee+h?|ya+)$/;
/** A word that trails off into a stretched final vowel ("raporuuu", "sonraaa"). */
const TRAILING_VOWEL = /([aeıioöuü])\1{2,}$/;
const TRAILING_PUNCT = /[.,;:!?…]+$/;

export type HesitationGuardConfig = {
  /** Trailing silence the guard applies to every turn before it is "over". */
  baseTrailingSilenceMs: number;
  /** Extra hold when the tail is a filler or a drawn-out vowel. */
  fillerExtensionMs: number;
  /** Cap on the total hold, whatever the tail looks like. */
  maxHoldMs: number;
  fillers?: ReadonlySet<string>;
};

export const DEFAULT_HESITATION_CONFIG: HesitationGuardConfig = {
  baseTrailingSilenceMs: 200,
  fillerExtensionMs: 700,
  maxHoldMs: 1500,
};

export type HesitationDecision = {
  /** How long to wait after `speech_stopped` before reporting end-of-turn. */
  holdMs: number;
  /** Why: "filler" | "elongated" | "none". */
  reason: "filler" | "elongated" | "none";
  /** The token the decision was made on (for the UI / audit payload). */
  tail: string | null;
};

export function turkishLower(text: string): string {
  // Turkish casing: İ → i, I → ı, then a normal lower-case for the rest.
  return text.replace(/İ/g, "i").replace(/I/g, "ı").toLowerCase();
}

export function lastToken(text: string): string | null {
  const tokens = turkishLower(text)
    .replace(/[‘’'"()[\]{}]/g, " ")
    .split(/\s+/)
    .map((t) => t.replace(TRAILING_PUNCT, ""))
    .filter(Boolean);
  return tokens.length ? tokens[tokens.length - 1] : null;
}

export function isFiller(token: string, fillers: ReadonlySet<string> = DEFAULT_FILLERS): boolean {
  return fillers.has(token) || ELONGATED_FILLER.test(token);
}

export function classifyTail(
  transcriptTail: string,
  fillers: ReadonlySet<string> = DEFAULT_FILLERS,
): HesitationDecision["reason"] {
  const token = lastToken(transcriptTail);
  if (!token) return "none";
  if (isFiller(token, fillers)) return "filler";
  if (TRAILING_VOWEL.test(token)) return "elongated";
  return "none";
}

export class HesitationGuard {
  private readonly config: HesitationGuardConfig;
  private holdUntil: number | null = null;
  private heldCount = 0;
  private resumedWithinHoldCount = 0;

  constructor(config: Partial<HesitationGuardConfig> = {}) {
    this.config = { ...DEFAULT_HESITATION_CONFIG, ...config };
  }

  /** Decide the hold for a `speech_stopped` at `now` given the transcript tail. */
  decide(transcriptTail: string, now: number): HesitationDecision {
    const reason = classifyTail(transcriptTail, this.config.fillers);
    const tail = lastToken(transcriptTail);
    let holdMs = this.config.baseTrailingSilenceMs;
    if (reason !== "none") holdMs += this.config.fillerExtensionMs;
    holdMs = Math.max(0, Math.min(holdMs, this.config.maxHoldMs));
    this.holdUntil = now + holdMs;
    if (reason !== "none") this.heldCount += 1;
    return { holdMs, reason, tail };
  }

  /** The owner started speaking again at `now`: was a hold still open? */
  speechStarted(now: number): boolean {
    const within = this.holdUntil !== null && now < this.holdUntil;
    if (within) this.resumedWithinHoldCount += 1;
    this.holdUntil = null;
    return within;
  }

  /** The hold elapsed without speech: the turn is over. */
  expire(): void {
    this.holdUntil = null;
  }

  get isHolding(): boolean {
    return this.holdUntil !== null;
  }

  stats(): { held: number; resumed_within_hold: number } {
    return { held: this.heldCount, resumed_within_hold: this.resumedWithinHoldCount };
  }
}
