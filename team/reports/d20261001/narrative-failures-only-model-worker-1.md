## Report: narrative-failures-only-model (cycle d20261001)

**Şu an üzerinde çalışılan:** `narrative-failures-only-model`; area `services/api/app/narrative/{facts,service}.py`, `app/explain/service.py`, two new unit test files, the ADR draft; machine: owner's dev PC, in this worktree.

**Outcome:** everything on the card is built, committed and pushed, except one acceptance point that cannot be met without changing the router. 'ne başarısız oldu' with Turkish letters still does not reach the narrative.

- **sha:** `4d92b694fe9e6275a1b948f99802960bf06c74d1` (pushed, worktree clean)
- **Files:** 6, all inside the area (3 source, 2 new tests, `team/plans/narrative-failures-only-model-adr.md`).

**What was built**
- `only_failures(facts)`: pure narrowing as the card specifies; period and device kept.
- `tell(..., failures_only=False)`: narrows before narrating. No failures returns `NO_FAILURES_TEXT` ("Bu dönemde başarısız iş yok.") and no narrator is called.
- `LedgerEvidenceSource(db, chat_provider=None)`: a provider that is given and `configured` narrates through `ModelNarrator`; otherwise the rule narrator.
- `evidence_source_factory` is now a function that builds the provider with `build_chat_provider(get_settings())` and never raises.
- `query_for`, the router, the intent, the collector, the auditor and both narrators are untouched.

**Not met: the card's end-to-end sentence**
- The router gives 'ne başarısız oldu' and every Turkish-letter variant I probed to the explain `failures` family, so `query_for` never makes it a narrative. `test_narrative_intent_wiring._OWNED` pins that ownership.
- Only the ASCII spelling ("ne basarisiz oldu") reaches the narrative with `failures_only=True` today.
- So the owner's spoken question behaves as before this change. Fixing it is a router decision, outside this area.

**Tests (PROVEN_AUTOMATED, fake provider, sqlite)**
- `test_narrative_failures_only.py` (14) and `test_explain_narrative_model.py` (14): 28 passed.
- RED first: collection `ImportError` on `only_failures`; then 13 explain tests failed, with the completed work listed ("Tamamlananlar: … Toplam 5 kayıt" where "Toplam 2 kayıt" was expected).
- End to end through `explain_to_briefing` with the real router and real factory: "bu hafta ne oldu" (model draft, failures put back) and "bu hafta ne basarisiz oldu" (failures only).
- The card's sentence 'ne başarısız oldu' is tested with only the router's decision substituted; everything after it is the real path.
- Existing narrative, explain, wiring, ledger-device and voice-explain-tool suites: 319 passed.
- Owner utterance corpus and corpus regressions: 2793 passed, none failed, in 24 min 17 s. My 1500 s `timeout` wrapper then returned 124 after the summary line, so the process exit code is not a clean 0.
- ruff check and format: clean.

**Mutation RED** (backup copy, sha256 before = after for all ten, on the committed bytes)

| Mutation | Result |
|---|---|
| `only_failures` keeps completed | 3 failed |
| `narrative()` ignores the provider | 7 failed |
| `tell` ignores the flag | 7 failed |
| source drops the flag | 6 failed |
| no constant sentence | 4 failed |
| `configured` ignored | 1 failed |
| factory builds no provider | 5 failed |
| total counts all rows | 5 failed |
| factory raises | 1 failed |
| device counts of all rows | 1 failed |

**NOT_RUN**
- Real Haiku over real PostgreSQL.
- PROVEN_REAL: the owner asking by voice in production.
- The full unit suite; only the files above were run.

**Open risks**
1. With a key configured (production), "bu hafta ne oldu" now makes one blocking Haiku request inside the voice tool, up to `assistant_chat_timeout_s` (20 s) plus one retry.
2. The factory reads the process-wide `get_settings()`, not the voice tool's `ctx.live` settings. A shell with `PAGENTOS_ANTHROPIC_API_KEY` set would make the corpus's "Bu hafta ne oldu?" case call the real API. No key is set on this machine. Passing the provider or `source=` from `tools.py` would fix it, but that file is outside the area.
3. The auditor does not reject a completed subsystem's name in a failures-only draft, only its counts. The model is never shown completed rows.

**ADR draft:** `team/plans/narrative-failures-only-model-adr.md` (unnumbered; includes the router limit and both risks).
