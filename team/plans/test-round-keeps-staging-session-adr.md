# ADR draft: a test round seeds the staging session once, and a dead session is 'environment'

Task: test-round-keeps-staging-session (cycle d20261006). The lead numbers this and moves it into
docs/DECISIONS.md.

## Context: what happened in t-d20261006 (measured 2026-10-06)

Staging api log (`docker logs -t pagentos-staging-api`, staging fba299af):

- 16:38:35Z `POST /v1/identity/sessions` 201: the 19:38 local seed (seed.ps1 rotates the
  credential with `docker exec ... app.identity.recover --rotate`, which leaves no HTTP line,
  then opens a session).
- 16:38:35Z..16:56:48Z: no identity request at all. Nothing in the round rotated anything first.
- 16:56:48Z: the testers' first requests, ten `identity_rejected refusal=revoked
  reason=owner_credential_rotated` in 0.4 s.
- 16:57:28Z, 16:59:18Z, 17:01:39Z/17:01:56Z: three more seeds. Each rotation revoked the
  sessions of the others, hence the later 401s (nöbet job, tester-3 at 16:59:55Z, 17:00:52Z).

Testers' round folder (K:\AI\tmp-team\testteam\t-d20261006, the stream-json logs):

- tester-1 at 16:56:57Z and tester-2 at 16:57:17Z read
  `C:\Users\alpak\AppData\Local\PagentOS\staging\owner.json` and got **seeded_at
  2026-10-04T09:16:00Z** (mtime Oct 4 12:16). That is a two-day-old session from a credential
  rotated since then.
- The 19:38 seed's file was in `...\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\PagentOS\staging\owner.json`.
  The seed ran from a Claude desktop session, and its %LOCALAPPDATA% writes go to the MSIX
  folder (see the HANDOFF note on the scheduler). So "owner.json unchanged since 19:38" was
  true of the desktop's copy. The round (started from the scheduler, outside MSIX) and its
  testers read the real one.
- Then tester-2 (16:57:27Z, `seed.ps1` inside its probe command), a desktop-side check
  (16:59:18Z, which rewrote the MSIX copy) and tester-1 (17:01:04Z, "Re-seed the staging owner
  session") each rotated the credential and revoked the others.

**Cause:** two owner.json files (the MSIX redirection), so the round started on a stale
session. Testers then re-seeded in parallel and each seed revoked the rest. No identity route was
called by a scenario.

## Decision

1. `test-round.ps1` runs `scripts/staging/seed.ps1` once, in its own process, before its first
   tester. The testers inherit that environment, so they read the file it wrote. If
   `/v1/identity/sessions/current` with the seeded token does not answer 200, the round starts
   no tester, opens no card, posts one board note and exits 1 (cards stay planned). `-NoAuth`
   (the tests' stand-in) skips both.
2. `run-scenario.ps1` does not send a step or cleanup step that would change identity: any
   write under `/v1/identity/`, `/v1/devices/enroll`, `/v1/devices/<id>/revoke`, or a path with
   a `rotate`/`credential(s)` segment. It is recorded under `refused` (state `refused`) with a
   Turkish reason. A breaking ladder aimed at one refuses the whole scenario (exit 2). GETs stay
   allowed.
3. A run whose failed steps are ALL 401 (at least one), while the session's own
   `/v1/identity/sessions/current` answers 401, is `environment` (exit 4, no ladder). The api's
   401 body is only `unauthorized`, and the reason `owner_credential_rotated` exists only in
   staging's log, so the client-side proof is that the session itself is dead. A 500 (or any
   non-401 failure) among them keeps the run `failed`: it happened while the session lived and
   is staging's bug. A 401 while the probe answers 200 stays `failed` too (an auth regression).
   The round treats an `environment` result as card state `environment` and forwards nothing,
   but only after its own check (Test-AllFailed401): a tester-written `environment` with a
   non-401 failure is turned back into `failed` and forwarded. It also treats a tester-written
   `failed` result whose failed steps are all 401 as `environment` when the session is dead at
   the job's end. On a retest, exit 4 leaves the card as it was (not reopened).
4. tester.md: never run seed.ps1 or `app.identity.recover`, and never call an
   enrol/rotate/revoke route. A dead session is reported as environment and not re-seeded.

## Consequences / follow-ups

- `environment` is set directly on the card. scripts/lib/TeamQueue.ps1's test-card move table
  (outside this task's area) does not list it yet. Follow-up: add `environment` (from
  `running`; back to `running` for the next deal) to `$TeamTestCardStates`/`$TeamTestCardMoves`.
- The round's seed revokes any other staging session, including one opened from a desktop
  session. That is intended: the round owns staging's session while it runs.
- A seed from a Claude desktop session still writes to the MSIX folder. Only the round's own
  seed is authoritative for testers.
