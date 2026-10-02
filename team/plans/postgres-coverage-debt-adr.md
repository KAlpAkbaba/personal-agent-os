# ADR (unnumbered): Postgres coverage debt, first slice — how a table leaves the baseline

Status: proposed by worker `postgres-coverage-debt`; the lead numbers it at merge. Extends
ADR-0214 addendum 4 (a database change only SQLite has seen does not pass the gate).

## Decision

A table leaves `UNEXERCISED_BASELINE` when a test under `tests/integration` writes it on the
dev stack's PostgreSQL **through the production function that writes it**, with the four
values SQLite forgives. Naming the table is what the ratchet can check; these are what the
test must actually do, and what the eight tables of this slice now have:

| Edge | How it is taken to PostgreSQL |
|---|---|
| longest string the surface allows | written through the service at exactly that length, in Turkish letters (two bytes each in UTF-8, so a byte-counted column or validation would fail) |
| one character more | sent to the surface that claims the refusal (REST → 422, a service's typed error); the test then proves no row was written |
| JSONB | one nested document with Turkish keys and values, a null, a boolean, a float and a list inside an object inside a list; read back through a fresh session and compared whole |
| timestamp | aware and not UTC (Europe/Istanbul); compared as an instant and asserted aware on the way back |
| NULL | every nullable column asserted `None` on a row the production path leaves that way |

Per table, the production writer the test goes through:

| Table | Writer |
|---|---|
| `memory_versions` | `service.remember_explicit`, `service.edit_memory`, `PATCH /v1/memory/{id}`, `POST /v1/memory/remember` |
| `memory_evidence` | `service.record_observation` (→ `lifecycle.add_evidence`) |
| `memory_audit_events` | `lifecycle.record_audit` via `service.record_observation` / `remember_explicit`, `lifecycle.reindex_missing`, `POST /v1/memory/remember`, the `memory.remember` voice tool |
| `entities` | `service.create_entity`, `graph.sync_from_events`, `POST /v1/memory/entities` |
| `entity_edges` | `service.create_edge`, `graph.sync_from_events`, `POST /v1/memory/edges` |
| `routines` | `routines_service.create_routine` / `pause_routine` / `resume_routine` / `cancel_routine`, `evaluate_due` (the condition edge), `POST /v1/routines`, the `routine.create` / `routine.pause` voice tools |
| `routine_firings` | `routines_service.evaluate_due` |
| `wake_alarms` | `alarms_service.create_alarm`, `reconcile_local_fired`, `snooze_alarm`, `cancel_alarm`, `POST /v1/alarms`; and the cloud ring: `alarms_service.tick` (arm, greeting) → `routines_service.evaluate_due` → `ActionDispatcher` → `WakeAlarmRunner.fire` → `fire_alarm` → `WakeSequence.fire` / `speak_greeting`, then `stop_alarm` |

The cloud ring is run with the production dispatcher, runner and wake sequence as `app.main`
wires them (the runner in its own session); only the device port and the TTS provider are
fakes. It is what writes `armed_at`, `last_firing_id`, `media_session_id`, `greeting_due_at`
and `greeted_at`, which the device-local path only ever leaves NULL.

## Rules the next slice should keep

1. **A found defect is a strict xfail that names its exception.** `xfail(strict=True,
   raises=DataError, reason="<the PostgreSQL error>")`: a different failure is a real failure,
   and the fix turns the marker into an XPASS that fails the run until it is removed. The
   assertion accepts either honest answer (a refusal in the surface's own words, or a stored
   value that fits) so the test does not choose the fix.
2. **Do not name a table you are not paying for.** The ratchet matches table and model names
   as words anywhere under `tests/integration`. `graph.sync_from_events` is fed plain objects
   with the four attributes it reads, and the alarm history is read through
   `app.alarms.history`, so the ledger's table is not "covered" by a file that never tests it.
   The baseline lost exactly the eight tables of this slice (51 → 43).
3. **The engine is never run over a routine the test did not create; the year is not what
   guarantees that.** `evaluate_due` has no filter: it takes every armed routine in the
   database. A `now` in 2001 keeps it off exactly one kind, an `at` trigger in the future.
   It does NOT protect the others: `check_schedule_due` compares the weekday and the wall
   clock and never the year, so a 2001 instant DOES fire any armed schedule routine whose
   weekday and time match (measured on PostgreSQL: `weekdays=[2]`, 07:30 Europe/Istanbul,
   evaluated at 2001-09-12 07:30:20 → `('triggered', '2001-09-12')`); a presence trigger
   does not read `now`; a condition trigger answers to the context the test passes. With the
   production dispatcher that is a developer's own alarm driven to PLAYING by a test.
   So every `evaluate_due` and every alarm `tick` in the file goes through a wrapper that
   first reads the armed routines and **fails the test** (`pytest.fail`, naming the rows) if
   one exists that the test did not create - by id, by its `pgcov-<token>` name, or by the
   `alarm:<id>` source_ref of an alarm it made. The comparison is done in Python: `NOT
   (source_ref LIKE …)` is NULL for a NULL `source_ref`, and that row would pass a SQL guard.
   A test proves the guard on a weekday routine seen from a test that does not own it.
   What 2001 is still for: an alarm "rung" in 2001 starts a display holdoff that ended long
   ago, and `alarms_service.tick` moves no foreign alarm (it arms what is within twelve hours
   of `now`, greets and completes by stored instants; by reading, not probed).
   Limits, stated: the guard and the engine are two statements, not one transaction - the
   suite's advisory lock keeps other pytest runs out, a live API on the same database is not
   kept out (conftest warns). And the 10-second routine clock of an open application object
   runs with the REAL `now` and is not guarded: no test leaves a routine armed while one is
   open, except with a trigger in 2099.
4. **Rows go when the test ends; the append-only ones stay.** Memories are forgotten through
   `service.forget_memory`; entities, routines and alarms have no production delete and are
   removed by the fixture (their children by the tables' own ON DELETE CASCADE, which only
   PostgreSQL enforces — a missing cascade fails the teardown). Memory audit rows and ledger
   rows are left, as production leaves them.
5. **A client gives its connections back.** `create_app` builds a pool per runtime and the
   application disposes none, so each `owner_client` left about four connections open until
   the process ended. Both files open their clients through a local `_client` that listens
   for the engines that connect while it is open and disposes them on exit (the suite's
   shared identity runtime excepted). Measured on the two files, idle baseline 43: peak 125
   without the disposal, 54 with it. The helper is duplicated in the two files because
   `tests/integration/conftest.py` is outside this task's area; it belongs there, in
   `owner_client` itself, where it would also relieve the rest of the suite (289 of 300
   without this branch) - for the lead to queue.

## Defects found (not fixed here; each has a strict-xfail test with the error quoted)

| # | Where | What PostgreSQL said |
|---|---|---|
| 1 | `POST /v1/routines`: `source` has no `max_length`, the column is VARCHAR(32) | `StringDataRightTruncation: value too long for type character varying(32)` → 500 |
| 2 | `routine.create` voice tool passes the model's `name` unbounded; VARCHAR(200) | `... character varying(200)` |
| 3 | `routine.pause` / `routine.cancel` voice tools pass `reason` unbounded; VARCHAR(500) | `... character varying(500)` |
| 4 | `evaluate_due`: `skip_reason` is every unmet condition joined, unbounded; VARCHAR(500). The exception leaves the tick, so every routine after it in that pass is not evaluated | `... character varying(500)` on `INSERT INTO routine_firings` |
| 5 | `alarms_service.cancel_alarm` with a reason of 189+ characters (REST allows 200; measured: 188 passes, 189 raises): the `alarm.cleaned_up` ledger row's `source_ref` embeds the reason and is VARCHAR(256); `_record_ledger`'s handler then reads `alarm.id` on the rolled-back session | `... character varying(256)`, then `PendingRollbackError` out of a function documented "never fails the caller"; alarm already CANCELLED, caller gets 500, cleanup row never written |
| 6 | `POST /v1/memory/entities` with U+0000 in `name` or inside `attrs` | `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`; `UntranslatableCharacter: unsupported Unicode escape sequence` → 500 |
| 7 | `POST /v1/routines` with U+0000 in `name` | `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes` → 500 |
| 8 | `memory.remember` voice tool bounds the statement (500) and passes the model's `key` unbounded (`tools_memory.py`, the tool's schema names no length); `memories.key` and `memory_audit_events.key` are VARCHAR(256). Driven through the tool handler itself | `StringDataRightTruncation: value too long for type character varying(256)` on `INSERT INTO memories` |
| 9 | `POST /v1/memory/remember` with U+0000 in `text` (what `memories.text` and `memory_versions.text` store) | `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes` on `INSERT INTO memories` → 500 |
| 10 | `POST /v1/alarms` with U+0000 in `label` | `DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes` on `INSERT INTO wake_alarms` → 500 |

