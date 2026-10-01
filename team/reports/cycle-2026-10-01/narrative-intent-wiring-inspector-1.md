**Inspector report: narrative-intent-wiring** (branch `team/cycle-2026-10-01/worker-narrative-intent-wiring`, sha 07d8fb1e)

**Pass 1: run it**
- I re-ran `test_narrative_intent_wiring.py` with the main checkout's venv. `app.__file__` points into the worktree. Result: **32 passed**.
- I ran the neighbouring suites (`-k "intent or explain or narrative or misroute"`): **896 passed, 0 failed**. `ruff check` is clean.
- My own mutations (the worker's three were not repeated):
  - **M-A:** I changed the router's `query_kind` from "narrative" to "artifacts". Result: **10 failed, RED**.
  - **M-B:** I made the engine's narrative text always empty. Result: **8 failed, RED**.
  - Both files were restored from a backup copy (no `git checkout`). The sha256 after restore matches the worker's values:
    - `intents.py`: `231feaefe0527067fa33ef302b7cdefb95bab39f8935c1ce3cda4eb7b7259ea8`
    - `engine.py`: `07c8e336f06bb65f37fcc62235196ab90d92e48de59be7528a144c2c48ba474f`
  - The tree is clean after the restore.
- The worker's last commit touches only the four files in the area. Files that show up in `git diff main...HEAD` come from earlier base commits, not from this task.
- The full `quality-gate.ps1` was not run. It is not an integration branch, and I ran the fast checks above instead.

**Pass 2: adversarial**
- **Shadowing:** the recogniser sits last in `_resolve_intent_rules`, just before NONE. Ten phrases owned by other families stay with them. That includes "bugün ne yaptın", which still goes to artifact_list, so nothing is shadowed.
- **Contract drift:** `query_kind="narrative"` flows into `_scope_for`, `classify_research_shape` and the realtime session service. Those consumers only compare against known kinds or pass the value through, so the 896 green tests cover them. The other consumers are unproven with this kind.
- **Import cycle:** `engine.py` now imports from `app.voice.intents`, and `intents.py` imports the narrative module locally inside a function. No cycle appeared in any test.
- **Secrets, paths, privacy:** the diff has none. The engine only passes through the text that `tell` returns; the auditor is the existing narrative component.
- **CPX32 load:** the recogniser is a pure local match. There is no extra model call unless a provider is wired.
- **Rollback:** the change is additive, so reverting the commit is enough.

**Findings**
1. **Not live yet.** The worker admits the path is not wired. `LedgerEvidenceSource.narrative` and `explain_to_briefing`'s `narrative_query(...) or classify(...)` live in `app/explain/service.py`, which is outside this area.
   - Until the lead adds both, the router resolves the utterance as EXPLAIN/narrative, but `activity.explain` answers "Bu konuda kayıt bulamadım." I checked that from the code paths and did not run it end to end.
   - This is an accepted "For the lead at merge" item, but it is a merge blocker for real use.
2. **No test through the real tool path.** The tests cover the router function and the engine. None goes through `activity.explain` or the voice tools, so finding 1 is invisible to the suite. A regression test belongs with the service.py wiring.
3. **The `_OWNED` table is brittle.** It is hard-coded, so it breaks if another family later widens what it takes. The worker flagged this, and I accept it as minor.
4. **Nothing ran against real systems.** Real Postgres, a live voice turn and a real Haiku call were not run.

**Evidence classes**
- The router function against the sqlite ledger with a fake provider is **PROVEN_AUTOMATED**.
- The live voice path is **NOT_RUN** and will not work until service.py is wired.
- The owner saying "bu hafta ne oldu" is **READY_FOR_OWNER**, after the service.py wiring.

**Verdict:** APPROVE. I approve the four in-area files as delivered. Merging is conditional on the lead doing the two service.py edits and adding a test that goes through `explain_to_briefing` with a `narrative` source.
