**Inspector report: task `onay-merkezi`, branch `team/pilot-01/worker-onay-merkezi`**

**Pass 1: run**
- I re-ran `test_team_approvals.py` and `test_team_queue_schema.py` together. Result: 76 passed. The tests imported the worktree's `app/team`. The worktree has no `.venv`, so I ran them with the main checkout's venv and checked the import path.
- `ruff check app/team` and the test file are clean.
- I ran two mutations of my own, different from the worker's:
  - Ambiguity check disabled (`len(waiting) > 1` → `> 99`): 1 failed, 37 passed, `test_a_voice_approval_is_refused_when_two_tasks_wait_at_the_gate`.
  - Cycle-lock refusal disabled (`if cycle_running(...)` → `if False`): 1 failed, 37 passed, `test_a_decision_is_refused_while_a_cycle_holds_the_queue`.
  - I restored from a backup copy. `approvals.py` matches the worker's sha256 `70198218…` again, and `git status` is clean.
- I could not run the full gate, `tsc`, oxlint or vitest for the web page. I did no live run: there is no dev stack or browser in this pass.

**Pass 2: break it**
- **Confirmed breakage: `team/queue.schema.json`.** It has `additionalProperties: false` at the task level. It does not contain `release_approved`, `release_approved_at` or `release_approved_by`. The first release approval writes a task the schema rejects.
  - `test_team_queue_schema.py` only validates the current queue, so it stays green.
  - I could not run a validation because `jsonschema` is not installed here. This rests on reading the schema and a grep that found nothing.
- **Nothing reads the flag.** No file under `scripts/team` mentions `release_approved`, so a release-approved task sits at `awaiting_release` and nothing happens. That is safe, but the flag does nothing yet. It needs a reader (`cycle.ps1` or the lead's release step) and a test.
- **Ledger event is easy to misread.** A release approval logs `awaiting_release -> awaiting_release`. The flag is not its own field in `detail_json`.
- **Ledger vocabulary is not wired.** The `team` subsystem and the two event types are not in `app/ledger/vocabulary.py`. Until the lead adds them, every decision returns 503 `ledger_refused`, and the queue is untouched. That is a safe failure, but the route works only after the merge wiring. `include_router` in `app/main.py` and the navigation link are also missing.
- **Voice rules, checked in the code:**
  - A voice decision without a gate is refused with 422.
  - Zero or several waiting tasks are refused with 409.
  - A gate mismatch is refused with 409.
  - The dotless-i spelling of `yayın` is handled.
  - Nothing starts a release: `decide` only edits the file.
- **Ledger order is correct.** The event is recorded before the queue write, and the file write is atomic through `os.replace`. A `Refused` from the ledger leaves the queue as it was.
- **Area:** all changes are inside the card's `area`. The card's goal says `apps/web/src/app/core/approvals`, but the tree has `apps/web/app`, so the worker's path is the right one.
- **Earlier inspector findings still stand:** the partial write, the lock race between the check and the write, the missing VM checkout, and the JSON formatting. `_WRITE_LOCK` only serialises writers inside one API process, not against `cycle.ps1`. The lock check is a time-of-check gap.
- **Privacy:** the ledger detail carries the owner's reason text and the task title. No secrets or absolute paths appear in the code.

**Evidence classes**
- PROVEN_AUTOMATED: API decision rules and the router (76 tests, two independent mutations RED).
- NOT_RUN: `tsc`, oxlint and vitest for `page.tsx` and `approvalsApi.ts`; any live run.
- READY_FOR_OWNER: the shell page and the voice path.

**Required from the lead before the merge:**
1. Add the three `release_approved*` fields to `team/queue.schema.json`. Add a schema test that validates a release-approved task.
2. Wire the `team` subsystem and both event types in the ledger vocabulary, `include_router` in `app/main.py`, and the `/core/approvals` navigation link. Add the ADR number and the DECISIONS entry.
3. Give `release_approved` a reader in the cycle, or record it as a known gap in the completion report.
4. Run the web gate (`tsc`, oxlint, vitest) on this branch after the merge.

Items 1–2 are outside the worker's area, so this is not a worker fault, and the worker's tests and mutations are sound. The task cannot be used in production until items 1 and 2 are done.

**APPROVE** (API and router only, on the condition that the lead does items 1–2 in the same merge; the web page stays NOT_RUN until the web gate is run)
