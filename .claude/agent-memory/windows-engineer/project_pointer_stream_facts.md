---
name: pointer-stream-facts
description: ADR-0199 device half (pointer stream, 2026-09-22) - the companion pipe's zero-byte buffers make a service write block until the companion reads (never forward inline from the receive loop), a backup-restore keeps the old mtime so MSBuild reruns the MUTATED binary, a real-input lab that mutates a "release" rule leaves the owner's real mouse button down, relative SendInput moves are rescaled by pointer acceleration
metadata:
  type: project
---

Facts from building the pointer stream (`PointerStreamController`, `CompanionPipeServer.ForwardPointerStreamAsync`, `PointerStreamForwardingTests`, `PointerStreamLabTests`) that each cost a red or hung run:

- **A service→companion pipe write completes only when the companion READS it.** `CompanionPipeServer` creates the pipe with `inBufferSize: 0, outBufferSize: 0`; measured 2026-09-21: `ForwardPointerStreamAsync` awaited inline from `AgentConnection`'s receive loop hung the whole test host for 10 min because the raw test companion was not reading. Anything forwarded from the WebSocket receive loop must be QUEUED (per-connection bounded `Channel`, drop-oldest, one drain task) so a stalled companion costs frames, not the device connection. `ForwardSidebandAsync` still writes inline — pre-existing, the same latent hazard.
- **Restoring a mutated source from a backup COPY keeps the backup's older LastWriteTime, so `dotnet build` skips recompiling and the MUTATED DLL runs again** (the "green" run showed the same 4 RED tests). After `Copy-Item`/`cp` from a backup, touch the file (`(Get-Item f).LastWriteTime = Get-Date`) before rebuilding. Related: [[mutation-testing-restore-discipline]].
- **Mutating a "release the held button" rule on a real-input lab leaves the owner's REAL left button down** (the mutation closed the stream without releasing, so the lab's `EndAll` teardown found nothing open). Released by hand with `mouse_event(MOUSEEVENTF_LEFTUP)` via `Add-Type`; the lab's `finally` now also calls a direct `ReleaseLeftIfDown()`. Before mutating any rule that undoes an injected input, check the lab's teardown does not depend on the rule.
- **A raw relative `MOUSEEVENTF_MOVE` is rescaled by Windows pointer speed/acceleration**; the companion applies stream deltas as `SetCursorPos(current + d)` + a zero-delta MOVE (pixel-exact; the lab reads back within 2 px).
- `OpenInputDesktop(0, false, DESKTOP_READOBJECTS)` failing = the session is locked (Winlogon desktop); used as the stream's lock probe, no hook needed.
- `dotnet test --blame-hang --blame-hang-timeout 3m --blame-hang-dump-type none` writes a `Sequence_*.xml` under `--results-directory` naming the test with `Completed="False"` — the fastest way to name a hung test; the Turkish failure block is `Hata İletisi:` / `Yığın İzleme:` (grep those, not "Error Message").
- An in-process `JsonValue` built from an int refuses `GetValue<long>()` (the M19 trap again): a receipt field read as long must be written as `0L`.
- Killing a stuck test host from this worktree: `Get-CimInstance Win32_Process` filtered on `CommandLine -like '*<worktree id>*'` and names `testhost.exe/dotnet.exe/vstest.console.exe`, plus Notepads whose ParentProcessId is that host; never a bare `Stop-Process -Name dotnet` (the owner's own).

**Why:** each of these looked like a code defect for one run and was the platform, the tooling, or the lab's own teardown.
**How to apply:** when adding another streamed frame family, reuse the outbox pattern; when RED-proving on the operator lab, mutate away from teardown-critical rules or add a direct undo; after any backup restore, touch before building.
