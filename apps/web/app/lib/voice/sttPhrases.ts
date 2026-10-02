/**
 * The words the on-device recogniser is told to expect (chrome-on-device-stt):
 * `SpeechRecognition.phrases`, Chrome's contextual biasing. Pure - the list is built
 * here from what the caller fetched, and only `localMode.ts` hands it to the recogniser.
 *
 * Three kinds, in the order the cap must respect:
 * 1. the device aliases - the ones the session's devices carry (`/v1/devices`), then the
 *    four spoken names the server always knows (`services/api/app/devices/aliases.py`);
 * 2. the application names of the capability list. `/v1/voice/capabilities` has no name
 *    field; it has the sentences the owner may say ("Hesap makinesini aç"), so the name is
 *    what stands before an open verb - in the case the owner says it in;
 * 3. the open-verb forms themselves (`_OPEN_VERB_FORMS` in `app/voice/intents.py`, the
 *    spellings with Turkish letters: a recogniser is biased towards words, not ASCII folds).
 *
 * The boosts are modest on purpose. Chrome accepts [0, 10] and throws outside it; a high
 * boost makes a recogniser hear the phrase in sentences that never held it.
 *
 * The list holds the owner's own device names: it goes to the recogniser and nowhere
 * else - not into a log line, a snapshot or an event payload.
 */

export type PhraseSources = {
  /** Aliases of the devices this session can address. */
  deviceAliases?: string[];
  /** The capability list's example sentences (`CapabilityRow.phrases`). */
  capabilityPhrases?: string[];
};

export type SttPhrase = { phrase: string; boost: number };

export const PHRASE_CAP = 64;
export const MAX_PHRASE_CHARS = 60;

export const BOOST_ALIAS = 2.0;
export const BOOST_APPLICATION = 1.5;
export const BOOST_VERB = 1.0;

/** The spoken device names the server resolves with no registration at all. */
export const ALIAS_FLOOR: readonly string[] = ["ev", "iş", "laptop", "ofis"];
export const OPEN_VERB_FORMS: readonly string[] = ["aç", "açsana", "açar", "açın", "açınız"];

const TURKISH = "tr-TR";

/** Chrome's range; anything that is not a number is the default boost. */
export function clampBoost(boost: number): number {
  if (typeof boost !== "number" || Number.isNaN(boost)) return 1.0;
  return Math.min(10, Math.max(0, boost));
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function tidy(text: string): string {
  return text.trim().split(/\s+/).join(" ");
}

/**
 * What stands before an open verb in a sentence the owner may say. The verb is matched
 * as the sentence's LAST WORD, whole: a stem match would read "açıkla" as "aç".
 */
export function applicationNamesIn(phrases: readonly string[], verbs: readonly string[] = OPEN_VERB_FORMS): string[] {
  const names: string[] = [];
  for (const raw of strings(phrases)) {
    const words = tidy(raw.replace(/[.!?…]+$/u, "")).split(" ");
    if (words.length < 2) continue;
    const last = (words[words.length - 1] ?? "").toLocaleLowerCase(TURKISH);
    if (!verbs.includes(last)) continue;
    names.push(words.slice(0, -1).join(" "));
  }
  return names;
}

export function buildPhrases(sources: PhraseSources | null | undefined, cap: number = PHRASE_CAP): SttPhrase[] {
  const limit = Number.isFinite(cap) ? Math.max(0, Math.floor(cap)) : PHRASE_CAP;
  const out: SttPhrase[] = [];
  const seen = new Set<string>();
  const add = (candidates: readonly string[], boost: number): void => {
    for (const candidate of candidates) {
      const phrase = tidy(candidate);
      if (!phrase || phrase.length > MAX_PHRASE_CHARS) continue;
      // Turkish casing: "IŞIK" is "ışık", and "İş" is "iş" - one phrase each, not two.
      const key = phrase.toLocaleLowerCase(TURKISH);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ phrase, boost: clampBoost(boost) });
    }
  };
  // Aliases first: the cap cuts from the end, so it can never cut them.
  add(strings(sources?.deviceAliases), BOOST_ALIAS);
  add(ALIAS_FLOOR, BOOST_ALIAS);
  add(applicationNamesIn(strings(sources?.capabilityPhrases)), BOOST_APPLICATION);
  add(OPEN_VERB_FORMS, BOOST_VERB);
  return out.slice(0, limit);
}
