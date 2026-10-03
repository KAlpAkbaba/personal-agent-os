**Inspector report: owner-trials-api, round 2** (branch `team/d20261003/worker-owner-trials-api` @ `7897acfa`)

Everything ran and passed, including PostgreSQL, and both of my own mutations went RED. The fix for my first-round blocker is real.

**Scope.** All 8 changed files are inside the area. There is no migration, no change under `infra/` and none to `scripts/cloud`, so the host snapshot check does not apply. The tree is clean after my run.

**Pass 1: run it**
- **Unit tests:** `test_team_trials.py` and `test_team_approvals.py` gave 80 passed, 0 skipped. That matches the worker's number, and no skips means the PowerShell half of the split-candidate test really ran.
- **Other team suites:** `approvals_while_running`, `queue_schema`, `office`, `state`, `proposals`, `models_setting`, `speech`, `voice_intents_team_status` and `allowlist_editor` gave 434 passed.
- **Ruff:** `check` and `format --check` are clean on `app/team` and both new test files.
- **PostgreSQL on the dev stack:** I created a fresh database `pagentos_insp_trials` and migrated it to head (`0064_memory_vocabulary_class`).
  - `tests/integration -m integration -k team` gave 17 passed, 151 deselected.
  - `test_team_trials_postgres.py` ran on its own gave 1 passed. That one test covers the listing, Oldu, Olmadı without its words (422), Olmadı with them, the second decision (409), the unknown trial (404), the fix task's empty area and its proposal, all while the lock is held.
  - I dropped the database afterwards.
- **Column widths:** `team_state.key` is VARCHAR(80); a fix id is at most 64 characters. `updated_at` is VARCHAR(32); the stamp is 20. In the ledger, `source_ref` is VARCHAR(256) and the longest possible value is about 150 characters; `action` and `event_type` also fit.
- **My own mutations** (sha256 `45062a69…6c0f` before and after each; restored from a backup copy):
  - M-A: in the "every trial passed" check I replaced `all(...)` with `any(...)`. RED, 2 failed: `test_oldu_records_…_leaves_the_state` on the file and the database store.
  - M-B: I removed the state filter from `list_open`. RED, 4 failed: the listing tests on both stores.
- **The card's own two mutations** ("said required" removed, duplicate guard removed) were proven RED in round 1. That code has not changed since, so I did not repeat them.

**Pass 2: break it**
- **My round-1 blocker is fixed.** The fix task now has `proposal == goal`, written as prose. The test runs the cycle's own `Test-TeamSplitCandidate` and `Get-TeamNextRole` from `TeamQueue.ps1` and gets split=True, next=rest. The worker's M2 (`FIX_STATE` set to `"assigned"`) shows that half fails on its own.
- **The route never claims PROVEN_REAL.** On Oldu the state stays `released` or `awaiting_real_evidence`, and the reason line is set only when every trial in object form is "oldu".
- **Database store while a cycle runs:** the decision is taken and the cycle's lock is left intact. The file store refuses it while a cycle holds the lock, which matches `approvals.decide`.
- **The cycle's writes keep the new shape.** The cycle writes the queue with `ConvertTo-Json -Depth 12`, deep enough for the trial objects. No PowerShell validator reads `owner_trials`.
- **Secrets and privacy:** no secrets or local paths in the code. The owner's words (at most 500 characters) go into the ledger detail and the fix task's goal. That is the owner's own data on a single-owner system, so I see no KVKK leak.
- **Non-blocking notes for the lead:**
  1. In `owner_trial`, `said` and `at` have no `type`, so a number would pass the schema. The schema could tighten them to string-or-null.
  2. The two risks the ADR records are real but documented. The ledger event is written before the queue, so a `stale_write` leaves an event for a decision that never landed. Olmadı is two writes, so a failure between them leaves the verdict with no fix task. Both are consistent with `approvals.decide`.
  3. A leftover database `pagentos_it_trials` from the worker's first round is still on the dev Postgres. It is harmless; drop it when convenient.
  4. Until the lead updates `TeamRun.ps1:443`, the cycle report prints a trial object as a raw PowerShell object (`@{id=…}`). That is part of the merge work the card names.
- **For the lead at merge:** add the vocabulary constants `team.trial.passed` and `team.trial.failed`, the inspector role text, the release step (a released task with open trials goes to `awaiting_real_evidence`), the `TeamRun.ps1` report reading the new shape, and 38.3–38.5 as the first open trials.

**Evidence**
- **PROVEN_AUTOMATED:** unit tests on both stores, the cycle's real PowerShell functions, and real PostgreSQL.
- **NOT_RUN:** the full unit suite, the full gate and the team-cycle PowerShell suite. This is a task branch, so those are for the integration branch.
- **READY_FOR_OWNER:** "Oldu" on one of the 38.3–38.5 sentences, after the merge.

APPROVE
