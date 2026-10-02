# Inspector report — `understanding-engine-startup` (branch `team/d20261001/worker-understanding-engine-startup` @ `ec0c1b72`)

**Pass 1 — run it**
- **Task tests:** `tests/unit/test_understanding_startup.py` is 26 passed (4.0 s) on a clean tree. All six `test_understanding_*.py` files plus `test_memory_local_embedder.py` pass (193 and 85 passed across two runs, 0 failed).
- **Owner Utterance Suite: not completed by me.** 2447 of 2756 passed, 0 failed, when my own 55-minute timeout killed it (exit 124); another session's full unit run was sharing the machine. The worker's "2756 passed" is not re-confirmed. Nothing under `app/` imports `startup.py` or `exemplars.py`, so the diff cannot reach that suite before the lead's line.
- **Lint:** ruff check and format are clean on the four files; `export_understanding_exemplars.py --check` exits 0. mypy is not installed in the venv: NOT_RUN.
- **My mutations (8, all RED, different from the worker's):** each was restored from a backup copy, sha256 equal before and after, tree clean.

| Mutation | Result |
|---|---|
| `isinstance(DeterministicEmbedder)` half of the guard removed | 1 failed |
| One exemplar reworded in `exemplars.json` (same count) | 5 failed, incl. byte-equality |
| `render` no longer sorts | 2 failed |
| Loader accepts extra keys | 1 failed |
| `import tests…` added to an app module | 1 failed |
| `active != local` guard removed | 1 failed |
| Off-switch ignored | 1 failed |
| Build-failure reason dropped | 1 failed |

- **Real run through the app object (PROVEN_PROXY):** `create_app(Settings(memory_embedding_provider="local"))`, then the lead's exact expression on `app.state.memory`.
  - Returns in 0.59 ms, not ready at return; the index builds in 337 ms for 1427 exemplars (462–555 ms across ten rebuilds while another thread embedded new sentences, 0 errors on either side).
  - `/v1/system/health` answers 200 with memory `provider: local, semantic: true`.
  - With `deterministic` and `auto`, nothing is configured and one `understanding_engine_not_configured` line carries the reason.
- **Real corpus pass, engine on vs off (LocalEmbedder, real `resolve_intent`, no session context):** 2591 distinct sentences, 22 repaired routes, **0 decisions changed where a rule matched**. `read_turn` costs p50 6.6 ms, p95 10 ms, max 18.8 ms. My upper bound (every expected intent treated as repaired) gave 0 questions; the worker's gave 1.
- **PostgreSQL / host snapshot:** not applicable. The diff touches no table, migration, store, broker, `scripts/cloud` or `infra/docker`.
- **Packaging:** the Dockerfile's `COPY app ./app` ships the JSON, `fastembed` is a dependency, and the prod compose defaults the provider to `local`. The file is 105 kB, LF only, with no e-mail, URL or long number in it.

**Pass 2 — break it**
1. **The "sentence no rule matches" is matched by a rule.** The real `resolve_intent("Dışarıda hava nasıl bugün")` returns `weather_query`, and the decision is layer `rule` 1.0. The test constant `RULELESS` and the report's proof hand-feed `rule=None`. The mechanism is right, the label is wrong. If the owner tries that sentence in production, the audit row says `rule` and reads as a failure. Use "Bugün nasılsın" (rule none → layer `semantic`, weather_query 0.62, acts=False; measured).
2. **Undocumented behaviour change at merge (PLAUSIBLE, not measured).** A turn with no rule match used to be band LOW. With the engine live it can be MEDIUM, and `tools_operator._read_back` (line 813) then adds "… açıyorum efendim" to a model-routed `app_open` receipt. It is harmless, but the ADR says such readings are "recorded only".
3. **GIL sharing.** The build is partly pure Python. My first probe spun a hot loop of cached embeds on the main thread and stretched the build to 39 s; that is a probe artefact, but the build does compete with the event loop for its duration.
4. **Unlocked LRU** in `LocalEmbedder` (worker flagged it; out of area): 0 errors in 11 concurrent builds. A lost race would be caught and leave the engine unconfigured for the life of the process, visible only in the log.
5. **Global engine is never un-configured.** A later `create_app` with the deterministic provider in the same process keeps the old engine. Production has one app per process; it matters only for tests that use the local provider.
6. No secrets, no paths, no file outside the area. The audit block carries intent names only. Rollback is removing one line or setting the off-switch.

**Evidence classes**
- Code and tests: PROVEN_AUTOMATED.
- Real embedder through `create_app`: PROVEN_PROXY.
- Owner Utterance Suite re-run: NOT_RUN to completion (2447/2756, 0 failed); the lead's gate runs it after the `main.py` line.
- Production audit row with layer `semantic`: READY_FOR_OWNER, using the sentence in finding 1.

**For the lead at merge**
- `main.py:218` is still `memory = MemoryRuntime(settings)`; the named line and import are correct as written.
- Correct `RULELESS` and the ADR wording (finding 1), and add finding 2 to the ADR.

APPROVE
