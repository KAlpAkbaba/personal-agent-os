## Inspector report — `webtask-model-planner` @ `58928a12` (local = origin, tree clean)

**Pass 1 — run it (worktree sources, confirmed by `__file__`)**
- `tests/unit/test_webtask_model_planner.py`: **33 passed** (matches the report).
- `tests/unit/test_webtask_*.py`: **269 passed** (matches).
- `tests/integration/test_webtask_worker.py -m integration` on the dev stack (real Temporal and PostgreSQL): **5 passed**.
- `ruff check` and `ruff format --check` on the three Python files: clean.
- sha256 matches the report: `model_planner.py` `1c672e21…30f5`, `activities.py` `cfcfd626…1ef7`.
- The commit touches exactly the 4 area files; `planner.py`, `loop.py`, `gate.py`, `service.py` and `config.py` are untouched. No table, migration, store or cloud script is touched, so the host snapshot does not apply.
- **My own mutations: 14 of 14 RED**, each restored from a backup copy with sha256 identical afterwards:
  - `tool_choice` changed to `{"type":"any"}`
  - PAGE moved between ELEMENTS and HISTORY
  - configured check dropped (always the model)
  - 500 added to the retryable statuses
  - `max_tokens` stop check disabled
  - transport exception text put in the error
  - two tool calls accepted
  - `temperature` dropped
  - capable model taken from the cheap setting
  - vendor error message put in the error
  - wrong tool name accepted
  - `system` emptied
  - a third attempt added
  - parse error echoing the arguments
- NOT_RUN by me:
  - the full unit suite (~5400 tests) and the full `quality-gate.ps1` — the lead runs these on the integration branch;
  - mypy — the worker says it is not installed; I did not check;
  - any real API call — excluded by the card.

**Pass 2 — break it**
1. **Low — non-httpx failures escape `plan()` instead of becoming `PlannerError`.** Probed with a fake `send`:
   - `httpx.InvalidURL` (not an `HTTPError`; reachable with a malformed `research_anthropic_base_url`) and `OSError` escape;
   - a non-numeric `usage.input_tokens` raises `ValueError`;
   - a non-dict payload raises `AttributeError` (the real `_http_send` prevents this one).
   
   Each escape fails the activity loudly (one attempt, `round_could_not_run`). The `InvalidURL` text could carry the URL but not page text. The card's list (HTTP error, timeout, text-only, no tool_use, `PlannerError`) is met; worth a guard in PR-C 2/2.
2. **Medium, for the next card — `temperature: 0` is unproven on both models.**
   - No other request in `app/` sends `temperature`, so there is no proxy evidence from production either.
   - Recent Claude models reject non-default sampling parameters with 400. If `claude-sonnet-5` does, every re-plan round fails with `FAIL_PLANNER`.
   - The card mandated the parameter, so this is not the worker's defect. The `claude-api` skill failed to load for me too, so I could not check the docs.
3. **Release behaviour change.** If production has `anthropic_api_key` set, every unruled round calls Haiku as soon as this is released. I did not check the production value, and the first real request will be the first-ever test of this body shape. Suggest PR-C 2/2 (real model against the fixture site) runs before or with the release.
4. **Low — English in the owner's message.** The `PlannerError` text is embedded in the Turkish message ("Planlayıcı yanıt vermedi (the model answered status 400)."). This is `loop.py`'s existing pattern and outside the area.
5. **Checked and clean:**
   - Logs carry only status, model id, token counts, exception class and the vendor error type (60 chars); no key, no page text, no model text.
   - The hint that goes into the GOAL block is built from fixed words in `verify.py` and the loop (I did not read every `gate.py` reason).
   - `parse_step`'s other errors name only schema keys, never values.
   - The no-key path sends nothing.
   - Worst-case wait is about 2×(30 s connect + 30 s read) + 1.5 s, under the 150 s heartbeat timeout.
   - Element names sit in ELEMENTS outside the wrapper — existing `build_prompt` design, disclosed by the worker.
   - No new dependency, no SDK.

**Evidence classes**
- Request shape, answer handling, model choice, retry, `default_planner()` wiring, one loop round: **PROVEN_AUTOMATED** (fake send, 14 extra mutations RED).
- Workflow and activity still run with the changed `activities.py`: **PROVEN_AUTOMATED** on the dev stack. The integration test replaces the ports, so `default_planner()` itself is exercised only by the unit tests.
- The real Messages API accepting this body (forced tool plus `temperature: 0`, both model ids): **NOT_RUN** — later card, as the card states.
- The owner's Chrome: **NOT_RUN** — later card.

For the lead at merge: number the ADR; carry findings 1–3 into the PR-C 2/2 card; decide whether the release waits for the real-model card.

`APPROVE`
