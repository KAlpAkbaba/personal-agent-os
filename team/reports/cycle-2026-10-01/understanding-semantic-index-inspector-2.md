**Inspector report: understanding-semantic-index (branch `team/cycle-2026-10-01/worker-understanding-semantic-index`)**

**Pass 1 — run it**
- **Claimed tests:** `test_understanding_semantic.py` and `test_understanding_combine.py` give 43 passed in 1.98 s. `ruff check` is clean. mypy was not run (not installed).
- **Owner Utterance Suite:** 2747 passed (656 s), run by me on this branch. The worker had not re-run it.
- **My mutations, different from the worker's:**
  - `min(vector, sim)` → `max(vector, sim)` in `semantic.py`.
  - `MATCH_CONFUSION: 0.75` → `1.0` in `combine.py`.
  - Result: 4 failed, 39 passed. The failures were `test_fuzzy_bounds_a_lexically_far_entity…`, `test_entity_confidence_is_the_minimum…`, `test_rule_candidate_confidence_by_match_kind[confusion-0.75]` and `test_ranking_is_by_confidence_and_the_rule_wins_a_tie`.
  - Restored from a backup copy. sha256 before and after is identical: `semantic.py` `01dc3473…cf73407`, `combine.py` `637a6697…7dfd7a9e`. The tree is clean.
- **Acceptance sentences (`DeterministicEmbedder`, my own probe):**
  - "Ofisü bilgisayarında hesap makinesini açın" → `app_open` 0.79, app `calc`, device `ofis`. Evidence is `ofisu~ofis fuzzy 0.80 vector 0.55 -> 0.55`.
  - The evidence shows the folded form `ofisu`, not `ofisü` as the card writes it. Cosmetic.
  - The preference sentence, the exact rule keeping 1.0, the far-entity bound and the alias rebuild are all covered by passing tests.

**Timing, measured by me with `DeterministicEmbedder`**
- Sentences that are also exemplars: 3.6 ms per sentence.
- Novel sentences (corpus plus " bir de", 600 sentences): **34.5 ms per sentence**, about 7× over the 5 ms target.
- The worker reported only the `LocalEmbedder` numbers (mean 4.9 ms, p99 8.4 ms), not the `DeterministicEmbedder` figure the card asked for.
- That is an unmet target, not a failure of the card, since timing is "measured, not asserted". The lead should know it. The in-repo timing test only guards against a hang (< 500 ms).

**Pass 2 — break it**
- **Wrong device on a bare noun:** "Bilgisayarda hesap makinesini aç", with no device named, gets device `ev` at 0.62. "Evdeki bilgisayarda …" also gets `ev` from the word "bilgisayarda", which fuzzy-matches the alias "ev bilgisayarı" at 0.64. The worker disclosed this. Nothing consumes it yet because the code is unwired. The policy task must enforce a device threshold above 0.65.
- **Negation:** "Araştırmayı iptal etme" scores `research_cancel` at 0.86 under the lexical embedder. Disclosed, and the semantic claim is limited to `LocalEmbedder`.
- **Shortlist gap:** 11 of 1418 exemplars miss their own intent at shortlist 20. Disclosed in the ADR, and tests were added.
- **Contract drift:** no change to BROWSER_CAPABILITIES, DEVICE_PROTOCOL or the API schemas. The diff against main also carries lead/cycle files (`cycle.ps1`, `queue.json` and similar), which are the lead's own commits. The worker's files stay inside the area.
- **Secrets and hard-coded paths:** none. Device aliases are passed in by the caller, never hard-coded.
- **Privacy (KVKK):** `Candidate.evidence` carries the owner's raw words (`heard~surface`, the exemplar text). The policy task must not persist it unredacted into audit or logs.
- **Dependencies:** none added. The rapidfuzz and py-rust-stemmers rejection record is in the worker's ADR file. The worker did not re-verify the licences, and the integrator's plan file is absent from this worktree.
- **Rollback:** the module is unwired, so reverting the commit is enough.

**Evidence classes**
- Rule candidate confidences, the `min(vector, fuzzy)` bound and ranking: PROVEN_AUTOMATED.
- `LocalEmbedder` timing: PROVEN_PROXY, a worker measurement I did not reproduce, and `LocalEmbedder` isn't installed here.
- Real-device behaviour: NOT_RUN.

**For the lead at merge**
- Paste the THIRD_PARTY entry into `docs/THIRD_PARTY_COMPONENTS.md`.
- Number the ADR and move it into `docs/DECISIONS.md`.
- Register `app/voice/understanding` in `protocol_files.py` and the falsification list if required.
- Carry two constraints into the policy task: a device threshold above about 0.65, and no raw evidence in logs.
- Record the 34.5 ms `DeterministicEmbedder` figure for novel sentences.

APPROVE
