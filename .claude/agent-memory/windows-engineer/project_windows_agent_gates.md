---
name: windows-agent-gates
description: Non-obvious gate behaviour for devices/windows-agent and scripts/ - CRLF via dotnet format, PS5.1 lints (bare .Count, $home), worktree branch may lag main
metadata:
  type: project
---

Gate facts for Windows-agent work that cost a round trip each the first time (2026-09-03, M13 track W):

- `dotnet format --verify-no-changes` is a README gate and the repo's .editorconfig wants CRLF. Files created with the Write tool are LF, so every new .cs file fails with `ENDOFLINE`. Run `dotnet format PagentOS.WindowsAgent.sln --no-restore` once before verifying; it also strips stray BOMs in untouched files (PipeTests/IpcWiringTests had them).
- `scripts/tests/installer-strictmode.tests.ps1` lints install-device-service.ps1 AND verify-device-service.ps1 for bare `$x.Count` (must be `@($x).Count`); `$home` is a read-only automatic variable in PS 5.1 (use `$venvHome`). `scripts/quality-gate.ps1` enumerates test scripts explicitly, so a new scripts/tests/*.tests.ps1 is NOT picked up by the gate until quality-gate.ps1 lists it.
- Referencing an Exe project from the test csproj copies its apphost `.exe` beside the tests (verified on SDK 10.0.400), so a fake worker process can be a plain console project and spawned by path.
- A fresh agent worktree can be behind `main`; `git merge --ff-only main` inside the worktree is allowed by the isolation guard, `git -C <main checkout>` is not.

**Why:** each of these produced a red gate that looked like a code problem but was tooling.
**How to apply:** before reporting a Windows-agent change, run dotnet format (fix mode), then verify, then the six installer PS suites with the absolute powershell.exe path.
