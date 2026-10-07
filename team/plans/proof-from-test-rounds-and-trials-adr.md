# ADR draft: the İlerleme strip's proof moves with test rounds and owner trials

Task: proof-from-test-rounds-and-trials (cycle d20261006). Status: proposed (the lead numbers it).

## Context

The owner, 2026-10-06: "kanıt kısmı neden ilerlemiyor, o da mı her döngüde gelişiyor". The Ofis
strip's "%20 gerçekte kanıtlı" was the v1.0 matrix's PROOF column, last edited 2026-09-19. The
team now works from the JARVIS roadmap, so that share never moves.

## Decision

1. The proof is computed per counted JARVIS row (the NEVER / HARDWARE row is not a goal), from
   what the Cloud Core stores, never from a document edit (`app.team.progress.proof`):
   - **staging'de kanıtlı**: of the stored test rounds on the CURRENT release (staging_sha and
     the release sha agree on their common prefix, 7 hex at least; any release when the server
     has none), the latest (`at`, then round id) that ran the row has no failed scenario;
   - **gerçekte kanıtlı**: the row's latest decided owner trial (`owner_trials` verdict
     oldu/olmadı, by `at`) is "oldu". A later "olmadı" takes it back; an open trial is no proof.
   - A round row or a task's `roadmap_row` names a JARVIS row (`progress.resolve_row`, one row
     at most, case-insensitive, `**` and trailing "(...)" notes dropped) when: the words are the
     same; the names before a remark agree ("Everywhere - the second PC", "Runs the house:
     lights" - split at " - ", " — ", ": "); or one is the other's leading words, AT LEAST THREE
     of them, ending at a word boundary (`PREFIX_MIN_WORDS`; "The", "Records", "Runs the" name
     no row - the return of 2026-10-06, d). A direct name wins over the table below.
   - The lead's table (`ROW_ALIASES` in progress.py; cards are never edited by hand):
     "browser-use, anywhere" -> "Researches anything, reads the world's data"; "Records
     everything" -> "Records everything and tells him, whenever he asks"; "Repairs" -> "Repairs
     and improves itself". `NOT_A_ROW`: "How it is built from here", "TEAM_PROTOCOL", "Runs the
     workshop", and an empty row. Their tasks and round rows are left out of the share and
     counted: `proof.outside = {count, unknown}`; `unknown` names a wording no rule knows. A test
     holds every distinct `roadmap_row` of `team/queue.json` to "names a row or is NOT_A_ROW".
2. A test round posts the passed and failed scenario counts per row and the round's ONE staging
   sha: `POST /v1/team/queue/proof {round, staging_sha, at, rows:[{row, passed, failed,
   families}]}`. A job's row is its `roadmap_row` when it has one, else its `why`, else its
   family, sent as written (300 chars at most): the Core resolves it, so the test-lead's role
   file needs no change. A card that never ran or wrote no result proves nothing; failed and
   broke are failures; a round whose results name no sha or two is not posted (said). The post
   never stops the round; `proof.json` is written beside the cards; `test-round.ps1 -PostProof
   -Round <r>` posts a finished round again. `progress.round_problems` is the one shape rule.
3. Storage: a `team_state` row of kind `proof` (no new table, no migration, as `status` and
   `models`), key = the round id (≤ 41 chars), `updated_at` = the round's `at`, doc = the round;
   a second post of a round replaces it. `DbStore` and `FileStore` (`team/proofs/<round>.json`)
   both refuse what either cannot keep (U+0000 in a row or family name, a Windows device name as
   round id) with 422. `read_proofs()` gives the newest 200 by `at`. The route sits under the
   owner session as every queue route; a non-object body is FastAPI's 422.
4. `GET /v1/team/office` feeds the strip the stored rounds and the queue it already read (the
   owner's trials); a store without `read_proofs` or one that fails leaves the strip without
   staging proof, never the office without an answer.
5. The strip reads, when the answer carries `proof`: "JARVIS hedefi: yapıldı %X · staging'de
   kanıtlı %Y · gerçekte kanıtlı %Z · satır dışı: N · Sıralı plan … · eski v1.0 listesi …", two
   more meters, and the rows of each proof. An answer without `proof` keeps the old line; `proof:
   null` says "kanıt okunamadı"; one without `outside` leaves "satır dışı" out.

## Left for the lead (outside the card's area - ALAN_ISTEGI)

- `services/api/tests/unit/test_team_state.py::test_every_route_and_body_field_the_powershell_
  client_uses_is_one_the_server_has` is RED: the new route is called by test-round.ps1, not by
  TeamQueue.ps1. One line in its `read_by_others`: `("POST", "/v1/team/queue/proof"):
  "scripts/testteam/test-round.ps1 posts a test round's proof (proof-from-test-rounds-and-trials)"`.
- `apps/web/app/core/office/officeApi.ts`: may move `ProgressProof` there (typed in
  OfficeProgress.tsx meanwhile).

## Consequences

- The share moves with every round on the current release and with every "oldu"; a release
  resets "staging'de kanıtlı" to the rounds run on it (honest: the old release's proof is not
  this one's). "gerçekte kanıtlı" outlives releases until a later "olmadı".
- A new card wording that is no row and not in the table shows in `outside.unknown` and turns
  the queue test RED when queue.json carries it; the lead adds it to the table.
- A `-Retest` pass is not posted yet (its cards carry their own retested sha); a follow-up.
