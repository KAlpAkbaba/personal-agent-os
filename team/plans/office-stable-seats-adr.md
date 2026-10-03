# office-stable-seats - ADR draft (STOPPED at the card's stop condition; not yet a decision)

Status: ALAN_ISTEGI. Nothing of (A) or (B) is built yet.

What was found (main a9ece545):

- The pool (cycle-seat-pool) gives a run NO seat number: `New-CycleStatus` writes each run entry as
  `task`, `role`, `started_at`, `model` only, and `office_view` seats worker runs by list position.
  So a seat number has to be built; nothing exists to reuse.
- `services/api/app/team/routes.py` validates the status strictly: `_Run(_Strict)` with
  `extra="forbid"` has no `seat` field. A PUT whose run carries `seat` is a 422 `extra_forbidden`
  (red test `test_the_seats_a_cycle_posts_on_its_worker_runs_come_back_on_the_office`, file and db
  stores).
- Worse than a dropped key: `Write-CycleStatus` in cycle.ps1 answers ANY 422 by switching to the
  legacy status for the rest of the cycle - so a cycle that sends `seat` to a server without the
  field loses `model` and `limits` on the Ofis page too. The route change must be released BEFORE
  (or with) the cycle change, and the cycle restarted only after.

Needed outside the area: `services/api/app/team/routes.py` - `_Run` gains
`seat: int | None = Field(default=None, ge=1)` (optional: an older cycle sends none). Strictness on
`seat` is a choice for the next run: `ge=1` refuses a bad seat with 422 (and trips the legacy
fallback); a plain `int | None` lets office.py's own fallback (invalid/duplicate -> unseated rule)
handle it. Recommendation: plain `int | None` - the page, not the heartbeat, should absorb a bad seat.
