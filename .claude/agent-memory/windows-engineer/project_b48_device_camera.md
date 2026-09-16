---
name: b48-device-camera
description: B48 device camera (2026-09-16) - how the companion got the WinRT projection without moving any script path, the camera seams, the heartbeat nested-object projection, and the traps met (restore needs network, CS0162 kills if(false) mutations, desktop.notify was never wired)
metadata:
  type: project
---

B48 (branch `worktree-agent-a6058a8b78b722474`, commit 4b54198) put a camera presence
provider in the Session Companion (`src/PagentOS.SessionCompanion/Camera/`) and
`desktop.camera_mode` at the END of `AgentCapabilities.Ambient` (manifest 12/13/43/103 for
none / -DisplayPower / +browser / +-Operator).

Facts that cost a round trip:

- **WinRT in the companion.** `<TargetPlatformVersion>` on a `net10.0-windows` project does
  NOT bring the projection (CS0246 on `Windows.*`). It takes the versioned TFM
  `net10.0-windows10.0.19041.0`; `AppendTargetFrameworkToOutputPath=false` +
  `OutputPath=bin\$(Configuration)\net10.0-windows\` keeps every script path. Every project
  that references the companion (Agent.Tests, Companion.Audio.Tests) must carry the same TFM
  (NU1201 otherwise); DeviceService/Core/Audio stay unversioned. Output +~25 MB.
- **Restore needs nuget.org** (`Microsoft.Windows.SDK.NET.Ref`), and the sandbox blocks it:
  the first build after the TFM change must run with the sandbox disabled. A scratch project
  needs a copy of `devices/windows-agent/NuGet.config` (the user-level config has no sources).
- **Verified on the owner's machine without opening the camera**: `FaceDetector.IsSupported`
  true, FaceDetector runs on a synthetic Bgra8->Gray8 bitmap (use alpha 255 or the grey
  un-premultiplies to 254), `MediaFrameSourceGroup.FindAllAsync` lists "Logi C615 HD WebCam"
  (enumeration opens nothing), ConsentStore webcam values = Allow, DDC/CI VCP 0xD6 answers
  `on` for both monitors (2560x1440 and 1440x2560 primary).
- **Heartbeat nested objects.** `HeartbeatStatus.Project` projects `camera`/`presence` onto
  their exact keys and nulls a nested object holding a non-scalar or a >64-char string - the
  Session-0 service is the last "no image leaves" guard. Two older tests pinned the status
  key set (`SerializationTests` fixture, `LivingCore...Activity_status_carries_exactly...`).
- **Real bug found:** `Program.cs` never constructed `NotifyCapabilities`, so
  `desktop.notify` answered capability_missing on every device since B11. Guarded by a
  composition test that reads the `new CompanionRuntime(` call.
- **Mutation traps:** `if (false)` fails the build (CS0162 + TreatWarningsAsErrors) - mutate
  with a runtime-false condition instead; a mutation whose pattern is not unique aborts the
  runner (the `if (vetoed)` in both CheckOnce and the veto handler); a mutation that stays
  green may be exposing a redundant guard - remove the dead code rather than keep the
  mutation.
- **Pre-existing reds met:** `SceneUnityTests.The_real_unity_editor...` fails on this machine
  (licensed editor exits 1; main checkout has uncommitted work on it); B52's
  `LegacyOfficeExtractor.cs` failed `dotnet format --verify-no-changes` (reformatted in B48).

**How to apply:** new WinRT use in the companion needs no further TFM work. Any camera test
uses `Camera/FakeCamera.cs` (fake source / idle / media / consent, SteppedClock with
`FrameSpacing = Zero`); never open the real camera in a test - the owner harness is
`scripts/core/qualify-device-camera.ps1`. See [[windows-agent-gates]], [[device-identity-chain]].
