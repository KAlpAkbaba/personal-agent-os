## Inspector report — `narrative-model-wiring` @ `f4da5aeb` (cycle d20261002)

**Pass 1 — run it**
- The commit touches 4 files, all inside the area; `config.py` gains the one setting and nothing else. Tree clean, HEAD unchanged after my run.
- New tests: `test_narrative_model_wiring.py` 15 passed (the worker's number holds).
- Related suites (narrative, explain, office01, assistant-chat, config, ledger, all 38 `test_voice_*.py`): 1244 passed, 0 failed. `ruff check` and `ruff format --check` clean on the three Python files.
- My mutations on `tools.py`, different from the worker's, each restored from a backup copy to sha256 `d3c6965f…2b11`:
  - provider built before the switch is read → RED, 2 failed (both OFF cases);
  - timeout classed as `provider_error` → RED, 2 failed;
  - narrator named for every explain kind → RED, 1 failed.
- PostgreSQL on the dev stack (`127.0.0.1:15432`), a throwaway probe through the real app's tool-call route, 4 passed. The `voice.explained` row is `jsonb` and held `rule/setting_off`, `rule/timeout`, `rule/audit_rejected` and `model` with a JSON null reason. No table, migration or store is touched, so no integration test is owed. PROVEN_AUTOMATED (probe deleted, not in the branch).
- Cross-cutting guards (54 files): 1465 passed, 1 failed. The failure is `test_qualification_evidence.py::test_every_proof_marked_row_points_at_something_that_exists`, on QUALIFICATION row 41.7. That row is on `main` and not in this diff: it is the lead's, and it will be red at the gate.
- Production does hand `settings` to the session (`main.py:439`), so the switch is reachable once a compose line exists.

**Pass 2 — break it**
1. **The ledger note can say `model` when the rule narrator spoke.** `_narrator_of` (`tools.py`, the `told.startswith(witness.draft + " ")` line) infers the narrator by string prefix. A draft the auditor rejects that happens to be the opening of the rule text is recorded as `narrator=model, narrator_reason=null`.
   - Reproduced through the real route: draft `"2 iş başarısız oldu."` (skips every completed area, so rejected), the rule text is spoken, the note reads `model / null`.
   - A terse model answer of exactly this shape is plausible, since the rule text opens with `"N iş başarısız oldu."`.
   - This breaks card item 4 and the ADR sentence "`model` is recorded only when the stored account IS the model's draft".
   - An in-area fix exists: accept only `told == draft` or `told.startswith(draft + " Ayrıca başarısız: ")`, the repair's own marker.
2. Acceptance says "a provider that times out … reason=timeout". Over the real `AnthropicChatProvider` the note reads `chat_unavailable`, as the worker disclosed and pinned. The fix is in `app/assistant_chat.py`, outside the area, so it needs a follow-up card, not this worker.
3. Informational: the switch is read with `bool(getattr(...))`, so a settings stand-in holding the string `"false"` reads as ON (probe: the provider was asked once). Production hands a real `Settings`, so there is no live risk.
4. No secrets or paths in the diff; the note and the `narrative_narrator` log line hold two words and never the draft. Rollback is the setting or a revert, with no schema change.

**Evidence classes**
- Default-off behaviour, fallback, and the note on SQLite and PostgreSQL: PROVEN_AUTOMATED.
- Real Haiku model, full unit suite, full `quality-gate.ps1`: NOT_RUN (task branch, not the integration branch).
- Switching the setting on, plus the compose line: READY_FOR_OWNER.

RETURN (1. `_narrator_of` records `model/null` for an audit-rejected draft that is a prefix of the rule text — fix the comparison in `tools.py`, add the RED test with draft = the rule text's first sentence expecting `rule/audit_rejected`, and correct the ADR sentence)
