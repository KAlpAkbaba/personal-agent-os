**Report: narrative-intent-wiring — verdict: DONE for the four in-area files, but it is not live until the lead makes two edits in `app/explain/service.py`.**

The last report ended without a verdict. The inspector's was APPROVE. This is a re-verification with no new code.

**Branch and sha**
- Sha: `07d8fb1e02b4202516d388d96b77504eda74ffc1`. It is pushed and the remote head matches.
- The worktree is clean.
- The commit changes 4 files, all inside the area:
  - `services/api/app/voice/intents.py`
  - `services/api/app/explain/engine.py`
  - `services/api/tests/unit/test_narrative_intent_wiring.py`
  - `team/plans/narrative-intent-wiring-adr.md`

**Re-run just now** (main checkout's venv, `app.__file__` points into the worktree): PROVEN_AUTOMATED
- `test_narrative_intent_wiring.py`: 32 passed.
- `ruff check` on the three code files: clean.
- Current sha256 values match the pre-restore values from the earlier mutation runs:
  - `intents.py`: `231feaefe0527067fa33ef302b7cdefb95bab39f8935c1ce3cda4eb7b7259ea8`
  - `engine.py`: `07c8e336f06bb65f37fcc62235196ab90d92e48de59be7528a144c2c48ba474f`

**Earlier evidence, carried forward and not re-run**
- The red-first output was kept in the original run.
- The router tests go through the real router function with a fake db and provider:
  - "bu hafta ne oldu" answers with the narrative.
  - "hafta sonu ne yapalım" does not.
  - At least five phrases owned by other intents stay where they were.
  - The answer names every failure in the fixed ledger fixture.
- The worker's mutations were RED, with the files restored by sha256:
  - The recogniser call removed.
  - The narrative placed before an existing intent it would shadow.
  - A third mutation, for the engine rule.
- The inspector's own two mutations (M-A on the router `query_kind`, M-B on the engine text) were also RED and restored to the same sha256 values.
- Neighbouring suites (`-k "intent or explain or narrative or misroute"`): 896 passed (inspector run).

**For the lead at merge** (outside the area, merge blocker for real use)
1. In `app/explain/service.py`, add `LedgerEvidenceSource.narrative`. It should call `app.narrative.service.tell(db, period, device, narrator)`, with `ModelNarrator` over the router's chat provider and `RuleNarrator` when there is none.
2. In the same file, make `explain_to_briefing` call `narrative_query(...) or classify(...)`.
3. Add a test that goes through `explain_to_briefing` with a `narrative` source. The current suite cannot see the gap in 1 and 2, because it stops at the router function and the engine.
4. Until 1 and 2 land, the router resolves the utterance as EXPLAIN/narrative but `activity.explain` answers "Bu konuda kayıt bulamadım."
5. Number the ADR text in `team/plans/narrative-intent-wiring-adr.md` and move it into `docs/DECISIONS.md`.

**Evidence classes**
- Router and engine against the sqlite ledger with a fake provider: PROVEN_AUTOMATED.
- Live voice path, real Postgres, a real Haiku call: NOT_RUN.
- The owner saying "bu hafta ne oldu": READY_FOR_OWNER, after the `service.py` edits.

**Open risks**
- The `_OWNED` table in the tests is hard-coded. It breaks if another family later widens what it takes.
- Other consumers of `query_kind="narrative"` (`_scope_for`, `classify_research_shape`, the realtime session service) are covered only by the green neighbouring suites, not by a test that uses this kind.
