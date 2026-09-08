---
name: m25-scenes-3d
description: M25 device half (the two 3D runtimes, the 3D root, scene.inspect) - Blender/Unity headless facts measured on the owner's machine, the manifest re-parse traps, and the two pre-existing flaky tests in PagentOS.Agent.Tests
metadata:
  type: project
---

M25 track B (2026-09-08, branch `m25-device`, worktree `.claude/worktrees/agent-a4b4f44c46a1421f0`) added `SceneCapabilityNames` / `AgentCapabilities.Scenes` (one name, `scene.inspect`, `AgentInfo.SoftwareVersion` 0.6.0), `OperatorOptions.ProjectsRoot3d`, two argv shapes on the M23 `ProjectManifest` allowlist, `ProjectRunner.RunBatchAsync`, `Scenes/{SceneTools,SceneInspection}.cs`, `DEVICE_PROTOCOL.md` §6m, lab `tests/PagentOS.Agent.Tests/Scenes/` (41 tests; whole project 739).

Facts that are not obvious from the code:

- **Blender refuses a missing `.blend`.** `blender -b scene.blend --python d.py -- …` with no `scene.blend` prints `Error: Cannot read file …`, exits **1** and never runs the script. So a 3D project needs its base scene on disk BEFORE the first `project.run`; `project.scaffold` writes text only, so the lab creates it with `blender -b --factory-startup --python <maker> -- <target>` (0.6 s, 82 KB). **The 3D scaffold still has no way to place a binary base scene — a named gap for track A.**
- **Blender headless numbers on this machine** (4.5.4 LTS, `C:\Program Files\Blender Foundation\Blender 4.5\blender.exe`): sphere + camera + sun + a 160×120 Workbench PNG + save + `out.json` = **~3.9 s**, PNG ~12.7 KB. It runs happily inside a Job Object with `JOB_OBJECT_UILIMIT_ALL`.
- **Unity resolves `-projectPath` against the CWD** even for an absolute path when the drives differ — the first probe produced `E:\…worktree\C:\Users\…`. The runner sets the working directory to the project folder, which is also what `-projectPath` names, so it works; a lab that runs `Unity.exe` by hand must set the working directory too.
- **Unity's `-logFile` gets everything; stdout gets nothing.** The licence refusal is only visible in that file, so the batch runner reads its tail (the argument is relative → combine it with the project folder) and merges it with the streams.
- **Unity today (measured 2026-09-08 09:55Z and 13:24Z): exit 198**, "No valid Unity Editor license found. Please activate your license.", ~2 s, entitlement 404 / `com.unity.editor.headless` not found. Owner item 32 (`docs/OWNER_ACTIONS.md`) is a Hub sign-in.
- **The marker round-trip is the trap.** `ProjectManifest.Json` is what the marker records and what `project.run` re-parses, so anything `Parse` accepts must survive being written back: a 3D manifest with no `port` records `port: 0`, and `ReadPort` has to accept 0 for `ProjectScope.ThreeD` or every 3D run is a `validation_error`. `port` is read BEFORE the commands, so a web-scope test of a 3D command must still supply a port to see `permission_denied`.
- **`AuthorisedRoots.ResolveFinal` returns null for a path that does not exist**, so confining a file that has not been written yet must resolve the DIRECTORY and combine the name — otherwise "there is no inspection yet" comes back as `permission_denied`, and those are different answers.
- **`ProjectRoots.RequireRoot` only created a root whose parent already existed.** The 3D root's parent is the Projects root, which may not exist on a fresh machine, so it now walks up to the nearest existing confined ancestor (≤ 4 levels) and creates the chain.
- **Two PRE-EXISTING flaky tests** in `PagentOS.Agent.Tests`, both reproduced with the M25 lab excluded: `Documents.BoundedReadTests.A_6_MiB_CSV_…` (its gauge is process-wide `GC.GetTotalAllocatedBytes`, so any parallel collection inflates it past its 16 MiB bound — ~1 run in 4) and `BrowserWorkerHostTests.A_worker_that_stops_answering_pings_…` (a timing race, ~1 run in 6). A red run of either is not evidence of a regression; re-run before investigating.

**Why:** each of these cost a red run or would mislead the next milestone.
**How to apply:** when a 3D run answers `validation_error`, suspect the marker round-trip; when it answers `permission_denied` for a file, suspect `ResolveFinal` on a missing path; when the whole suite is red by exactly one test, check the two named above before touching anything.
