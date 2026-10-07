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

## Addendum (return 1, inspection of 2026-10-07)

- A test card's `proposal` is ONE line free of newlines, control characters and `"<>|`
  (`ConvertTo-TestTeamProposalLine`: lines joined ` ; `, `->` as `→`, `<>` as `‹›`, `"` as `'`,
  `|` as `/`). The split run's prompt carries only id/title/roadmap_row/proposal, so the finding
  stays in it; but cycle.ps1 `Send-IdeaTexts` runs `Path.GetFileName` on every `proposed` card's
  proposal, and on PowerShell 5.1 a multi-line proposal threw there and stopped the cycle.
  The guard in cycle.ps1 itself (test the `team/proposals/` pattern before `GetFileName`) is
  outside this card's area and is asked for (alan isteği): any other card with a prose proposal
  would still stop the cycle.
- A second failure of a signature in the SAME round is held by the id of the card opened for the
  first (or `test-fail-ozet-<round>` when it was summarised), never a placeholder, so the retest
  finds and closes that card.
