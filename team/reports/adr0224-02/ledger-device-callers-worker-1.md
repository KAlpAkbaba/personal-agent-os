**Report: ledger-device-callers**

**Şu an üzerinde çalışılan:** ledger-device-callers · area: tools_operator.py, actions.py, test, ADR · machine: worker worktree. I did not touch `docs/HANDOFF.md` (the lead writes it at merge).

- sha: `036a2ef95fe1fa439023914cfb6bd35200a31986`, pushed. Worktree is clean.
- Files changed: 3, all inside the area.
  - `services/api/app/voice/realtime_sessions/tools_operator.py`
  - `services/api/tests/unit/test_ledger_device_callers.py` (new)
  - `team/plans/ledger-device-callers-adr.md` (new)

**Change**
- One helper, `_bound_device_word(ctx, device_action)`.
  - If the sentence named a machine, it returns `device_action.targets[0]`.
  - Otherwise it returns the first alias (or the name) of `session_device_ids[0]`, read through the existing `_device_named`.
  - It returns `None` when nothing is bound or the device can't be read.
- It is passed as `device=` to all 10 `operator.start_task(...)` calls (the task says about twelve; the file has 10).
- It is also passed to the `record_receipt` inside `_receipt`, which is the one operator receipt writer.
- `actions.py` is unchanged on purpose. Its two `record_receipt` callers (the eye action and `release_promote`) have no device and are cloud-side, so they stay `bulut`. The ADR records this.

**Tests** (7, through the real `operator_app_open` / `operator_window_control` handlers, using the `FakeDeviceAction` from `tests.alarms_support` bound as the real view is)
- app_open rows carry `device == 'ofis'`, with the device bound by session id.
- A named `targets=('ofis',)` also gives `'ofis'`.
- The window action (maximize) gives `'ofis'`.
- An action receipt row gives `detail_json['device'] == 'ofis'`.
- With no bound device there is no `device` key and the tool still succeeds.
- A bound id that can't be read leaves no stamp.
- The collector's `ofiste` filter counts exactly the stamped rows: `counts_by_device` is `{'ofis'}` only.

**RED→GREEN:** I made the helper a stub returning `None` and ran the new tests first. The unstamped rows were `{'<none>'}` against the expected `{'ofis'}`, and `KeyError: 'device'` on the receipt. After implementing: 7 passed.

**Mutation:** inserting `return None` at the top of the helper gives 5 failed, 2 passed (the two no-stamp tests, as expected). I restored from a backup copy; sha256 before and after both start `62d46ed4bd6fa122`. I did not use `git checkout --`.

**Other runs**
- `test_ledger_device_callers.py` + `test_operator_app_open_named_device.py` + `test_ledger_device_stamp.py`: 33 passed (run before the ruff import re-sort).
- Re-run of the first two files after ruff `--fix` and `format`: 17 passed.
- ruff check and format: clean.

**Evidence class:** PROVEN_AUTOMATED for all claims above. PROVEN_REAL is NOT_RUN. It needs the owner to ask "ofiste ne yaptın" after an operator action on the office PC.

**Not run:** the full unit corpus (NOT_RUN); only the three related test files.

**Risks**
- I added the sed substitution to the call sites before writing the test. I then made a stub helper so the RED run was real, but the order was not strictly test-first.
- `_receipt` stamps whatever the bound port reports, including a session device that is not the one that actually ran the command. A `targets` entry names the intended machine rather than a confirmed one. This is acceptable because it matches how `_open_application_directly` already speaks.
- The ADR is unnumbered, so the lead numbers it and moves it into `DECISIONS.md`.
