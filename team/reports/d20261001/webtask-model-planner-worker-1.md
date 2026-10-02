## Şu an üzerinde çalışılan
- **Görev:** `webtask-model-planner` (roadmap 2a, ADR-0207 PR-C 1/2) — tarayıcı görev döngüsüne gerçek model planlayıcı.
- **Alan:** `services/api/app/webtask/{model_planner.py,activities.py}`, `services/api/tests/unit/test_webtask_model_planner.py`, `team/plans/webtask-model-planner-adr.md`
- **Makine:** sahibin Windows geliştirme PC'si, worktree `.claude/worktrees/team/d20261001/worker-webtask-model-planner`
- **Durum:** bitti, commit edildi, push edildi, worktree temiz. HANDOFF'a dokunulmadı; lead yazar.

## Report
- **sha:** `58928a12e428b13860f0005153f3089164135fd9` on `team/d20261001/worker-webtask-model-planner` (local = origin).
- **Files changed:** 4, all inside the area. `planner.py`, `loop.py`, `gate.py`, `service.py` and `config.py` are untouched.
- **What was built:** `ModelPlanner` sends one Messages API request per plan, shaped as the card specifies, with no `thinking`. One retry after 1.5 s on 429/529, 30 s timeout. `default_planner()` chains rules + `ModelPlanner` when `anthropic_api_key` is set, otherwise rules + `NoModelPlanner` as today.
- **RED → GREEN:** RED was `ImportError: cannot import name 'model_planner' from 'app.webtask'` at collection. GREEN is 33 passed in `test_webtask_model_planner.py`, with a fake `send` everywhere; no real API call was made.
- **Acceptance points covered:**
  - a `step` tool_use answer becomes the parsed `Step`;
  - the body names `tool_choice` step; GOAL is first, PAGE is last, and the hostile sentence appears exactly once, between the untrusted markers, and nowhere in `system` or `tools`;
  - `capable=True` picks the capable model id, otherwise the cheap one;
  - a text-only answer, a refusal, an empty answer, a wrong tool, two calls, a cut-off answer, or a step that does not parse each raise `PlannerError` with no page text;
  - 529 and 429 are retried once, then `PlannerError`; 400/401/404/500 are not retried; a timeout or connect error raises `PlannerError`;
  - `default_planner()` with an empty key asks the owner and sends nothing; with a key the model is asked with the configured ids; a consent banner is still answered by rule with zero sends;
  - `run_round` over `FakeBrowser` with `ModelPlanner` completes one round, acted and verified; a model that answers in words fails the round with `FAIL_PLANNER` and nothing reaches the site.
- **Mutation proof:** each mutation was restored from a backup copy, and sha256 before and after is identical (`model_planner.py` `1c672e21…30f5`, `activities.py` `cfcfd626…1ef7`).
  - `tool_choice` removed → 1 failed.
  - PAGE placed before GOAL → 1 failed.
  - Configured check inverted → 2 failed.
  - Extra mutations (run before the whitespace-only `ruff format`; sha `729a9548…` restored): capable choice inverted → 3 failed; unknown-key guard removed → 1 failed; retry removed → 3 failed; page text also appended outside the wrapper → 1 failed.
- **Other runs:** `tests/unit/test_webtask_*.py` 269 passed; eight source-scanning and neighbouring unit files 923 passed; `tests/integration/test_webtask_worker.py` 5 passed; `ruff check` and `ruff format --check` clean on the three Python files.
- **Evidence class:** PROVEN_AUTOMATED (fake send) for every claim above.
- **NOT_RUN:**
  - The full unit suite (~5400 tests) and the local gate; only the files listed above were run.
  - Any request to the real API, the real model against the fixture site, and the owner's Chrome (all later cards).
  - mypy: it is not installed in the venv.
- **One addition beyond the card:** `parse_step` puts unknown key names, which the model writes, into its error. `ModelPlanner` replaces that case with a fixed sentence so page text cannot ride out in a `PlannerError`. `planner.py` itself is unchanged.
- **ADR text:** `team/plans/webtask-model-planner-adr.md`, unnumbered, for the lead to number at merge.
- **Open risks:**
  1. `temperature: 0` against `executive_planner_model` (`claude-sonnet-5` by default) is unverified. If that model rejects a non-default temperature, every capable round fails with "status 400" — loudly, not silently. The next card (real model, fixture site) should check this first.
  2. If production has `anthropic_api_key` set (the ADR assumes it does, for research; not checked here), `default_planner()` returns the model planner as soon as this is released. Per ADR-0207 a task still needs the device's `-AuthorizeTasks` grant; after that each unruled round costs one Haiku call.
  3. Element names are written by the page and sit in ELEMENTS, outside the wrapper. That is `build_prompt`'s existing design; the guarantee remains `parse_step` plus the gate.
  4. The trail's `planner` field says `model`, not which model was used.
  5. The `claude-api` skill failed to load, so the request shape follows `assistant_chat` and the card rather than a fresh check against the docs.
