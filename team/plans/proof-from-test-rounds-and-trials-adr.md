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
   - A round row or a task's `roadmap_row` names a JARVIS row when the words are the same, or
     one is the other's leading words ending at a word boundary (case-insensitive, `**` dropped):
     "Runs the house" names "Runs the house: lights, doors"; "Talk" never names "Talks".
2. A test round posts, per row its plan's jobs name (`roadmap_row` on a job of plan.json), the
   passed and failed scenario counts and the round's ONE staging sha:
   `POST /v1/team/queue/proof {round, staging_sha, at, rows:[{row, passed, failed, families}]}`.
   A card that never ran or wrote no result proves nothing; failed and broke are failures; a
   round whose results name no sha or two shas is not posted (said). The post never stops the
   round; `proof.json` is written beside the cards; `test-round.ps1 -PostProof -Round <r>`
   posts a finished round again. `progress.round_problems` is the one validation rule.
3. The strip reads, when the answer carries `proof`: "JARVIS hedefi: yapıldı %X · staging'de
   kanıtlı %Y · gerçekte kanıtlı %Z · Sıralı plan … · eski v1.0 listesi …", two more meters, and
   the panel lists the rows of each proof. An answer without `proof` keeps the old line; `proof:
   null` says "kanıt okunamadı".

## Left for the lead (outside the card's area - ALAN_ISTEGI)

- `services/api/app/team/store.py`: a `team_state` row kind `proof` (String(16) - no new table,
  no migration, as `status`/`models`), key = the round id (≤ 41 chars < VARCHAR(80)),
  doc = the round; `put_proof(doc)` (validated by `progress.round_problems`, a second put of
  one round replaces it) and `read_proofs()` (all, or the newest N by `at`) on BOTH stores
  (FileStore: `team/proof/<round>.json`).
- `services/api/app/team/routes.py`: `POST /v1/team/queue/proof` (the queue token's auth, 422
  with the problems on a bad doc) and `_progress(request)` passing
  `rounds=store.read_proofs(), queue=<the queue read_office already holds>`.
- `scripts/testteam/roles/test-lead.md` (+ the installed `.claude/agents/test-lead.md`): every
  job carries `roadmap_row`, the JARVIS row (its first column's words) the family exercises.
- `apps/web/app/core/office/officeApi.ts`: may move `ProgressProof` there and add
  `proof?: ProgressProof | null` to `OfficeProgress` (typed in OfficeProgress.tsx meanwhile).

## Consequences

- The share moves with every round on the current release and with every "oldu"; a release
  resets "staging'de kanıtlı" to the rounds run on it (honest: the old release's proof is not
  this one's). "gerçekte kanıtlı" outlives releases until a later "olmadı".
- A `-Retest` pass is not posted yet (its cards carry their own retested sha); a follow-up.
