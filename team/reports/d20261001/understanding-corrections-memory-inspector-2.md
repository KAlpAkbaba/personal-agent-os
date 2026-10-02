# Inspector report 2 — `understanding-corrections-memory` @ `c627f088`

**Verdict: RETURN.** The three points of the first return are fixed and hold on real PostgreSQL, but an ordinary sentence still silently teaches a function word as a machine, and the next launch goes to the wrong PC at HIGH.

**Pass 1 — ran it**
- **Diff:** `db1ff2d9..HEAD` is 9 files; four are outside the card's area, as the worker says, and each is named by the first return.
- **Unit:** `test_understanding_corrections.py` gives 45 passed, 0 skipped.
- **Related suites:** memory, understanding, `test_migration_compatibility` and the relay world give 596 passed.
- **Owner Utterance Suite:** 2756 passed, 0 failed (1469 s).
- **Lint:** ruff check and format are clean on the 8 touched Python files.
- **PostgreSQL, table (dev stack, scratch database):** the new integration test plus `test_memory_persistence` and `test_migrations` give 12 passed at `0064`; the widened constraint is on the real table.
- **PostgreSQL, relay (the worker ran this on SQLite only):** both relay tests pass against a scratch database migrated to head. The row is written durable and explicit, and neither taught word appears in any `audit_events` row.
- **Host snapshot:** `collected_at` 2026-10-01T19:18Z is after the 16:27Z release. `memories.key` is 256 wide and the longest possible key is 105; `memory_class` is 32 wide.
- **Mutations:** six of my own, restored from backup copies; sha256 `954631ff…`, `3f717a53…`, `36f81c71…` equal afterwards, tree clean.
  - `_unlike` never refuses → 5 RED
  - `vocabulary()` ignores row status → 1 RED
  - relay never pops the correctable → 1 RED
  - relay calls `policy.read_turn` → 2 RED
  - migration adds no value → 1 RED on Postgres
  - **relay rule-first guard removed (`if intent.intent is Intent.NONE`) → 45 passed.** The card's "rule first" is not tested at the relay.
- **Not run:** `quality-gate.ps1`, the rest of the unit suite, the other integration suites, `LocalEmbedder`.

**My own mistake, already undone:** my first relay probe ran `alembic upgrade head` against the shared dev database and took it from 0063 to 0064. It held 0 vocabulary rows; I downgraded it and it is back at `0063_team_state` with the six-value constraint. Both scratch databases are dropped. Sibling worktrees that ran `alembic upgrade head` in those few minutes would have failed to find revision 0064.

**Pass 2 — tried to break it**
1. **Blocking: the pair form reads ordinary speech as teaching.** `_PAIR` makes the address word optional, and `_NOT_A_NAME` lacks pronouns and adverbs. Through the real relay, in an office-bound session:
   - "Bunu bana deme, evde de." is stored as `bana = ev (cihaz)`; then "Bana hesap makinesini aç." is HIGH 1.0 and the command lands on MAIL (the home PC).
   - The same happens with "Öyle deme, evde de." (`öyle`), "Kimseye deme, evde de." (`kimseye`) and "Ona aptal deme, evde de." (`aptal`).
   - "Ona ev deme, hemen de." stores `hemen = ev`, which the module's own docstring says must never be taught.
   - The row is durable and applies in every session; nothing is spoken, so the owner cannot know.
2. **Test gap:** the surviving rule-first mutant above.
3. **Notes, not blocking:**
   - `memory.remember` cannot create a synonym (it passes no value), but with key `device:<word>` it would supersede one; that fails towards asking again.
   - The proposal file holds the two words and sits in a tracked directory.
   - The ContextVar note and `no_proposals_dir` on the image are unchanged, as reported.
   - "Ona hesap makinesi deme, hesaplayıcı de" works end to end, including the tool call.

**Evidence classes**
- Module, SQLite relay, corpus, mutations: PROVEN_AUTOMATED
- Vocabulary row on PostgreSQL at 0064: PROVEN_AUTOMATED (committed test, re-run by me)
- Relay end to end on PostgreSQL: PROVEN_PROXY (my out-of-tree probe, not committed)
- `LocalEmbedder` near form and the full gate: NOT_RUN
- Owner's first correction in production: READY_FOR_OWNER

`RETURN (1. the pair form must require its address word (ona/buna/şuna) and refuse pronouns, adverbs and politeness words as a machine name - "Bunu bana deme, evde de", "Öyle deme, evde de", "Kimseye deme, evde de" and "Ona ev deme, hemen de" teach nothing, RED-first through the relay, asserting the next "Bana hesap makinesini aç" does not land on ev; 2. one relay test that fails when the rule-first guard is removed)`
