# ADR (taslak): a negated cancel verb is not the cancel verb

- Card: negation-fix-cancel-verb-stems (cycle d20261006)
- Status: proposed (the lead numbers it at merge)

## Context

`_CANCEL_VERB_STEMS` ("iptal", "sil", "kaldır") were matched by `_has`, a prefix match, so
"silme" read as "sil" and "iptal etme" as "iptal". The test team (t-manual-20261006e,
dil-dayanikliligi, staging 72884b71) heard 9 negated sentences cancel an alarm/routine, and a
bare "kaldırma" / "onu kaldır" create an alarm. The owner's rule (2026-09-19, 2026-10-06): a
negated or garbled sentence never deletes or cancels.

## Decision

1. `intents._positive_verb(tokens, *stems)` replaces `_has` for the cancel stems in the
   routine, memory and alarm families, and for the alarm wake stems. It reads the grammar,
   not a list of forms: what follows the stem is the negative suffix (`-ma/-me`, not the
   infinitive `-mak/-mek`; `-mı/-mi` before `-yor`) or layer 1's own negative form
   (`normalize.is_negative`); for "iptal" the negation is on the auxiliary "et".
2. The sentence forbids as a whole when it holds "sakın" (not ASCII "sakin" = calm) or a
   negative present verb ("istemiyorum"): no cancel.
3. The verbal noun (`-mayı/-meyi`) asks for the act only under "don't forget"
   ("uyandırmayı unutma" sets the alarm); otherwise ("silmeyi unut") it asks for no act.
4. "kaldır" with no alarm noun and a thing for its object ("onu/bunu/şunu kaldır") is not
   the wake verb: no alarm create. "Beni kaldır.", "Yarın yedide kaldır." keep the wake.
5. A negated sentence falls through to the other families and the model (`none`), which can
   ask - never a guess at the positive.

## Consequences

- 18 KNOWN_OPEN entries of the guard closed (72 -> 54). The other 54 belong to their own
  cards (research, calendar, evolution, macro, operator/exec, discard, native, process,
  rollback); `_positive_verb` is the helper those cards should reuse rather than per-table
  negation lists.
- Conservative by design: "Alarmı sil, artık istemiyorum" now reaches `none` (the model
  asks) - the owner rule prefers a question over a wrong delete.
