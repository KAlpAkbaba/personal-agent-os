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
| `memory_versions` | `service.remember_explicit`, `service.edit_memory`, `PATCH /v1/memory/{id}` |
| `memory_evidence` | `service.record_observation` (→ `lifecycle.add_evidence`) |
| `memory_audit_events` | `lifecycle.record_audit` via `service.record_observation` / `remember_explicit`, `lifecycle.reindex_missing`, `POST /v1/memory/remember` |
| `entities` | `service.create_entity`, `graph.sync_from_events`, `POST /v1/memory/entities` |
| `entity_edges` | `service.create_edge`, `graph.sync_from_events`, `POST /v1/memory/edges` |
| `routines` | `routines_service.create_routine` / `pause_routine` / `resume_routine` / `cancel_routine`, `evaluate_due` (the condition edge), `POST /v1/routines`, the `routine.create` / `routine.pause` voice tools |
| `routine_firings` | `routines_service.evaluate_due` |
| `wake_alarms` | `alarms_service.create_alarm`, `reconcile_local_fired`, `snooze_alarm`, `cancel_alarm`, `POST /v1/alarms` |

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
3. **Every instant is in the past (2001).** `evaluate_due` looks at every armed routine in the
   database; a `now` in 2001 can only make the test's own routines due, and an alarm "rung"
   in 2001 starts a display holdoff that ended long ago. No test leaves a routine armed while
   an application object (and its 10-second routine clock) is open, except with a trigger in
   2099.
4. **Rows go when the test ends; the append-only ones stay.** Memories are forgotten through
   `service.forget_memory`; entities, routines and alarms have no production delete and are
   removed by the fixture (their children by the tables' own ON DELETE CASCADE, which only
   PostgreSQL enforces — a missing cascade fails the teardown). Memory audit rows and ledger
   rows are left, as production leaves them.

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

Defect 5 probably generalises: `app.alarms.service._record_ledger` logs an ORM attribute in its
`except` branch without rolling the session back first, so any ledger write PostgreSQL refuses
turns "the ledger is evidence, not a dependency" into an exception in the caller (measured for
the alarm path only). `app.routines.service._record_ledger` has the same shape by reading; it
was NOT run. Defects 6–7 are one class (no surface strips or refuses U+0000) and
almost certainly reach every text and JSONB column behind a REST body; only the in-slice
tables were probed.
