## Şu an üzerinde çalışılan
- **Task:** `postgres-coverage-debt` (the inspector's return, four points)
- **Area:** the two integration test files and the ADR text; the ratchet file is unchanged
- **Machine:** owner's dev PC, dev stack PostgreSQL on 127.0.0.1:15432

**Result:** all four returned points are addressed and pushed.
- **Commit:** `583135309765bbc30034b56164b1faf623197e67`, tree clean. Three files changed since `645cd72d`, all inside the area.
- **Full integration suite:** 132 passed, 11 xfailed, exit 0, 159 s (was 131 / 8).
- **Ratchet:** 3 passed; the baseline is still 43.
- **Lint:** `ruff check` and `ruff format --check` clean on the three test files.

**The four points**
1. **Voice key over 256:** `test_a_spoken_memory_key_longer_than_its_column_is_refused_in_words` drives the `memory.remember` tool handler itself with a 257-character key. Strict xfail, RED first: `StringDataRightTruncation: value too long for type character varying(256)` on `INSERT INTO memories`.
2. **NUL, two surfaces:** both strict xfail, RED first with `psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes`.
   - `POST /v1/memory/remember` with U+0000 in `text` fails on `INSERT INTO memories`.
   - `POST /v1/alarms` with U+0000 in `label` fails on `INSERT INTO wake_alarms`.
   - The ADR's NOT_RUN line now lists the five fields probed and the ones not probed.
3. **`terminal_reason` at 188:** the green lifecycle test now cancels with a 188-character Turkish reason, asserts the stored length and asserts the cleanup ledger row exists. The docstring no longer claims 200.
4. **Cloud ring path exercised:** new green test `test_the_cloud_rings_a_wake_alarm_through_the_routine_engine_on_postgres`.
   - Path: `alarms_service.tick` → `routines_service.evaluate_due` → `ActionDispatcher` → `WakeAlarmRunner.fire` → `fire_alarm` → `WakeSequence.fire`, then the greeting tick, then `stop_alarm`.
   - Only the device port and the TTS provider are fakes.
   - `armed_at`, `last_firing_id`, `media_session_id`, `greeting_due_at` and `greeted_at` are written and read back; the three timestamps are asserted timezone-aware.

Also done: the 65-character timezone refusal is now asserted to be `string_too_long` (inspector's low point 6).

**Defects found** (not fixed; each is a strict xfail, listed in the ADR as 8–10)
- 8: the `memory.remember` voice tool passes `key` unbounded into a VARCHAR(256) column.
- 9: `POST /v1/memory/remember` answers 500 for U+0000 in `text`.
- 10: `POST /v1/alarms` answers 500 for U+0000 in `label`.

**Mutation proof** (production files, each restored from a backup copy, sha256 identical, tree clean): 5 RED of 5.
- `media_session_id()` made 129 characters: cloud ring test RED.
- Cleanup `source_ref` prefix one character longer: the 188 test RED with `varying(256)` then `PendingRollbackError`.
- Voice key cut to 256: `XPASS(strict)` fails the run.
- NUL stripped from the alarm label: `XPASS(strict)` fails the run.
- `armed_at` written naive: cloud ring test RED.

**Evidence classes**
- Points 1–4 and defects 8–10: PROVEN_AUTOMATED on real PostgreSQL.
- Defect 5 with a wake sequence attached (188 passes, 200 raises, same as without): PROVEN_AUTOMATED by a scratch probe that is not in the tree.
- NOT_RUN:
  - U+0000 on the other fields of the same bodies, on the voice tools, and on tables outside the slice.
  - The NUL test for `/v1/memory/remember` passing mutated.
  - The unit suite beyond the ratchet, `quality-gate.ps1 -Fast`, and mypy.

**Open risks**
- My scratch probe crashed before its cleanup and left three 2001 alarms and their routines in the dev database. I deleted them; alarms and routines both count 0 after the final run.
- Inspector's low point 5 stands: the condition-trigger test would give a developer's own armed `device_idle` routine a 2001 firing. `evaluate_due` has no filter; recorded in the ADR as a known limit.
- `wake_alarms` paths still unpaid, listed in the ADR for the lead to queue:
  - the failed ring (`terminal_reason` at exactly 200);
  - the tone fallback and the stand-down when the device already rang;
  - a recurring alarm's release and catch-up;
  - `complete_alarm` and `reconcile_local_snoozed`;
  - a greeting that cannot be spoken.
- `wake_alarms.device_id` has no writer in `app/`, so it is only ever asserted NULL.
