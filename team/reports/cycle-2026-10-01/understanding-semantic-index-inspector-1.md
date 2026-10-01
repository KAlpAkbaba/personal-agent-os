**Inspector report: understanding-semantic-index (ADR-0224 layer 2)**

**Verdict: RETURN.** The code is sound. Two gaps need closing: the THIRD_PARTY record was never written, and nothing is committed.

**Pass 1, run it**
- **New tests:** 41 passed in 2.24s from a clean `uv run --frozen`. ruff check and format are clean. mypy isn't installed in the worktree, so types are NOT_RUN.
- **Owner Utterance Suite:** 2747 passed in 10m24s (PROVEN_AUTOMATED).
- **Acceptance cases:** the Ofisü/calc/ofis case with `ofisu~ofis` evidence, the language-preference case (preference ranks first), exact rule = 1.0 ranking first, and index rebuild on alias change are all covered by passing tests.
- **My mutations** (sha256 verified before and after, restored from a backup copy):
  - RED: `FUZZY_FLOOR` = 0.0, margin factor removed, no sort, confusion confidence 0.75 → 0.95, constant cache key.
  - **Survived:** `_CENTROID_SHORTLIST` 20 → 1, and cache eviction at 8 entries disabled. Neither is pinned by a test.
- **Timing:** I measured `understand()` over 2665 corpus sentences with `DeterministicEmbedder`: mean 3.48 ms, p99 6.77 ms, max 18.6 ms. That meets the < 5 ms target on average but not at p99. Index build took 0.06 s for 1418 exemplars. The `LocalEmbedder` timing is **NOT_RUN** here; the card asks for it from the home PC.

**Pass 2, break it**
1. **Missing THIRD_PARTY record.** The card required one "either way". `docs/THIRD_PARTY_COMPONENTS.md` has no entry for rapidfuzz or py-rust-stemmers. The card names `docs/THIRD_PARTY.md`, which doesn't exist, so the worker likely skipped the file. The rejection reasoning lives only in `team/plans/understanding-semantic-index-integration.md` and a `fuzzy.py` docstring. The worker must add a "considered and rejected" entry to `THIRD_PARTY_COMPONENTS.md`.
2. **Nothing is committed.** `git status` shows the package, both test files and the ADR as untracked, and the branch has no commit of its own. The worker must commit, updating the HANDOFF section in the same commit.
3. **Shortlist gap.** The centroid shortlist lets a sentence miss its own exemplar: 11 of 1418 with shortlist 20, 3 with shortlist 1000. The worker's ADR discloses this. A test should pin the shortlist or the exact-exemplar behaviour.
4. **Wrong-device risk.** The bare word "bilgisayar" takes the device slot from an alias like "ev bilgisayarı".
   - "Ofisteki bilgisayarda Not Defteri'ni aç" resolves to device **ev** at 0.62, because "ofisteki" is 4 characters longer than "ofis" and exceeds the length gap.
   - Confidence is honestly low, so it's safe only if the policy task enforces a device threshold above about 0.65. That threshold belongs on the policy task's card.
5. **High-confidence semantic misses** with the lexical embedder: "Araştırmayı iptal etme" (no intent expected) → `research_cancel` 0.86. A negation fools it. This is expected for a lexical embedder, and the ADR already says semantic quality is claimed only for `LocalEmbedder`.
6. **Contracts and files.** No edits outside the area. No secrets or paths in code. Nothing is wired into `resolve_intent`. The memory footprint is small (vectors as Python lists).

**Evidence classes**
- PROVEN_AUTOMATED: unit tests, mutations, Owner Utterance Suite, and `DeterministicEmbedder` timing.
- NOT_RUN: `LocalEmbedder` timing and mypy.

**For the lead at merge**
- Register `app/voice/understanding` in `protocol_files.py` / the falsification test list if required.
- Call `configure_default_engine` at start-up in the policy task, with exemplars from the corpus.

RETURN (1) add the THIRD_PARTY_COMPONENTS.md "considered and rejected" entry; (2) commit with the HANDOFF update; (3) add a test that fails on shortlist 1 and one for cache eviction (optional); (4) send the `LocalEmbedder` timing from the home PC.
