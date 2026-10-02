# Inspector report — `cycle-auto-integrate` @ `1bf19e8d`

The step is not ready to schedule: its tests pass, but a refused lead run can still put an ungated commit on main, and the real `pnpm` resolution fails on this machine.

**Pass 1 — run it** (clean tree, the 5 files all inside the area)
- `team-integrate`: 38 passed / 0 failed, exit 0. It took 18m18s, not "about 4 minutes" — the lead's full gate and a live cycle were loading the machine.
- `script-syntax` 143/0, `installer-strictmode` 24/0, `provision` 13/0, `team-cycle` 121/0.
- **My mutations** (scratch copies outside the repo; the worktree's sha256 is identical before and after, tree clean):
  - Docker-down check disabled → RED (1).
  - Merge of main forced with `-X ours` → RED (1).
  - File lock not released in `finally` → RED (7).
  - `team/queue.json` and `team/lock.json` no longer refused → RED (1).
  - The `--ff-only` path skipped → RED (4).
  - **The "HEAD left the branch" guard removed (`integrate.ps1:399`) → 38/0, survived.** No test exercises it.
- **Real runs:**
  - `-DryRun` here: exit 0, "nothing to integrate", tree unchanged.
  - Real `docker info` through `Invoke-NativeProcess`: success.
  - Real `uv` resolves and runs (0.12.7).
- **Not run:** the full or fast gate (the lead's gate, pid 29196, and a cycle own the shared dev stack; a second gate would disturb them), a real `claude -p` lead run, real `uv sync` / `pnpm install` in a gate worktree, API mode against the Cloud Core.
- **`Read-TeamGateLog` has never seen a real gate log.** I found none kept on this machine. The gate's child scripts print their own `=== x ===` lines, so a failed step may be reported under the wrong name.
- No table, migration, `scripts/cloud` or `infra/docker` change, so the PostgreSQL and host-snapshot rules do not apply.

**Pass 2 — break it** (each reproduced in a sandbox with the suite's own harness)
1. **A refused lead run still moves main.** With main checked out nowhere (the main checkout sits on `team/nightly/lead` today), the lead stand-in ran `git checkout main` and committed a file outside the allow-list. The step exited 7 saying "hiçbir şey birleştirilmedi", but main had the ungated commit, and the next green run pushed it to origin. The check looks only at the worktree's diff and HEAD; the shared repo's refs are not compared before and after the run.
2. **Real `pnpm` cannot start when it is on PATH.** `Resolve-ToolPath` takes `Get-Command pnpm`, which is `pnpm.ps1` here. `Invoke-NativeProcess` throws "geçerli bir uygulama değil", so every run would end "ortam kurulamadı" (exit 10). The `pnpm.cmd` fallback works (11.24.0) but is only reached when PATH lacks it. The tests always pass `-PnpmPath`.
3. **After a red gate the next run gates the identical commit again.** Same sha twice, nothing changed, then the TEAM_PROTOCOL 10 stop. On the real gate that is a second hour holding the lock, for a result already known.
4. **A `returned` task's code reaches main.** Red returned task-two; the next run was green; main carried `src/b/task-two.txt` while task-two was still `returned` and task-one was `awaiting_release`.
5. Minor: a gate that leaves a tracked file modified ends in exit 12 "beklenmeyen hata"; the next run finishes without a second gate.
6. Minor: after a failed push the tasks are `awaiting_release`, so nothing retries the push until another branch goes green.
7. No release, tag or last-known-good name, no force, no secret; the token is a file path only.

**Evidence classes**
- Decisions and the sandboxed flow: PROVEN_AUTOMATED (fakes).
- Docker probe, `uv` resolution, dry-run read path: PROVEN_PROXY.
- Real gate in a gate worktree, real lead run, real `pnpm install`, API mode: NOT_RUN.

## For the lead at merge
- Decide before scheduling: the lock is held for the whole gate, against addendum 8; and `-Base main` while worker branches open from `team/nightly/lead`.
- Keep one real gate log and feed it to `Read-TeamGateLog` before the first scheduled run.
- The worker's wiring list stands: `scripts/quality-gate.ps1`, `.github/workflows/ci.yml`, `scripts/team/register-nightly.ps1`, `scripts/team/cycle.ps1`, `scripts/tests/team-cycle.tests.ps1`, `docs/DECISIONS.md`.

**Verdict:** `RETURN (1. compare refs/heads/<Base>, the integration branch and the remote-tracking ref before and after the lead run; a moved ref refuses the run and is said loudly, never "nothing merged" — with a test where main is checked out nowhere; 2. resolve only .exe/.cmd for uv/pnpm/docker, never a .ps1 — with a test that does not pass -PnpmPath; 3. do not gate a commit whose last record is red on the same sha with main unmoved — wait for a new tip; 4. decide and test what happens to a returned task's code on the integration branch — a green gate must not put it on main while the task is 'returned', or the ADR says why it may; 5. a test that goes RED when the HEAD-ancestry guard at integrate.ps1:399 is removed)`
