**Inspector report: onay-merkezi (pilot-01)**

**Pass 1: run it**
- I re-ran `test_team_approvals.py` and `test_team_queue_schema.py` from `services/api` on the main checkout's `.venv`. Result: 72 passed, 0 failed. `app.team` imported from this worktree (path checked).
- ruff check is clean and ruff format reports 4 files already formatted.
- Worktree is clean. The diff touches only the 7 area files. No `HANDOFF`, `DECISIONS`, `BUILD_STATE`, `ledger/vocabulary.py` or `main.py` edits, and no new dependency.
- The tests do run through the real `create_app(settings)` plus `include_router`, with a real `require_owner_session`. That is stronger than the worker's "NOT_RUN through create_app". Only the wiring in `app/main.py` is missing, and that is the lead's step.
- My mutations, both different from the worker's eight. `approvals.py` sha256 prefix was `8800c7f7e180d2ba` before, and the same after each restore. I restored from a backup copy, not `git checkout --`.

| Mutation | Result |
|---|---|
| Removed the shell stale-page gate guard (`gate and named_gate != actual`) | RED, 1 failed / 33 passed |
| Changed lock staleness to `timedelta(hours=0)` | RED, 2 failed / 32 passed |

**Pass 2: adversarial**
- **Contract:** the state names match `queue.schema.json`. The `reason` field exists in the schema and rejection writes it. The schema test stays green.
- **Voice refusals:** none of these write anything to the queue or the ledger: a missing gate, a gate word that isn't fikir/yayın, two tasks at the gate, none at the gate, or a task at the other gate. A near miss sits beside each.
- **Auth:** the router depends on the owner session. Reject requires a reason.
- **Release approval:** it only rewrites `queue.json`. Nothing starts a release, and the response says `applied: next_cycle`.
- **Ledger order:** the ledger event is written before the queue. A ledger refusal returns 503 and leaves the queue untouched (covered by a test).
- **Privacy:** the ledger `detail_json` carries the owner's reject reason, which is the owner's own text. No secrets or paths were found in the code.
- **Findings, none blocking:**
  1. **Release semantics (ADR-0214 drift).** `awaiting_release` → `approved` matches the acceptance text. But `cycle.ps1` has no consumer for `approved` at that point, so a merged, release-approved task is likely to be reassigned to a worker. The lead has to decide the target state (`done`, or a dedicated state) before the first real release approval.
  2. **Partial write.** If the ledger event lands and `os.replace` then fails, the ledger holds a decision the queue never got. The window is narrow and the failure is safe: the owner retries, and `source_ref` is deterministic.
  3. **Lock race.** Checking the lock and then writing is not atomic against a cycle that acquires the lock in between. This is inherent to the design, and the 6-hour staleness matches `TeamQueue.ps1`.
  4. **Cloud Core VM.** There is no checkout on the VM. `app.state.team_root` must be set or the default repo path won't exist. The list route then returns a clean 503 `queue_unreadable`.
  5. **Queue format.** The rewrite uses 2-space JSON, no BOM, LF and a trailing newline. It is valid, but it will show as a diff against the PowerShell writer's formatting.
- **Lead wiring at merge:**
  - Add the `team` subsystem plus `team.task.approved` and `team.task.rejected` to `app/ledger/vocabulary.py`.
  - Add `include_router(team_router)` in `app/main.py`.
  - Add the `/core/approvals` link in the shell navigation.
  - Write the ADR and DECISIONS entry, including finding 1.

**Evidence classes**
- **PROVEN_AUTOMATED:** the API and its rules (72 tests green, 2 fresh mutations RED).
- **NOT_RUN:** the web page. `tsc` and oxlint were not re-run because the worktree has no `node_modules`. I relied on the worker's claim for those.
- **READY_FOR_OWNER:** the shell page and the voice path. No live run was done because the dev stack and ledger vocabulary are not wired yet.

APPROVE
