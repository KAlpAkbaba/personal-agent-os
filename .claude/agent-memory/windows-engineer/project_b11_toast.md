---
name: b11-toast
description: B11-toast (2026-09-17) - measured WinRT toast behaviour for unpackaged processes on the owner's 19045 desktop (Setting throws until the first Show, no popup even for PowerShell's id), the heartbeat notify_actions return path, and the mutation-runner traps
metadata:
  type: project
---

B11-toast put real Windows toasts into the Session Companion (`Notify/WindowsToastSink.cs`,
`ToastXml.cs`, `ToastPlatform.cs`, `AppIdentityShortcut.cs`, `NotifyActionQueue.cs`) and a
press return path as heartbeat `status.notify_actions`.

Measured facts (Windows 10 19045, owner desktop):

- `ToastNotificationManager.CreateToastNotifier(id).Setting` throws `0x80070490` for an id that
  has never shown a toast, with or without a Start Menu shortcut, and answers `Enabled` after
  the first `Show`. Treating the throw as "platform unavailable" makes a fresh install
  balloon-only forever. Windows then remembers the id (a second run answered at once).
- `Show` also succeeds for an id with NO shortcut, and the toast lands in
  `History.GetHistory(id)`. So the history read-back proves "Windows holds it", not "popup".
- No `HKCU\...\Notifications\Settings\<id>` key appeared for new ids.
- On this desktop NO toast popup window appeared at all, not even for Windows PowerShell's
  registered id, while `SHQueryUserNotificationState` said accepts_notifications and the
  foreground was a normal window: a Focus-Assist-like rule no public API reports. The UIA
  press lab is therefore opt-in (`PAGENTOS_TOAST_PRESS_LAB=1`).
- A UIA walk of all root children is slow (~35 s); find shell windows with Win32 `EnumWindows`
  by class `Windows.UI.Core.CoreWindow` instead.
- .NET regex `$` matches before a final `\n` (as Python's does): the action-id check had to
  become `\z` / `fullmatch`.
- The test project gets `System.Windows.Automation` transitively from the companion's WPF
  framework reference.

Mutation-runner traps: under PS 5.1 `$ErrorActionPreference='Stop'` turns `dotnet test`
stderr into a terminating error (the runner jumps to `finally` and prints nothing); use
`Continue`. `Get-Content | Set-Content` rewrites a script with CRLF and multi-line
replacement patterns stop matching LF sources. A BOM-less script with Turkish text is read as
ANSI by PS 5.1, so match `Başarısız` with an ASCII-only regex. After mutations, REBUILD before
any later gate: the binaries are of the mutated source.

**How to apply:** any new toast or notification work uses `FakeToastPlatform` for unit tests
and lab ids of its own; never claim a popup from a history read-back. See
[[b48-device-camera]], [[windows-agent-gates]], [[m19-operator-lab]].
