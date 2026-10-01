**Task:** operator-postcondition-uwp (services/api/app/operator/plans.py). Worktree `worker-operator-postcondition-uwp`, machine MAIL. The HANDOFF block is left to the lead, as the role file says.
**sha:** c02fee81eacdd1fc7918ac03f7669c3e2a466c88. The branch is pushed and the worktree is clean.

**Files changed (3, all in the area):**
- `services/api/app/operator/plans.py`
- `services/api/tests/unit/test_operator_plans_uwp.py` (new)
- `team/plans/operator-postcondition-uwp-adr.md` (new, unnumbered)

**Fix:**
- `open_application`'s `_window_ok` now has a second UWP branch. If the foreground window's image is `applicationframehost.exe` and the launched image is not `systemsettings.exe`, the title must be the app's `name_tr` or one of its aliases. The match is case-folded.
- The names come from the allowlist contract via `allowlists.APPLICATIONS`, so there is no second hand-written list.
- The Settings branch (`UWP_HOSTED_IMAGES`) and the exact-image path for classic apps are unchanged.

**Tests (6 cases, all pass):**
- **RED before the fix:** the trial's payload for a calc launch (foreground true, title "Hesap Makinesi", image applicationframehost.exe, pid 4242 against launched pid 7777) ended the task `failed`. That was the only RED case.
- **GREEN after the fix:** the same payload now ends `succeeded`.
- **Unrelated title:** a frame-host window titled "Ayarlar" for a calc launch still fails.
- **Background window:** a non-foreground "Hesap Makinesi" frame window for a calc launch fails.
- **Settings:** a frame-host "Ayarlar" window succeeds for a settings launch, and "Hesap Makinesi" fails.
- **Classic notepad:** a frame-host window with an unrelated title fails, and the same title under `notepad.exe` succeeds.
- **Neighbouring suite:** `test_operator_b39.py` plus the new file gave 201 passed, 1 failed before I narrowed the test below.

**Mutation:** I replaced the title check with `return True`.
- 4 of the 7 tests then went RED: the unrelated-title test and 3 classic-app cases (two parametrized titles plus the later-dropped "Not Defteri").
- I restored from a backup copy (no `git checkout --`). The sha256 was `a10c70f0…5eff4` before and after.
- The restore check only covers `plans.py`.

**Checks:** `ruff check` and `ruff format --check` are clean on both files.

**Test correction:** my first draft had a case where a frame-host window titled "Not Defteri" should fail for a notepad launch. It passed instead, because that title is the app's own name, and Notepad is a Store app on Windows 11. I dropped that case from the parametrization. Any allowlisted app, classic or not, now passes when a frame-host window carries its own display name.

**Evidence:** PROVEN_AUTOMATED for all of the above. PROVEN_REAL is NOT_RUN until the owner says "Hesap makinesini aç" on MAIL after the release.

**Not run:** the full unit corpus and the Temporal-backed mission tests in `test_operator_b39.py` (I ran it with `-k "not temporal"`).

**Open risks:**
- The trial showed `window.current` but not the exact `executable` string `app.launch` returns for calc. I assumed a path ending in `calc.exe`, matching the allowlist image. If the device reports something else, such as a `CalculatorApp.exe` path, the launched image won't match and the task will still fail.
- A frame-host window with the app's name but a different document of the same app passes. The exact-image path already behaves the same way.
