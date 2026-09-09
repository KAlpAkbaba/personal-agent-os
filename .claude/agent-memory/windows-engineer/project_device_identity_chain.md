---
name: device-identity-chain
description: The agent version/capability identity chain end to end (AgentInfo -> hello -> apply_hello -> GET /v1/devices row -> installer verifier), the 2026-09-08 rollback it caused, and the gates that now hold it together
metadata:
  type: project
---

The chain a staged Windows-agent update is judged by, and where each link lives. Written after
2026-09-08, when a healthy candidate 0.6.0 was rolled back because one link was unowned
(ADR-0090).

**The chain, in order.**

1. `AgentInfo.SoftwareVersion` in `devices/windows-agent/src/PagentOS.Agent.Core/Protocol/ProtocolConstants.cs`
   — the ONE number. `devices/windows-agent/Directory.Build.props` `<Version>` must equal it;
   `AgentIdentityTests` fails the build otherwise. Bump both together.
2. The `capabilities` verb (`PagentOS.DeviceService/Program.cs`) prints `software_version`,
   `component`, `assembly_version`, `capability_manifest_version`, `display_power_enabled`,
   `browser_enabled`, `operator_enabled`, `capabilities`. It reads config from
   `appsettings.json` **and `PAGENTOS_AGENT_*` environment variables** — so any test can ask the
   built binary what it would advertise under any flag combination without writing a file
   anywhere. That is how `scripts/qualify-staged-update.ps1` proves `-DisplayPower` for real.
3. `hello.software_version` (`AgentConnectionOptions.SoftwareVersion`, same constant) →
   `app.broker.service.apply_hello` → `devices.software_version` column.
4. `GET /v1/devices` row. The canonical identity keys are at the TOP of the row and listed in
   `app.devices.types.DEVICE_IDENTITY_KEYS`. `health.software_version` carries the same value
   for the Cockpit.
5. `Test-AgentHeartbeatOnCore` / `Get-DeviceRowSoftwareVersion` in `scripts/lib/AgentUpdate.ps1`
   — reads the top-level key, falls back to `health.software_version` (so the installer works
   against a Cloud Core that has not been redeployed), and fails with an explicit "Cloud Core
   contract fault" when neither exists.

**Capability arithmetic (memorise, it appears in every install log).** `AgentCapabilities.Compose`:
Desktop 2 + Alarm 2 + Ambient 6 = 10 always; +1 for `desktop.display_off` behind
`DisplayPowerEnabled`; +29 for the browser family (1 marker + 28 operations) behind
`BrowserEnabled`; +operator/documents/projects/scenes behind `OperatorEnabled`. So
`-DisplayPower` + browser worker, no `-Operator` = **40**, and 39 without the flag. The old
production build was 29 (2 + 2 + 25). `capability_manifest_version` is a DERIVED SHA-256
fingerprint of the superset manifest — never hand-bump it.

**The two traps this incident was made of.**

- A fixture that invents the other side's shape proves nothing. `agent-update.tests.ps1` had a
  `New-Listing` helper that built a device row with a top-level `software_version` that
  production never emitted. Both suites were green for months.
  `services/api/tests/unit/test_device_identity_contract.py` now reads
  `scripts/lib/AgentUpdate.ps1` as text, and `AgentIdentityTests` reads
  `scripts/lib/InstallEvidence.ps1` as text, so the halves cannot drift alone.
- The deployment engine's rollback path re-runs a health predicate on the RESTORED previous
  release. Pass `Invoke-AgentDeployment -TestRollbackHealth` with a BASELINE predicate (answers
  its verb, advertises `desktop.open_application`, runtime up) — never the candidate's contract,
  which the old release predates by definition. Without it the log accuses a correct rollback of
  a capability regression.

**A gate that asserts an effect must be shown to fail when the effect is absent (2026-09-09,
ADR-0096).** `qualify-staged-update.ps1` gate 4 claimed "the browser worker proof was required
before the commit" while the proof was a counter the health handler incremented on itself, in a
sandbox that staged only `service` and `companion`. The installer really deploys
`-Components service,companion,browser -NonExecutableComponents browser`. Gate 4b now falsifies
the handler on purpose. Two reusable levers found doing it:

- `Test-LiveBrowserWorker` takes `-Processes` (Win32_Process shaped: ProcessId, Name,
  ExecutablePath, CommandLine, CreationDate), so the live-worker proof is drivable from a script
  with no python at all. `Get-ExpectedWorkerRelease` needs only a `browser_agent\worker.py` with
  a `WORKER_VERSION = "x.y.z"` line, and the module path must sit under `<BrowserRoot>\.venv`.
- **Never nest `.GetNewClosure()` inside a closure.** It copies only the CURRENT scope, so
  variables living in the enclosing closure's module scope arrive as `$null`. A plain script
  block keeps the session state and resolves them.

**`[hashtable]` silently copies an `[ordered]` dictionary** bound to it, so a function that
mutates the parameter mutates a copy. Use `[System.Collections.IDictionary]` for any recorder
that appends to a caller's ordered dictionary.

**Why:** these are the exact links that were each individually correct while the chain was
broken, and the arithmetic is what makes an install log readable at a glance.

**How to apply:** touching any version, capability name or device-row shape means running
`scripts\qualify-staged-update.ps1` (it needs only `dotnet build -c Release` first) plus
`services/api/tests/unit/test_device_identity_contract.py` and
`test_desktop_capability_mirror.py`. See [[windows-agent-gates]] for the tooling traps.
