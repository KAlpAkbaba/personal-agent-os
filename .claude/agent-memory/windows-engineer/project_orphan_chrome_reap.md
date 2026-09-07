---
name: orphan-chrome-reap
description: 2026-09-03 Chrome window cascade incident - how the companion/installer reap the PagentOS-profile Chrome, how it is tested with fake processes, and the non-obvious gotchas
metadata:
  type: project
---

Incident (2026-09-03): a killed Browser Worker left the PagentOS-profile Chrome running; each later launch on the locked profile opened another window in the orphan. Fix on branch worktree-agent-a51e2468919789b0c (`fix(m13/windows): reap orphan Chrome ...`).

- The reap rule lives in ONE place per side: `ChromeOrphanReaper` (companion, WMI `Win32_Process` via the `System.Management` 10.0.0 package - it was NOT in the NuGet cache, restore needs network) and `Select-OrphanBrowserProcess` / `Test-BrowserProfileCommandLine` in `scripts/lib/AgentRuntime.ps1`. Rule: image `chrome.exe`, no `--type=`, `--user-data-dir=` equal to the whole configured profile path. Keep the two in sync if either changes.
- Testing without Chrome: `ChromeOrphanReaper` takes an injectable image name; the tests launch the fake Browser Worker with `--no-hello` (blocks on stdin forever) and a `--user-data-dir=<dir>` argument, and reap by the fake's own image name. .NET `ArgumentList` quotes a whole argument (`"--user-data-dir=C:\a b"`), so the matcher handles that form as well as `--user-data-dir="..."`.
- The companion now logs to `<DataDir>\logs\companion.log` (FileLoggerProvider with `maxBytes`/`keepRotated`; the Device Service call is unchanged and unbounded). `FileLoggerProvider` must not expose a member named `Path` - it shadows `System.IO.Path` inside the class.
- `browser_lifecycle_violation` is forced non-retryable by the host regardless of what the worker says.

**Why:** the reap is the only thing standing between a dead worker and a desktop full of Chrome windows; a widened filter would kill the owner's own Chrome.
**How to apply:** any change to how the worker launches Chrome (Playwright `launch_persistent_context` -> `--user-data-dir=<profile>`) must keep the marker, or both reapers go blind; re-run ChromeOrphanReaperTests and installer-evidence.tests.ps1.
