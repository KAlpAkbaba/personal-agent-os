---
name: m19-digital-operator-security-review
description: Findings from the M19 Digital Operator device-side review (branch m19-agent, commits 7ad13e3/87f75fb, merged into main 2026-09-07); High - OperatorRoots junction escape; Medium - focus-guard batch race on keyboard.type, unrestricted app.launch args
metadata:
  type: project
---

Reviewed `git diff 338ffec..m19-agent` (devices/windows-agent Operator/ module, InteractiveCapabilityExecutor,
CompanionRuntime, broker frames.py, device-protocol.schema.json). See [[junction-escape-recurring-pattern]].

**High — OperatorRoots path confinement is a lexical string-prefix check, not filesystem-resolved; an NTFS
junction inside the root escapes it.** `TerminalRunner.IsUnderAuthorisedRoot`
(`devices/windows-agent/src/PagentOS.SessionCompanion/Operator/TerminalRunner.cs:241-259`), used by both
`RequireAuthorisedPath` (`file.open`/`file.reveal`, OperatorCapabilities.cs:1210-1245) and the `<path>` token
in `terminal.execute`'s allowlist matcher, does `Path.GetFullPath(path).StartsWith(normalisedRoot)` — purely
lexical, never resolves reparse points. Verified live: created a junction (`New-Item -ItemType Junction`, no
admin needed) inside a simulated authorised root pointing at a directory outside it; `Path.GetFullPath` +
`StartsWith` returned `true` ("under root") and `File.ReadAllText` through the junctioned path returned the
outside file's content. `OperatorOptions.DefaultRoots()` is the owner's **entire user profile**, which
includes Downloads — exactly the directory the existing `browser.download` (M13) capability writes
attacker-influenced files into. Chain: a downloaded item (or anything else landing in Downloads) that is
itself a junction to an arbitrary directory turns "confined to the owner's profile" into "confined to
nothing" for `file.open`, `file.reveal` and `Get-ChildItem <path>` in `terminal.execute`. `file.open`
blocks executable extensions on the leaf but nothing stops reading arbitrary non-executable files (SSH keys,
browser cookie DBs, config) outside the intended root; `file.reveal` has no extension check at all.
**Fix:** resolve the real path first (`GetFinalPathNameByHandle` / open the file and check the handle's final
path) THEN check containment, the same fix already used elsewhere in this codebase
(`remediation.py::_authorized_file`, per [[junction-escape-recurring-pattern]]) — or refuse outright when any
path component is a reparse point.

**Medium — the focus guard is verified once per call, not once per SendInput batch, so a large
`keyboard.type` can partially land in the wrong window despite invariant 2's wording.**
`OperatorCapabilities.KeyboardType` (OperatorCapabilities.cs:543-556) calls `Guarded(target)` (a single
`FocusGuard.Verify()`) once, then `Win32InputSynthesizer.TypeText` (InputSynthesizer.cs:180-210) sends the
text as up to ~8192 SendInput events (4096 chars max) in batches of 64 with `Thread.Sleep(5)` between
batches — several hundred ms to over a second of elapsed time with no re-check. If the foreground window
changes mid-stream (another app steals focus, a system dialog pops up), the remaining unsent characters go
to whatever now has focus, not to the window the guard verified. No test exercises a focus change
mid-`keyboard.type`; `FocusGuardTests.cs` and `PayloadGuardTests.cs` only check a single verify-then-act
sequence with no intervening delay. Pointer actions and single keys/shortcuts have this same shape but the
event count is small enough the window is negligible. **Fix:** re-verify focus per batch (or after every N
events) inside `TypeText`, aborting with `focus_mismatch` if it changes mid-stream.

**Medium — `app.launch`/`file.open` `args` are validated only for count/length/control-chars, not
allowlisted per application.** `ReadArgs` (OperatorCapabilities.cs:1273-1308) caps at 16 args × 1024 chars,
no control chars — but places no restriction on argument *content*. For `chrome`/`msedge` this lets a caller
(e.g. a plan step shaped by prompt-injected browsing content reaching the operator tool layer) pass
flags like `--remote-debugging-port=<port>`, `--load-extension=<path>`, or `--disable-web-security`, none of
which the application allowlist anticipates. Args cannot break out of the argv boundary itself
(`ProcessStartInfo.ArgumentList` — verified sound, no shell string concatenation), so this is flag-injection,
not command-injection. **Fix:** an explicit args allowlist/blocklist per allowlisted application, denying
flags that change security posture (remote debugging, extension loading, disabling web security).

**Verified sound (no finding):**
- Invariant 1 (no alternate desktop path): `AgentCapabilities.IsInteractive`/`IsOperator`
  (ProtocolConstants.cs:170-181), double-gated on both Device Service
  (`InteractiveCapabilityExecutor.ExecuteAsync`, refuses before the pipe when `OperatorEnabled=false`) and
  companion (`CompanionRuntime.cs`, refuses when `operatorCapabilities` is null/disabled) — both defaults
  false, no code path advertises or routes the family with either gate off.
- Secrets: `secret:true` refused before any window is touched (`RefuseSecret`, checked before
  `Registry.Resolve`/`Guarded`); UI Automation password fields never read (`IsPassword` checked in both
  `Describe` and `ReadValue`, UiAutomationInspector.cs); every operator result passes the (unmodified)
  browser family's `BrowserWorkerHost.FindForbiddenKey` scan before leaving the companion;
  `screen.capture` never writes to disk (confirmed — no File I/O in ScreenCapture.cs); logs/audit only
  record capability name + outcome + duration, never payload text or window titles.
- TerminalRunner allowlist: composition-character scan runs on the raw string before tokenizing/quote-
  stripping, so a quoted `;`/`|`/`` ` `` etc. still trips it; PATH is hardened with System32 prepended;
  timeout kills the process tree; no shell is invoked (`powershell.exe -Command` with the pre-authorised,
  separator-free string appended, not built from concatenated untrusted fragments).
- Broker/schema change (`frames.py` ERROR_CLASSES, device-protocol.schema.json): purely additive
  (`focus_mismatch`, `permission_denied`, `postcondition_failed`), no validator weakening; a test
  (`SerializationTests.cs`/`AdvertisementTests.cs`) cross-checks the C# error set against the schema file.
- Authority boundary (#8): `OperatorEnabled` is a local config flag (appsettings.json /
  `PAGENTOS_AGENT_OperatorEnabled` env var) on both service and companion, default false in both shipped
  appsettings.json files; no Cloud Core or evolution-engine code references `OperatorEnabled` or an
  `-Operator` installer switch (the installer switch itself is not yet implemented in this diff) — self-
  evolution has no code path in this diff that could remotely flip this switch.

**Low/info, not escalated:** `FocusGuard`'s "image" leg (`WindowRegistry`/`OperatorNative.ImageName`) compares
only the process image *filename*, not full path, so two different binaries with the same name are
indistinguishable by that check alone — but handle+pid must already match exactly, so this adds no
independent bypass. `screen.capture`'s 8192px `MaxDimension` allows a pathological window rect to force a
transient ~750MB allocation; bounded by the family's single-flight (`SemaphoreSlim(1,1)`) execution and 30s
cap, not a practical DoS in this single-owner model.
