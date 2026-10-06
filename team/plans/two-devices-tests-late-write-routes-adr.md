# ADR draft - two-devices-tests-late-write-routes: the eleven late write routes raced; three races found

Status: proposed (worker, cycle d20261006, 2026-10-07). Number: the lead's.

## Context

The 2026-10-07 integration put eleven write routes into the two-devices ratchet's
`UNCOVERED_BASELINE` (ceiling 190 -> 201): inbound-calls-bridge (`POST /telephony/inbound/voice`,
`/status`), money-ledger (`POST /v1/money/cash`, `/entries/{id}/cancel`,
`/questions/{id}/answer`) and cloud-task-loop-core (`POST /v1/web-tasks` and its `cancel`,
`confirm`, `continue`, `decline`, `read-back`). They were built in parallel with the ratchet.

## Decision

1. `tests/integration/test_two_devices_late_routes_pg.py` races all eleven with `fire_together` on
   real PostgreSQL. A check-then-write is held open with `meet_after` on the route's read; a
   second round sent after the first has landed is "the late device", where the idempotent
   answer or the refusal is decided.
2. The eleven are out of `UNCOVERED_BASELINE`; `BASELINE_CEILING` is back to 190.
3. The two routers `create_app` does not mount yet are tested the way their modules say: the
   web-task router is included on the real application (`owner_client`); the inbound router is
   on its own app (as in its unit test), with an ASGI shim that turns `fire_together`'s JSON
   into the signed form Twilio posts. The signature check and everything behind it are real.
   Temporal and the web task's target choice are stubbed, because neither one is what races.
4. A path given to `fire_together` is written out at the call site (an f-string or an imported
   constant). The ratchet reads it from the AST, so a helper that builds the path hides the
   route from it. Found while writing the file.

## Product defects found (the card's; each test is RED on the branch as it stands)

- **money-answer-race-double-books**: three devices answer "evet" to one spend question at once
  -> three `money_entries` rows (750 TL counted three times). One says "evet" and one says
  "hayır" -> both are told they won. Cause: `answer_question` reads the question with
  `db.get` and checks `answered_at` without a lock. Fix: read it `with_for_update=True`. The
  loser then sees `answered_at` and gets the existing `already: true` answer.
- **web-task-start-race-two-tasks**: three starts at once -> three running tasks, which breaks
  "one task at a time". Cause: `start_task_db` checks `active_task` and then inserts, with no
  lock. Fix: a transaction-level advisory lock (`pg_advisory_xact_lock`) before
  `active_task`. A partial unique index would need a migration.
- **web-task-owner-word-race**: `confirm` / `decline` / `continue` from three devices -> every
  device is told 200; with `continue` the answers of two devices are silently lost (last
  writer wins on `state_json`). Cause: `get_task` reads without a lock. Fix: a locking read
  (`with_for_update=True`) used ONLY by the owner-word functions (`confirm_db`, `decline_db`,
  `continue_db`, `request_cancel_db`, `note_read_back_db`). It must never be used by
  `run_round_db`: a round drives the browser for seconds and must not hold the row against
  the owner's cancel. The loser then meets a task that no longer waits and gets the existing
  409 (`not_waiting` / `nothing_to_confirm`).

Guards that already hold (mutation-proven): the cash route keeps every booking; the money
cancel and the web-task cancel are idempotent (the ledger's `(source, source_ref)` dedupes the
"finished" row); the read-back status check refuses a late surface; the inbound line's busy
check answers one call; the recorder's per-call lock writes one ledger row and ONE
notification (without the lock the ledger dedupes but three notifications are written).

## Consequences / open

- Web-task refusals carry `error_class` plus an English `detail` ("another browser task is in
  flight"). The card's rule asks for a Turkish refusal, so the REST surface needs a Turkish
  sentence per `error_class` (follow-up; not widened here).
- The fixes touch `services/api/app/money/routes.py` and `services/api/app/webtask/service.py`,
  which are outside this card's area (ALAN_ISTEGI).
