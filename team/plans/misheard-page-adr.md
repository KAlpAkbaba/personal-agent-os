# ADR (unnumbered; the lead numbers it): the misheard notebook's page, `/core/misheard`

Status: accepted (card misheard-page, cycle d20261003). Builds on ADR-0254 (the store and
`/v1/voice/misheard`).

## Decision

1. **Its own page for now.** The proposal (team/proposals/2026-10-02-yanlis-anlasilan-cumle-defteri.md)
   places 'Ne demek istemiştin?' in the Onay Merkezi, but `apps/web/app/core/approvals` belongs
   to another approved card in the same cycle (owner-trials-page). Two cards editing one page in
   one night is a merge conflict by design, so the list lives at `/core/misheard` and the core
   controls get one static link after the Ofis link (no count, no new prop). Embedding the list
   in the Onay Merkezi is a later card; the view (`MisheardView`) is a pure component so that
   card can mount it as it is.
2. **'Defteri unut' is one press.** One button, ONE `DELETE /v1/voice/misheard`, no dialog, no
   second question (owner rule 2026-09-18: the first word applies). The page then says how many
   sentences were deleted, from the server's `{deleted: n}`. 'Sil' on a row is the same: one
   press, that row's id.
3. **What a row shows:** the one sentence exactly as the recogniser wrote it; when it was heard,
   in the owner's local time; the mode in Turkish ('Ücretli' / 'Yerel'); the machine, the
   recogniser and the confidence band when the server knew them (left out, never "null", when
   it did not); the failed tool's name for `tool_failed`; why it is here as one Turkish sentence
   per reason (four reasons, four sentences; an unknown reason is shown as its own code, never
   hidden); an input 'Ne demek istemiştin?' with 'Kaydet' (1..2000 characters) until answered,
   and the owner's meaning once answered. The head says how many are open and, from the
   server's `retention_days`, that the sentences are kept as text for that many days and then
   delete themselves, and that no sound is kept.
4. **What a row never shows:** audio (there is none - the store keeps text only), any
   transcript beyond the one sentence, the session id, the confidence number or the router's
   resolved intent (they are for the evaluation, not for the owner's answer).
5. **A refusal stays a refusal.** Every call returns `{ok: false, code, message}` for a non-2xx
   answer and the page shows the server's own Turkish sentence; nothing is turned into success.

## Consequences

- The client's paths and the item's fields are held to `routes.py` and `models.py` by
  `apps/web/tests/misheard/contract.test.ts`; a renamed route or column turns it red.
- When the Onay Merkezi embeds the list, the link in CoreControls may go; the nav test in
  `tests/misheard/nav.test.ts` moves with it.
