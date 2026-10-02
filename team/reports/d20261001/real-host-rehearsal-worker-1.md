## Şu an üzerinde çalışılan
`real-host-rehearsal` · alan: `scripts/cloud/host-snapshot.sh`, `collect-host-snapshot.ps1`, `scripts/tests/fixtures/host-snapshot.json`, `host-snapshot.tests.ps1`, `maintenance-reboot.tests.ps1`, `test_host_snapshot_schema.py`, ADR · makine: sahibin ev PC'si (Windows 10), worktree `worker-real-host-rehearsal`. Bitti; ağaç temiz.

## Report
- **sha:** `1941196945aa63042a4e37dd3f1a234883fcc9ac`, pushed to `origin/team/d20261001/worker-real-host-rehearsal`.
- **Files:** 7, all inside the area (6 new, `maintenance-reboot.tests.ps1` edited). No write to the host anywhere; I never reached it.
- **One test is RED on this branch:** `test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite` names `host-snapshot.tests.ps1` as missing until it is registered in `ci.yml` and `quality-gate.ps1` (yours at merge).

**Suites (all run, PROVEN_AUTOMATED):** host-snapshot 63/0 · maintenance-reboot 33/0 (was 28) · `test_host_snapshot_schema.py` 10 passed · script-syntax 141 checked, 0 failed · installer-strictmode 24/0 · ruff check and format clean · `bash -n` ok.

**RED first:** `host-snapshot.tests.ps1` was run before either script existed (`host-snapshot.sh: No such file or directory`, every collector case FAIL).

**Mutations** (each restored from a backup copy, sha256 identical before and after):
- **38.12 put back** (wait for `api-blue` by name) → 8 RED, e.g. `FAIL --run reboots on the host's own shape: api-green serving and the lock held 2s when asked`, `FAIL --run order: backup -> timer stopped -> leftover removed -> apt upgrade`. `maintenance-reboot.sh` sha `6a694f69…` both sides.
- **38.17 put back** (`flock -n`) → 10 RED, e.g. `FAIL preflight passes when every condition holds`, `FAIL the reconcile's 2 s hold does not postpone the window`.
- **Model `String(64)` for `team_state.updated_at`** → `team_state.updated_at: the model declares String(64), production has character varying(32)`. `app/team/models.py` sha `a1c85503…` both sides.
- **`host-snapshot.sh`** (sha `b0b447b9…` both sides), six mutations, each RED:
  - `docker exec … psql -c "UPDATE…"`
  - `SELECT … FROM team_state`
  - `cat $base/.env`
  - read-only session flag removed
  - the missing-lock-file guard removed
  - a file written under the base
- **Collector** (sha `81fe544b…` both sides), four mutations, each RED: stderr merged into the document, validation skipped, a file written when ssh is unreachable, exit 0 instead of 4.

**Decisions that depart from the card** (ADR text in `team/plans/real-host-rehearsal-adr.md`):
- **A column production lacks does not always fail.** It waits when a migration outside the serving release's tree adds it (`git ls-tree <markers.release>`); otherwise it fails. Taken literally, the card's rule would block the very release that creates the column. The width rule has no exception.
- **Markers can be `invalid`** as well as 40-hex or `missing`: a damaged marker is not reported as missing, and its content is never printed.
- **Schema read goes through `docker exec <postgres> psql -Atc`** in a server-side read-only session; the allow-list requires that flag.
- **Explicit GREEN, BLUE and 2 s-hold cases stay** beside the fixture-driven ones, so a later collection that samples the lock free, or finds blue serving, keeps both proofs. The two test names QUALIFICATION 38.12 cites are unchanged.
- **Fixture fields beyond your facts:** markers come from HANDOFF, timers from the repo's unit files and ADR-0223, and `team_state.doc` as `jsonb` from migration 0063. The fixture's `notes` says which is which.

**Bug found on the way (fixed, regression case added):** under this machine's tr-TR culture a case-insensitive `INTO` does not match `into`, so the allow-list would have let a lower-case `select … into` through. Patterns are now culture-invariant.

**NOT_RUN:** the collector against the real host; PROVEN_REAL waits for your first collection and window 38.13. The full API unit suite and the full gate were not run, only the files touched plus the two ratchets.

**For the lead at merge**
1. Register `host-snapshot.tests.ps1` in `ci.yml` and `quality-gate.ps1`.
2. Run `scripts\cloud\collect-host-snapshot.ps1` to replace the hand-written fixture (about 60 s on the host; exit 4 means Tailscale's check and nothing changed), then rerun the three consumer suites.
3. Add the inspector rule: a diff touching `scripts/cloud/*.sh`, `infra/docker` or a migration is inspected against a fixture collected after the last release.
4. Number the ADR and add the QUALIFICATION row.

**Open risks**
- The first real collection may turn `test_host_snapshot_schema.py` RED: 86 of 87 mapped tables are not in the fixture yet (the test reports this number as a warning). Each failure would be a real model-versus-production difference.
- Each `flock -n <lock> true` probe takes the operation lock for an instant, sixty times. A release or reconcile asking in that instant gets exit 82. A `/proc/locks` probe would take nothing, but the card names `flock`.
- The "waits for a release" rule needs git and the release commit in the checkout; without them a new column fails.
