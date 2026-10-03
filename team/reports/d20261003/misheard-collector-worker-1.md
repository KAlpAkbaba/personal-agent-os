## Şu an üzerinde çalışılan
- **misheard-collector** (d20261003): the misheard notebook feeds the STT collector. Area: `scripts/voice/collect-stt-corpus.ps1` plus two new test files and the ADR text. Machine: the owner's build PC, worktree `.claude/worktrees/team/d20261003/worker-misheard-collector`.

## Report
**sha `553e17ea00f984ceb38127ff2558713976ab31f1`**, pushed to `team/d20261003/worker-misheard-collector`. The worktree is clean. 4 files changed, all inside the area:
- `scripts/voice/collect-stt-corpus.ps1`: +102 / −8
- `services/api/tests/unit/test_stt_collector_misheard.py`: new, 11 tests
- `services/api/tests/integration/test_misheard_collector_postgres.py`: new, 1 test
- `team/plans/misheard-collector-adr.md`: ADR text, unnumbered

**What changed in the script:**
- `-ShowQuery` now prints three read-only SELECTs. The old session/audit `union all` became two statements, and the third reads `misheard_utterances` with all 15 contract columns.
- A `misheard` line becomes a proposal the same way a `session` line does.
- The day is taken in UTC, whatever offset the database wrote.
- A `misheard` proposal carries mode, engine, device_id, reason, resolved_intent, band and confidence.
- Status is `owner_answered` when the row has an answer and `needs_owner_meaning` when it does not.
- One sentence gives one proposal across both line kinds. `times_heard` is summed and the earliest day is kept. A line without an answer never erases one; two different answers are kept as `meant` + `meant_also` and never merged.
- The report gains `misheard {rows, by_reason, by_mode, answered}`.

**Evidence (PROVEN_AUTOMATED, Windows PowerShell 5.1):**
- **Red first:** before the script change, the new suite was 9 failed, 2 passed. The 2 that passed were case 7 and case 8; passing on the old script proves the expected list in case 7 is what the script produced before this card.
- **After the change:** the new unit suite is 11 passed.
- **Existing suite:** `test_stt_utterance_corpus.py` passes unchanged: 135 passed, 1 xfailed.
- **PostgreSQL round trip, real dev stack:** 1 passed (7 passed together with `test_misheard_postgres.py`).
  - Rows are written through `service.record` and `service.answer`.
  - The dump comes from running the third statement taken from the script's own `-ShowQuery` output.
  - The proposals match cases 1 and 2, with the Turkish letters intact.
  - This is not NOT_RUN.
- **Mutation 1** (an unanswered line clears the answer): cases 3 and 4 went RED (2 failed). Restored from a backup copy.
- **Mutation 2** (the real-rendering skip removed for `misheard` lines): case 5 went RED (`already_in_corpus` 0 == 1). Restored from a backup copy.
- **Restore hashes:** sha256 `e49cd3d4c48955917de2d1219db5582e6389047b1d0c25438a5aa4d64134b231` before, and the same after each restore.
- **Fast checks:** `ruff check` and `ruff format` are clean on both new files.
- **Case 8:** the sha256 of `stt_corpus.py` is the same after every run, and all four existing refusals still exit non-zero with "refusing".

**Not done:**
- **Red-first for the integration test:** not run separately. It is red by construction on the old script, which has no `misheard` statement, but I did not execute that.
- **READY_FOR_OWNER item:** not claimed. That is the owner's real answer showing up in the proposals; it depends on misheard-relay-wiring's trial.

**Open risks:**
- **Same-process error:** running the unit and integration files in one pytest process gives an ERROR on the integration test, from the unit suite's real-database guard. Run separately they pass, which is how the gate runs them. Running them together will show this error.
- **Merge into a `session` proposal:** when a `misheard` line joins a `session` proposal, the session's resolved_intent, band and confidence are kept and the notebook adds only mode, engine, device_id, reason and `meant_also`. This was my choice; it is recorded in the ADR text.
- **New dump command:** a dump produced with the new `-ShowQuery` is three statements' output, still under `psql -At -f`. The procedure is unchanged, but an old saved copy of the query has no notebook statement.
