/**
 * Two-stage interruption policy while the assistant is speaking (owner-observed
 * defect, 2026-09-04: other people talking in the room cut the assistant off).
 *
 * The provider no longer cancels its own response on speech (Cloud Core sets
 * `turn_detection.interrupt_response = false`); the client owns interruption:
 *
 * A. Fast control lane — an explicit control phrase ("dur", "bekle", …) in a
 *    provisional or final owner transcript interrupts IMMEDIATELY, whatever
 *    the conversational lane decided about the same speech. Token match after
 *    Turkish-safe folding, never a substring: "durum" is not "dur".
 * B. Conversational lane — a speech onset only applies the REVERSIBLE early
 *    mute (ADR-0047 §2) and becomes a potential barge-in; the irreversible
 *    cancel needs, inside `BARGE_IN_CONFIRM_WINDOW_MS`, a STABLE onset
 *    (≥ `BARGE_IN_STABLE_ONSET_MS` of ongoing speech), NEAR-FIELD confidence
 *    from the local gate (level above the calibrated floor + open margin +
 *    playback margin, which already carry the per-device learned offsets of
 *    ADR-0047 §4, with a speech-like spectrum — a distant voice is quieter and
 *    has less speech-band energy) and a PLAUSIBLE WORD in a provisional
 *    transcript (any non-filler token; fillers per hesitation.ts). Otherwise
 *    the mute is reverted and playback simply continues.
 *
 * Pure helpers and the named constants; the controller owns the clock.
 */

import { DEFAULT_FILLERS, isFiller, turkishLower } from "./hesitation";
import type { OnsetLevel } from "./ports";

/** How long the conversational lane waits for its evidence before reverting the mute. */
export const BARGE_IN_CONFIRM_WINDOW_MS = 800;
/** Speech shorter than this (onset → end) is a burst, never an interruption. */
export const BARGE_IN_STABLE_ONSET_MS = 350;
/** Peak level of the onset above the gate's (playback) open threshold that counts as near-field. */
export const NEAR_FIELD_MIN_MARGIN_DB = 6;
/** Spectral score (speech-band energy, non-flatness, ZCR) the onset must reach to count as near-field. */
export const NEAR_FIELD_MIN_SPECTRAL = 0.5;
/** A confirmed interruption with no final owner utterance inside this window was a false interruption. */
export const FALSE_INTERRUPTION_WINDOW_MS = 3000;
/** A plausible word has at least this many letters (a lone "a"/"o" is not evidence of a sentence). */
const MIN_WORD_CHARS = 2;

/**
 * Explicit control phrases. Single tokens match one token; multi-word phrases
 * match consecutive tokens. Mirrors the server's STOP_WORDS vocabulary and its
 * token (not substring) semantics.
 */
export const CONTROL_PHRASES: readonly string[] = ["dur", "durdur", "bekle", "sus", "kes", "yeter", "bir dakika", "tamam dur"];

/** Turkish-safe folding and punctuation stripping: the tokens of a transcript fragment. */
export function foldTokens(text: string): string[] {
  return turkishLower(text)
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .split(" ")
    .filter(Boolean);
}

/** The control phrase the text contains (token match), or null. */
export function findControlPhrase(text: string, phrases: readonly string[] = CONTROL_PHRASES): string | null {
  const tokens = foldTokens(text);
  if (tokens.length === 0) return null;
  for (const phrase of phrases) {
    const parts = foldTokens(phrase);
    if (parts.length === 0) continue;
    for (let i = 0; i + parts.length <= tokens.length; i += 1) {
      let matched = true;
      for (let j = 0; j < parts.length; j += 1) {
        if (tokens[i + j] !== parts[j]) {
          matched = false;
          break;
        }
      }
      if (matched) return phrase;
    }
  }
  return null;
}

/** At least one token that is not a filler and long enough to be a word. */
export function hasPlausibleWord(text: string, fillers: ReadonlySet<string> = DEFAULT_FILLERS): boolean {
  return foldTokens(text).some((token) => token.length >= MIN_WORD_CHARS && !isFiller(token, fillers));
}

/**
 * Near-field confidence rule on the local gate's onset level. `undefined` =
 * the detector cannot measure levels at all (no local gate): the rule cannot
 * block, the other two carry. `null` = the gate saw no candidate for this
 * speech (below its calibrated margin): distant or too quiet to be an
 * interruption. Otherwise both the level and the spectrum must pass.
 */
export function isNearField(level: OnsetLevel | null | undefined): boolean | undefined {
  if (level === undefined) return undefined;
  if (level === null) return false;
  return level.marginDb >= NEAR_FIELD_MIN_MARGIN_DB && level.spectralScore >= NEAR_FIELD_MIN_SPECTRAL;
}

/** Keep the strongest evidence seen for one onset (the gate's accumulator can close before the window ends). */
export function strongerLevel(current: OnsetLevel | null, next: OnsetLevel | null | undefined): OnsetLevel | null {
  if (!next) return current;
  if (!current) return { ...next };
  return {
    marginDb: Math.max(current.marginDb, next.marginDb),
    spectralScore: Math.max(current.spectralScore, next.spectralScore),
    frames: Math.max(current.frames, next.frames),
  };
}
