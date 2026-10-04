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
4. Expand-only is an ALLOW-list of alembic calls (see the addendum below): only an ADDED
   version file is judged, and its `upgrade()` may hold ONLY bare `op.create_table`,
   `op.create_index`, `op.add_column` (nullable=True or a server_default, no primary key) and
   `op.create_foreign_key` whose source table the same upgrade creates. Everything else - any
   `op.execute` (even `SELECT 1`), raw SQL, another op, an ORM write, a helper, a loop, a
   variable, an f-string - is not expand-only. A changed, deleted or unreadable migration stops.
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

**Addendum (Proje Yöneticisi, 2026-10-03 21:00): expand-only is an ALLOW-list of alembic
calls; no raw SQL is safe.** Three inspector returns each found one more SQL form a deny-list
let through (optional keywords in `ALTER TABLE`, `UPDATE ONLY`, `MERGE ... DELETE`, `DROP
FUNCTION/SEQUENCE/TRIGGER/MATERIALIZED VIEW`, an ORM write with no execute at all, tr-TR's
dotless i). A deny-list never ends; the owner's rule (ADR-0214 addendum 9) is that an
irreversible migration is not released by itself. So the SQL parsing was removed: a migration
is expand-only only when `upgrade()` consists solely of the four allowed calls above, their
arguments call only schema builders (`sa.<Name>()`, `sa.func.<name>()`, `postgresql.<Name>()`,
`op.f()`, `.with_variant()`), and the module around it holds only imports (op/sa bound the
usual way), constants, docstrings and the two defs; `downgrade()` is not read. Anything else -
including every `op.execute` - stops and leaves the release to the Danışman/owner. The real
0065 migration passes. Cost: a hand-written-SQL migration (an extension, a `CREATE INDEX
CONCURRENTLY`) is always released by a person; that is the intended trade.

Return 4 (Denetleyici-4): the allow-list reads structure, not text. A star import, a `:=`
anywhere and a module-level assignment to `op`/`sa`/`upgrade`/`downgrade` or to more than one
target stop. `add_column` passes only on the `sa.Column(...)`'s OWN top-level keywords - bare
`nullable=True`, or a `server_default` that is not `None`/`sa.null()`; a `*`/`**` spread, a
non-literal `nullable`, a primary key or a column that is not a direct `sa.Column(...)` stops.
`create_index(..., unique=<not False>)` stops (it may reject rows the old colour writes).

Return 5 (Danışman, 2026-10-04 04:50): a WHITE list, so no new escape is left.
- Code outside strings and comments must be ASCII: Python reads non-ASCII names (fullwidth
  `ｏｓ` is `os`, `from os import system as é`), so any such character stops. Turkish in a
  comment, a docstring or a string is not code and still passes.
- Every string literal of the judged code (module constants and `upgrade()`) is on a white
  list (`Get-TeamStringObjection`): the first argument of `sa.text` / `sa.literal_column` /
  `sa.CheckConstraint` / `sa.Computed` / `sa.DDL` must be ONE literal whose SQL is digits, a
  single-quoted string of at most 64 characters without `;`, `\` or a newline, `now()` or
  `CURRENT_TIMESTAMP`; a `comment=` / `server_default=` literal (SQLAlchemy quotes it) holds no
  `;` or `\`; `ondelete=`/`onupdate=` is one of SQLAlchemy's phrases; every other literal is a
  plain name (`[A-Za-z0-9_]` with dots). So an index expression (`'lower(name)'`, `sa.text(...)`,
  `postgresql_where`), a CHECK, a computed column or SQL in a constant stops.
- Calls inside the arguments were narrowed with it: `sa.<Capitalised>()`, `sa.text`,
  `sa.literal_column`, `sa.true/false/null`, `sa.func.now/current_timestamp` only (so
  `sa.select`, `sa.func.pg_sleep`, `sa.func.lower` stop), `postgresql.<Capitalised>()`.
- `op.add_column(sa.Column(..., unique=True))` stays expand-only: the new column starts NULL in
  every row (NULLs do not collide), and the old colour does not know the column, so it never
  writes it; only the new colour's writes meet the constraint. With a `server_default` on a
  non-empty table the unique index fails AT MIGRATION time, before the switch - the release
  script's preflight/migrate fails and nothing is promoted.
- Of the repository's 65 real migrations the same 21 are expand-only before and after this
  change (0065 among them): the white list costs no real migration that passed before.
