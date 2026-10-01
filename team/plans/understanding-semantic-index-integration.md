# understanding-semantic-index — integration plan (integrator, cycle-2026-10-01)

## Decision: ADAPT, no new dependency

Question (ADR-0224 layer 2): does a licence-clean Turkish-aware fuzzy/stemming library beat a
hand-written Damerau-Levenshtein and the closed stripper of layer 1?

| Candidate | Licence | In lock? | Verdict |
|---|---|---|---|
| `rapidfuzz` 3.14.x (maxbachmann) | MIT | NO | Fast C++ wheels, healthy. Reject for now: a new compiled dependency to save a few ms we do not need (measurement below). Revisit only if the corpus run shows understand() over budget AND pruning/caching cannot fix it. It does not fold Turkish I/İ anyway - we fold first either way. |
| `py-rust-stemmers` 0.1.8 (Snowball, `turkish`) | MIT | YES, transitive of `fastembed` only | Reject as the stripper. Measured on this host: `açsana->açsa`, `okuyun->okuy`, `istediğim->istedik`, `ofisü->ofisü` (no fix), `makinesini->makine`. It is a heuristic stemmer with no stem vocabulary: exactly the ADR-0205 failure (`istediğim` must never become `iş`/a wrong stem) and it cannot tell a verb from a noun. Layer 1's closed, table-driven stripper is strictly safer. Also not a declared direct dependency; importing it would make a fastembed internal load-bearing. |
| `zeyrek` / `snowballstemmer` (py) / `jellyfish` | MIT / BSD / MIT | no | Not evaluated further: zeyrek pulls a morphology lexicon and is GPL-adjacent data (Zemberek port, licence mix); jellyfish adds nothing rapidfuzz does not. |
| `model2vec` | MIT | no | Not needed: `LocalEmbedder` (fastembed + potion) already serves the `Embedder` seam. |

Conclusion: fuzzy.py = pure-Python optimal-string-alignment Damerau-Levenshtein
(adjacent transposition), over the folded form, `similarity = 1 - dist/max(len)`.
Zero dependency; THIRD_PARTY entry is a "considered and rejected" record.

## Footprint (measured, this host, pure Python OSA)
- Unpruned: 7.07 ms per 5-token sentence against 120 random entities (worst case).
- Required worker measures to stay < 5 ms/sentence: (1) skip the DP when `abs(len a - len b) > 3`
  (similarity cannot reach the floor), (2) `functools.lru_cache` on (token, entity) pairs,
  (3) skip stopwords / tokens < 3 chars for the entity pass. Expected 1-2 ms. Worker reports the
  real number on the corpus; LocalEmbedder timing is measured on the home PC (not here).
- Memory: intent index = 2745 exemplars x 256 floats = ~2.8 MB as Python lists; store as a flat
  `array('f')`/tuple rows, or one exemplar per corpus *case* (the task says one per case) - keep
  it a list of (intent, text, vector). Entity index: a few hundred x 256. Negligible vs the 0.5 GB model.
- Network: none. Nothing phones home. Device risk: none (pure compute).

## Seams
- `app.memory.embedding.Embedder` (protocol: `embed(text)->list[float]` L2-normalised, so cosine =
  dot). `LocalEmbedder` in `app.memory.providers` (prod), `DeterministicEmbedder` (tests).
  Embed ONLY at index build/reload; per-sentence cost = one `embed()` of the sentence plus one per
  entity token candidate (cache token vectors).
- Input from layer 1 (`understanding-normalize`, same cycle): normalised tokens + lemma/dropped suffix
  trail. **Risk:** that task may not be merged when this worker starts. Worker must depend only on a
  tiny local `fold()` (Turkish I/İ casefold + diacritic fold) inside fuzzy.py and accept pre-tokenised
  input in `understand(text, *, device_aliases, rule_result)`; never import `normalize.py`.
- `rule_result`: a duck-typed object with `.intent`, `.entities`, and a `.match_kind` in
  {"exact","suffix_dropped","confusion"} -> confidence 1.0 / 0.9 / 0.75. Define the small Protocol in combine.py.

## Files to touch (worker area, unchanged)
`app/voice/understanding/{__init__,semantic,fuzzy,combine}.py` (new package dir - check whether
normalize task created `__init__.py`; merge trivially), `tests/unit/test_understanding_semantic.py`,
`tests/unit/test_understanding_combine.py`. Exemplars: import `tests/voice_corpus/corpus.py` only from a
builder function taking the case list as a parameter (production code must not import from `tests/`;
the corpus seeding is a test-side/ops-side call, or a generated `exemplars` JSON the lead decides - see lead notes).

## Tests to add / pitfalls for the worker
- Acceptance tests as written in the card, all on DeterministicEmbedder. NOTE the Deterministic embedder is
  lexical n-gram, so 'Bundan sonra ... Türkçe oku' must be won by a preference/none exemplar that shares
  n-grams; the worker must add a `preference` exemplar family (`kalıcı tercih`) or the assertion will pass
  only via the low-confidence branch - both are allowed by the card, test the confidence band explicitly.
- Calibration: confidence = cosine x margin factor; define it as a pure function with its own table test
  (memory: "both sides from one source" - expected values hand-written, not computed by the function).
- `min(vector, fuzzy)` bound and the rule 1.0: mutation RED with sha256, restore from backup copy.
- Index reload: key the cache by a hash of (sorted aliases, vocabulary); assert rebuild count changes.
- Never a device guess: a device entity below the fuzzy floor (suggest 0.6) is dropped, not defaulted.
- Turkish pitfalls (memory): fold `I/İ` before lower; do not prefix-match suffixes (`unutma`).

## Rollback
Package is additive and unwired (`resolve_intent` untouched). Delete the three modules + two tests.

## THIRD_PARTY entry text (for docs/THIRD_PARTY_COMPONENTS.md; the file named in the card,
`docs/THIRD_PARTY.md`, does not exist - the record file is `THIRD_PARTY_COMPONENTS.md`)

```
## Voice understanding: fuzzy string and Turkish stemming (ADR-0224 layer 2, 2026-10-01)

Role: lexical bound on entity matches ("ofisü" ~ "ofis") and suffix handling.
Decision: NO new dependency. Hand-written optimal-string-alignment Damerau-Levenshtein
(pure Python, length-pruned, cached) in `app/voice/understanding/fuzzy.py`.
Considered and rejected:
- `rapidfuzz` (MIT) - fast, but a new compiled dependency for a ~7 ms worst case the pruned
  pure-Python version avoids; revisit if the corpus run exceeds the 5 ms/sentence target.
- `py-rust-stemmers` (MIT, Snowball Turkish; already in the lock as a fastembed transitive) -
  measured to over-strip ("okuyun"->"okuy", "istediğim"->"istedik", "açsana"->"açsa") and has no
  stem vocabulary; the closed table-driven stripper of layer 1 is the ADR-0205-safe choice.
Nothing phones home; no native code added.
```

## For the lead at merge
- Lead appends the THIRD_PARTY entry above (integrator does not edit the tree).
- No protocol file, no dependency, no quality-gate change from this task; `understanding/__init__.py`
  may collide with `understanding-normalize` - take the union.
- Decide how production gets exemplars without importing `tests/`: the threshold-policy task or a
  generated `app/voice/understanding/exemplars.json` built from the corpus by a script.
