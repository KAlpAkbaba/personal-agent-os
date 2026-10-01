Both inspector points are fixed and committed; the branch push printed no error, but I didn't check it reached the remote. Commit sha: `dc17cedd7641e6b61e79f9257827f8fb53d8c791`.

**Seat indexing fix.** In `office.py`, a worker seat with no live run now takes the returned/stopped worker tasks by position among the free worker seats. It used to use the absolute seat index, which dropped task `a` when worker-1 was running. Returned tasks are also filtered by running task id up front.

**`updated_at` decision.** I put it in both the code and the ADR addendum.
- **Format:** only UTC `YYYY-MM-DDTHH:MM:SSZ` (the `team_store.stamp` form) is read. A value with no timezone or with an offset like `+03:00` makes `running=false` with no error. The status writer must emit `Z`; the ADR gives the PowerShell form.
- **Skew:** a value more than 2 minutes ahead of the clock is not live (`STATUS_FUTURE_SKEW_MINUTES = 2` in `office.py`). A value up to 2 minutes ahead is still live.

**Files changed (3, all in the area):**
- `services/api/app/team/office.py`
- `services/api/tests/unit/test_team_office.py`
- `team/plans/office-data-api-adr.md`

**Tests added.** All were RED before the fix, except the 2-minute-skew test, which is a guard that passes either way. Seven cases in total:
- A returned worker task is shown on worker-2 while worker-1 runs a task (the inspector's mixed working/returned case).
- Two returned/stopped tasks fill the free seats newest first around a running one.
- No timezone, `+03:00` offset, and 30 minutes in the future (3 parametrized cases): none is live.
- 30 seconds ahead is still live.

I ran the new tests with the full office file before the fix: 3 failed, 22 passed. The failures were the two seat tests and the future-dated case. The no-timezone and offset cases already passed because the strict parser rejects them.

**Mutation proof.** `office.py` sha256 is `c455fc4f…` before and after both mutations. I restored it from a backup copy each time.
- **Seat index reverted to absolute:** RED, 2 tests fail.
- **Future-skew rule removed:** RED, 1 test fails.

**Checks run:** `test_team_office.py` passes (25). With `test_team_state.py`, 74 passed and 1 failed. ruff check and ruff format are clean on the touched files.

**Failing test:** `test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has` in `test_team_state.py`. The inspector flagged it for the lead, and I did not look into it. It is outside my area.

**NOT_RUN:**
- Postgres DbStore.
- Full unit corpus and `quality-gate.ps1`.
- The PROVEN_REAL screenshot, because the page and the status writer don't exist yet.
- The first-round staleness and 40-line mutations, which were not re-run since I didn't touch those rules.

**For the lead at merge:**
- Resolve the PowerShell client contract test.
- Number the ADR into `docs/DECISIONS.md`.
- Update HANDOFF and BUILD_STATE.
- Tell the status-writer task that `updated_at` must be UTC `Z`.

**Open risk:** the DB `put_status` is last-writer-wins. The lock already keeps it to one machine.
