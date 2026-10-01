Inspector report, task `ledger-device-callers`, branch `team/adr0224-02/worker-ledger-device-callers` (HEAD 036a2ef9).

**Pass 1: run it**
- The three related files (`test_ledger_device_callers`, `test_operator_app_open_named_device`, `test_ledger_device_stamp`) give 33 passed.
- The wider unit selection `-k "operator or realtime or receipt or ledger or narrative"` gives 1670 passed, 0 failed (12175 deselected).
- ruff check and `ruff format --check` are clean.
- My own mutation was different from the worker's. I made `_bound_device_word` ignore `session_device_ids` (`ids = ()`). The result was 4 failed, 3 passed. The failures were the app_open, window, action-receipt and collector-filter tests. The 3 passes are the targets-named test and the two no-stamp tests, as expected.
- I restored from a backup copy. sha256 before and after both start `62d46ed4bd6fa122`, and the working tree is clean.
- The change adds no table, migration or store. The device word goes into the existing `detail_json`, so there is no PostgreSQL surface and no integration test is owed.

**Pass 2: break it**
- `grep` finds all 11 `operator.start_task(` calls in `tools_operator.py` passing `device=_bound_device_word(...)`. The worker's report says 10; the real number is 11, all covered. There are no other `start_task` callers in `app/`.
- The only operator receipt writer, `_receipt`, is stamped.
- `actions.py` is unchanged. Its two `record_receipt` callers are `SUBSYSTEM_PRESENCE` (the eye action) and `SUBSYSTEM_DEPLOYMENT` (`release_promote`). Both are cloud-side with no bound device, so leaving them unstamped is right. The ADR records this.
- `tools_mission.py:126` writes a `SUBSYSTEM_OPERATOR` receipt with no device. It is a cloud-side mission row, so I leave it unstamped. It is outside the task area.
- The helper returns `None` when nothing is bound or the device can't be read. In that case there is no `device` key and the tool still succeeds; this is tested.
- Privacy: only the owner's own alias or device name is stamped. I found no secrets or paths.
- Files stay inside the area, and there is no contract drift.

**Findings**
1. Minor: only `app_open`, the window action and the receipt are tested through real handlers. The other 9 call sites (type, shell, close, process, service, key, pointer, ui, inspect) share the same pattern but have no test. If one of those calls lost `device=`, nothing would fail. This does not block the merge, but a table-driven test over all handlers would close it.
2. As the worker noted, a `targets` entry stamps the intended machine rather than a confirmed one. This matches `_open_application_directly`. I accept it.
3. The worker admits the change was not strictly test-first. They stubbed the helper to get a real RED, which is acceptable.
4. The ADR in `team/plans/` is unnumbered, so the lead numbers it and moves it into `DECISIONS.md`.

**Evidence classes**
- PROVEN_AUTOMATED for the stamping on the three paths, the no-device path and the collector's `ofis` filter.
- NOT_RUN: the full unit corpus and the full `quality-gate.ps1`. I ran the targeted selection above, not the whole gate.
- NOT_RUN: PROVEN_REAL. It needs the owner to ask "ofiste ne yaptın" after an operator action on the office PC.

APPROVE
