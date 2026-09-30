The worktree is clean and the branch is pushed. Its tip, `82711a29b23f118ff423fba0831399cfabb401c4`, matches `origin/team/pilot-01/worker-onay-merkezi`. I only re-checked git state this turn and re-ran nothing. The test, ruff and mutation results below are from the earlier run.

**Şu an üzerinde çalışılan (for the lead):** task `onay-merkezi`, area `services/api/app/team` plus the web page `apps/web/app/core/approvals`, machine = this worktree. On merge the lead sets it to "Yok".

**Files changed (7, all inside the area):**
- `services/api/app/team/__init__.py`
- `services/api/app/team/approvals.py`
- `services/api/app/team/routes.py`
- `services/api/tests/unit/test_team_approvals.py`
- `apps/web/app/core/approvals/approvalsApi.ts`
- `apps/web/app/core/approvals/page.tsx`
- `team/plans/onay-merkezi-adr.md`

**Tests**
- **Red first:** the new test file failed at collection because `app.team` did not exist. I kept that output.
- **Green:** 34/34 in `test_team_approvals.py`, and 72 passed together with `test_team_queue_schema.py`.
- **Checks:** ruff check and format are clean. `tsc --noEmit` and oxlint on the web page are silent.
- **Rules, each with a hit and a near miss:**
  - A task at `awaiting_owner` or `awaiting_release` is listed; `in_progress`, `done` and `awaiting_real_evidence` are not.
  - The shell may name the gate. Naming the wrong one (an out-of-date page) is refused.
  - A voice approval is refused with no gate, with a word that is not a gate, with two tasks at the gate, with none, or naming a task from the other gate.
  - A cycle lock held for under 6 hours blocks a decision. A lock older than 6 hours, unheld or missing does not.
  - A proposal path outside `team/` is never read.
- **Mutation proof:** one mutation per rule went RED, each restored from a backup copy of `approvals.py` (no `git checkout --`). The rules were voice gate required, ambiguity, cycle lock, reject reason, ledger before write, stays in `team/`, stale gate, and `awaiting_real_evidence` added as a gate.
- **sha256 caveat:** the sha256 was identical before and after the mutations. I ran `ruff format` after that proof, so the restore matches the pre-format file, and the 72 tests were green on the formatted file.

**Evidence per claim**
- **PROVEN_AUTOMATED (the API and its rules):**
  - Onayla writes `approved` and Reddet writes `stopped` with the owner's reason.
  - A release approval changes only `queue.json` on disk and never starts a release.
  - Every decision is exactly one ledger event, and a refused decision writes none.
- **READY_FOR_OWNER:** the shell page, and the voice path (only the API shape: `channel:"voice"`, `gate`).
- **NOT_RUN:** the page in a browser, and the routes through the real `create_app` (not included there yet).

**For the lead at merge** (details in the ADR text)
1. Add subsystem `team` and event types `team.task.approved` and `team.task.rejected` to `app/ledger/vocabulary.py`. The constants are in `app/team/approvals.py`. Until then every decision is refused with 503 `ledger_refused` and the queue is untouched; a test covers that.
2. Add `include_router(team_router)` from `app.team.routes` to `app/main.py`. `app.state.team_root` can override the default repo-root `team/`, because the Cloud Core VM has no checkout.
3. Add a link to `/core/approvals` in the shell navigation.

**Open risks**
- **Release approval target state:** the acceptance says Onayla moves both gates to `approved`, and I did that. For `awaiting_release` the cycle reads `approved` as "assign a worker", so a merged task approved for release would be re-assigned next cycle. That needs a decision from you, and the ADR explains it.
- **Web path:** the card's area says `apps/web/src/app/core/approvals`, but the repo has no `src/`. I used `apps/web/app/core/approvals`.
- **Queue formatting:** the rewrite uses 2-space JSON, UTF-8 without BOM and a final newline. It is valid and schema-conformant but formatted slightly differently from the PowerShell writer.
- **Environment:** the worktree has no venv. I ran tests with the main checkout's `.venv` python, and `app.team` did import from the worktree.
