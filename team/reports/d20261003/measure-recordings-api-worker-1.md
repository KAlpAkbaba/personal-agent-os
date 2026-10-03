Both of the inspector's points are fixed and pushed. In the full unit suite, the only failure is doc row 41.7, and it fails the same way on base `b2797727`.

## Şu an üzerinde çalışılan
`measure-recordings-api` (fixing the inspector's return), area widened to `test_health_endpoint.py` and `test_maintenance.py`. Machine: the owner's build PC, worktree `worker-measure-recordings-api`.

**sha:** `896390a75a5545ceaeb41af7bc072dba5398cbe8`, pushed; the remote branch is at the same sha and the tree is clean.

**Files changed:** 2, both in the widened area, 8 lines added. Compared with `5843e31e`:
- `services/api/tests/unit/test_health_endpoint.py`: `"measurement_recordings"` added before `"audit_retention"` in the pinned sweeps list. That is the order `main.py` registers them in.
- `services/api/tests/unit/test_maintenance.py`: `Recordings.purge` is stubbed to return 0, and the expected dict now has `"measurement_recordings": 0`. The import is added.

**Tests (PROVEN_AUTOMATED):**
- **Affected files:** I ran `test_health_endpoint`, `test_maintenance`, `test_measurement_recordings` and `test_measurement_routes` together: **97 passed, 0 failed**. The two tests that failed on `5843e31e` now pass.
- **Full unit suite:** 14 786 tests collected, every one run, in two parts:
  - **Part 1:** run in one process, it stopped making progress at `test_research_focus.py::…[Bunu anlat.]`. Its CPU time was nearly frozen while three other workers' full suites ran on the machine. That is the known contention signature, not this diff. I stopped my own process (traced to this worktree before killing it). Tests 1–11 116 had run: **11 112 passed, 3 skipped, 1 failed**.
  - **The stuck file alone:** `test_research_focus.py` gave **38 passed** in 16 s.
  - **Part 2:** the remaining 146 files, starting at `test_research_focus.py`, gave **3 669 passed, 2 skipped, 1 xfailed, 0 failed** in 958 s.
  - **The one failure:** `test_qualification_evidence.py::test_every_proof_marked_row_points_at_something_that_exists`, doc row 41.7.
- **Row 41.7 on base `b2797727`:** I ran the test in a temporary detached worktree, removed afterwards. Result: **1 failed, 4 passed**, with the same assertion naming `41.7 (PROVEN_REAL): gate 38/38 on d93f6a2a…`. This branch touches no file under `docs/`, so it is not caused by this task.

**Fast checks:**
- `ruff check` on both files: all checks passed.
- `ruff format --check` flags `test_health_endpoint.py`: it wants an extra blank line before `_reconcile_status_reader`, around line 404. The base `b2797727` version is flagged the same way, so I left it, to keep the change to the two edits.

**Mutation proof:** none redone for this round, since these are pinned-list edits. The three mutation REDs and the five from the inspector were done on `5843e31e`, and `service.py` is unchanged since.

**Not redone:** the MinIO and integration runs. No product code changed since the inspector's passes: 1 passed on MinIO, 154 passed and 11 xfailed on integration.

**Still true from the first round:**
- The release has no migration and no new dependency, so it ships automatically under ADR-0214 addendum 9.
- The owner's approval of this idea is his permission for twenty scripted sentences of his voice to be kept 30 days on the Cloud Core.
- The two spoken sentences ('Ölçüm kaydını başlat' and 'ölçüm kayıtlarını sil') are deferred until `intents.py` is free.

**Open risk:** a full unit run on this machine can hang while several other workers run theirs at the same time. My run hit this, and it is not a product defect.
