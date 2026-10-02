## Inspector report: `narrative-model-wiring` @ `e71b9e14` (second pass, cycle d20261002)

**Pass 1: run it**
- The worker's commits touch 4 files, all inside the area. `config.py` gains only the one setting. The `docs/*` changes in `main...HEAD` are the lead's commits (`e1543a97`, `e6d13b14`), not the worker's. Tree is clean and HEAD is unchanged after my run.
- `test_narrative_model_wiring.py`: **17 passed**, which matches the worker's number.
- Related suites (narrative, explain, all `test_voice_*.py`, assistant_chat, config, ledger, office01, briefing): **1292 passed, 0 failed** (198 s). This closes the worker's NOT_RUN on the voice and ledger suites.
- `ruff check` and `ruff format --check` are clean on the three Python files.
- Cross-cutting guards (54 files): **1470 passed, 1 failed**. The failure is `test_qualification_evidence::…points_at_something_that_exists` on QUALIFICATION row **41.7**. That row is already on `main` and this task touches no docs, so it is the lead's to fix and it will be red at the gate.
- My own mutations on `tools.py`, each different from the worker's. The file was restored from a backup copy after each one, sha256 `bb69c09f…` before, after every restore and at the end:
  - Accept only `told == draft` (the auditor-repair branch dropped): RED, 2 failed.
  - The `witness.failure` branch never taken: RED, 4 failed (timeout ×2, provider_error, real-provider chat_unavailable).
  - A session without the setting reads ON: RED, 1 failed.
  - The `audit_rejected` reason dropped: RED, 3 failed.
- **PostgreSQL on the dev stack (the worker's NOT_RUN).** I ran a throwaway probe through the real app's tool-call route and deleted it afterwards (not in the branch). `detail_json` is `jsonb`.
  - The dev ledger's own rule text opens `"4 iş başarısız oldu."`. I used that sentence as the rejected draft and the note reads **`rule / audit_rejected`**.
  - Timeout gives `rule / timeout`. The echoed rule text gives `model` with a JSON null reason (`jsonb_typeof = null`). Setting off gives `rule / setting_off`, and the provider was not asked.
  - 1 passed. No table, migration or store is touched, so no integration test is owed.

**Pass 2: break it**
1. The first inspection's RETURN item is fixed. The comparison is now `told == draft or told.startswith(draft + " Ayrıca başarısız: ")`, held by two RED-proven cases, and it also holds on PostgreSQL.
2. Wrong-direction check: could a model draft be recorded as `rule`? I probed with multi-line, padded, trailing-newline and inner-newline drafts that the auditor repaired, and all four were recorded as `model / null`. The stored briefing keeps the text verbatim (`engine._narrative_briefing`).
3. A `not ok` answer with provider text gives `rule / chat_busy`. The provider's words are nowhere in the note or the speech.
4. Two asks in one session each get their own correct note (`timeout`, then `model`).
5. Still open, informational (same as last pass): the switch is read with `bool(getattr(...))`, so a stand-in holding the string `"false"` or a `MagicMock` reads as ON (probe: 2 failed).
   - Production hands a real `Settings` (`main.py`).
   - I grepped the repo: no `live` settings stand-in has the attribute (`object()`, a `SimpleNamespace` without the attribute, test classes), so none hits this.
   - Hardening it to `is True` is a small follow-up.
6. Disclosed and still true: over the real `AnthropicChatProvider` a timeout is recorded as `chat_unavailable`. The fix belongs in `app/assistant_chat.py` and needs a follow-up card.
7. Two more disclosed gaps: the repair marker literal lives in two files, and production has no compose line yet.
8. No secrets or paths in the diff. The note and the log line hold two words, never the model's text, so there is no KVKK leak. Rollback is the setting (default off) or a revert, with no schema change.

**Evidence classes**
- Default off, the fallback, and the narrator/reason note on SQLite and on dev-stack PostgreSQL: PROVEN_AUTOMATED.
- Real Haiku model, full unit suite, `quality-gate.ps1`: NOT_RUN (task branch, not the integration branch).
- Switching the setting on, plus the compose line: READY_FOR_OWNER.

Probes are removed, the tree is clean, and no command is left running.

APPROVE
