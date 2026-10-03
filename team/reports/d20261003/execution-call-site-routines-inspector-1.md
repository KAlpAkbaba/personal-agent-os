I'm approving this task. The unit tests, the dev-stack PostgreSQL integration tests and two mutations of my own all came out as expected. Two departures from the card rest on rulings the ADR attributes to you, which I could not check.

**Pass 1 — run it** (worktree clean, `git diff main...HEAD` = only the 6 area files)
- **Unit:** `test_routines_execution_target.py` + `test_routines_dispatch.py` + `test_execution_wiring.py` gave **91 passed** in 180 s.
- **Integration on dev-stack PostgreSQL:** I ran it on a fresh scratch database, `pagentos_scratch_insp_routines`. The shared dev database is at alembic `0065`, which this branch lacks; I copied no migration in, and dropped the scratch database afterwards.
  - Both files together: **13 passed, 7 xfailed**.
  - All 4 tests in `test_routines_execution_target_postgres.py` passed: the cloud is selected and PostgreSQL holds the row; cloud down means nothing is sent and PostgreSQL holds the refusal; with the setting off the real app sends where main does and writes no row; with it on the real app sends to the cloud and writes the row.
  - The 9 routine/alarm PostgreSQL tests still pass, alongside the 7 xfails.
- **My own mutations** (not the worker's; each restored from a backup copy, sha256 matches the original):
  - M-a: `_ScheduledDeviceAction.selection_for` passes `scheduled=False` → **RED**, 8 probe==run parametrizations plus the probe test.
  - M-b: `_spoken` drops the "a machine word wins" priority → **RED** on `[("bulutta","ev")]`.
  - Run together, 10 failed / 27 passed. Files: `dispatch.py` f02c380a…4d3b, `target.py` 13c1e78e…dd23.
- **Lint:** `ruff check` and `ruff format --check` are clean on the 5 code and test files. The diff adds no secrets and no machine paths.
- **Not run (machine load):** a neighbour slice of 6 files and then a slice of 3 files (research call site, session affinity, spoken alias) both hit my 590 s timeout without a summary. Four full unit suites from other seats were running at the same time. I checked that I left no python processes behind. The full `quality-gate.ps1` and mypy (no module in the venv) were also not run.
- **Host snapshot rule:** does not apply. Nothing under `scripts/cloud`, `infra/docker` or migrations is touched.

**Pass 2 — break it**
- **Shared port is safe.** The rule is asked only through `BrokerDeviceAction.scheduled()`, and only `ActionDispatcher._browser_action` takes that view. Alarms, the operator, voice and media keep `_select_for`. A refusal returns before `_select_for`, so a scheduled browser action never falls back to a machine.
- **M6 is fixed, not open.** The worker lists it as still open, but `_is_browser_capability` now also requires `operation in BROWSER_ACTION_ALLOWLIST`.
- **Departure 1 — off by default.** `routines_execution_rule_enabled=False`, attributed to a lead ruling of 2026-10-03. Merging changes nothing in production, and the card's goal is not live until the owner turns it on. Please confirm the ruling is yours.
- **Departure 2 — deny-list acceptance withdrawn.** The ADR says you withdrew "deny-listed url → deny_listed_site". It also shows the rule outside the area (`test_execution_wiring.py`) selects the cloud for a scheduled read, so the refusal mapping is tested only against a stubbed decision. Please confirm that ruling too; whether scheduled reads of deny-listed sites should be refused is a separate card in `app/execution/`.
- **Open owner question: `media_playback`.** Leaving it outside the rule is documented and argued: the cloud worker has no display or audio.
- **Note — the `selected` row is not proof the action ran.** The ADR says, without softening, that a `browser_action` without a cloud-opened session fails on the worker. So the card's PROVEN_REAL criterion ("the first ledger row target=cloud") is not enough. PROVEN_REAL needs that row plus a succeeded cloud command for the same firing.
- **Note — routine names a device.** A routine that names a device for a browser action is refused (`forced_target_not_allowed`). The ADR records this, though no routine field names a device today.
- **Note — `selection_for` ignores the url.** The probe is called without the payload's url, so it cannot see a url-based refusal. It is consistent today, because the rule's answer for a scheduled read does not depend on the url.
- **Note — ledger-failure path.** "A ledger that cannot be written does not stop the action" is proven only on SQLite.

**Evidence:** PROVEN_AUTOMATED (unit + dev-stack PostgreSQL). Production run: READY_FOR_OWNER — it needs the setting on and a succeeded cloud command, not just the row.

APPROVE
