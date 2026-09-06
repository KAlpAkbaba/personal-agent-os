---
name: windows-agent-gates
description: Non-obvious gate and structure facts for devices/windows-agent and scripts/ - LF line endings (not CRLF any more), Bash-tool refusals for computed dotnet paths, PS5.1 lints, where the audio fake boundary already is, and that AgentCapabilities.Desktop is locked
metadata:
  type: project
---

Gate facts for Windows-agent work that cost a round trip each the first time (2026-09-03, M13 track W):

- `dotnet format --verify-no-changes` is a README gate. The repo's root `.editorconfig` now says `end_of_line = lf` (verified 2026-09-07; it used to want CRLF, which cost a round trip in M13). Files created with the Write tool are LF, so new `.cs` files pass as written - do NOT convert them to CRLF. `.gitattributes` still checks `*.ps1` out as CRLF, so a script rewritten through Python must be written back with CRLF or the working copy drifts (git renormalises on commit, so the diff hides it). If a format run is ever needed: `dotnet format PagentOS.WindowsAgent.sln --no-restore`; it also strips stray BOMs.
- The Bash tool refuses a command whose executable name is computed (`"/c/Program Files/dotnet/dotnet.exe"` in a quoted path counts) or whose `sed` argument comes from `$(...)`. Run dotnet through the PowerShell tool with the absolute path `& "C:\Program Files\dotnet\dotnet.exe"`; its output is Turkish (`Hata`=error, `Uyarı`=warning, `Başarılı`=passed, `Başarısız`=failed), so filter with `Select-String` on those words too.
- `scripts/tests/installer-strictmode.tests.ps1` lints install-device-service.ps1 AND verify-device-service.ps1 for bare `$x.Count` (must be `@($x).Count`); `$home` is a read-only automatic variable in PS 5.1 (use `$venvHome`). `scripts/quality-gate.ps1` enumerates test scripts explicitly, so a new scripts/tests/*.tests.ps1 is NOT picked up by the gate until quality-gate.ps1 lists it.
- Referencing an Exe project from the test csproj copies its apphost `.exe` beside the tests (verified on SDK 10.0.400), so a fake worker process can be a plain console project and spawned by path.
- A fresh agent worktree can be behind `main`; `git merge --ff-only main` inside the worktree is allowed by the isolation guard, `git -C <main checkout>` is not.
- The isolation guard also refuses a Bash call containing a shell `for` loop (and heredocs / `&&`-chained cd); one plain `cd <worktree> && <single command>` per call works, so run build, each test pass and format as separate calls.
- The fake Browser Worker serialises requests per `session_id` (contract §7); a host/dispatch test that wants two requests to overlap must give them different sessions or it queues and the `IsCompleted` assertions invert.
- Any companion capability that needs to make a SOUND already has a fake boundary: `PagentOS.SessionCompanion` references `PagentOS.Companion.Audio`, so `IAudioDeviceFactory` / `IAudioPlayback` and the `FakeDeviceFactory` / `FakePlayback` / `ManualTimeProvider` triple are available to companion code and to `PagentOS.Agent.Tests`. Do not invent a second audio seam - wrap or reuse these, and no test ever opens WASAPI (used only from `Program.Main` and `VoiceCompanionHost`).
- `AgentCapabilities.Desktop` and `.All` are locked to the M1/M3 `open_*` pair by assertions in `BrowserDispatchTests`; every later family is added through `AgentCapabilities.Compose(...)` only. Add a new interactive capability to its own list plus `IsInteractive`, never to `Desktop`.

**Why:** each of these produced a red gate that looked like a code problem but was tooling.
**How to apply:** before reporting a Windows-agent change, run dotnet format (fix mode), then verify, then the six installer PS suites with the absolute powershell.exe path.
