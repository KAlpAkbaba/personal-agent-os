---
name: m23-projects-family
description: M23 device half (projects family) facts - where the job-object runner's seams are, the marker-carries-manifest rule, the <port>/<root> placeholders, the file-path/forbidden-key trap for track A, the HasExited-vs-object-signal teardown race, the log-handle and port-release races the lab found
metadata:
  type: project
---

M23 track B (2026-09-08, branch `m23-device`, worktree `.claude/worktrees/agent-ab14cddc99c8e342d`) added `ProjectCapabilityNames` (5 names after documents, same `OperatorEnabled` gate, `AgentInfo.SoftwareVersion` 0.5.0), `Projects/` in the companion (ProjectRoots, ProjectManifest, ProjectScaffold, JobObject, ProjectRunner, ProjectCapabilities), `DEVICE_PROTOCOL.md` §6l, ADR-0086 addendum 1, lab `tests/PagentOS.Agent.Tests/Projects/` (99 tests; whole project 695).

Facts that are not obvious from the code:
- **The marker carries the manifest.** `project.run`/`project.test` re-parse `.pagentos-project.json`, never a `manifest.json` in the file list; a tampered marker is `permission_denied` before any process. Track A must send `manifest` as a separate payload field of `project.scaffold` (`{entry, port, run:{key:cmd}, test:{key:cmd}}`), with the run command spelled `python -m http.server <port> --bind 127.0.0.1` (the `<port>` placeholder or the number) and `npm --prefix <root> run start` only with `package-lock.json` in the file list.
- **A file path that trips the forbidden-key scan is refused at scaffold** (`sha256_by_path` is keyed by path): `token.js`, `reset-password.html`, `secrets.md`, `api-key.json` are `validation_error`. Track A's generator/validator should mirror `BrowserCapabilities.ForbiddenResultKeyFragments`.
- **`.js` is the only executable extension a file list may carry**; `.bat/.cmd/.ps1/.lnk/.url/.jse/...` and macro Office are refused.
- **Seams**: `ProjectRunner(logger, start: Func<ProcessStartInfo, Process?>, lifetime, testTimeout, portWait, maxRunning)`; `ProjectLab(...)` mirrors them; `ProjectRun.InJob` / `.Limits` read the job back from the kernel (`JobObject.ReadLimits`). `ProjectRunner.FindOnPath("python.exe")` skips `WindowsApps` Store aliases.
- **Teardown race**: `Process.HasExited` (GetExitCodeProcess) turns true before the child's handle table is torn down; a node child's CWD handle on the project folder made `Directory.Delete` fail in the lab. Wait on the process OBJECT (`WaitForExit(ms)`) and retry the delete.
- **Log-handle race**: one `run.log` per project; the previous run's FileStream must be closed when the run ends (the exit watcher does it), else the next `project.run` (FileMode.Create) collapses into an IOException. `BoundedLog.Open()` retries a sharing violation for 3 s.
- **Port-release race**: a terminated `http.server` still holds its listener for a few ms; `RequirePortFree` retries for 2 s before `port_busy`.
- `<` and `>` must NOT be composition characters in the manifest parser (the placeholders carry them); the token-exact match refuses them elsewhere.
- The production companion's Browser Worker shows up as two `python.exe` (`-m browser_agent.worker`, parent = the companion) - not a lab leak.
- Job assignment happens right after `Process.Start` (microseconds unassigned); `PROC_THREAD_ATTRIBUTE_JOB_LIST` would close that window if ever needed.

**Why:** each of these cost a red run or would trip track A's integration.
**How to apply:** when track A wires `project.*`, point them at §6l for the payloads; when a lab leaves folders under `%TEMP%\pagentos-operator-fixture\projects`, suspect the teardown race, not the runner.
