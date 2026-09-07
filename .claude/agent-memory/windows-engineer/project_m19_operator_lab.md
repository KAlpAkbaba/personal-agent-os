---
name: m19-operator-lab
description: Non-obvious facts from building the M19 Digital Operator on the companion (2026-09-07) - Notepad's UIA shape, the JsonValue int/double trap, the child-process PATH, xunit v2 conditional skips, UIA reference without UseWPF, where the error-class list must be mirrored
metadata:
  type: project
---

Facts that cost a red run each while building `PagentOS.SessionCompanion/Operator/` and its lab (branch `m19-agent`, worktree `E:\AI\pagentos-wt-m19-agent`):

- Classic Win10 Notepad (19045, no Store Notepad on this machine, UI culture tr-TR): the edit is a UIA `Document` (class `Edit`, automation_id `15`) with a Text pattern and NO Value pattern; `ui.set_value` falls back to `WM_SETTEXT` on the element's native window and reads back through the Text pattern. Title `Adsız - Not Defteri`; the Save dialog's buttons are `Kaydet` / `Kaydetme` / `İptal`, found via UIA `ControlType.Button` on an owned window of the same pid.
- `JsonValue` built in-process from an `int` (`new JsonObject { ["x"] = 100 }`) throws on `GetValue<double>()`; parsed JSON converts freely. Payload readers must try `TryGetValue<int>` / `<long>` before `<double>`, or every in-process caller (tests, the lab) fails while the pipe path works.
- The broken spawned-shell PATH (see [[machine-tool-paths]]) reaches child processes: a headless `powershell.exe -Command hostname` said "hostname is not recognized". The terminal runner prepends System32 / Windows / Wbem / WindowsPowerShell to the child's PATH; keep that. Headless PowerShell writes redirected output in OEM CP 437 here, so the runner's fixed `[Console]::OutputEncoding=UTF8;` prefix is needed for Turkish names.
- xunit 2.9.3 has no `Assert.Skip`; a conditional skip is a `FactAttribute` subclass whose constructor sets `Skip` (`[LabFact]` in `tests/.../Operator/OperatorLab.cs`). All lab classes share one `[Collection]` with `DisableParallelization = true` - the project has no other parallelisation config, so two desktop test classes would otherwise run at once.
- `UIAutomationClient`/`UIAutomationTypes` come from `<FrameworkReference Include="Microsoft.WindowsDesktop.App.WPF" />` (WPF profile, no `UseWPF`); `LegacyIAccessiblePattern` does not exist in the managed wrapper. The Windows Desktop runtime (10.0.11 here) must exist on the target machine.
- A new error class must be added in THREE places or a device ack is refused: `ErrorClasses.All` (agent validator), `packages/schemas/device-protocol.schema.json` `$defs.errorObject.properties.class.enum`, and `services/api/app/broker/frames.py` `ERROR_CLASSES`. `AdvertisementTests` asserts the C# set equals the schema enum.
- Foreground activation from the test host works with AttachThreadInput + AllowSetForegroundWindow + a zero-delta pointer move on retries; no Alt-key trick needed (an Alt press would focus Notepad's menu bar and eat the next keystroke).

**Why:** each of these looked like a product bug for one run and was tooling or platform shape.
**How to apply:** when extending the operator (Store Notepad, other apps), read the tree first with `ui.inspect depth 5` and take the element with a value; keep payload readers tolerant of typed nodes; never rely on PATH in a child process.
