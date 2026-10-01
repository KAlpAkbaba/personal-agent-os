**Inspector report — operator-postcondition-uwp (branch at c02fee81)**

**Pass 1 — run it**
- The worker's file, run fresh: 6 passed. The venv is the main checkout's, but `app.__file__` resolves to this worktree, so it tested this tree's code.
- Broader operator corpus (`-k "operator and not temporal"`): 1107 passed, 0 failed. The worker's "201 passed, 1 failed" on the neighbouring suite is gone; its one failure was the dropped case.
- `ruff check` is clean on both touched files. I did not run `ruff format --check`.
- My mutation was different from the worker's. I changed the `if app.image == image` filter in `_title_names_app` to `if True`, so any app's name would satisfy any launch.
  - RED: 2 failed, 4 passed. The failures were the "Ayarlar title for a calc launch" test and the classic-notepad case with title "Hesap Makinesi".
  - I restored from a backup copy. The sha256 of `plans.py` is `a10c70f0…5eff4` before and after, and the tree is clean.
- I did not repeat the worker's `return True` mutation, which goes RED on the unrelated-title test by construction.

**Pass 2 — break it**
- **Diff scope:** `git diff main...HEAD` lists 18 files, but `main` has moved on, so that count includes the lead's unrelated commits. The worker's own commit touched only the 3 area files.
- **Contract drift:** none. The names come from `operator-allowlists.json` via `_allowlists.APPLICATIONS`. There is no second list.
- **Settings:** the B39 branch runs first and is untouched. A calc title on a settings launch still fails.
- **Unrelated title:** a foreground frame-host window titled "Ayarlar" fails for a calc launch.
- **Classic-app claim is weaker than the task card says.** After this change, any allowlisted app, including notepad, passes when a frame-host window carries its own name. The worker disclosed this, and the card's "classic app needs its own image" now holds only for unrelated titles. It is low risk, but it is a real semantic loosening.
- **`_title_names_app` compares `app.image == image` case-sensitively.** The launched image is lower-cased and the allowlist image may be mixed-case, for example `SystemSettings.exe`. Such an entry would never match, but Settings is handled by the earlier branch, so there is no live defect.
- **Main residual risk, unverifiable here:** the fix depends on `app.launch` returning an `executable` that ends in `calc.exe`. The trial log showed only `window.current`, not the launch result. If the device returns a different path, the launched image is wrong and the task still fails with "açamadım". The worker flagged this too. The tests use `calc.exe` as the fixture, so they cannot catch it.
- **A frame-host window with the right title but the wrong pid passes.** That is intended, and it is the trial's exact scenario.
- **Privacy and resources:** no secrets, paths or logging added. The added code is a cheap linear scan of a small list.
- **ADR:** `team/plans/operator-postcondition-uwp-adr.md` exists and is unnumbered. I did not review its content.

**Evidence classes**
- PROVEN_AUTOMATED: the trial payload now passes, unrelated and background windows fail, Settings and classic behaviour are pinned, and the mutation goes RED.
- NOT_RUN: a real device run and the Temporal-backed mission tests.
- READY_FOR_OWNER: the owner says "Hesap makinesini aç" on MAIL after the release. Before then, check the real `app.launch` `executable` string for calc in production `device_commands`.

**Verdict:** APPROVE
