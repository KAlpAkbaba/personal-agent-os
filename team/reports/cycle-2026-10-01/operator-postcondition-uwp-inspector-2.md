**Inspector report: operator-postcondition-uwp** (branch at c02fee81, 3 files, all inside the area)

**Pass 1: I ran it myself, using the main checkout's venv**
- I confirmed pytest imported this worktree's `plans.py` and not the copy the main checkout's venv is installed from.
- `test_operator_plans_uwp.py`: 6 passed.
- Every operator unit test (`-k operator`): 1107 passed, 0 failed.
- `ruff check` and `ruff format --check` are clean on both files. mypy is not installed in that venv, so NOT_RUN.
- **RED before the fix:** with the parent commit's `plans.py`, the trial-payload test fails (1 failed, 5 passed). The regression is real.
- All three mutations were restored from a backup copy. The sha256 was a10c70f0…eff4 before and after, and `git status` was clean. I did not use `git checkout --`.
  - **M1, the title check replaced by `return True`:** 3 failed. These were the unrelated-title test and both classic-app cases. This is the mutation the acceptance asks for, and it goes RED.
  - **M3, name_tr and casefold dropped from the match:** 1 failed, the trial test.
  - **M2, the frame-host branch moved above the B39 branch:** survived, 6 passed. This is not a test gap. Settings is itself in the allowlist (`name_tr` "Ayarlar", aliases ayarlar/settings), so the new path gives the same verdict for every title in `UWP_HOSTED_IMAGES`. The only difference is that it is more lenient on case and on "windows ayarları", and the order in the code protects exactly that.
- **The worker's open risk 1 (what `app.launch` returns for calc):** this is settled from the device source. `OperatorCapabilities.cs:142` resolves calc to `%WINDIR%\System32\calc.exe`, and `AppLaunch` returns that path as `executable` (line 280). So `launched.image` is `calc.exe`, which matches the test fixture. Evidence class: PROVEN_PROXY, from source only; I did not see the real payload.
- **The worker's open risk 3 (case-sensitive image comparison):** this is not a defect. `allowlists.py:49` lowercases the image on load and `_executable_name` lowercases the other side, so the claim in the report is wrong but harmless.

**Pass 2: attempts to break it**
- **Contract drift:** none. The names are read from `operator-allowlists.json` through `allowlists.APPLICATIONS`, and the package copy and the bundle copy are identical. There is no second hand-written list, as the card required.
- **Tests passing for the wrong reason:** no. The tests drive the real `open_application` plan through `run_task`, with a fake device and a window pid (4242) different from the launched pid (7777). Expected and actual do not come from the same source.
- **The loosening is bounded:** a frame-host window passes only when it is in the foreground and its title exactly equals, after casefolding, a name of the launched app. A substring does not count. A calc launch with the title "Ayarlar" fails. A notepad launch with the title "Hesap Makinesi" fails. A background calculator window fails.
- **Accepted residuals, low risk:**
  - An old calculator window already in the foreground would satisfy a failed relaunch. The same is already true on the exact-image path, and Calculator runs as a single instance anyway.
  - The English title "Calculator" passes, which is correct.
- **Scope, secrets and safety:** no files outside the area, no secrets or paths in code, no KVKK leak into audit or logs, and no resource cost on CPX32 (one tuple scan over 8 apps). Rollback is reverting one commit.
- **Not checked:** the full `quality-gate.ps1`, because this is a task branch, not the integration branch. It runs at integration.
- **No real device run:** the fix needs the owner's MAIL device with the release deployed, so NOT_RUN.

**Evidence classes**
- **PROVEN_AUTOMATED:** the postcondition fix and all four acceptance cases: the trial payload passes, the "Ayarlar" title fails for calc, Settings keeps its B39 behaviour, and a classic app still needs its own image.
- **PROVEN_PROXY:** the device returns the `calc.exe` executable.
- **READY_FOR_OWNER:** the owner says "Hesap makinesini aç" on MAIL after the release. The QUALIFICATION row 30.10 note (b) closes only on that.
- **NOT_RUN:** mypy, the full gate, and a real device run.

**APPROVE**
