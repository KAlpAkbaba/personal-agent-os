# Inspector report: real-host-rehearsal (`19411969`)

**Verdict in short:** the worker's numbers and mutations hold, and the snapshot works against a real PostgreSQL. I am returning it for two holes in the read-only allow-list and one RED the report did not mention.

## Pass 1: run it
- **Suites re-run:** host-snapshot 63/0, maintenance-reboot 33/0, script-syntax 141 checked / 0 failed, installer-strictmode 24/0. `test_host_snapshot_schema.py` 10 passed (86 of 87 tables unlisted, as reported). Ruff and `bash -n` are clean. PROVEN_AUTOMATED.
- **Area:** the commit touches only the seven area files; the tree is clean after my work.
- **My mutations, maintenance suite** (each RED, each restored from a backup copy, sha256 `6a694f69…` / `4a466959…` identical):
  - wait for `api-(blue|green)-1` → 10 RED, e.g. `FAIL --run reboots on the host's own shape: api-green serving and the lock held 2s when asked`.
  - `flock -w 2` → 10 RED, e.g. `FAIL preflight passes when every condition holds`.
  - fixture `longest_run` 50 → 9 RED, so the fixture really drives the fake flock.
  - fixture colour blue with green up → `FAIL the snapshot is a host this suite can build…`.
- **My mutations, snapshot suite** (each RED, sha `b0b447b9…` / `81fe544b…` restored):
  - `host-snapshot.sh`: `systemctl stop`, `FROM pg_settings`, `printenv` of the decoy, `$(<file)` in place of `cat`, `flock -w 5`.
  - collector: 255 → 254 (exit 4 lost), BatchMode removed, unknown-member check removed.
- **Real PostgreSQL (the worker's NOT_RUN, run on the dev stack):** I ran `host-snapshot.sh` unchanged in Git Bash against the real Docker and `pagentos-postgres`, database `pagentos` at head `0063_team_state`. Only `systemctl` was faked, and there was no lock file.
  - Exit 0, one document, 1126 columns over 88 tables; the collector's `-ValidateFile -Raw` accepts it.
  - `SHOW transaction_read_only` under the script's `PGOPTIONS` answers `on`.
  - `compare()` on that document with `hand_written=False`: 87 tables, 436 `String(n)` columns, 0 failures, 0 waiting. PROVEN_PROXY.
  - This lowers the worker's first open risk: the models match what the migrations build. Production can still differ if it has drifted.
- **NOT_RUN:**
  - The collector against the host (the lead's; READY_FOR_OWNER if Tailscale asks for its browser check).
  - `flock` sampling on a real Linux.
  - `quality-gate.ps1 -Fast` as a whole, because it is RED by construction (first finding below). I ran the 18 unit files that enumerate scripts or fixtures instead: 409 passed, 1 failed.

## Pass 2: break it
1. **The branch is RED in the gate and the report does not say so.** `tests/unit/test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite` fails with `['host-snapshot.tests.ps1']`. Registration is the lead's by the card, but it must land in the same merge commit.
2. **Hole: `set +x` turns the recorder off.** Adding `set +x; touch /tmp/probe; set -x` to the script left the suite at 63/0, and the file was created outside the fake host. `set` is allowed with any argument, so "every command is on the allow-list" does not hold.
3. **Hole: `docker inspect` can dump a container's environment.** Adding `say "$(docker inspect --format "{{json .Config.Env}}" pagentos-prod-api-green)"` left the suite at 63/0. On the host that prints every production secret to stderr. The script never uses `inspect`.
4. **Low: "waits for a release" is loose.** It holds when the table name and the column name each appear quoted anywhere in the unreleased migrations, not in the same statement. Read from the code, not run.
5. **Low: the lock probe is a side effect.** The release takes the lock with `flock -n 9` (`release-cloud-core-bluegreen.sh:103`), so a release started during the 60-second collection can be refused. The lead should not collect during a release; a `/proc/locks` probe would take nothing.
6. **Low: a transient temp file.** The here-string holding the column listing (about 137 KB) is larger than a pipe, so bash writes and deletes a temp file under `/tmp` on the host. Nothing lands under `/opt/pagentos`.
7. **Clean:** container names, user, database, lock, marker and edge paths match `docker-compose.prod.yml` and the release scripts. No secret or host path beyond the named markers is in the code.

## For the lead at merge
Register the suite in `ci.yml` and `quality-gate.ps1` in the merge commit, then run the full unit suite. Collect the real fixture outside a release and rerun the three consumer suites.

`RETURN (1. the allow-list accepts 'set' only as 'set -eu -o pipefail' — or refuses any 'set +x' / '+o xtrace' — with a regression case; 2. remove 'docker inspect' from the allow-list or pin it to a state-only format, with a refused case for '.Config.Env'; 3. the report states that test_ci_covers_every_suite is RED until the lead registers the suite; optional: 'waits' requires table and column in the same migration file)`
