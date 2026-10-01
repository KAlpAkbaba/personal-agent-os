**Report: understanding-semantic-index (returned task, second pass)**

**sha:** `7f778f2cf6a1d9a22691e312e95b8b4c48cf2287`, pushed. The worktree is clean. 7 files, all inside the area.

**Şu an üzerinde çalışılan (for the lead's HANDOFF):** `understanding-semantic-index`, area `services/api/app/voice/understanding/` plus its two test files, home PC. Done; no longer in progress.

**Return points**
1. **THIRD_PARTY entry:** written as a ready-to-paste "considered and rejected" entry for rapidfuzz and py-rust-stemmers. It is at the end of `team/plans/understanding-semantic-index-adr.md`.
   - **Not in `docs/THIRD_PARTY_COMPONENTS.md`:** my role file bars me from that file, and the card's `docs/THIRD_PARTY.md` doesn't exist. The lead must paste the entry at merge.
   - **Licences:** I did not re-verify them live, and the entry says so. The integrator's plan file `team/plans/understanding-semantic-index-integration.md` is not in this worktree, so I couldn't cite it.
2. **HANDOFF:** I did not edit `docs/HANDOFF.md` (shared file, the lead writes it from this report). The commit contains the code, tests and ADR text only.
3. **Two new tests in `test_understanding_semantic.py`:**
   - `test_the_centroid_shortlist_keeps_an_intent_whose_centroid_ranks_fifteenth`.
   - `test_the_entity_cache_is_bounded_and_evicts_when_full`.
4. **`LocalEmbedder` timing:** measured on the home PC. It uses `minishlab/potion-multilingual-128M`, the 1418 corpus exemplars and 300 sampled corpus sentences. Model load was a 156 s first-time download, then 22 s.

   | Run | Index build | Mean | p50 | p99 | Max |
   |---|---|---|---|---|---|
   | Plain corpus sentences | 0.18 s | 4.41 ms | 4.15 ms | 8.9 ms | 10.25 ms |
   | Sentences with " bir de" appended | 0.17 s | 4.90 ms | 4.68 ms | 8.41 ms | 9.68 ms |

   The plain-sentence run is optimistic: those sentences are also exemplars, so `LocalEmbedder`'s text cache served them. The suffixed run avoids that and is the number to quote. Mean is under the 5 ms target; p99 is about 8.4 ms, over it.

**Test proof (PROVEN_AUTOMATED)**
- **New tests:** 43 pass across the semantic and combine files (41 before, 2 added). `ruff check` and `ruff format --check` are clean.
- **Shortlist mutation:** `_CENTROID_SHORTLIST` 20 → 1 turns the new shortlist test RED.
- **Cache mutation:** eviction disabled (`if False:`) turns the new eviction test RED.
- **Restore:** `semantic.py` restored from a backup copy. sha256 before and after is `01dc3473f71b8cf79f1109380bc6db67c759b4c97440f944cb290f7e8cf73407`, identical.
- **Earlier mutations** (from the first pass): removing the `min(vector, fuzzy)` bound and the rule candidate's 1.0 were both RED.
- **Owner Utterance Suite:** I did not re-run it. This pass added no production-code change (tests only, mutations restored), and the inspector's run of the same code was 2747 passed.

**NOT_RUN:** mypy (not installed in the worktree).

**Open risks**
- **Shortlist gap:** 11 of 1418 exemplars miss their own intent with shortlist 20. It is disclosed in the ADR; the policy task decides whether to widen it.
- **Wrong device on a bare noun:** "Ofisteki bilgisayarda …" can resolve to device `ev` at about 0.62, and the word "bilgisayar" can take the slot from an alias like "ev bilgisayarı". The policy task must enforce a device confidence threshold above about 0.65.
- **Negation:** "Araştırmayı iptal etme" scores `research_cancel` at 0.86 with the lexical embedder. Semantic quality is claimed only for `LocalEmbedder`.

**For the lead at merge**
- Paste the THIRD_PARTY entry into `docs/THIRD_PARTY_COMPONENTS.md`.
- Number the ADR and move it into `docs/DECISIONS.md`.
- Register `app/voice/understanding` in `protocol_files.py` and the falsification test list if required.
- The policy task must call `configure_default_engine` at start-up with exemplars from the corpus.
