# Worker report: real-host-rehearsal (return round)

## Şu an üzerinde çalışılan
- Task `real-host-rehearsal`, return round: the three inspector points and the optional one.
- Area: `scripts/tests/host-snapshot.tests.ps1`, `services/api/tests/unit/test_host_snapshot_schema.py`, `team/plans/real-host-rehearsal-adr.md`. Machine: the owner's home PC, in the task's worktree.

## Result
- **sha:** `f689e2fbeabaf2700ed66895de79810fb71c6a58`, pushed to `team/d20261001/worker-real-host-rehearsal`; the tree is clean.
- **Files:** 3 changed, all inside the area. `host-snapshot.sh` is unchanged (sha256 `b0b447b9…`).

## The return points
1. **`set`:** allowed only as `set -eu -o pipefail`; `set +x`, `set +o xtrace`, `set -x` and the right options plus one more are refused.
   - I also closed the same hole by other routes: `PS4='+zz '` hid every later command from the suite when I tried it.
   - Variables other than the script's own lower-case ones (`PS4`, `BASH_XTRACEFD`, `PATH`) may not be assigned, plainly or through `local` / `read` / `for` / `printf -v`, nor named in the source.
   - The trace must now reach the script's last command, with none of it on stderr.
2. **`docker inspect`:** removed from the allow-list, with refused cases for `{{json .Config.Env}}` and for a state-only inspect. `docker ps` is now allowed only with a `--format` of `.Names` and `.State`.
3. **`test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite` is RED on this branch** with `['host-snapshot.tests.ps1']` (re-run: 1 failed, 6 passed), and so is `quality-gate.ps1 -Fast` as a whole. It stays RED until the lead registers the suite in `ci.yml` and `quality-gate.ps1` in the merge commit; both files are outside my area. The ADR text now says this.
4. **Optional, done:** a column waits for a release only when one unreleased migration file names both its table and itself.

Both inspector holes are also live regression cases: a scratch copy of the script with the line added, run under the fakes.

## RED → GREEN (PROVEN_AUTOMATED)
- **Snapshot suite, new cases before the fix:** `passed: 71 failed: 17`, e.g. `FAIL refused in the trace: set +x: the recorder is switched off…`, `FAIL refused: docker inspect of a container's environment ('.Config.Env')`, `FAIL refused when run: 'set +x' around a command hides it from the recorder (0 violation(s))`.
- **After the fix:** `passed: 95 failed: 0`.
- **Schema test:** the new same-file case was RED before the change, but only as a `TypeError` (the old code took one string, not a list of files). The behavioural RED is mutation M8. After: 11 passed, with 86 of 87 tables still unlisted.

## Mutations (each restored from a backup copy, sha256 identical)
| # | Mutation | RED |
|---|---|---|
| M1 | checker allows any `set` | 5, e.g. `FAIL refused in the trace: set +o xtrace` |
| M2 | checker allows `docker inspect` | 3, e.g. `FAIL refused: docker inspect at all, even of a state` |
| M3 | checker allows foreign assignments | 3, e.g. `FAIL refused in the trace: PS4 assigned…` |
| M4 | script: `set +x; uname -a; set -x` | 15, incl. `FAIL every command it ran is on the read-only allow-list` |
| M5 | script: `docker inspect … .Config.Env` | 20, incl. the same line and `FAIL the recorder ran to the script's last command…` |
| M6 | script: `: $((BASH_XTRACEFD = 2))` | 2: the source lint names it, and the recorder assertion |
| M7 | script: `docker ps` format with `{{.Command}}` | 2 |
| M8 | schema test: migration files joined | `FAILED …test_a_column_waits_only_when_one_migration_names_its_table_and_itself` |

In M4 and M5 several of the RED lines come from the added line breaking the run itself (`uname -a` prints to stdout; `say` is not defined yet at that point). The scratch-copy cases in the suite isolate the allow-list as the cause.

## Fast checks (PROVEN_AUTOMATED)
- maintenance-reboot 33/0, script-syntax 141 checked / 0 failed, installer-strictmode 24/0.
- `bash -n` is clean; ruff check and format are clean on the Python file.

## NOT_RUN
- The collector against the real host (the lead's).
- `flock` sampling on a real Linux.
- `quality-gate.ps1 -Fast` and the full unit suite: RED by construction until the suite is registered.

## Open risks
- The allow-list guards against an honest change that does harm. It is not a sandbox against an author who hides a name on purpose (`x=PS; (( ${x}4 = 1 ))`); the ADR says so.
- The `local` / `read` name rule can go falsely RED if a traced value ever holds a space followed by a capitalised word. That is loud, not silent.
- Unchanged from the inspector's report: the `flock -n` probe can refuse a release started during collection, and a transient temp file is written under `/tmp` on the host. Both are now in the ADR.

## For the lead at merge
- Register `host-snapshot.tests.ps1` in `ci.yml` and `quality-gate.ps1` in the merge commit, then run the full unit suite.
- Collect the real fixture outside a release and rerun the three consumer suites.
- Add the inspector rule (fixture collected after the last release for diffs touching `scripts/cloud/*.sh`, `infra/docker` or a migration).
- Number the ADR from `team/plans/real-host-rehearsal-adr.md`.
