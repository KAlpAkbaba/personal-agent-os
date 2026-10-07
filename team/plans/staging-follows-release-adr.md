# ADR (taslak): staging follows every release; a test round refuses a stale staging

Status: proposed (staging-follows-release, cycle d20261006). The lead numbers it.

## Context

On 2026-10-06 at 13:05 production served 72884b71 but staging (127.0.0.1:28001) still served
6a21294c. Neither the automatic release step (scripts/team/release.ps1, ADR-0287) nor the
Danışman's hand release ran scripts/staging/deploy.ps1, and test-round.ps1 started anyway, so
every watch step failed with 404. Those false failures would have become software cards. At
13:20, right after a staging redeploy, every authenticated step answered 401 because the staging
owner session had not been re-seeded.

## Decision

1. Once release.ps1 has a verified release (RELEASE = APPROVED_SHA = sha, reconcile OK, edge
   healthy) and has saved the queue as 'released', it runs
   `scripts\staging\deploy.ps1 <the released 40-hex sha>` and, only if that succeeds,
   `scripts\staging\seed.ps1`. The scripts come from the main checkout, so the deploy's
   WorkRoot is the same one the Danışman's hand runs use. Each script writes its stdout and
   stderr to its own files: `release-<n>.staging-deploy.out/.err` and
   `release-<n>.staging-seed.out/.err`. The two streams are never merged. The deploy may take
   up to `-StagingMinutes` (default 45); the seed may take up to 300 s.
2. `Get-TeamStagingOutcome` (scripts/lib/TeamRelease.ps1) judges the result. Staging is fine only
   when both of these hold:
   - deploy.ps1 exits 0 and prints `STAGING DEPLOYED: <the sha>`;
   - seed.ps1 exits 0 and prints `STAGING SEEDED`.

   If both hold, the report records the sha staging reached (`staging: <sha>`). If either fails,
   the release step does three things:
   - adds a `risk:` line to the report (the report goes to the Onay Merkezi);
   - adds an Onay Merkezi note to every task's reason, telling the owner to update staging by
     hand;
   - keeps exit 0 and writes no release-blocked.json. Production is already promoted, so a
     staging failure is never a failed release.

   A release that fails or is rolled back never touches staging.
3. Before the plan, test-round.ps1 checks that staging's `/v1/system/health` `release.version`
   equals origin/main's tip (fetched now; `-MainSha` for the tests). If not, the round refuses:
   exit 4 (distinct from 1, a failure, and 2, a refused host or path), a board note naming both
   shas, no plan, no cards.json, no software card, no tester, and no seed (a refused round does
   not rotate staging's credential). `-AllowStaleStaging` skips the check for a deliberate test
   of an old build. A test's stand-in staging (`-NoAuth` or `-AllowTestPort`) is checked only when
   `-MainSha` names the sha it must serve; a real round passes neither flag.
4. The session check is the round's own seed (test-round-keeps-staging-session, merged into the
   same integration branch first): the round runs seed.ps1 once before its first tester and
   starts no tester unless `/v1/identity/sessions/current` answers 200 (exit 1, a board note
   saying 'staging oturumu açılamadı ... 401'). This task first had its own pre-plan session
   check (exit 4, 'oturum yok'); it was dropped when the two branches met on the integration
   branch (2026-10-07), so one mechanism owns the session. The release step's seed (point 1)
   stays: it leaves staging usable for the Danışman's hand checks between rounds.

## Consequences

- The team lock is held during the staging deploy, which builds two images. This adds
  ~10-20 min to a release run before the next cycle can take the lock. It is acceptable: a
  release is rare, and the alternative (a deploy after the lock is released) could race the
  next release.
- A staging failure costs one test round (the round refuses) instead of a batch of false
  cards.
- The Danışman's hand release (release-cloud-core.ps1 run directly) still does not move
  staging. The round's refusal catches that case: its note names both shas and the deploy
  command.
