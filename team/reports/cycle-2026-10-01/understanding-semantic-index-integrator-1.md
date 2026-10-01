**Integrator report: understanding-semantic-index**

Plan: `team/plans/understanding-semantic-index-integration.md`

**Choice: ADAPT, no new dependency.** The worker writes `fuzzy.py` as pure-Python Damerau-Levenshtein. Layer 1's closed stripper stays the stemmer.

Candidates:
- **`rapidfuzz` 3.14.x (MIT):** not in `uv.lock`. It would add a compiled wheel to save a few ms we don't need. I rejected it for now. Revisit only if the corpus run goes over 5 ms per sentence and pruning and caching don't fix it.
- **`py-rust-stemmers` 0.1.8 (MIT, Snowball Turkish):** it is in the lock, but only as a `fastembed` transitive. I ran it here and it over-strips:
  - `okuyun` becomes `okuy`.
  - `istediğim` becomes `istedik`.
  - `açsana` becomes `açsa`.
  - `ofisü` is left unchanged.

  It has no stem vocabulary, so it is exactly the ADR-0205 failure shape. Rejected as the stripper.
- **`zeyrek`, `jellyfish`, `model2vec`:** not adopted. `zeyrek` brings a lexicon with a mixed licence, `jellyfish` adds nothing `rapidfuzz` doesn't, and `LocalEmbedder` already covers embedding.

**Licences:** every library I looked at is MIT or BSD. None was added to the tree.

**Footprint:**
- I measured pure-Python OSA at 7.07 ms for 5 tokens against 120 random entities, unpruned and uncached. That is over the 5 ms target, so the plan requires three mitigations:
  - skip the DP when the length difference is more than 3;
  - cache (token, entity) pairs;
  - skip tokens under 3 characters.
- I expect 1–2 ms with them. The worker must measure the real number on the corpus.
- **Not measured:** `LocalEmbedder` timing on the home PC. The worker or inspector has to run it there.
- Memory is about 3 MB for the intent index, which is negligible next to the 0.5 GB model.
- There is no network and no native code, and nothing phones home.
- Device risk: none.

**Risks for the worker:**
- **Layer 1 may not be merged when this starts.** The worker must use a local `fold()` and accept pre-tokenised input. It must not import `normalize.py`.
- **`DeterministicEmbedder` is a lexical n-gram hash, not semantic.** The 'Türkçe oku' acceptance test needs a preference exemplar family, or it passes only through the low-confidence band. The plan says to test the band explicitly.
- **Production code must not import `tests/voice_corpus`.** Exemplars have to arrive as a parameter or a generated file.
- **Calibration:** expected values in the calibration table test must be hand-written, not computed by the function under test.
- **Device slot:** a device entity below the fuzzy floor is dropped, never defaulted.

**For the lead at merge:**
- The card's area names `docs/THIRD_PARTY.md`, which doesn't exist. The record file is `docs/THIRD_PARTY_COMPONENTS.md`, and the entry text is in the plan. I did not edit the tree, so you need to append it.
- No protocol file, dependency or quality-gate change comes from this task.
- `understanding/__init__.py` may collide with the `understanding-normalize` task. Take the union.
- You need to decide how production gets exemplars without importing `tests/`. Either a generated `exemplars.json` built from the corpus, or the policy task supplies them.

**Rollback:** the package is additive and unwired (`resolve_intent` is untouched). Delete the three modules and two tests.
