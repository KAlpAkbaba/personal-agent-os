Verification finished; nothing is left running and the worktree is clean.

## Inspector report: `misheard-ledger-store` (cycle d20261002, pass 1)

**Subject:** `dd036e5c45215f1e13efdc93c7061e5ffc2029cd`, equal to origin, one commit on top of `0ec2b308`. The commit touches 2 files, both inside the area. No code was changed by me.

**Pass 1: what I ran**
- **Red run, PROVEN_AUTOMATED:** both test files fail at collection with `ModuleNotFoundError: No module named 'app.voice.misheard'`; `2 errors in 4.45s`, exit 2. This matches the report.
- **Ruff:** `ruff format --check` says 2 files already formatted; `ruff check` gives 2 × I001, as reported.
- **Stop reason, confirmed:** `integrate/d20261002` (`1b08509a`) and `gate/d20261002-2` (`ae9d8cf7`) carry `20261001_0064_memory_vocabulary_class.py`, with `revision = "0064_memory_vocabulary_class"` and `down_revision = "0063_team_state"`. The card's 0064 on 0063 would be a second alembic head.
- **Second blocker, confirmed:** HEAD, `main` and `team/nightly/lead` hold only 0063. `0ec2b308` is an ancestor of `integrate/d20261002` and `git merge-tree` is clean, so the rebase is mechanical.
- **Other migrations:** no branch or sibling worktree of d20261002 carries a 0065 or another 0064.
- **PostgreSQL: NOT_RUN, nothing exists to run.** There is no model, no migration and no `tests/integration/test_misheard_postgres.py`. The host snapshot was not judged, since no migration is in the diff yet.
- **NOT_RUN, as the report says:** green run, three mutations, full unit suite, integration suite, coverage ratchet, host-snapshot and migration-model agreement tests, ADR. `service.py`, `routes.py`, `models.py`, the `env.py` registration and the `main.py` lifespan are absent.

**Pass 2: findings**
1. **The wordless-log test will pass for the wrong reason.** `test_no_log_record_of_any_path_contains_the_sentence` reads `caplog` only. The house logger (`app.logging.get_logger`, used by every `app/voice` module) is structlog with `PrintLoggerFactory(sys.stdout)`. I emitted a warning through it with a stdlib handler attached: the line went to stdout and the handler saw 0 records. A `service.py` that logs the sentence through the house logger would leave this test green. It must also read `structlog.testing.capture_logs()` or `capsys`.
2. **`tool` has no width in the CONTRACT and `record()` does not cut it.** Sentence, engine and resolved_intent are cut; `tool` is not. This is the ADR-0214 addendum 4 shape (42 characters into VARCHAR(32)). The lead should fix a width in all four misheard-* cards; the Postgres test must write an over-long `tool`.
3. **The fault test does not prove the savepoint.** It patches `db.flush` and asserts `None` only. The claim that the caller's transaction stays usable rests entirely on the unwritten Postgres test.
4. **The lifespan purge has no test.** BUILD item 4 (purge at start, every 24 h, cancelled cleanly at shutdown) appears in no acceptance line, and the routes fixture uses `TestClient(app)` without a context manager, so the lifespan never runs.
5. **`band` and `confidence` are unguarded.** An unknown `band` is neither refused nor tested, while unknown reason and mode are.
6. **`is_request` is tested with integers.** If the relay passes a list of candidates, the signature differs; the lead should pin the type across the four cards.
7. **Count drift in the report:** it says 26 store test functions; `grep -c "^def test_"` gives 25 (routes 9, as reported).
8. **Hold boundary:** tested at 119 s and 121 s, not at 120 s.
9. **Clean on:** secrets, paths, files outside the area, contract drift. The worker's commit did not update `docs/HANDOFF.md` (CLAUDE.md asks every commit to); HANDOFF is outside the card's area, so this is the lead's call.

**For the lead before re-issuing**
- Change the area line to `20261002_0065_misheard_utterances.py`, with `down_revision = 0064_memory_vocabulary_class`.
- Rebase the branch onto `integrate/d20261002`.
- The stale name `0064_misheard` is also in `team/plans/d20261002-split-idea-2026-10-02-yanlis-anlasilan-cumle-defteri.json`.
- The release exception stands: the card carries a migration, so its release is asked of the owner (ADR-0214 addendum 9).

The worker's stop was correct and honestly reported; the task itself is not done.

RETURN (1: area → `20261002_0065_misheard_utterances.py` on `0064_memory_vocabulary_class`, branch rebased onto `integrate/d20261002`; 2: implement models/service/routes/migration/env.py/main.py + ADR, green with raw counts; 3: write the Postgres test `tests/integration/test_misheard_postgres.py` and run it on the dev stack, including the savepoint and an over-long `tool`; 4: wordless-log test must capture structlog/stdout, not caplog alone; 5: fix a width for `tool` in the CONTRACT and cut it in `record()`; 6: a test for the lifespan purge and its clean cancel; 7: three mutation REDs with sha256 restore; 8: correct the test count)
