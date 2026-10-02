# Inspector report — `understanding-corrections-memory` @ `0a581524`

**Verdict: RETURN.** The module works as reported, but on the real schema a `vocabulary` row is rejected, there is no Postgres test for it, and the pair form can teach a wrong-device launch at HIGH.

**Pass 1 — ran it**
- **Diff:** the worker commit touches 5 files, all inside the area. `git diff main...` shows 10 more because the branch sits on `team/nightly/lead` (`db1ff2d9`), not on local main; those are not the worker's.
- **Unit:** `test_understanding_corrections.py` gives 34 passed, 2 skipped, matching the report. The two skipped are the only tests through the real relay.
- **Related suites:** memory and understanding suites give 354 passed, 2 skipped. Ruff check and format are clean.
- **Owner Utterance Suite:** 2756 passed, 0 failed (1268 s).
- **Mutations:** five of my own, each RED, each restored from a backup copy with sha256 `74ee1e9b…` and `17671d0a…` equal before and after:
  - taught word no longer binds in `read_turn` → 6 RED
  - cache never invalidated after first read → 6 RED
  - closed endings replaced by prefix match → 2 RED
  - policy row 1a disabled → 1 RED
  - proposal opened `"w"` instead of `"x"` → 1 RED
- **Postgres (the worker's NOT_RUN, run in a scratch database on the dev stack, alembic head `0063_team_state`, dropped afterwards):**
  - As committed: `learn()` fails with `IntegrityError` on `ck_memories_class` and returns `written=False, reason='write_failed'`. No row is written and the session stays usable.
  - With the worker's proposed 0064 constraint applied by hand: created → corroborated → superseded → forgotten all work, `vocabulary()` follows each change, and the proposal file is written once.
  - Existing `tests/integration/test_memory_persistence.py` and `test_migrations.py`: 10 passed.
- **Not run by me:** the full fast gate (it runs the whole unit suite), the 5-hunk relay patch, `LocalEmbedder`, and `supersede_memory` on a vocabulary row (my probe called it with the wrong arguments).

**Pass 2 — tried to break it**
1. **No Postgres proof on the branch (blocking).** The branch has no migration, no `MemoryClass.VOCABULARY`, and no integration test. Merged as-is with the relay patch, every correction in production is silently `write_failed` and the owner is asked again every time.
2. **The pair form teaches the router's own words as a device, silently, at HIGH.** `_why_not` only refuses whole known names.
   - "Ona ofis deme, hesap de" is learned as `hesap = ofis (cihaz)`; afterwards "Hesap makinesini aç" opens on ofis at HIGH 1.0 with no machine named.
   - "Ona ev deme, makinesini de" does the same for ev.
   - "Ona ev deme, bilgisayar de" makes "Bilgisayarda hesap makinesini aç" open on ev at HIGH.
   - There is no spoken receipt, so one misheard sentence is enough.
3. **Acceptance through the relay is not proven on any commit.** The two relay tests skip; the patch in the ADR is an abbreviated diff that cannot be applied mechanically.
4. **Notes, not blocking:**
   - `vocabulary()` leaves the ContextVar set after the turn; `resolve_intent` later in the same thread still reads the taught word.
   - `heard` has no length bound: a 212-character word was written and became the proposal file's name.
   - On the Cloud Core image no proposal is written (`no_proposals_dir`), as the worker reported.
   - No secrets or paths in code, no words on the audit row, `stt-confusions.json` untouched.

**Evidence classes**
- Module behaviour on SQLite, mutations, corpus: PROVEN_AUTOMATED
- Vocabulary row on PostgreSQL with the 0064 constraint: PROVEN_PROXY (my scratch run, not a committed test)
- Relay end to end: NOT_RUN on the committed branch
- `LocalEmbedder` near form: NOT_RUN
- Owner's first correction in production: READY_FOR_OWNER

**For the lead:** items 1 and 3 need files outside the card's area (`alembic/versions`, `app/memory/types.py`, `tests/integration`, `realtime_sessions/service.py`). Either widen the area on the return, or do them on the integration branch and send that back for inspection before merge.

`RETURN (1. add migration 0064, MemoryClass.VOCABULARY and a tests/integration Postgres test that writes, reloads, supersedes and forgets a vocabulary row through learn(); 2. refuse as a taught DEVICE word any token of an app alias phrase, the "bilgisayar" stem and the rule tables' verbs, with a RED-first test that "ona ofis deme, hesap de" teaches nothing; 3. land the relay wiring so the two relay tests run instead of skip, or hand over an appliable patch file)`
