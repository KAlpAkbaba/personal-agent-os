## Şu an üzerinde çalışılan
- **Task:** `understanding-engine-startup` (ADR-0224 layer 2 in production). Area: the six files on the card. Machine: home PC.
- **Branch:** `team/d20261001/worker-understanding-engine-startup`

## Verdict: DONE — ready for inspection
Everything is committed and pushed, the worktree is clean, and nothing I started is still running.

- **sha:** `ec0c1b72300815da13589e1f50673bb4dd174e95` (local HEAD equals the remote branch head).
- **Why it came back:** the last report ended with the suite still running, and the six files had never been committed (branch head was still `6f097a43`). This run committed them, ran the suite to the end, and only then reported.
- **Files changed:** 6, all inside the area (`exemplars.json` with 1427 entries, `exemplars.py`, `startup.py`, the export script, the test file, the ADR text).

## Tests (PROVEN_AUTOMATED)
- **Task tests:** `tests/unit/test_understanding_startup.py` is 26 passed.
- **All understanding unit tests:** `tests/unit/test_understanding_*.py` is 234 passed.
- **Owner Utterance Suite:** 2756 passed, 0 failed, exit 0, in 36 minutes on the shared machine. It ran to completion on the files as committed; the second commit only changes the ADR text.
- **ruff:** check and format are clean on the four Python files.
- **RED before:** with `startup.py` absent the test file fails at collection (1 error); restored, it is 26 passed.

## Mutations
Each was restored from a backup copy with sha256 equal before and after.

| Mutation | Result |
|---|---|
| Semantic-only guard removed | 2 failed (both deterministic-embedder cases) |
| Byte-equality comparison removed from `--check` | 1 failed |
| `exemplars.json` one entry short | 3 failed, including the byte-equality test |
| Unknown-intent refusal removed | 1 failed |
| Local-provider guard removed | 1 failed (the openai case) |
| Index build run inline instead of spawned | 2 failed (slow-build bound, background thread) |

## Real measurement (LocalEmbedder, potion-multilingual-128M, home PC)
- **Index build:** 374 / 310 / 299 ms for 1427 exemplars, measured while the suite was running.
- **Return time:** `configure_understanding` returns in about 0.5 ms, and the engine is not ready at that moment, as designed.
- **Sentence no rule matches:** "Dışarıda hava nasıl bugün" comes back as layer `semantic`, weather_query 0.71.
- This proves the real embedder in a script, not the served app.

## For the lead at merge
- In `services/api/app/main.py`, inside `create_app`, directly after `memory = MemoryRuntime(settings)` (line 218 today), add:
  `configure_understanding(settings, memory.embedder, report=memory.embedder_report)`
- Add the import `from app.voice.understanding.startup import configure_understanding`.
- Optional off-switch: `understanding_semantic_enabled: bool = True` in `app/config.py`.
- The ADR text, including why there is no new health field, is in `team/plans/understanding-engine-startup-adr.md`.

## Not done
- **PROVEN_REAL: NOT_RUN.** A production audit row saying layer 2 needs the `main.py` line, which is the lead's, and a release.
- **Corpus-case drift shown in-area.** I showed it by shortening `exemplars.json` rather than editing `tests/voice_corpus/corpus.py`, which is outside the area. A separate test also renders the corpus plus one extra case and asserts it differs from the committed file.

## Open risks
- **The real embedder does not fix the two guard sentences.** "Bugün nasılsın" still reads weather_query 0.62 and "Araştırmayı iptal etme" research_cancel 0.93. I did not check what the policy then does with them; the ADR says the negation rule caps the second below HIGH and that a reading no rule matched is recorded, not acted on.
- **The Owner Utterance Suite cannot see the live engine.** Its apps run the deterministic provider, so the engine is never configured there.
- **One corpus sentence could become a question.** The ADR records, from the earlier session, that a repaired rule route can be contested by a HIGH semantic reading for "Araştırmayı tekrar dene."; I did not re-measure this.
- **Thread safety.** The build runs on a second thread through `LocalEmbedder`, whose LRU cache is unlocked; a lock belongs in `app/memory/providers.py`, outside this area.
- **Workflow change.** A corpus change now needs the export re-run in the same commit; the failing test names the command.
