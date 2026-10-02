## Şu an üzerinde çalışılan
**cycle-seat-pool** (worker, döngü d20261002) · alan: `scripts/team/cycle.ps1`, `scripts/lib/TeamRun.ps1`, `scripts/lib/TeamQueue.ps1`, `scripts/tests/team-cycle.tests.ps1`, `scripts/tests/lib/fake-claude.ps1`, `team/plans/cycle-seat-pool-adr.md` · makine: bu PC · dal `team/d20261002/worker-cycle-seat-pool`. Durum: bitti, commit edildi ve push edildi; denetleyiciyi bekliyor.

## Report
- **sha:** `ab3d3b9c0563841e2b880fdb8a90dfb3c24ba5fc`, pushed; worktree clean.
- **Files changed:** 6, all inside the area. The fake listener (`scripts/team/fake-team-api.ps1`) is untouched.
- **What was built:** the pool replaces the batch loop; the researcher and the lead split run inside it, so `Invoke-RoleRun` and the blocking `Wait-UsageLimit` are gone.
  - Runs in flight are polled; one that ends is completed at once and free seats are filled in queue order.
  - Seats are per role: `-MaxParallel` workers, `-MaxInspectors` 2, `-MaxIntegrators` 1, one researcher and one lead split beside them.
  - A worker or inspector whose area overlaps a task in flight waits for that run; merges are made one after the other.
  - The store is re-read at every refill; the task of a run in flight stays the cycle's copy with its version.
  - `team/cycle-settings.json` is read at every refill; `-MaxHours` (4) starts nothing new and ends when the runs do.
  - The usage limit is waited out without blocking.
- **Tests:** full suite 203 passed, 0 failed (baseline 184 before my change; 19 new).
- **RED→GREEN:** 14 of the first 16 new tests were RED on the batch loop, then all green on the pool.
  - Behavioural RED: seat refilled while a long worker runs; workers beside inspections; five runs in flight; settings honoured; late card started beside a long run; store-assigned overlapping task held back; researcher beside the worker.
  - RED only by a missing function or parameter: the four seat/settings unit tests, `-MaxHours`, `-RefillSeconds`, two merges in one poll.
  - Green before and after ("still holds"): overlapping approved pair never in flight together; stop flag with a run in flight.
  - Three tests were written after the pool and have no RED-before: limit waited out without blocking, one stop line for two limited runs, a dying cycle kills its runs. Each is held by a mutation.
- **Mutation proof:** 17 mutations, all RED, each restored from a backup copy with sha256 equal before and after. They include the two the card names (area check removed; seat count ignored). One survived on the first pass (second run for a task in flight); I tightened the unit test and the seat fill, and it is RED now.
- **Existing tests I changed (6):**
  - Three stated the old meaning of `-MaxParallel 1` and now state per-role seats: the money cap (3 runs, not 2), the safe stop during an inspection (task-two's worker also finishes), and "finished run is gone" (asserts the finished worker is absent rather than an exact list).
  - Four store-outage tests are pinned with `-PollMilliseconds 6000` so both runs end in one poll, the batch shape they replay. Their assertions are unchanged.
- **Fast checks:** script-syntax 147/0, installer-strictmode 24/0, provision 13/0 (parameter lint), team-feed 65/0.
- **Evidence class:** PROVEN_AUTOMATED (fakes) for every claim above. PROVEN_REAL (Ofis page showing a worker seat refilled while another worker runs) is NOT_RUN. The Python suite (`test_team_state.py`) and the full gate are NOT_RUN; no API path or field changed.
- **Decisions I made (in the ADR text, unnumbered):**
  - A periodic refill every `-RefillSeconds` (120), because "when a run ends" never sees a card stored beside a single long run.
  - A cycle that dies now kills the runs it leaves in flight, so nothing writes to a worktree after the lock is released.
  - Caps are checked at every refill that has something to start.
  - The limit wait is cycle-wide but non-blocking.
- **Could not do, outside the area:** `tick.ps1` and `register-nightly.ps1` still pass only `-MaxParallel`, so inspector and integrator seats take their defaults until the lead passes them or writes the settings file. The Ofis capacity number does not read `cycle-settings.json`.
- **Open risks:**
  - The seat file is this PC's, also in API mode; there is no team-store route for it.
  - The four pinned tests assume a fake run ends within 6 s; a very loaded machine could break the pin.
  - A lead split is now judged against the queue when its run ends, so a task merged meanwhile no longer refuses an overlapping split.
  - One 0.6 MB queue GET every two minutes while a cycle lives.
  - `-MaxHours` defaults to 4, so a long cycle ends itself and relies on the scheduler's next tick.
