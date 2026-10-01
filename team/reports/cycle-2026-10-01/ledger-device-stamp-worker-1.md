**Worker report: ledger-device-stamp** (branch `team/cycle-2026-10-01/worker-ledger-device-stamp`)

**Şu an üzerinde çalışılan** (for the lead's HANDOFF): task `ledger-device-stamp`, area = the 4 app files + `test_ledger_device_stamp.py` + ADR text. I did not touch `docs/HANDOFF.md`.

- **sha:** `463d8043b6cc180641722d10cb5c073af03a9cf9`, pushed, worktree clean.
- **Files changed (6, all inside the area):**
  - `app/actions/receipt.py`
  - `app/operator/service.py`
  - `app/operator/mission_service.py`
  - `app/research/browser_activities.py`
  - `tests/unit/test_ledger_device_stamp.py` (new)
  - `team/plans/ledger-device-stamp-adr.md` (new)

**What each writer now stamps** (through `stamp_device`; no device means no stamp):
- **Receipt:** `record_receipt(..., *, device=None)`.
- **Operator task:** `start_task(..., device=None)` stamps the started/finished rows and the receipt.
- **Mission:** `_ledger` stamps the one word in `mission.device_targets`. Two words are already refused at start, so none means unstamped.
- **Research:** the quality-gate, provider-fallback, completed and failed rows take the run's device. That is its owner-set alias when it has one, else its name. A run that never got a device stays 'bulut'.

**Tests:** 16 in the new file, all mine, using the writers' own fakes and an in-memory SQLite.
- **RED before the change:** 13 failed, 2 passed. The 2 passing were the "no device" cases, which pass vacuously. Evidence class: PROVEN_AUTOMATED.
- **GREEN after:** 16 passed, three repeated runs, no flake.
- Per writer:
  - **Hits:** 'ev', 'ofis' and alias-less name → 'mail'.
  - **Near misses:** no device, blank device, and a run with no device or no run.
  - **No overwrite:** an existing stamp is kept.
- **Collector test:** rows produced by the receipt, mission and research writers. `collect("ofiste")` selects exactly the 3 office rows, 'evde' gets 1 and 'bulut' gets 1.
- **Adjacent suites green:** 295 passed across receipt, collector, operator (capability regression, b39, focus, heartbeat), spoken-alias and research browser activities. I ran the first set (121 tests) before the final `ruff --fix` import-order pass and the 295 set after it, but not the full unit corpus.
- **Lint:** ruff check and format are clean.

**Mutation proof:** 6 mutations (one per stamp site; operator and research have two), each RED on the first failing test. Each file was restored from a copy and its sha256 matched before and after; `git checkout --` was never used.

| File | Before = after sha256 |
|---|---|
| `receipt.py` | `37fdc47f1bcb2bd577a17ae06c837b2d2d1edb007fc1cb2e35050a2bdeb8475a` |
| `operator/service.py` (2 mutations) | `7f8cea7aa2dcf91b2cb6d4062500a854d4923ec40cae134bcf20fc85b4f1026d` |
| `mission_service.py` | `a43730350539e5c68f8cfcda395cbb549428ddbefe6b29412383d7bd0993ba6d` |
| `browser_activities.py` (2 mutations) | `88b789eb167df6de67eeec715672240dae29495ae2909fdd2495b7d0dbcf4142` |

**Bug found on the way:** `_record_provider_fallback` built its `ActivityEvent` without the required `source` and `source_ref`. The TypeError was swallowed, so that ledger row had never been written. I added `source="live"` and `source_ref=research_runs:{tid}:synthesis_fallback:{used}`. The new fallback tests act as its regression test.

**For the lead at merge:**
1. Number the ADR text in `team/plans/ledger-device-stamp-adr.md` and move it into `docs/DECISIONS.md`.
2. Only the operator path can pass `device` within my area, and nobody passes it yet. Real rows stay unstamped until the callers do.
   - **Files outside the area:** the `operator.start_task(...)` calls in `app/voice/realtime_sessions/tools_operator.py` (about 12) and `record_receipt` callers such as `voice/realtime_sessions/actions.py`.
   - **What to pass:** the bound device's alias or name. Missions and research already stamp themselves.
3. `_record_provider_fallback` now actually writes its row, so check that nothing expects the ledger to lack it.

**Could not do / open risks:**
- The research completed/failed paths (`persist_artifact_activity`, `fail_run_activity`) are covered only through the shared helper `_stamp_run_device` plus the real builder. I did not drive those activities end to end. The fallback writer is covered directly.
- The collector reads only completed and failed rows, so research provider-fallback rows (info status) are stamped but never counted by it.
- `canonical_device` folds a device name with no alias to its lowercase name, for example 'mail'. So "mail'de" would only match if the owner says the device name.
