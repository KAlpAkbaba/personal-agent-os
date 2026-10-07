# ADR draft: test findings go to the Proje Yöneticisi's split, once per failing step, at most ten a round

Task: test-findings-to-pm-not-owner (cycle d20261007). Status: proposed (the lead numbers it).

## Context

Measured 2026-10-06 22:20: 61 `test-fail-*` cards of two automatic rounds sat in `awaiting_owner`
(the Onay Merkezi: "CTO 61 onay"), against the owner's rule of 2026-10-03 (findings go
worker -> PM -> Danışman -> PM -> worker, never via the owner). Cause: a forwarded card was a plain
`proposed` card with an area and no `proposal`; `Get-TeamNextRole` moves such a card to the owner's
gate. Most were duplicates (30 language, 22 alarm; one sentence three times): the card id hashed the
actual and the scenario, so the same failing step with another actual was a new card, and a card the
Danışman folded by hand (done, "BİRLEŞTİRİLDİ") did not stop the next round.

## Decision

1. A forwarded test card is `proposed`, with NO area, a `proposal` text and reason/proposal prefixed
   `Test PY bulgusu:`; `Test-TeamSplitCandidate` makes it the Proje Yöneticisi's split, never the
   owner's gate. The family's known code paths go into the proposal as "Önerilen ilk alan".
2. A finding's identity across rounds is its signature: family + step name + expected, folded (lower
   case, Turkish letters to ASCII, white space collapsed). Each card carries `İmza:` lines; cards
   written before (the 61) are read by their title and `Beklenen:` line. A signature held by a card
   of the store (any open state, or `done` with a reason saying birleştirildi / bölündü) opens no
   card; a card done by its fix does not hold it (the step failing again is news).
3. At most 10 cards a round (`$script:TestTeamMaxForwards`); the rest go into one card
   `test-fail-ozet-<round>` that names each failure and holds their signatures.
4. Each card's proposal tells the PM to fold open test cards of one family into one card with a file
   area. The PM's own duty text (`.claude/agents/lead.md`) was outside this card's area: the lead
   may add the same sentence there.

## Consequences

- No test finding reaches the owner's approval queue; the PM triages by the prefix.
- A recurring failure costs no new card while its card is open, merged or folded.
- A burst of failures costs at most 11 cards a round.
- Folding detection keys on the reason words "birleştirildi"/"bölündü"; a fold recorded with
  other words would let the step open a card again (visible, not silent).
