**Report — execution-target-rule (pilot-01)**

**Şu an üzerinde çalışılan:** `execution-target-rule`, area `services/api/app/execution` + tests + ADR text, machine: this worktree. I did not touch HANDOFF.

- **sha:** `02f72cdbd0edc43c068fa705dbc15f9e15510988`, on `team/pilot-01/worker-execution-target-rule`, pushed, worktree clean.
- **Files (5, all inside the area):**
  - `app/execution/__init__.py`, `rule.py`, `vocabulary.py`
  - `tests/unit/test_execution_target.py`
  - `team/plans/execution-target-rule-adr.md`
- **Package:** pure functions `decide(ExecutionRequest) -> Decision`, `events(decision)` and `forced_target_of(word)`.
  - Chains (order considered): research and browser_task go cloud, owner_chrome, device; desktop is device only; scheduled and compute are cloud only.
  - Signed-in jobs drop cloud from the chain.
  - The deny-list is read through `app.webtask.sites.denied`, so nothing is copied.
  - Event-type constants are in `vocabulary.py` for you to register in `app/ledger/vocabulary.py`.
- **Tests:** 46 passed (`PROVEN_AUTOMATED`), ruff check and format clean.
  - Rules with a hit and a near miss: scheduled vs research with the cloud offline, signed-in vs plain, forced cloud vs `bulutlu`/`bulutbank`, alias resolving to no device, wall words vs `slow_page`, payment, deny-list acting vs reading.
  - Chain order and completeness are checked for every JobKind.
  - `events()` gives exactly one selected/refused per decision and one fallback per skip, each with a reason.
- **RED proof:**
  - Test-first order slipped: I wrote `rule.py` before the test file.
  - I then moved `app/execution` aside and ran the test, which failed at collection with `ModuleNotFoundError: No module named 'app.execution'`. I restored the package and it went green.
- **Mutation RED** (each restored from a backup copy; `rule.py` sha256 identical before and after, `8de7766b…040f7`, then 46 passed):
  1. Owner Chrome added to the scheduled chain: 3 failed.
  2. Signed-in cloud guard removed: 3 failed.
  3. Forced-unavailable refusal turned into a fallback: 3 failed.
- **Choices to review:**
  - A whole-word match forces cloud, so `bulutlu` and `bulutbank` count as device aliases, not cloud.
  - A forced target the kind doesn't allow is refused as `forced_target_not_allowed`. That covers forcing cloud on a signed-in or desktop job, and a device on compute or scheduled.
  - A wall (`auth_wall`, `captcha`, `challenge`) gives outcome `ask_owner`. It is written as one `execution.refused` event with `ask_owner: true`, because only three event types were allowed.
  - Signed-in research falls back from owner_chrome to device. That is one tuple in `_CHAINS` if you disagree.
- **ADR text:** the rule table, chain and events are in the plan file. The open question on ADR-0207 decision 3 (a cloud job vs "no unattended task") lists four options and decides nothing.
- **Not done (`NOT_RUN`):**
  - Nothing is wired into the broker, API or ledger; that is PR 2 and your merge step.
  - No availability probes.
  - No full `services/api` suite run; only this file plus ruff.
- **Risks:**
  - `app.webtask.sites.denied` matches host label parts by substring, so it over-matches on purpose and acting on `foodbank.*` is refused.
  - The venv is the main checkout's, and pytest imported the worktree tree, as the missing-module failure showed.
