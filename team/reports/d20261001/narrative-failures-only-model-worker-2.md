## Şu an üzerinde çalışılan
- Task `narrative-failures-only-model` (return pass, cycle d20261001), area `services/api/app/explain/service.py` + its two test files + the ADR draft, on the owner's dev PC, in the worktree `worker-narrative-failures-only-model`.
- Return item 2 is fixed inside the area. Return item 1 is **not** fixed: it cannot be done inside this card's area.

## Report
- **sha:** `b362c653ed21105bcaf9cbdd9efe7ab7590f1f11`, pushed, on top of `4d92b694`; tree clean.
- **Files:** 3 changed in this pass (`app/explain/service.py`, `tests/unit/test_explain_narrative_model.py`, the ADR draft); all inside the area. The branch still touches exactly the six area files.

**Return item 2 (a keyed shell must not reach the real API) — fixed, PROVEN_AUTOMATED**
- The factory no longer reads the process-wide `get_settings()`; that import is gone from `explain/service.py`.
- New `narrative_chat_provider(live)` takes the session's provider the way `tools_assistant.py:171` does. It returns `None` when the session carries no usable key and never raises.
- `explain_to_briefing(..., chat_provider=None)` and `evidence_source_factory(db, chat_provider=None)`: only the caller hands a provider over. One-argument test factories (`lambda db: source`) still work.
- **RED → GREEN:** 18 failed / 9 passed before the change, 41 passed after. The two keyed-process tests failed because the model draft was spoken (the recorder transport had been reached), not for a signature reason. They put `PAGENTOS_ANTHROPIC_API_KEY` really in the environment and replace the transport with a recorder.
- **Mutations:** 11, each restored from a backup copy with sha256 equal before and after; all RED.
  - The card's two: `only_failures` keeping completed → 8 failed; `narrative()` ignoring the provider → 8 failed.
  - Process-settings fallback in the seam → 3 failed; in the factory → 2 failed.
  - The other seven (unconfigured provider returned, live provider ignored, no catch, provider dropped, keyword always passed, `failures_only` ignored, `tell()` not narrowing) → 1 to 13 failed each.
- My first mutation runner applied nothing (a stdin redirect swallowed the mutator) and showed 41 passed; its own applied/UNCHANGED check caught it, and I discarded that run.

**Return item 1 (the owner's sentence through the real router) — NOT done**
- The card freezes `query_for` and the router, and the `_OWNED` decision lives in `app/voice/intents` and `test_narrative_intent_wiring`, both outside the area.
- Measured through the real router: "ne başarısız oldu", "bu hafta …", "bugün …" and "neler …" all go to the explain `failures` family. It speaks only the latest failure (1 of 2) and lists no completed work.
- Only the ASCII "ne basarisiz oldu" reaches the narrative with `failures_only=True`.
- Tests no longer claim otherwise: the router-substituted test's docstring now opens with "ROUTER SUBSTITUTED". A new real-router test over the four owner spellings asserts only what holds on either side of the decision.
- The merge title must not say "'ne başarısız oldu' tells the failures only"; the ADR draft proposes a title.

**Consequence the lead must know**
- `tools.py::activity_explain` does not pass the provider yet (outside the area), so production keeps the rule narrator after this merge and sends no Haiku request.
- The one line to wire it: `chat_provider=narrative_chat_provider(ctx.live)`. That line is what introduces the per-ask Haiku call the inspector flagged, so its card title should name it.
- This departs from the card's build item 3 (factory builds from settings). I chose it because any process-wide fallback reopens the leak. It is in the ADR draft, sections 4 and B.

**Checks run**
- Area tests: 41 passed.
- Narrative, wiring, voice-explain and neighbouring suites (15 files): 264 passed.
- Utterance corpus (`test_owner_utterance_corpus.py` + `test_voice_corpus_regressions.py`): 2793 passed in 26:41, exit 0.
- `ruff check` and `ruff format --check` on the area: clean.

**NOT_RUN**
- Full unit suite, `quality-gate.ps1`.
- Real PostgreSQL in this pass (the inspector's PROVEN_PROXY run predates this commit; no table or query changed).
- Real Haiku.
- The owner's voice in production — not READY_FOR_OWNER until item 1 is decided.

**Open risks**
- Which narrator spoke (model or rule, fallback reason, tokens) is still not recorded.
- "Toplam 2 kayıt" and the device-less constant sentence are unchanged, as the card specified.
