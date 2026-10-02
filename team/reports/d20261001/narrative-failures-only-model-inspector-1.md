**Inspector report: narrative-failures-only-model @ `4d92b694`**

The code in the area is correct and proven, but the owner's actual sentence never reaches it, so the card's end-to-end acceptance line is not met.

**Pass 1: run it**
- **New tests:** `test_narrative_failures_only.py` and `test_explain_narrative_model.py` give 28 passed, matching the report.
- **Existing suites:** the 15 narrative / explain / office01 / ledger-device / voice-explain files in `tests/unit` give 295 passed. The worker's 319 used a wider selection I did not reproduce.
- **Lint:** `ruff check` is clean. `ruff format --check` flags only `app/explain/research_context.py`, which is not in the diff.
- **Area:** the worker's commit touches exactly the six area files; the tree was clean before and after my run.
- **My mutations** (different from the worker's ten; backup copy, sha256 equal before and after each):

| Mutation | Result |
|---|---|
| `only_failures` keeps the subsystem counts of all rows | 1 failed |
| constant sentence returned for every ask, not just failures-only | 1 failed |
| `narrative()` passes `failures_only=True` always | 5 failed |
| factory catches only `ValueError` | 1 failed |

- **Real PostgreSQL (dev stack, PG 16.15, schema `0063_team_state`, rolled back, 208 ledger rows before and after):**
  - "bu hafta": 194 rows, 14 failed, 180 completed. Failures-only text has total 14, no "Tamamlananlar", passes the auditor (933 characters).
  - "dün": 0 rows gives the constant sentence.
  - `explain_to_briefing("bu hafta ne basarisiz oldu")` through the real router and factory with a fake provider whose draft drops everything: one provider call, all 14 failures put back.
  - This is PROVEN_PROXY for the database half. No integration test was added, and the diff touches no table, migration or store.
- **Real provider seam, closed port** (key set, base URL `127.0.0.1:9`): the transport error falls back to the rule text in about 2.5 s, for both normal and failures-only.
- **Not run by me:** the full unit suite, the 24-minute utterance corpus (the worker's run exited 124 after the summary line), and `quality-gate.ps1` (not on the integration branch).
- **NOT_RUN:** real Haiku; the owner's voice in production.

**Pass 2: break it**
1. **Acceptance unmet.** The real router sends every Turkish-letter spelling to the explain `failures` family, not the narrative. I probed "ne başarısız oldu", "bu hafta ne başarısız oldu", "bugün …", "ofiste bu hafta …" and "neler …". Only the ASCII "ne basarisiz oldu" reaches `failures_only=True`. The test for the card's sentence monkeypatches `resolve_intent`. The worker reported this honestly, but the title feature does not exist for the owner and the card's PROVEN_REAL step cannot pass after this merge.
2. **Production behaviour change the title does not name.** With the key set (the prod compose passes it), "bu hafta ne oldu" now sends the failure summaries to Haiku on every ask. It blocks the tool thread, not the event loop (tools run under `asyncio.to_thread`), for up to 20 s plus one retry.
3. **Test-seam drift.** The factory reads the process-wide `get_settings()`, while `tools_assistant.py:171` uses `ctx.live["chat_provider"]` / `ctx.live["settings"]`. A shell or `.env` with `PAGENTOS_ANTHROPIC_API_KEY` makes the corpus and voice-explain tests call the real API. Nothing scrubs the key in `conftest`. There is no key on this machine today, so it is latent.
4. **Provider built on every explain query,** not only narratives. It is cheap (no client is created, nothing is sent), so acceptable.
5. **Model-or-rule not recorded.** `tell()` returns only the text, so which narrator spoke, the fallback reason and the token count are dropped; only a warning log on failure remains.
6. **Minor wording.** "Toplam 2 kayıt" in a failures-only answer reads as the period's total (the card specified it), and the constant sentence does not name the device that was asked about.
7. **Clean on the rest.** No secrets or paths in code, no contract or schema drift, and rollback is the two-argument revert the ADR draft names.

**Evidence classes**
- `only_failures`, `tell(failures_only)`, source and factory: PROVEN_AUTOMATED.
- The same over PostgreSQL: PROVEN_PROXY.
- 'ne başarısız oldu' (Turkish letters) end to end: NOT_RUN, unreachable through the real router.
- Real Haiku: NOT_RUN.
- Owner's voice: READY_FOR_OWNER only after item 1 is resolved.

**For the lead:** both return items sit outside the card's area (router, `tools.py`), so the worker cannot fix them without a widened card. If you merge as is, the roadmap row and release note must not say that 'ne başarısız oldu' tells the failures only.

RETURN (1: the owner's sentence 'ne başarısız oldu' does not reach the narrative through the real router, so the acceptance e2e is met only with the router substituted — re-card with the router/`_OWNED` decision in the area, or merge under a title that does not claim it; 2: take the chat provider from the voice tool's `ctx.live` (`tools.py` → `source=`/provider) or scrub the key in the test conftest, so a keyed shell cannot make the corpus call the real API)
