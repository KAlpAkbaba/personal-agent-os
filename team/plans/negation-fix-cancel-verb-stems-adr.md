# ADR (taslak): a negated cancel verb is not the cancel verb

- Card: negation-fix-cancel-verb-stems (cycle d20261006)
- Status: proposed (the lead numbers it at merge)

## Context

`_CANCEL_VERB_STEMS` ("iptal", "sil", "kaldır") were matched by `_has`, a prefix match, so
"silme" read as "sil" and "iptal etme" as "iptal". The test team (t-manual-20261006e,
dil-dayanikliligi, staging 72884b71) heard 9 negated sentences cancel an alarm/routine, and a
bare "kaldırma" / "onu kaldır" create an alarm. The owner's rule (2026-09-19, 2026-10-06): a
negated or garbled sentence never deletes or cancels. The inspector's return added: "silmek
istemem", "sil demedim", "silinmesin", "sildirme", "iptal ettirme", a verb taken back ("sil,
hayır silme"), "takibi kaldır" -> alarm_create and "uyandırmayı unut" -> memory_forget.

## Decision

1. `intents._positive_verb(tokens, *stems)` replaces `_has` for the cancel stems in the
   routine, memory and alarm families, and for the alarm wake stems. It reads the grammar,
   not a list of forms: after the stem and its voice suffixes (passive/reflexive -(ı)l/-(ı)n,
   causative -dır/-t) stands the negative suffix (`-ma/-me`, not the infinitive `-mak/-mek`;
   `-mı/-mi` before `-yor`); for "iptal" the negation is on the auxiliary "et"/"ed".
2. ANY negated occurrence of the stem in the sentence blocks: "Alarmı sil, hayır silme." and
   "Alarmı silme, kaldır." take the request back - no cancel.
3. The sentence forbids as a whole when it holds "sakın" (not ASCII "sakin" = calm) or a
   finite negative verb of any tense: present "istemiyorum", past "demedim", aorist "istemem"
   / "istemez" / "istemeyiz" (three-letter head, so "tamam" is no "-mam"), future, reported,
   necessitative. "silemem" (cannot) is read here, so no separate "can"-vowel branch.
4. The verbal noun (`-mayı/-meyi`) asks for the act only under "don't forget"
   ("uyandırmayı unutma" sets the alarm); "silmeyi unut" asks for no act, and "X-mayı unut"
   is no memory deletion (forgetting an act is not a memory).
5. "kaldır" is the wake only with the sleeper as its object ("Beni kaldır."): the object is
   read, not a pronoun list, so "Takibi kaldır." / "Onu kaldır." are no alarm. Without an
   object ("Yarın yedide kaldır.") it is no longer a wake: none, the model asks.
6. Layer 1's `normalize.is_negative` branch was removed from `_is_negated`: no test killed
   its mutation; the suffix reading covers every form it held.
7. A negated sentence falls through to the other families and the model (`none`), which can
   ask - never a guess at the positive.

## Consequences

- 18 KNOWN_OPEN entries of the guard closed (72 -> 54). The other 54 belong to their own
  cards (research, calendar, evolution, macro, operator/exec, discard, native, process,
  rollback); `_positive_verb` is the helper those cards should reuse rather than per-table
  negation lists.
- Conservative by design: "Alarmı sil, artık istemiyorum" and "beni yedide uyandırmayı unut"
  now reach `none` (the model asks) - the owner rule prefers a question over a wrong act.
