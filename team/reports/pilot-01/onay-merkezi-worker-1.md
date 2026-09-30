**Onay Merkezi: the Cloud Core half and the shell page are committed and pushed, but the shell page and the voice path have not been tried.** Branch `team/pilot-01/worker-onay-merkezi`, sha `82711a29b23f118ff423fba0831399cfabb401c4`. The worktree is clean.

**Şu an üzerinde çalışılan** (for the lead's HANDOFF): onay-merkezi, `services/api/app/team` and `apps/web/app/core/approvals`, machine = this home PC.

**Files:** 7, all inside the area. `app/team/{__init__,approvals,routes}.py`, `tests/unit/test_team_approvals.py`, `apps/web/app/core/approvals/{page.tsx,approvalsApi.ts}` and `team/plans/onay-merkezi-adr.md`.

**Tests**
- Red first: the new test file failed at collection with no `app.team`, and I kept that output.
- Green after the implementation: 34/34 in `test_team_approvals.py`, and 72 passed together with `test_team_queue_schema.py`.
- Ruff check and format are clean; `tsc --noEmit` and oxlint on the web page are silent.
- Each rule has a hit and a near miss:
  - a task at a gate is listed, while in_progress / done / awaiting_real_evidence are not;
  - the shell may name the gate, and naming the wrong one (an out-of-date page) is refused;
  - a voice approval is refused with no gate, with a word that is not a gate, with two tasks at the gate, with none, or naming a task from the other gate;
  - a lock held under 6 hours blocks a decision, while one older than 6 hours, unheld or missing does not;
  - a proposal path outside `team/` is never read.
- Mutation proof, one per rule, each went RED, using a backup copy of `approvals.py` (no `git checkout --`):
  - voice gate required;
  - ambiguity;
  - cycle lock;
  - reject reason;
  - ledger before write;
  - stays in `team/`;
  - stale gate;
  - awaiting_real_evidence added as a gate.
- sha256 is identical before and after the mutations, but I ran `ruff format` after that proof, so the restore matches the pre-format file. The 72 tests were green on the formatted file.

**Evidence per claim**
- PROVEN_AUTOMATED: the API and its rules, including:
  - Onayla writes `approved` and Reddet writes `stopped` with the owner's reason;
  - the release approval changes only `queue.json` on disk;
  - every decision is one ledger event, and a refused decision writes no event.
- READY_FOR_OWNER: the shell page and the voice path, which is only the API shape (`channel:"voice"`, `gate`).
- NOT_RUN: the page in a browser.
- NOT_RUN: the routes through the real `create_app`, because they are not included there yet.

**For the lead at merge** (details in the ADR text)
1. `app/ledger/vocabulary.py` needs subsystem `team` and event types `team.task.approved` and `team.task.rejected`; the constants are in `app/team/approvals.py`. Until then every decision is refused with 503 `ledger_refused` and the queue is untouched; a test covers that.
2. `app/main.py` needs `include_router(team_router)` from `app.team.routes`. `app.state.team_root` can override the default repo-root `team/` (the Cloud Core VM has no checkout).
3. The shell's navigation needs a link to `/core/approvals`.

**Open risks**
- **Release approval target state:** the acceptance says Onayla moves both gates to `approved`, and I did that. For `awaiting_release` the cycle reads `approved` as "assign a worker", so a merged task approved for release would be re-assigned next cycle. That needs a decision from you, and the ADR explains it.
- **Web path:** the card's area says `apps/web/src/app/core/approvals`, but the repo has no `src/`. I used `apps/web/app/core/approvals`.
- **Queue formatting:** the rewrite uses 2-space JSON, UTF-8 without BOM and a final newline. It is valid and schema-conformant but formatted slightly differently from the PowerShell writer.
- **Environment:** the worktree has no venv. I ran tests with the main checkout's `.venv` python, and `app.team` did import from the worktree.
