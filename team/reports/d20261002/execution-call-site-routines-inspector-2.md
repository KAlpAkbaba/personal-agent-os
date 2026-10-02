## Inspector report — `execution-call-site-routines` @ `7a3a11ae` (second pass)

**Pass 1 — run (all waited for, tree clean, nothing left running)**
- **Unit:** `test_routines_execution_target.py` 30 passed. PROVEN_AUTOMATED.
- **PostgreSQL (dev stack, `pagentos-postgres`):** `test_routines_execution_target_postgres.py` 2 passed, not skipped. Afterwards 0 `execution`/`routine` ledger rows and 0 test devices remain. PROVEN_AUTOMATED.
- **Neighbour integration:** `test_routines_alarms_postgres.py` + `test_research_execution_target.py` 15 passed, 7 xfailed.
- **Neighbour unit files (16):** 391 passed in total, with one failure seen once. `test_alarms_wiring.py::test_the_alarm_path_from_the_owners_words_to_the_receipts` failed in a loaded 380 s run that crossed local midnight, then passed 2/2 (alone and in its file). I did not capture the failure text, so the cause is unknown and not attributed to this diff.
- **Lint:** `ruff check` and `ruff format --check` clean on the four area files.
- **Area:** the two task commits touch only the five area files. `dispatch.py` is `b3b46096…` and `target.py` is `13c1e78e…`, as reported.
- **Production wiring:** `app/main.py:289/482` hands the real `BrokerDeviceAction` to the one `ActionDispatcher`, so `scheduled()` is taken in production.
- **NOT_RUN:** full unit suite, mypy, `quality-gate.ps1` (13 other pytest processes on the machine; one 40-test file took 321 s). A real cloud-worker run is also NOT_RUN: none is enrolled on the dev broker.

**Mutations (restored from backup copies each time; sha256 back to `b3b46096`/`13c1e78e`)**

| Mutation | Result |
|---|---|
| M1: dispatcher does not take the scheduled view | RED, 8 failed |
| M2: probe ignores `targets` | RED, 5 failed |
| M4: `ledger_required=True` | RED, 1 failed |
| M5: payload url not passed | RED, 1 failed |
| A (card): browser-capability check removed | RED, 2 failed |
| B (card): refusal falls back to a machine | RED, 15 failed |
| M6: `_is_browser_capability` accepts any `browser.*` | **survives**, 30 passed |

**Pass 2 — findings**
1. **Acceptance line not met:** "a deny-listed url → refused `deny_listed_site`". The real rule selects the cloud for it, and `test_execution_wiring.py:200` (outside the area) asserts exactly that. Only the mapping is tested, with a stubbed decision. The card forbids touching `app/execution/`, so the worker cannot close this; the lead amends the card or opens a rule card.
2. **No off switch.** The research call site sits behind a setting that is off; this one is live at release, and `config.py` is outside the area.
3. **Routine `browser_action` will fail on the cloud in practice.** `browser_agent/worker.py:380-388` and `:1154` confirm the ADR: every operation needs a `session_id`, and it must be one opened on that worker. One operation per firing means only `session_open` can succeed there. A `selected target=cloud` row is therefore not proof of execution.
4. **Unverified ADR claim:** that production's cloud worker does not advertise `browser.navigate` until its image is rebuilt. `host-snapshot.json` (collected 2026-10-02 06:54Z, before the 11:30Z release, so stale) holds no device capabilities.
5. **M6 survivor, low.** A `browser.*` operation outside `BROWSER_ACTION_ALLOWLIST` on the scheduled view would go to a machine via `_select_for`. It is unreachable today because the dispatcher refuses such an action first and `main.py` passes no custom allowlist; no test pins it.
6. **Ledger-failure path is SQLite-only.** The rollback-and-send path is proven with a missing table on SQLite, not on PostgreSQL. PROVEN_PROXY for PostgreSQL.
7. **`media_playback` stays outside the rule**, with a test and an open question in the ADR. Owner/lead decision.
8. **Clean on:** secrets, paths, contract drift, schema or migration changes, KVKK (the ledger rows carry no url; the log line carries only the alias word). Rollback is a revert of two commits.

**Evidence classes**
- Routing, refusal and ledger rows: PROVEN_AUTOMATED (unit + dev-stack PostgreSQL).
- "The cloud worker executes it": NOT_RUN.
- First production routine with a cloud row and a succeeded command: READY_FOR_OWNER.

**Lead, before release (none is the worker's to fix inside the area)**
- Count production routines whose action kind is `browser_action`.
- Decide on a setting (finding 2) and on the deny-list line (finding 1).
- Collect a fresh host snapshot.
- Run the full gate on the integration branch.

APPROVE
