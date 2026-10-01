I've inspected the allowlist-editor branch: the 25 tests pass, but the owner's list does not yet reach the worker. That is a merge-time wiring item, not a defect in the branch.

**Pass 1: run it**
- **Tests:** I re-ran `test_allowlist_editor.py` from `services/api` with the main checkout's `.venv`. `app.__file__` points into the worktree. Result: 25 passed.
- **Worker's mutations, re-done by me:** with the deny check removed, `test_a_deny_listed_site_is_refused_even_when_asked_twice` goes RED. With the suffix check removed, three suffix tests go RED (`com.tr`, `co.uk`, `co.example`).
- **My own mutations** (files restored from backup, sha256 checked):

| Mutation | Result |
|---|---|
| Subdomain `.` boundary dropped from `acting_allowed` | RED |
| Owner-session dependency removed from the router | RED |
| Deny filter on owner rows removed from `entries()` | RED |
| Duplicate-add guard removed | RED |
| IDNA normalisation removed | still 25 passed (survives) |

- **Restore:** `allowlist_store.py` is back to `fa86f273…c36a5`. `allowlist_routes.py` is back to `2dfe7173…3c51`. The 25 tests are green again and `git status` is clean.
- **Neighbouring suites:** ruff is clean on the three Python files. A run of `-k "allowlist or team_ or webtask or cloud"` stopped at its first failure with 303 passed: `test_team_state.py::test_the_schema_the_store_validates_with_is_the_schema_the_protocol_holds`. It compares `app/team/queue.schema.json` with `team/queue.schema.json`. This branch changes neither `app/team/` file, and `team/queue.schema.json` (changed 7 lines on the branch base, which carries other commits) differs from the served copy. So it isn't caused by this task, but the lead must sync `app/team/queue.schema.json` at merge. I stopped at that first failure, so the rest of that selection did not run.
- **Not run:** the full gate, and web `tsc`, `oxlint` and `vitest`, because the worktree has no `node_modules`. The page was not run in a browser.

**Pass 2: break it**
- **IDNA not covered:** the IDNA line in `_normalise` has no test, so a Turkish-letter domain is unproven either way. Low severity.
- **Unwired store, silent ignore:** until `main.py` binds the store, `acting_allowed` answers from the seed only, which is safe by default. But the worker's job still gets only the seed, so nothing the owner adds takes effect until merge step 3 is done. Without it, the feature looks finished and does nothing.
- **Ledger vocabulary:** the two event types are not in the closed vocabulary yet. Until the lead adds them, every POST and DELETE returns 503. The tests inject the vocabulary to get around this. One test covers the refused case. The worker's merge item 4 covers it.
- **Suffix heuristic:** this is not the real Public Suffix List, as the worker admits. A made-up suffix such as `blogspot.example` passes. It errs conservative elsewhere, and the owner session gates who can add.
- **Ordering:** the event is recorded before the row, and a ledger refusal leaves no row. A crash between the two leaves an event with no row. That is the safe direction.
- **Privacy, contract, secrets:** only `{site, actor, channel}` goes into the ledger, which is no personal data. I found no secrets or paths, no contract drift, and no edits outside the area (6 files; the rest of the branch diff is base-branch churn).
- **Unproven:** `DELETE /{site}` with an encoded slash or dot, and the row-count growth on CPX32. Neither is a concern for a list this small.

**Evidence classes**
- **PROVEN_AUTOMATED:** the API rules and the store contract. These are the routes (add, list, remove, 401 without a session), the deny and suffix refusals, `acting_allowed` following the store, and the ledger event per change.
- **READY_FOR_OWNER:** the page. It has had no web gates and no browser run.
- **NOT_RUN:** the full gate and the web gates.

**For the lead at merge** (the worker's six items hold; I add one)
- Sync `app/team/queue.schema.json` with `team/queue.schema.json`.
- After wiring the store and the worker dispatch, run a real POST followed by a job against the dev stack.

`APPROVE`
