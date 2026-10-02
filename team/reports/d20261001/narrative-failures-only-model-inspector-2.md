# Inspector report — narrative-failures-only-model @ `b362c653` (second pass)

The code in the diff is correct and safe, but the card's acceptance is not met: the owner's sentence "ne başarısız oldu" never reaches the narrative through the real router. This is the same item I returned last time, and it cannot be fixed inside the card's area.

**Pass 1 — re-run (worktree clean before and after)**
- **Area tests:** 41 passed.
- **Neighbouring suites:** 333 passed across the narrative, explain, ledger, assistant-chat and voice-explain files; 422 passed across the 18 unit files that reference the explain service or `app.narrative`.
- **Lint:** `ruff check` and `ruff format --check` are clean on the changed files.
- **Merge:** `git merge-tree main HEAD` is clean against main `ca5cc795`; tests were run on the branch, not on the merged tree.
- **My mutations:** five, each different from the worker's, restored from a backup copy with sha256 equal; all RED.
  - `only_failures` keeping the period's total → 8 failed.
  - `tell()` without the no-failure constant → 4 failed.
  - `narrative()` not checking `configured` → 1 failed.
  - `explain_to_briefing` not handing the provider on → 3 failed.
  - `only_failures` keeping the whole period's device counts → 1 failed.
- **Keyed shell (return item 2):** fixed, PROVEN_AUTOMATED. With a fake `PAGENTOS_ANTHROPIC_API_KEY` in the environment and a transport spy, 204 tests across seven voice/explain/narrative suites passed with 0 requests sent.
- **Real PostgreSQL:** PROVEN_PROXY on the dev stack (`pagentos-postgres`, fake provider, 2 failed + 3 completed rows).
  - Rule text names both failures, "Toplam 2 kayıt", no completed subsystem.
  - A draft that drops a failure has it put back; the prompt carries `"tamamlanan": []`.
  - A draft that says "3 iş tamamlandı" is replaced by the rule text.
  - No failures gives the constant sentence with 0 provider calls.
- **Dev-ledger incident (mine):** `ledger_service.record` commits, so my five probe rows (`insp-nfo-*`) landed in the dev ledger. I deleted exactly those five; the count is back to 38. Production was never touched.
- **Not run by me:** full unit suite, `quality-gate.ps1`, the utterance corpus (the worker's 2793 stands unverified), real Haiku.

**Pass 2 — findings**
1. **Acceptance unmet.** I measured the real router on seven Turkish-letter spellings, including "ne başarısız oldu", "bu hafta …", "bugün …", "neler …", "dün neler …" and "ofiste bu hafta …". Every one goes to the explain `failures` family, which speaks only the latest failure. Only the ASCII "ne basarisiz oldu" reaches the narrative with `failures_only=True`. The worker's report says this honestly, and the tests no longer claim otherwise.
2. **No production behaviour change.** `tools.py:2131` (`activity_explain`) calls `explain_to_briefing` without `chat_provider`, so the rule narrator keeps answering and no model request is sent. Roadmap row 2c does not advance for the owner; the evidence is PROVEN_AUTOMATED for plumbing only, not READY_FOR_OWNER.
3. **Commit title overclaims.** `4d92b694` says "'ne başarısız oldu' tells the failures only"; a merge title must not repeat it.
4. **Departure from build item 3 is sound.** The factory takes the caller's provider instead of process settings; I accept it, since any process-wide fallback reopens the leak. It needs an ADR number at merge.
5. **For the wiring card, not this diff.**
   - A synchronous Haiku call per ask on the tool thread, up to `assistant_chat_timeout_s` plus a retry.
   - Ledger summaries leave for Anthropic (KVKK); the ADR for that card should say so.
   - Which narrator spoke, and why it fell back, is still not recorded.
6. **Auditor limit, unchanged by this diff.** In failures-only mode a draft that invents completed work without a number would pass, because the auditor checks numbers and failures, not prose.
7. **HANDOFF.** Neither worker commit touches `docs/HANDOFF.md`, which CLAUDE.md requires of every commit.
8. No secrets, no paths, no files outside the area, no table or migration changed. Rollback is two arguments in `narrative()`.

**Verdict**

`RETURN (1. the card's acceptance "explain_to_briefing('ne başarısız oldu') reaches narrative with failures_only=True" fails through the real router — the lead must widen the area to app/voice/intents and test_narrative_intent_wiring._OWNED, or recut the card as "plumbing" with a truthful title and cut the router card; 2. production wiring is missing — tools.py::activity_explain needs chat_provider=narrative_chat_provider(ctx.live), in the area or in its own card whose title names the per-ask Haiku call; 3. the merge title must not claim the owner's sentence; 4. HANDOFF was not updated in the worker's commits. This is the second RETURN on item 1, so the task stops with the lead.)`
