**Worker report: allowlist-editor** — sha `d50b632c8385a50876838262a76fe89915355ad1`, branch pushed, worktree clean.

**Şu an üzerinde çalışılan (for the lead's HANDOFF):** allowlist-editor · area: allowlist_store, allowlist_routes, test, web page `approvals/allowlist`, ADR text · machine: worktree `worker-allowlist-editor`.

**Files:** 6 changed, all inside the area. Both new Python modules, `test_allowlist_editor.py`, the web page `page.tsx` and `allowlistApi.ts`, and `team/plans/allowlist-editor-adr.md`.

**Design**
- The owner's sites are `team_state` rows with `kind="allowlist"`, one row per domain. No migration is needed, so there is no 'For the lead' migration text.
- The shared JSON stays the seed. `allowlist_store.acting_allowed()` merges seed and rows on every call, so an add or remove applies at once.
- A process that never called `bind()` answers from the seed only. Deny-listed rows are ignored even if written behind the editor.
- Routes: `GET` and `POST /v1/team/allowlist`, `DELETE /v1/team/allowlist/{site}`, all owner-session-gated.
- Refusals are 422 with `empty_site`, `not_a_registrable_domain`, `bare_public_suffix` or `deny_listed_site`. A second add of a listed site returns `already_listed` and writes no second event.
- A seed site cannot be removed here (409 `seed_site`); an unlisted one gives 404.

**Two ledger event types (for the lead to add to the vocabulary):** `allowlist.site_added` and `allowlist.site_removed`. Subsystem `team` already exists. The event is recorded before the row, so a ledger refusal is a 503 `ledger_refused` and writes nothing.

**Tests** (`test_allowlist_editor.py`, 25 tests):
- **RED then GREEN:** RED was a collection error because the modules did not exist (evidence class PROVEN_AUTOMATED). GREEN is 25 passed, run from `services/api` with the main `.venv`; `app.__file__` points into the worktree.
- **Coverage:** add, list and remove. 401 on all three verbs without a session. A deny-listed site refused twice, with no row and no event. Bare suffixes refused. `acting_allowed` follows the store. Seed and rows merged. The ledger event per change. A ledger refusal leaves no row.
- **Near misses:** `co.com`, `web.com.tr` and `magaza.com.tr` are accepted, `evilmagaza.com.tr` is not allowed, and a subdomain is refused.

**Mutations** (file restored from a backup copy; sha256 before and after both `fa86f2732bc4358538f576175b64edd5fc082e2fd9851ff7bb84ae5c813c36a5`; 25 passed after restore):
- Deny check removed from the editor: RED, `test_a_deny_listed_site_is_refused_even_when_asked_twice`.
- Suffix check removed: RED, three suffix tests (`com.tr`, `co.uk`, `co.example`).
- Extra: deny-first removed from the store's `acting_allowed`: RED, `test_the_deny_list_wins_over_a_row_written_behind_the_editor`.

**Checks:** ruff check and format are clean on the three Python files.

**For the lead at merge**
1. In `app/main.py`: `include_router(allowlist_router)` from `app.team.allowlist_routes`. Also call `allowlist_store.bind(app.state.artifacts.session)` at startup. Without it, only the seed is read until the first editor request binds the store.
2. In `app/execution/wiring.py:110`: `_acting_allowed` still imports `allowlist.acting_allowed`. Point it at `allowlist_store.acting_allowed`.
3. In the dispatch that sends a job to the worker: pass `allowlist_store.effective_sites()` with the job. The worker's verbatim copy is only the seed, so this is what makes the owner's list reach it. I did not do it (outside the area).
4. Add the two event types to `app/ledger/vocabulary.py`, and add a constants-held-equal test like the approvals one.
5. `docs/DECISIONS.md`: number the ADR text in `team/plans/allowlist-editor-adr.md`.
6. Optionally link the page from the Onay Merkezi page; I did not touch it.

**Not done**
- NOT_RUN: web `tsc`, `oxlint` and `vitest`. The worktree has no `node_modules`, and I did not touch the main checkout. The page is READY_FOR_OWNER only after the lead runs the web gates.
- NOT_RUN: the broader unit suite, `test_cloud_allowlist` and `test_team_*`. I ran only my file, and ruff.

**Open risks**
- The suffix rule is a heuristic, not the full Public Suffix List. A two-label name whose first label is one of the 14 second-level words in the `_SECOND_LEVEL` table (`co`, `com`, `org`, …) under an unlisted gTLD is refused. An unknown public suffix with a different first label, for example `blogspot.example`, passes. `site_of` also refuses some real three-label domains, which errs on the conservative side.
- Listing a site is shell-only; there is no voice channel for it.
