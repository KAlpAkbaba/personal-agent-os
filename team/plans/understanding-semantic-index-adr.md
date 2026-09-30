## ADR (lead numbers it) — Layer 2 of ADR-0224: intent and entity indexes, fuzzy bound, combiner

**Status.** Accepted (worker, cycle-2026-10-01). Additive, unwired: `resolve_intent` is untouched.

**Decision.**
- `app/voice/understanding/fuzzy.py`: local Turkish `fold` (I/İ before lower, then diacritics) and an
  optimal-string-alignment Damerau-Levenshtein `similarity` in [0,1], pure Python, cached, and 0.0
  when the lengths differ by more than 3. No dependency (integrator: rapidfuzz and
  py-rust-stemmers rejected; THIRD_PARTY record is "considered and rejected").
- `semantic.py`: `IntentIndex` (exemplars passed in; production never imports `tests/`;
  `exemplars_from_cases` builds them from the corpus on the test/ops side; a built-in
  `preference` family so a standing preference is not bent into an action) and `EntityIndex`
  (allow-list apps with Turkish names, device aliases passed by the caller, vocabulary
  synonyms). Entity confidence = `min(vector, fuzzy)`; a span under fuzzy 0.6 is dropped and a
  device is never defaulted. Intent confidence = cosine x margin factor (0.6 with no lead to 1.0
  at a lead of 0.10). The entity index is cached by a hash of (aliases, vocabulary) and rebuilt
  when they change. Intent lookup is two-stage (centroid shortlist of 20 intents, then exact best
  exemplar) to stay pure Python and under 5 ms a sentence.
- `combine.py`: rule result is candidate #1 (exact 1.0, suffix dropped 0.9, confusion 0.75; any
  other kind is a ValueError); a semantic candidate for the rule's own intent is folded into it
  (rule slots win, semantic fills gaps); ranking is by confidence, the rule first among equals.
  `understand()` needs a configured engine (`configure_default_engine`) or an `engine=`;
  it refuses rather than silently falling back to a different embedder.

**Consequences.** The shortlist can miss the own intent of a sentence (measured in the report);
the policy task decides whether to widen it. `DeterministicEmbedder` is lexical, so semantic
quality is only claimed for `LocalEmbedder` where measured.

## THIRD_PARTY record (lead pastes into `docs/THIRD_PARTY_COMPONENTS.md` at merge; the worker may not edit that file)

### rapidfuzz, py-rust-stemmers — considered and rejected (ADR-0224 layer 2)

Role considered: Turkish-aware fuzzy matching (rapidfuzz, MIT, C++ Damerau-Levenshtein) and suffix
stemming (py-rust-stemmers, Snowball Turkish). Neither is in `services/api/uv.lock`.
Rejected for now, default "no new dependency":
- rapidfuzz adds a native wheel to every device/cloud image for a 12-line optimal-string-alignment
  routine that already meets the budget (mean 3.5 ms a sentence with the lexical embedder, 4.9 ms
  with LocalEmbedder, most of it in the intent pass, not in fuzzy); Turkish folding (I/İ, ü/u) is ours
  either way and rapidfuzz does not do it.
- A Turkish stemmer over-stems (`unutma` = "remember" stems to `unut` = "forget" — the recorded
  hard-delete trap) and layer 1 already has a closed, reviewed suffix stripper.
Revisit only if the measured p99 of `understand()` breaks the budget; licence/security status to
be re-verified at that time.
