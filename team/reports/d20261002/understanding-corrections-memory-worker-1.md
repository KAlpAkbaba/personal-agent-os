## Şu an üzerinde çalışılan
`understanding-corrections-memory` (ADR-0224 corrections, third pass) · area: `corrections.py`, its unit test, the ADR text · owner's dev PC, worktree `worker-understanding-corrections-memory`. Both return points are closed, committed and pushed; tree clean.

**sha:** `5b2e1dd2460ec4440bd86b751d3aa3558a6e60fa` on `team/d20261001/worker-understanding-corrections-memory`
**Files changed:** 3, all inside the area — `app/voice/understanding/corrections.py`, `tests/unit/test_understanding_corrections.py`, `team/plans/understanding-corrections-memory-adr.md`. `service.py` is untouched (sha `3f717a53…` as the inspector left it).

**What changed (point 1)**
- **Address word required:** the pair form must open with `ona` / `buna` / `şuna`. "Bunu bana deme…", "Öyle deme…", "Kimseye deme…" and "Şirket deme, iş de." are not corrections.
- **Known side is a bare name:** "evde de", "ofisteki de", "Chrome'da de" name nothing, so "Ona aptal deme, evde de." is not a correction.
- **Pronouns, adverbs, politeness words are `not_a_name`** for both kinds, from closed lists. "Ona ev deme, hemen de." teaches nothing.
- **Added beyond the card's wording:** a taught machine word now binds only before the computer word or with a place ending ("ofüs bilgisayarında", "ofüste"). Even with a row `bana = ev` in the table, "Bana hesap makinesini aç." names no machine. A bare "Ofüs hesap makinesini aç." no longer binds, the same as the grammar's own aliases.

**Point 2:** the new relay test says "Ona dur deme, ev de." (routed as stop) and "Ona devam et deme, ev de." (resume), and asserts no row is written. It first asserts the module alone would learn each, so the guard is what is being tested.

**RED → GREEN (PROVEN_AUTOMATED)**
- Before the fix: 20 failed, 47 passed — 19 unit cases plus the relay test, where "bana = ev" was stored.
- After: `test_understanding_corrections.py` 69 passed (was 45).
- The rule-first relay test was green from the start; its RED is mutation M5.
- Two older "guessed word" cases (`hemen`, `şimdi`) now report `not_a_name` instead of `not_similar`; I added `kırmızı` and `küçük` so the similarity rule keeps three cases.

**Mutations** — restored from backup copies, sha256 equal afterwards (`corrections.py` `d685efc4…`, `service.py` `3f717a53…`):

| Mutation | Result |
|---|---|
| M1 address word optional | 3 RED, incl. the relay test |
| M2 the three lists dropped | 11 RED, incl. the relay test |
| M3 bare-name rule dropped | 6 RED, incl. the relay test |
| M4 positional binding dropped | 1 RED (unit) |
| M5 relay guard `if intent.intent is Intent.NONE` removed | 1 RED (the new relay test) |

**Other runs**
- Owner Utterance Suite: 2756 passed, 0 failed (1834 s). I wrapped one docstring line in `corrections.py` just after it started; no code changed.
- Related understanding, memory, migration-compatibility and relay-world suites: 415 passed.
- PostgreSQL: the committed integration test gives 2 passed in a scratch database at `0064`, dropped afterwards; the shared dev database is still at `0063_team_state`.
- ruff check and format: clean on both Python files.

**NOT_RUN**
- The new relay tests on PostgreSQL (they run on SQLite; the change is sentence reading only).
- `quality-gate.ps1`, the rest of the unit suite, the other integration suites.
- `LocalEmbedder` near form.
- Owner's first correction in production: READY_FOR_OWNER.

**Open risks**
- Application words have no positional rule: the lists and router-word refusals are the whole guard there.
- The lists are closed and will miss words; for machines the positional rule covers that, for applications nothing does.
- "Dur" and "devam et" are learnable by the module alone; only the relay guard stops them, and M5 now proves it.
- Layer 2 still gets every device synonym as a surface form, so a near form anywhere in a sentence can be read back at MEDIUM.
- The pair form still has no spoken receipt.

**For the lead at merge:** nothing new. The ADR text has the third-pass section (the four rules, the cost, the open points); the earlier list — migration numbering, schema release, HANDOFF, DECISIONS — is unchanged.
