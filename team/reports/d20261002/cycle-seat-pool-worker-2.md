## Şu an üzerinde çalışılan
- **İş:** `cycle-seat-pool` (denetleyiciden geri dönen iki madde), alan: `scripts/team/cycle.ps1`, `scripts/tests/team-cycle.tests.ps1`, `team/plans/cycle-seat-pool-adr.md`; makine: ev PC'si, worktree `worker-cycle-seat-pool`.
- `docs/HANDOFF.md`'ye dokunulmadı; lead bu rapordan yazar.

## Report
**sha:** `9ed17633b3d7a2aa506dbe47e5dfa3ddd651e57f`, pushed to `team/d20261002/worker-cycle-seat-pool`; worktree clean. 3 files changed, all inside the area.

**Return point 1 — a failed start no longer ends the cycle.**
- A refill now counts its failed starts. One failed start makes the next refill due at once and is not an idle pass.
- This also holds beside runs in flight: the free seat goes to the next task immediately, not when another run ends.
- It terminates: each failed start stops its task.

**Return point 2 — a researcher or lead split that cannot be started is caught.**
- Researcher: one line under the stops (`araştırmacı: koşu başlatılamadı`), no finished-run marker, so the next cycle tries again.
- Lead split: one line under the risks (`bölme koşusu: <id>: koşu başlatılamadı`), the proposal stays where it was, one try per cycle.
- Neither throws out of the refill any more, so the `finally` does not kill runs in flight.

**Tests added (5), RED on `ab3d3b9c` → GREEN on the fix — PROVEN_AUTOMATED (fakes):**
1. Three assigned tasks, one worker seat, first worktree blocked. RED: `stopped,assigned,assigned`. GREEN: `stopped,merged,merged`.
2. Four failed starts in a row, fifth task run. RED: `stopped,assigned,assigned,assigned,assigned`.
3. Failed start beside an 8 s run: task-three's snapshot names `task-one:worker` beside it. RED: `task-three:worker` alone.
4. Researcher role file missing, with `-Research`. RED: exit 1. GREEN: task merged, one report line, no marker.
5. Lead role file missing. RED: exit 1. GREEN: task merged, proposal unchanged, one report line.

**Mutations (4), all RED, restored from a backup copy; sha256 `d224985e571a6731…` before and after each:**
- Failed starts not counted against the idle pass: tests 1 and 2 fail.
- No refill after a failed start: test 3 fails.
- Researcher's failed start rethrown: test 4 fails.
- Lead's failed start rethrown: test 5 fails.

**Suite runs:**
- `installer-strictmode.tests.ps1`: 24 passed, 0 failed. `script-syntax.tests.ps1`: 147 scripts, 0 failed.
- `team-cycle.tests.ps1` was not completed in one run. The first run was killed at the 60-minute background limit with 146 passed, 0 failed.
- The remaining 62 cases, run as a slice: 59 passed, 3 failed.
- The three failures are existing store-outage cases pinned with `-PollMilliseconds 6000`:
  - "each run's result is written when it is applied…"
  - "a refused 'merged' is not forgotten…"
  - "a refused 'merged' is TAKEN BACK…"
- Re-run alone, all three pass (3/3). During the failing slice about 40 `powershell.exe` processes were running on the machine; at the re-run, 13.
- A single clean full-suite run on the final sha: NOT_RUN.

**ADR:** `team/plans/cycle-seat-pool-adr.md` gained the "a start that fails is a change, never the end" decision and the new evidence; unnumbered, for the lead to move.

**NOT_RUN:**
- Ofis page showing a worker seat refilled while another worker runs (PROVEN_REAL; needs a real cycle).
- `quality-gate.ps1` and the Python suite.
- The three failed cases on the base commit under the same load, so "load, not this diff" is my reading, not proven. The fix only acts when a start fails, and none does in those tests.

**Open risks:**
- The one-poll pin is a stopwatch: under heavy load the two fakes end in different polls and those three trace-shape tests fail. Run the gate for this branch on a quiet machine, or replace the pin with a barrier hook in the fake as a separate task.
- With nothing in flight, each failed start costs about 1 s before the next refill.
- The inspector's earlier risks stand: `-MaxParallel` now means worker seats only in `tick.ps1` / `register-nightly.ps1`, and merges run beside live workers.
