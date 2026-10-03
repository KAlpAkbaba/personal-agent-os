**Şu an üzerinde çalışılan:** execution-call-site-routines (2b, ADR-0213 row 1) — area: routines/dispatch.py, target.py, config.py (only the one new field), the two test files, the ADR plan. Machine: owner's build PC, worktree `worker-execution-call-site-routines`.

**Result:** all three ruling points are built. The rule now sits behind a setting that is off by default. Committed and pushed; the remote branch is at the same sha. Worktree clean.

**sha:** `a54f8f544e234012bef3efb13ea1bd36c22c0151`. The `team/nightly/lead` merge (f38a5e62, main 65cd94ff) went in cleanly first. The task commit changes 5 files, all inside the area. `dispatch.py` sha256 `f02c380a…`, `target.py` `13c1e78e…` (unchanged), `config.py` `3fd69055…`.

**What changed:**
- **Off switch:** new `routines_execution_rule_enabled: bool = False` in `config.py`, with one comment line, beside the research setting. `ActionDispatcher._browser_action` reads it at each firing.
  - OFF: it calls `self._device_action.run(...)` with main's exact arguments. No scheduled view, no probe, no ledger row.
  - ON: this branch's existing behaviour.
  - No compose line.
- **Real application object:** tests capture the routine dispatcher `create_app` itself builds over `app.state.device_action`.
  - In the unit tests only the session factory and command client are swapped.
  - In the integration test only the command client is swapped; the port's own dev-stack PostgreSQL session factory is kept.
- **Found on the way:** `get_settings` is `lru_cache`d, so setting the env var inside a test does nothing. The tests replace `dispatch_mod.get_settings` instead, the same way the research tests do. In production the value is read once, at process start.

**Tests (PROVEN_AUTOMATED):**
- RED before the change: 5 off-switch tests failed (the rule was asked and the cloud was chosen).
- GREEN after: unit file 37/37; with `test_routines_dispatch.py`, 70/70. Dev-stack PostgreSQL integration 4/4: two old, plus real-app off and real-app on.
- Off test checks: action goes to the healthiest machine (main's `_select_for`, matching `port.selection_for`), no ledger row, and the rule raises if asked.

**Mutations** (each restored from a backup copy, sha256 back to the original):

| Mutation | Result |
|---|---|
| Setting read as always-on | RED: 4 unit + 1 integration failed |
| Config default flipped to True | RED: 3 failed |
| (card) Browser-capability check removed | RED: 2 failed |
| (card) Refusal falls back to `select_device` | RED: 16 failed |

**ADR** (`team/plans/execution-call-site-routines-adr.md`):
- New decision 5: the setting.
- Deny-list: acceptance line withdrawn; the rule's own answer is the cloud and a routine inherits it; the lead cards the question separately.
- A `selected target=cloud` row is not proof the action ran.
- First production evidence needs the setting on: READY_FOR_OWNER, not part of the merge.
- The claim that the cloud worker advertises `browser.navigate` is marked UNVERIFIED.
- The old "no setting guards this" bullet is removed.

**Lint:** `ruff check` and `ruff format --check` clean on the 5 code files (import order fixed by `--fix`).

**NOT_RUN:**
- mypy: there is no mypy module in the venv.
- Full unit suite and `quality-gate.ps1`: the machine is busy; one 37-test file took 3–7 minutes.
- A real cloud-worker run.

**Open risks:**
- **Dev DB ahead of this branch:** it is at alembic `0065_misheard_utterances` (the gate9 / main checkout migration), which this tree lacks. The integration conftest's `upgrade head` fails without it. To run the integration test I copied that one migration file in temporarily and deleted it afterwards; it was never committed. Once the lead branch carries 0065 this goes away.
- Older inspector items that are still open: M6 (`_is_browser_capability` accepts any `browser.*`), the ledger-failure path proven only on SQLite, and the `media_playback` question.
