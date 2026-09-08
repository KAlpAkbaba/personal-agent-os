---
name: m22-file-fetch
description: M22 device half (file.fetch) facts - where the dialled origin really lives (service side, carried in the pipe challenge), the DocumentCapabilities/FileFetch seams, HttpListener-on-loopback works unelevated, PS5.1 git-commit here-string trap, xunit int/long Assert trap
metadata:
  type: project
---

M22 track B (2026-09-08, branch `m22-device`, worktree `E:\AI\pagentos-wt-m22-device`, commits 328a887 / 6ee99cc / 9e1b2ea) added `file.fetch` as the SEVENTH documents name (`DocumentCapabilityNames.FileFetch`, `AgentInfo.SoftwareVersion` 0.4.0), `Documents/FileFetch.cs`, `DEVICE_PROTOCOL.md` §6k, ADR-0085 addendum 1, lab `Documents/FileFetchTests.cs` (35 tests; whole project 565).

Facts that are not obvious from the code:
- **The companion has no "dialled origin" of its own.** Only the Device Service dials Cloud Core (`BrokerRestUrl`; `InteractiveCapabilityExecutor.AudioOrigin/BrokerOrigin = HttpOrigin.Of(...)`). The seam built for M22: `ServiceChallenge.broker_origin` (optional) filled by `CompanionPipeServer(brokerOrigin:)`, read by `CompanionRuntime` into `DocumentCapabilities.FetchOrigin` on every connect. A lab sets `FetchOrigin` directly (`DocumentLab(fetchOrigin:, downloadsRoot:, withOperator:)`); `IpcTestSupport.NewServer(pipe, brokerOrigin:)` stages the real pipe path. The service ALSO refuses a foreign origin before the pipe (`ValidateFetchOrigin`, permission_denied). The voice `CloudCoreUrl` key is a different subsystem's dial - do not reuse it as the fetch origin.
- `file.open` is reached from the documents object through `DocumentCapabilities(fileOpener: OperatorCapabilities)`; `OperatorCapabilities.ExecutableExtensions` is now public and shared. `OperatorOptions` has a 4th positional `DownloadsRoot` (labs pass `<Root>\Downloads`).
- `HttpListener` on `http://127.0.0.1:<free port>/` works UNELEVATED on this host (no urlacl needed); a chunked 80 MiB "endless" handler sees its write fail once the client disposes the response past the bound (served landed between 50 and 80 MiB). C: had 16.9 GB free for the 50 MiB temp write.
- `HttpClient.Send` (sync) + `ResponseHeadersRead` + `AllowAutoRedirect=false` is fine inside the documents dispatcher's `Task.Run` thread; `HttpRequestException.HttpRequestError` gives a host-free reason for messages.
- `ValidateName` TRIMS whitespace (so `"x.txt "` is accepted as `x.txt`); a trailing dot is refused. A test that expects a trailing space to be refused is wrong.
- xunit: `Assert.Equal(int, long)` does not compile (cast); `Select(Path.GetFileName)` is an ambiguous method group (use a lambda); `System.Text.Json` polymorphic serialisation puts `"type"` first - parse the JSON rather than substring-matching it.
- PowerShell 5.1 tool: `git commit -F - @'...'@` does NOT feed the here-string to stdin - git treats the text as pathspecs and everything stays staged. Write the message to a scratch file and `git commit -F <file>`.
- The `[LabFact]` Notepad test in the documents collection ran fine in the parallel phase (`operator-lab` is the only DisableParallelization collection, so it runs after).

**Why:** each of these cost a round trip or a wrong assumption on 2026-09-08.
**How to apply:** when Cloud Core (track A) wires `artifact.open`, the render URL must be on the broker REST origin (scheme+host+port) and under `/v1/artifacts/`, carry its own signature (the companion adds no header), and the payload must carry `name`, `sha256`, `size` (the exact byte count; Content-Length must equal it). Extend the lab through `LocalOrigin` in `FileFetchTests.cs`.
