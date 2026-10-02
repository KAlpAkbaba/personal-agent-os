# Inspector report — real-host-rehearsal @ `f689e2fb` (second inspection)

**Pass 1 — re-run from a clean tree (PROVEN_AUTOMATED)**
- `host-snapshot.tests.ps1` 95/0; `maintenance-reboot.tests.ps1` 33/0; `script-syntax` 141 checked / 0 failed; `installer-strictmode` 24/0.
- `test_host_snapshot_schema.py` 11 passed, with the warning "86 of 87 mapped tables are not in the fixture yet".
- `test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite` is RED (`['host-snapshot.tests.ps1']`), as the worker disclosed. It is the lead's to register at merge.
- The diff of the two task commits is 7 files, all inside the area. The scripts are pure ASCII, so no BOM is needed. Tree clean at the end.

**My mutations (all different from the worker's, each restored from a backup copy, sha256 verified)**

| Mutation | Result |
|---|---|
| maintenance script waits for `api-blue` by name (38.12) | 8 RED, incl. `FAIL --run reboots on the host's own shape: api-green serving…` |
| maintenance script back to `flock -n` (38.17) | RED, incl. `FAIL preflight passes when every condition holds` and `FAIL the reconcile's 2 s hold does not postpone the window` |
| fixture: godseye `exited` | RED (`containers still missing`) — the suite really reads the fixture |
| model `team_state.updated_at` → `String(64)` | `team_state.updated_at: the model declares String(64), production has character varying(32)` |
| snapshot script: `nl=x mkdir -p /tmp/…` (env-prefixed command) | RED on the allow-list — bash traces the prefix and the command separately |
| snapshot script: `docker exec … psql -c "UPDATE…"` | RED |
| snapshot script: `SELECT … FROM team_state` | RED, 3 lines |
| collector: stderr merged into stdout | 5 RED |
| collector: exit 255 not treated as unreached | 2 RED (the exit-4 cases) |
| collector: unknown members accepted | 1 RED |

**Real infrastructure (the worker's NOT_RUN items, run by me → PROVEN_PROXY)**
- **Real PostgreSQL:** the unmodified `host-snapshot.sh` with the real `docker ps -a` and real `docker exec … psql` (only `systemctl` shimmed) against the dev stack's `pagentos` database at `0063_team_state`. Exit 0, one document, 1126 columns, and `-ValidateFile -Raw` accepts it.
- **Models against that real schema:** the test's own `compare()` with `hand_written=False` gives 87 tables, 436 `String(n)` widths, 0 failures, 0 waiting. The first real collection should therefore not go RED from model drift, unless production differs from dev at head.
- **Real `flock` on Linux:** in a throwaway `pgvector:pg16` container (util-linux 2.38.1, bash 5.2) with a real 3 s hold, the script reported `held 3, longest_run 3` of 9. The lock file's size, mtime and mode were unchanged, and no file was created.
- **NOT_RUN:**
  - the collector over real ssh and real `systemctl list-timers` output (the lead's; an inspector never reaches the host);
  - `quality-gate.ps1 -Fast` (RED by construction until the suite is registered);
  - the worker's own eight mutations and its RED-before-fix counts, which I did not repeat.

**Pass 2 — findings**
1. **Confirmed, low severity: the statement checker accepts a statement that reads an application table.** `Test-ReadOnlyStatement` strips `'…'` literals without knowing `E'\''`, so `SELECT table_name FROM information_schema.columns WHERE table_name = E'\'' ; SELECT key FROM team_state --'` is ACCEPTED (run through the suite's own function). On the dev PostgreSQL the same shape executed and returned `team_state`'s count. The `DELETE` form is accepted by the checker too; I did not run it on the server. This needs deliberate obfuscation, which the ADR scopes out, but the ADR names only the bash-side limit. Fix is one line: refuse a backslash, `$` and `--` in the statement, plus one refused case.
2. The fixture's markers and timers are not the lead's facts; they come from HANDOFF and the unit files, and the fixture's `notes` say so. No consumer reads the timers.
3. Open risks confirmed as the ADR states them: the sixty `flock -n` probes can refuse a release that starts during collection, and bash keeps a here-string temp file under `/tmp` on the host.
4. No secrets, no environment values and no merged stderr found. Rollback is deleting 5 files and reverting 1 test.

**For the lead at merge**
- Register the suite in `ci.yml` and `quality-gate.ps1`, then run the full unit suite and the gate.
- Collect the real fixture outside a release and rerun the three consumer suites.
- Add the inspector rule and number the ADR.
- Cut a follow-up card for finding 1, or fold the one-line fix into the merge.

**Evidence class:** PROVEN_AUTOMATED (suites and mutations) plus PROVEN_PROXY (real PostgreSQL, real flock). READY_FOR_OWNER: none. The real-host collection is NOT_RUN and is the lead's.

`APPROVE`
