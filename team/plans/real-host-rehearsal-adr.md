# ADR (unnumbered - the lead numbers it at merge): the fake hosts are built from a read-only snapshot of the real one

Date: 2026-10-01 · Task: `real-host-rehearsal` · Status: proposed by the worker, PROVEN_AUTOMATED

## Context

Three defects in one day were green on a fake and red on the Cloud Core, each because the fake
did not model one property of the host: the serving colour (QUALIFICATION 38.12), how long the
blue/green operation lock is held (38.17), a column's width (38.15). The fakes were written
from what the author believed the host looked like.

## Decision

1. **One snapshot, one file.** `scripts/cloud/host-snapshot.sh` runs on the host and prints one
   JSON document; `scripts/cloud/collect-host-snapshot.ps1` (the LEAD, home PC) sends it on the
   stdin of `ssh ... bash -s`, validates it and writes `scripts/tests/fixtures/host-snapshot.json`.
   Workers and inspectors never reach the host; they read the file.
2. **The schema is closed** (version 1, held by the collector): a member it does not name is
   refused, so an environment dump or a file's content cannot ride along. A marker is 40 hex,
   `missing`, or `invalid` (present but not 40 hex - the content is never printed; `invalid` is
   an addition to the task card's "40-hex or missing", because calling a damaged marker
   "missing" would be untrue).
3. **Read-only is proven by recording, not by reading the source.** The suite runs the script
   under `bash -x` (every command word, builtins included) with fakes that log exact arguments,
   and fails on anything outside an allow-list. bash's trace does not show redirections, so the
   script may hold none (`<<<`, `>&2`, `2>/dev/null` only) - files are read with `cat`. The
   schema query runs in a session the server itself holds read-only
   (`PGOPTIONS=-c default_transaction_read_only=on`); the allow-list requires that flag.
4. **Consumers keep explicit cases beside the snapshot's.** The maintenance suite takes its
   container list, serving colour and lock-hold from the fixture, and still runs GREEN, BLUE and
   the measured 2 s hold as named cases: a later collection that samples the lock free sixty
   times, or finds blue serving, must not take the proof of 38.12 / 38.17 away.
5. **A column production does not have fails - unless a migration the serving release does
   not contain adds it.** The task card said "fails"; taken literally the gate would block the
   very release that creates the column (the fixture can only gain it after that release).
   `markers.release` is asked of git (`git ls-tree <release> services/api/alembic/versions`);
   a table/column named by a migration outside that tree is reported as waiting, not failed.
   When git cannot answer, nothing waits: it fails. The width rule has no exception.
6. **Patterns that decide what is allowed are culture-invariant.** On this machine (tr-TR) a
   case-insensitive `INTO` does not match `into`; found while writing the allow-list, covered by
   a refused lower-case `select ... into` case.

## Consequences and open risks

- `flock -n <lock> true` takes the operation lock for the life of `true`, sixty times. A release
  or the minute reconcile that asks in that instant is told "another operation is running"
  (exit 82; the reconcile runs again a minute later). The maintenance preflight already probes
  the same way. A probe that takes nothing (`/proc/locks` by inode) is possible and was not
  built: the task card names `flock -n ... true` as the allowed command.
- A missing lock file is not probed (flock would create it): the snapshot says `present: false`.
- The first real collection may turn `test_host_snapshot_schema.py` RED: 86 of 87 mapped tables
  are not in the hand-written fixture. Each failure then is a real difference between a model
  and production, not a test defect.
- The fixture is only as fresh as its `collected_at`. Freshness is the inspector's rule, not a test.

## For the lead at merge

- Register `scripts\tests\host-snapshot.tests.ps1` in `.github/workflows/ci.yml` and
  `scripts/quality-gate.ps1`: `services/api/tests/unit/test_ci_covers_every_suite.py` is RED on
  this branch until it is named there (both files are outside the worker's area).
- Run `scripts\cloud\collect-host-snapshot.ps1` (it replaces the hand-written fixture; exit 4 =
  Tailscale's browser check, nothing changed), then the three consumer suites.
- Inspector rule (`.claude/agents/inspector.md`): a task whose diff touches `scripts/cloud/*.sh`,
  `infra/docker` or a migration is inspected against a fixture collected after the last release.
- QUALIFICATION: this work is PROVEN_AUTOMATED; PROVEN_REAL when the first real collection
  replaces the fixture and the next maintenance window (38.13) passes without a fix on the host.
