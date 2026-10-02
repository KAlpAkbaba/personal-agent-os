## Şu an üzerinde çalışılan
owner-trials-api (cycle d20261003). Area: `services/api/app/team/{trials,approvals,routes}.py`, both `queue.schema.json` copies, the two test files and the ADR text. Machine: MAIL. Status: done, waiting for the inspector.

**sha:** `003c27a4fb2dea30a4e72cd770f0b24e2b2e9b7d`, pushed to `team/d20261003/worker-owner-trials-api`. The worktree is clean.
**Files changed: 8, all inside the area.** New: `trials.py`, `test_team_trials.py`, `test_team_trials_postgres.py`, `team/plans/owner-trials-api-adr.md`. Modified: `approvals.py` (only adds a public alias for the shared write lock), `routes.py`, and both schema copies (byte-identical, checked with `cmp`).

**What it does**
- Each trial in `owner_trials` is now an object `{id, sentence, machine, expect, verdict, said, at}`. The old plain-string form is still accepted.
- `GET /v1/team/approvals` now includes `trials`: the undecided trials of tasks in `released` or `awaiting_real_evidence`.
- `POST /v1/team/trials/decision` records the owner's verdict:
  - **oldu:** saves the verdict, his words and the time. The task's state never changes and nothing claims PROVEN_REAL. Once every trial on the task is "oldu", the reason line becomes `sahip denedi: oldu (<at>) - PROVEN_REAL satırını lead yazar`.
  - **olmadi:** his words are required, at most 500 characters. It opens `fix-<task>-<n>` in `approved`, with an empty area, reason `alan: lead belirler`, the roadmap row copied, and the sentence, his words and the sha quoted in the goal.
  - **Refusals:** 409 for a second decision on the same trial, 404 for an unknown task or trial, 409 `not_on_trial` for a task in any other state.
  - The ledger event is written before the queue. On the database store a decision goes through while a cycle holds the lock.

**Tests, RED → GREEN (PROVEN_AUTOMATED)**
- **RED first:** before the code, the module failed to import. With a stub module: 5 failed and 23 setup errors, because the old schema refused trial objects.
- **GREEN:** `test_team_trials.py` 30/30, run on both the file and the database store. Covers every acceptance item: listing both trials, oldu, olmadi with and without words, the 409 on a duplicate, the 404s, a decision while the lock is held, the ledger refusing the event (503, queue untouched), 401 without an owner session, the two schema copies identical, both trial forms valid and malformed objects refused.
- **Neighbouring suites:** 487 passed across 10 team test files (approvals, schema, state, office, proposals, speech, models, voice team status). ruff check and ruff format are clean.
- **PostgreSQL (PROVEN_AUTOMATED, real database):** `test_team_trials_postgres.py` passed, alongside the existing team approvals and team state tests (6 passed). I ran them on a new scratch database, `pagentos_it_trials`, in the dev container. The shared `pagentos` database is already at migration `0065_misheard_utterances`, which this branch doesn't have, so alembic stopped there. I left the scratch database in place for the inspector.

**Mutation proof** (restored from a backup copy each time; sha256 `17591477…46fc0` is the same before and after)
1. Remove the "words required for olmadi" rule → RED, `test_olmadi_without_the_owners_words_is_422…` fails on both stores.
2. Remove the duplicate-decision guard → RED, `test_a_second_decision_on_the_same_trial_is_409…` fails on both stores.

**Not done, and why**
- **NOT_RUN:** the full unit suite, the full gate, and the PowerShell `team-cycle` suite. That suite reads only the schema's states and required fields, which I did not change.
- **NOT_YET_PROVEN:** PROVEN_REAL needs the owner to press "Oldu" on one of the 38.3–38.5 sentences.
- **Schema limitation:** the trial definition has no `type`, because neither validator in the repo knows `anyOf`. Objects are fully checked and strings pass, but a number or null item is not refused by the schema. The code skips anything that isn't an object.

**For the lead at merge** (also in the ADR file)
- Add `team.trial.passed` and `team.trial.failed` to the ledger vocabulary. Until then the real route answers 503 `ledger_refused`.
- `scripts/lib/TeamRun.ps1:443` still reads trials as strings; it needs to read `.sentence`, `.machine` and `.expect`.
- The inspector's role text should write READY_FOR_OWNER lines as trial objects.
- The release step should move a released task with open trials to `awaiting_real_evidence`.
- Enter the 38.3–38.5 sentences as the first open trials.
- Old string trials are not listed in `trials`; the lead converts them.

**Open risks**
- On "olmadi" the original task is written first, then the fix task. If the second write fails, the verdict stands with no fix task, and the request returns 409 `stale_write` or 422 `invalid_task`.
- The Claude Google Drive and artlist connectors need authorizing in claude.ai settings. This task didn't use them.