Defect 5 probably generalises: `app.alarms.service._record_ledger` logs an ORM attribute in its
`except` branch without rolling the session back first, so any ledger write PostgreSQL refuses
turns "the ledger is evidence, not a dependency" into an exception in the caller (measured for
the alarm path only, with and without a wake sequence: 188 passes and 200 raises either way).
`app.routines.service._record_ledger` has the same shape by reading; it was NOT run. Because
of defect 5, `terminal_reason` (VARCHAR(200)) is proven by a passing test at 188 characters
only; 189–200 is the strict-xfail test.

Defects 6, 7, 9 and 10 are one class: no surface strips or refuses U+0000. Five fields of
four in-slice POST bodies were probed and every one answers 500 (entity `name` and `attrs`,
routine `name`, remembered `text`, alarm `label`).
NOT_RUN for this class: the other text and JSONB fields of the same bodies (memory `key`,
`value`, `source`; `PATCH /v1/memory/{id}`; edge `relation`; routine `source_ref`,
`detail_json`, reasons; alarm `greeting_text`, `media`, cancel `reason`), the voice tools,
and every table outside this slice. The fix belongs at one place in front of all of them,
not per field.

## Unpaid in this slice's tables (for the lead to queue)

`wake_alarms` paths no integration test takes yet, each with writes PostgreSQL has not seen:

- the ring that fails (`WakeSequence.fire` → FAILED): `terminal_reason` cut to exactly 200
  from the device's own messages, `events.alarm_failed`'s notification;
- the tone fallback after a media failure (`detail_json.media_failure_reason`) and the
  stand-down when the device already rang (`local_fallback_already_rang`);
- a recurring alarm's release and catch-up (`_release` re-scheduling, `_catch_up_recurring`),
  `complete_alarm` at `max_play_seconds`, and `reconcile_local_snoozed`;
- a greeting that cannot be spoken (`detail_json.greeting_failure`, delivered as text).

`wake_alarms.device_id` has no writer in `app/` at all: it is NULL in every row production
can make, which is what the tests assert. `media_session_id` is VARCHAR(128) and the only
value production writes is `alarm-<id>`, 42 characters; that is the value tested.
