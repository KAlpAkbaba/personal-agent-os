# Inspector report — cycle-auto-integrate (branch tip `60fccc55`)

**Verdict in short:** the suite is green and the worker's numbers hold, but the lead-diff check has a hole that puts an unauthorised change on main, and a red gate can stall a branch silently in API mode.

**Pass 1 — run**
- `team-integrate`: 48 passed / 0 failed, exit 0, 4 min 37 s. Hashes match the report (lib `bd9f5384…`, step `214199a4…`).
- `script-syntax` 143/0, `installer-strictmode` 24/0, `provision` 13/0.
- `team-cycle` (the worker's NOT_RUN): 121 passed / 0 failed.
- My mutations, both restored from a backup copy with identical sha256 and a clean tree:
  - Gate's last word not required for green → 2 FAIL.
  - Docker check disabled → 1 FAIL.
- Real `-DryRun` on this machine: exit 0, "nothing to integrate", tree unchanged.
- The worker's three commits touch only the area. No release script, tag or last-known-good name in either script.
- **PostgreSQL (dev stack, `DbStore` directly, dialect postgresql):** everything the step writes fits.
  - Lock taken and released with a 61-character `integrate-integrate/<41>` cycle id; the other machine is refused.
  - A task goes `merged → returned` with a 1130-character Turkish reason, then `awaiting_release` with a 40-hex sha, and reads back intact.
  - A write on an old version is refused as stale; a report under a 54-character name is stored.
- The host-snapshot rule does not apply: the diff touches no `scripts/cloud`, `infra/docker` or migration.

**Pass 2 — break** (sandbox probes with the suite's own helpers; scratch files deleted)
1. **MAJOR — the lead-diff check misses a rename.** A lead run that does `git mv src/a/task-one.txt docs/task-one.txt` is not refused: exit 0, task `awaiting_release`, and main no longer has the file in the task's area. `Get-TeamWorktreeChanges` uses `git diff --name-only` with rename detection on, so only the destination under `docs/` is listed. Needs `--no-renames` and a test.
2. **MAJOR — a red gate whose queue write fails stalls the branch silently (API mode).** I changed one task on the fake API while the gate ran red. Run 1: exit 12, the red record is written, no task is returned and no reason is stored. Run 2 and after: exit 0, "waits: the gate was red on this commit". Nobody is told, and only `-ClearGateStop` moves it. A Cloud Core restart during the write would do the same.
3. **MEDIUM — an environment failure repeats a paid lead run every cycle.** With `uv sync` failing, three runs gave exit 10 three times, three lead runs, no strike and no stop. That is up to 48 lead runs a day. Build the environment before the lead run, or count the failure.
4. **MEDIUM — blame is wider than "the failure names the file".**
   - A PASS line inside a failing step returns that file's task. The real `script-syntax` prints `PASS  scripts\…` for every script, so one bad script returns every task that touched a `.ps1`.
   - A gate that dies without its last word blames from the whole log, so a task named only in a passing step is returned.
5. **MEDIUM — no default cap.** `-GateMinutes 0` and `-LeadMinutes 0` mean a hung gate holds the team lock until the six-hour stale takeover, and a later run would then reset the worktree under it. The scheduled call must pass both.
6. **MINOR (by reading, not run)**
   - The queue is read before the lock is taken.
   - Gate worktrees, each with its `.venv` and `node_modules`, are never removed: one per cycle id.
   - `gate-<n>.json` records are untracked and local, so strikes and the moved-ref stop are per machine.
   - Any other unexpected error after the lead run also repeats without a strike.
- The worker's open risks stand: the lock held for the whole gate conflicts with owner rule (c), and a failed push is not retried.

**Evidence classes**
- Decisions and sandboxed flow: PROVEN_AUTOMATED (fakes).
- Store widths on PostgreSQL: PROVEN_PROXY (the store called directly, not through `integrate.ps1` over HTTP).
- Tool resolution on this machine and `-DryRun`: PROVEN_PROXY.
- NOT_RUN: the real gate in a gate worktree, a real `claude -p` lead run, real `uv sync` / `pnpm install` there, `integrate.ps1` against the Cloud Core API, `Read-TeamGateLog` on a real gate log (I found none on this machine), and the Onay Merkezi showing "yayın bekliyor".

**Verdict**

`RETURN (1: lead-diff check must see renames — --no-renames plus a RED-first test of a file moved out of a task's area; 2: a red gate whose task writes fail must not end in a silent wait — re-derive returned/reason on the next run or do not treat the commit as already judged, with an API-mode test; 3: no paid lead run repeated on an environment failure — build first or count it; 4: blame only from failing lines, never from PASS lines or the whole log of a gate that died; 5: a default cap on gate and lead minutes, or refuse to run uncapped when scheduled)`
