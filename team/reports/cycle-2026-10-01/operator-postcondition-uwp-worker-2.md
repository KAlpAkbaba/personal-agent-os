**Verdict: DONE.** The fix is committed and pushed, and the tree is clean. Last time the report ended without a verdict; this one has it.

**Worker report: operator-postcondition-uwp**
- **sha:** c02fee81eacdd1fc7918ac03f7669c3e2a466c88. `git ls-remote` shows the pushed branch at the same sha.
- **Files changed:** 3, all inside the area: `services/api/app/operator/plans.py` (+21), `services/api/tests/unit/test_operator_plans_uwp.py` (+78, new) and `team/plans/operator-postcondition-uwp-adr.md` (+16, unnumbered).
- **HANDOFF:** the lead writes it at merge time, so I did not touch `docs/HANDOFF.md`.

**What changed**
- In `open_application`, a launched image whose window is hosted by `applicationframehost.exe` now passes when the window is foreground and its title is the app's own Turkish name or an alias.
- The names come from the existing allowlist contract through `_title_names_app`, so there is no second hand-written list.
- The B39 Settings branch still runs first and is untouched. The exact-image path for classic apps is unchanged.

**Tests (6 in the new file)**
- **Trial payload:** foreground, title "Hesap Makinesi", image `applicationframehost.exe`, pid different from the launched one, for a calc launch now passes. It was RED before the change, with the RED output kept.
- **Unrelated title:** the same payload with title "Ayarlar" fails for a calc launch.
- **Settings:** keeps its B39 behaviour, and a calc title on a settings launch still fails.
- **Classic app:** notepad.exe still needs its own image when the title is unrelated.
- **Fresh rerun now:** 6 passed, and `ruff check` is clean on both touched files (using the main checkout's venv).

**Mutation proof**
- My own mutation removed the title check (`return True`). The unrelated-title test went RED, and I restored from a backup copy with sha256 checked before and after.
- The inspector's mutation (`if app.image == image` changed to `if True`) gave 2 failed and 4 passed, and its restore sha256 matched.
- I did not use `git checkout --`.

**Evidence classes**
- **PROVEN_AUTOMATED:** all of the above.
- **NOT_RUN:** a real device run and the Temporal-backed mission tests.
- **READY_FOR_OWNER:** the owner says "Hesap makinesini aç" on MAIL after the release.

**Open risks**
- The fix assumes `app.launch` returns an `executable` ending in `calc.exe`. The trial log showed only `window.current`, and the tests use `calc.exe` as the fixture, so they cannot catch a different path. Before the release, check the real `app.launch` result for calc in production `device_commands`.
- Any allowlisted app, notepad included, now passes when a frame-host window carries its own name. "A classic app needs its own image" holds only for unrelated titles. I judge this low risk.
- `_title_names_app` compares images case-sensitively, so a mixed-case allowlist image never matches. The only such case is Settings, which the earlier branch handles, so there is no live defect. I did not widen the task to change it.
