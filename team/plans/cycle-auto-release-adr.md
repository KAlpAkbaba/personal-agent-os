# ADR (draft, cycle-auto-release): the automatic release step - "Kapı yeşilse otomatik yayınla"

**Context.** ADR-0214 addendum 9: roadmap work that passed the full gate and reached main is
released blue/green without asking the owner; a listed set of cases is not automatic. Until now
the lead ran preflight, release, the recovery pin and the checks by hand in a chat session.

**Decision.** `scripts/team/release.ps1` (+ `scripts/lib/TeamRelease.ps1`) runs after
`integrate.ps1`:

1. It acts only on tasks `awaiting_release` whose 40-hex sha IS `origin/main`'s tip (fetched
   now); awaiting tasks whose sha is an ancestor of it are released with it.
2. Evidence before anything runs (not even a look at the host): the integrate step's green
   record `team/reports/<cycle>/gate-<n>.json` whose `main` is the sha, and its log beside it
   that the integrate step's own reader (`Read-TeamGateLog`) judges PASS. Also stop on
   `team/release-blocked.json` and on the team lock held by another run.
3. Under the team lock, ONE read-only ssh probe (markers, `MAINTENANCE_MARKER`, the seconds to
   `pagentos-maintenance-window.timer` counted on the HOST's clock, health through the edge at
   127.0.0.1:8001, the reconcile journal's last `RECONCILE` line) and the diff between the
   host's RELEASE and the sha. `Get-TeamReleaseDecision` stops on: a migration that is not
   expand-only, `infra/docker/docker-compose.prod.yml` or `infra/docker/edge/` changed, health
   not `ok`, a maintenance marker, a window within 30 min (or unreadable), an unreadable host or diff.
4. Expand-only is read conservatively: only an ADDED version file is judged; the module minus
   its `downgrade()` must hold no `drop_*(`, `rename_table`, `alter_column` with
   `type_`/`nullable`/`new_column_name`, NOT NULL `add_column` without `server_default`, or SQL
   that deletes/updates/truncates/drops/renames - inside `ALTER TABLE` any `DROP` or `RENAME`
   (PostgreSQL makes `COLUMN` optional). A changed, deleted or unreadable migration stops; an
   `execute()`/`exec_driver_sql()` whose argument is not a string literal (a variable, an
   f-string, a file read) is unreadable. Today two existing versions (0018, 0023) use an f-string
   execute; both drop a constraint, so stopping on them is right.
5. Release from a clean detached worktree `.claude/worktrees/release/<sha12>` (never the main
   checkout): `release-cloud-core.ps1 -BlueGreen -Preflight`, then `-BlueGreen`, then over ssh
   `install-recovery-supervisor.sh <40-hex>`, then the probe until RELEASE == APPROVED_SHA ==
   sha, the last reconcile line is `RECONCILE OK ... (release <sha>)` and edge health is ok
   serving the sha. stdout and stderr of every command go to SEPARATE files
   `release-<n>.<step>.out/.err`; no line merges them.
6. Success: tasks `released`, `release_approved` true, `release_approved_by` `standing_rule`,
   reason `yayinlandi <UTC>, main <sha> (<colour>)`; report section `## Yayın` with what, sha,
   colour, LKG. Failure after the preflight (release script non-zero = its own rollback ran;
   pin failed; verification failed): recorded (`geri alındı` / `doğrulanamadı`),
   `team/release-blocked.json` written, tasks stay `awaiting_release`. The step never
   improvises a rollback. A failed preflight changes nothing and writes no marker.
7. A stop writes the reasons into the report and every task's reason ("Onay Merkezi: sahibin
   kararı bekleniyor"); the tasks stay at the owner's release gate.

**Alternatives rejected.** Health over HTTP from the home PC (a second network path and a
fake HTTP server in tests; the edge on the host is what the release script itself checks);
judging migrations by the whole file (every expand-only migration's downgrade drops what it
added); counting the maintenance window on the home PC's clock (two clocks for one decision).

**Consequences / for the lead at merge.**
- `standing_rule` must be added to `release_approved_by`'s enum in BOTH
  `team/queue.schema.json` and `services/api/app/team/queue.schema.json` (outside this area);
  until then the Cloud Core store (API mode) refuses the 'released' write with 422.
- Wire `scripts/team/release.ps1` into the scheduled task after `integrate.ps1`, and
  `scripts/tests/team-release.tests.ps1` into `scripts/quality-gate.ps1` / the ci list.
- `team/release-blocked.json` should be git-ignored (a machine-local marker), like team/reports/.
- Exit codes: 0 released/nothing; 2 protocol; 3 lock; 5 stopped by a rule; 6 failed (marker);
  7 preflight failed; 12 unexpected/queue write.

**Addendum (inspector return 2, 2026-10-03): SQL ALTER is judged by an ALLOW-list.** A
deny-list kept missing PostgreSQL forms where a keyword is optional (`ALTER TABLE t ALTER c
TYPE`, `SET NOT NULL`, `ADD c int NOT NULL`, `ALTER TYPE e RENAME VALUE`). Now every ALTER in an
execute()'s literal SQL must be one of: `ALTER TABLE t ADD [COLUMN] [IF NOT EXISTS] c <type>`
(NOT NULL / PRIMARY KEY only with DEFAULT; no constraint, reference or generated column) or
`ALTER TYPE e ADD VALUE ...`. Anything else stops - in doubt, not expand-only. Found on the way:
on a tr-TR machine (the owner's PC) `(?i)` folds 'I' to dotless 'ı', so upper-case `DROP
INDEX/CONSTRAINT/VIEW` slipped the deny-list; it now matches CultureInvariant, and the
allow-list upper-cases invariantly and matches case-sensitively.
