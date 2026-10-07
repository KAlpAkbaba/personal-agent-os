# ADR (draft): a guard under services/api/tests/integration/ runs on a database of its own

Status: proposed (card guards-integration-tests-own-db, cycle d20261006)

## Context

2026-10-07 23:25:58Z: proof-from-test-rounds-and-trials stopped at "koruyucu kırmızı: işin kendi testi
kırmızı (services/api/tests/integration/test_team_proof_postgres.py)", all three tests ERROR at the
fixture. Get-TeamDutyTaskTests turns a task's own test files into pytest guards and Get-TeamGuardCommand
started them with the parent's environment, so tests/integration/conftest.py migrated
Settings().database_url = the dev server's SHARED database `pagentos`. That database was at
0067_wake_alarm_song (no released tree knows it), the upgrade failed, and the task was called red. On a
healthy shared database the guard would instead have migrated it to the integration branch's head (the
dev-db-branch-migration-leak shape of 2026-10-04).

## Decision

In scripts/lib/TeamGuards.ps1, a pytest guard whose path starts with `services/api/tests/integration/`:

- gets a fresh name `pagentos_g_<12 hex>` per run (Get-TeamGuardCommand: `Database`), and
  `PAGENTOS_DATABASE_URL` = the application's URL with that database, set in the child's environment only
  (`Environment`); the shared database is never named;
- the database is created on the server of the application's own URL (Settings, asked of the worktree's
  app; `-DatabaseUrl` overrides) through the guard interpreter's psycopg - `CREATE DATABASE`, then
  `CREATE EXTENSION vector` - and dropped `WITH (FORCE)` in a finally: green, red or killed by the hang
  guard. The URL travels in an environment variable, never on a command line, and is never printed;
- a guard that cannot get its database is outcome `no-database`, line "koruyucunun veritabanı açılamadı,
  test koşmadı (<path>)" - not the label "işin kendi testi kırmızı". The run is not green.
- a drop that fails turns the row red with "koruyucu veritabanı kaldırılamadı": a leftover is said.

psycopg (the venv's) rather than `docker exec psql` (GateDatabase.ps1): the server is the one the
application's URL names, a closed port fails the same way the test would, and no `pagentos_gate_` /
`pagentos_scratch_` name rule (GateDatabase's, outside this area) had to change.

## Consequences

- Integration guards no longer depend on, or move, the shared dev database's revision.
- Each integration guard pays one create/drop (about a second) and a full `alembic upgrade head`.
- Open: a guard process killed from outside the runner (the whole cycle killed) can leave a
  pagentos_g_* database; no sweep exists for the prefix yet (GateDatabase's sweep reads only
  pagentos_gate_*). Candidate follow-up card: sweep pagentos_g_* older than a bound.
