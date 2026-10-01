**Inspector report: ledger-device-stamp. Verdict: APPROVE**

**Pass 1: ran it**
- **Venv check:** `app.__file__` resolves to the worktree. I used the main checkout's `.venv` from `services/api`.
- **New file:** `test_ledger_device_stamp.py` gives 16 passed, clean.
- **Wider subset:** `-k "research or ledger or receipt or narrative or operator or mission or collector"` gives 2511 passed, 1 skipped, 0 failed. That covers 11051 deselected tests unrelated to the change. I did not run the full unit corpus (11 min).
- **Lint:** `ruff check` is clean. `ruff format --check` is clean on the 5 touched files. The 12 reformat warnings in the wider directories are in files the diff doesn't touch.
- **Scope:** `git diff f13f438c..HEAD` touches exactly the 6 area files (4 writers, the test file, the ADR text). HANDOFF, DECISIONS, BUILD_STATE and THIRD_PARTY are untouched. No new dependency. The `main...HEAD` diff is larger only because the branch is behind `main`.
- **My own mutations (different from the worker's), each RED and restored, sha256 before = after, `git status` clean:**

| Mutation | Result |
|---|---|
| `receipt.py`: hard-code `"ev"` instead of `device` | RED, 1 failed on the first test |
| `operator/service.py`: drop `device=` on the `record_receipt` call | RED, 1 failed after 3 passed |
| `mission_service.py`: `device_targets[0]` replaced by `None` | RED, 1 failed after 6 passed |
| `browser_activities.py`: ignore the alias, use `device.name` | RED, 1 failed after 10 passed |
| `browser_activities.py`: `_stamp_run_device` never stamps | RED, 1 failed after 15 passed |

**Pass 2: adversarial**
- **Near misses:** the tests cover no device, a blank device, an existing stamp that is kept, and a run with no device or no run. A no-device row stays unstamped, which is 'bulut'. The worker's RED run showed 2 of the 15 cases passing vacuously; the hit cases and my mutations still fail correctly.
- **Collector:** the test runs receipt, mission and research rows through `collect()`. 'ofiste' gets exactly 3 rows, 'evde' 1, 'bulut' 1, which is the acceptance criterion.
- **Privacy/KVKK:** only a short device word goes into `detail_json`. No secrets or paths were added.
- **Contract drift:** `record_receipt` and `start_task` gained a keyword-only `device=None`, so existing callers are unchanged. No browser or device-protocol contract was touched.
- **Research lookup:** `_run_device_name` swallows every exception. Its broad `except` is a small risk on Postgres, where a failed query could abort the surrounding transaction. `get_run` and `get_device` are plain reads, so I rate this low.
- **Fallback fix:** `_record_provider_fallback` was silently failing because `source` and `source_ref` were missing. The fix is correct and the new tests cover it. I grepped nothing outside the test subset for code that expects that row to be absent. The 2511 tests that ran passed.

**Evidence class:** PROVEN_AUTOMATED, unit tests with the writers' own fakes on in-memory SQLite.
- **NOT_RUN:** the research completed/failed paths end to end. They are covered only through the shared helper and the real event builder.
- **READY_FOR_OWNER:** nothing yet. Real rows stay unstamped until the callers pass `device`.

**For the lead at merge**
1. Number the ADR text in `team/plans/ledger-device-stamp-adr.md` and move it into `docs/DECISIONS.md`.
2. Wire the callers (outside this area): about 12 `start_task` calls in `voice/realtime_sessions/tools_operator.py`, and the `record_receipt` callers such as `voice/realtime_sessions/actions.py`. Pass the bound device's alias or name. This is a follow-up task, and without it the feature does nothing on production data.
3. The collector reads only completed and failed rows. Stamped research provider-fallback rows (info status) are never counted by it.
4. A device with no alias folds to its lowercase name, for example 'mail'. "Mail'de" would match only if the owner says the device name.

APPROVE
