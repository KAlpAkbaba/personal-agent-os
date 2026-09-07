# Windows Device Agent (M1)

.NET 10 implementation of the Personal Agent OS Windows Device Agent. The normative contract is
`packages/protocol/DEVICE_PROTOCOL.md` with message schemas in
`packages/schemas/device-protocol.schema.json` (protocol v1, snake_case fields, `type`
discriminator).

## Layout

| Project | Purpose |
| --- | --- |
| `src/PagentOS.Agent.Core` | Protocol models + System.Text.Json serialization, message validation, ECDSA P-256 device identity, enrollment REST client, WebSocket connection loop (handshake, heartbeat, reconnect with 1 s → 60 s full-jitter backoff), command dispatcher (expiry, cancellation), persistent LRU idempotency store, JSONL audit log, structured file logging with `trace_id`. |
| `src/PagentOS.DeviceService` | Background worker (Windows Service-capable via `AddWindowsService`, runs as console in dev). Owns keys/config/state and the named-pipe **server** for the companion. CLI verbs: `enroll`, `run`. |
| `src/PagentOS.SessionCompanion` | Console app for the interactive owner session. Connects to the service pipe, executes `desktop.open_application` against a configurable allowlist (default: `notepad`, `calc`) and `desktop.open_artifact` against an artifact-root + extension allowlist, reconnects with backoff. M13: hosts the Browser Worker child process (`BrowserWorkerHost`) and dispatches the `browser.*` family to it asynchronously. M19: `Operator/` — the Digital Operator (`DEVICE_PROTOCOL.md` §6i): a window registry with stable ids, window actions with verified activation, the focus guard, `SendInput` synthesis, a bounded UI Automation inspector, PNG screen capture without `System.Drawing`, the allowlisted headless terminal runner and the dispatch with an `observed` block on every result; behind `OperatorEnabled`. |
| `tests/PagentOS.Agent.Tests` | xUnit suite: schema fixtures, signature verification, idempotency/LRU, backoff bounds, allowlist, real named-pipe round trips, a Kestrel fake broker covering handshake, duplicate delivery, cancel, malformed frames and broker-restart reconnection, and (M13) the Browser Worker host driven over real stdio against the fake worker plus the service→pipe→companion→worker chain. `Operator/` (M19) is the Digital Operator lab: real Notepad and Explorer windows in the owner's session, Turkish text typed and read back over UI Automation, the focus guard refusing a wrong-window type, modal detection on the Save dialog, the headless terminal, cancellation and timeout, payload guards, advertisement and the service→pipe→companion→operator chain. Tests that need a desktop carry `[LabFact]` and skip with the runner condition named when there is none. |
| `tests/PagentOS.Agent.Tests.FakeBrowserWorker` | A .NET stand-in for `python -m browser_agent.worker` speaking the exact §7 stdio protocol (hello, exec/result, cancel, ping/pong, shutdown) with payload-selected behaviours (echo, typed error, sleep, crash, oversize, forbidden key). Needs no Python or browser; built beside the tests and spawned by them. |
| `src/PagentOS.Companion.Audio` | M12 track C (ADR-0037): the realtime voice client, additive to the companion. WASAPI capture/playback with device enumeration and switching (NAudio), a Communications-category capture path for driver AEC/NS with fallback, DC blocker + noise gate, echo-aware energy VAD, the Turkish hesitation guard, the M4-faithful client FSM, stop-playback-first barge-in, the WebSocket media leg with a pure OpenAI wire codec (WebRTC adapter reserved, deferred), the Cloud Core sideband (session, tool-call relay, event reporting, reattach) over the DPAPI-stored owner session token, and an in-process fake Cloud Core + deterministic fake media leg for tests and the bench. |
| `src/PagentOS.Companion.Audio.Bench` | Offline latency harness: the real client against fake devices, a scripted provider and the fake Cloud Core; prints mic→uplink, end-of-turn→first audio, barge-in→playback stopped, tool preamble and tool-done→speech. `--list-devices` enumerates real WASAPI endpoints without opening a stream. |
| `tests/PagentOS.Companion.Audio.Tests` | xUnit: FSM ordering, barge-in stop→cancel→report with measured latency, hesitation guard and end-of-turn detector on synthetic frames, device selection/switching, sideband idempotency (client_seq, call_id, retries), DPAPI round trip through real `powershell.exe`, wire codec mappings, the WebSocket leg against a loopback Kestrel provider, the orchestrator end-to-end on fakes, and the offline bench. |

## Two-process model

Per `DEVICE_PROTOCOL.md` §9 and the CLAUDE.md Windows Agent rule:

- **Device Service** (Session 0-capable): owns the ECDSA keypair, the outbound WebSocket to the
  broker, the idempotency store and the local audit log. It never touches the interactive
  desktop. No inbound network port is ever opened; the WS connection is always outbound.
- **Session Companion** (interactive owner session): executes interactive capabilities. It dials
  the service's named pipe `\\.\pipe\pagentos-companion-{user-SID}` (ACL restricted to the
  current user) and announces its capabilities. The service forwards interactive commands over
  the pipe and returns results/typed errors.
- If no companion is connected, interactive commands fail fast with
  `dependency_unavailable` (`retryable: true`) — the broker can retry after the owner session
  companion is up.

## Build and test

The machine's spawned-shell PATH is broken; use absolute paths:

```powershell
$env:DOTNET_ROOT = "C:\Users\alpak\AppData\Local\Microsoft\dotnet"
$dotnet = "C:\Users\alpak\AppData\Local\Microsoft\dotnet\dotnet.exe"
cd E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\devices\windows-agent

& $dotnet build PagentOS.WindowsAgent.sln          # warnings are errors
& $dotnet test  PagentOS.WindowsAgent.sln
& $dotnet format PagentOS.WindowsAgent.sln --verify-no-changes
```

The SDK is pinned to 10.0.400 via `global.json`. `NuGet.config` in this directory points to
nuget.org (the machine had no configured NuGet source).

## Enroll and run (dev)

1. Obtain a one-time enrollment token from the broker:
   `POST http://127.0.0.1:8001/v1/devices/enrollment-tokens`.
2. Enroll (generates the P-256 keypair on first run, stores `device_id` in `state.json`):

   ```powershell
   & $dotnet run --project src\PagentOS.DeviceService -- enroll `
       --broker-url http://127.0.0.1:8001 --token <token> --name owner-pc
   ```

3. Run the service loop (console): 

   ```powershell
   & $dotnet run --project src\PagentOS.DeviceService -- run
   ```

4. In the owner session, run the companion:

   ```powershell
   & $dotnet run --project src\PagentOS.SessionCompanion
   ```

Configuration comes from `appsettings.json` with environment overrides prefixed
`PAGENTOS_AGENT_` (e.g. `PAGENTOS_AGENT_BrokerWsUrl`, `PAGENTOS_AGENT_DataDir`,
`PAGENTOS_AGENT_PipeName`, `PAGENTOS_AGENT_HeartbeatIntervalOverrideS`). Defaults: broker
`ws://127.0.0.1:8001/v1/devices/connect` + `http://127.0.0.1:8001`, data dir
`%LOCALAPPDATA%\PagentOS\agent`. Keys, `state.json`, the idempotency store, logs and the JSONL
audit log all live in the data dir at runtime — nothing secret is committed.

## E2E flow (joint test with the broker)

1. Enroll as above; the agent sends `{token, name, platform, public_key_spki_b64, capabilities}`
   and stores the returned `device_id`.
2. On `run`, the agent dials the WS endpoint and performs
   `hello → challenge → auth → welcome`. The auth signature is ECDSA-SHA256 over
   `nonce_bytes || device_id_utf8`, **DER-encoded (RFC 3279 SEQUENCE of r,s)**, base64.
3. Heartbeats flow at the welcome-provided interval; presence shows in `GET /v1/devices`.
4. `POST /v1/devices/{id}/commands` with capability `desktop.open_application` and payload
   `{"application":"notepad"}` → the service forwards to the companion → Notepad opens in the
   owner session → acks `accepted → running → succeeded` with `{pid, executable}`.
5. Duplicate deliveries re-send the recorded terminal ack (never re-execute); expired commands
   are rejected with `command_expired`; `cancel` aborts in-flight commands with `cancelled`.
6. Kill the broker: the agent reconnects forever with 1 s → 60 s full-jitter backoff and the
   broker redelivers pending commands on reconnect.

In this repo the same flow is exercised end-to-end by `tests/PagentOS.Agent.Tests`
(`ConnectionTests` against an in-test Kestrel broker, `PipeTests` over real named pipes).

## Capability: `desktop.open_artifact` (M3)

Opens a downloaded artifact with its OS-associated application via ShellExecute
(`Process.Start(UseShellExecute = true)`), executed in the interactive owner session by the
companion. Payload `{"path":"<absolute local path>","artifact_id":"<uuid>"?}` →
`{"opened":true,"path":"<resolved path>","handler":"shell-associated"}`.

The actual shell open is behind `IFileOpener` (production `ShellFileOpener`; tests inject a
recording fake), so the suite is headless and never pops up a handler. Every attempt writes a
companion-side JSONL audit entry (`event":"artifact_open"`, capability, status, `path`,
`artifact_id`) under `<data-dir>\audit\companion-audit.jsonl`, in addition to the service-side
per-command audit.

Allowlist (mirrors `desktop.open_application` rigor), checked in this order — each rejection is a
typed error from the protocol taxonomy:

| Gate | Rule | Rejection class | retryable |
| --- | --- | --- | --- |
| shape | `path` present, non-blank, **absolute/fully-qualified**, **not UNC** (`\\`/`//`) | `validation_error` | false |
| executable denylist | extension ∉ {`.exe .bat .cmd .com .ps1 .psm1 .js .vbs .msi .scr .hta .jar .reg .lnk .dll …`} — hard-denied even inside a root | `security_scope_error` | false |
| extension allowlist | extension ∈ default {`.pdf .docx .html .htm .txt .md`} (override via `PAGENTOS_AGENT_ArtifactExtensions`) | `capability_missing` | false |
| containment | canonical full path (after collapsing `..`) is under a configured root; catches traversal and plainly-outside paths | `security_scope_error` | false |
| existence | file exists | `dependency_unavailable` | false |
| link escape | a symlink/junction whose real target leaves every root | `security_scope_error` | false |

Roots default to `%LOCALAPPDATA%\PagentOS\agent\artifacts` plus any semicolon-separated
`PAGENTOS_AGENT_ArtifactRoots`. Both `desktop.open_application` and `desktop.open_artifact` are
advertised in the enrollment capability manifest (`AgentCapabilities.All`) and forwarded over the
same service→companion named pipe.

## Capability family: `browser.*` (M13, off by default)

Contract: `packages/protocol/BROWSER_CAPABILITIES.md` (binding) and `DEVICE_PROTOCOL.md`
§6b (routing, advertisement, timeout cap). The chain is

```
Cloud Core --command--> Device Service (Session 0) --pipe exec_request--> Session Companion (owner session)
                                                                            └─ stdio JSON lines ──> Browser Worker (python -m browser_agent.worker) ──> Chrome
```

- **Manifest.** `AgentCapabilities.Desktop` is what every device advertises;
  `AgentCapabilities.Compose(browserEnabled)` adds `BrowserCapabilities.All` (the marker
  `browser.chrome` + 24 operations) only when the family is configured. The service uses
  its `BrowserEnabled` option (enrollment and every WS hello); the companion uses "is a
  worker command configured" (companion hello). `PagentOS.DeviceService.exe capabilities`
  prints the manifest an install would advertise, as one JSON document.
- **Service.** `InteractiveCapabilityExecutor` routes `browser.*` over the same pipe as
  `desktop.*`, with a per-family cap: desktop 60 s, browser 120 s. `BrowserEnabled=false`
  → `capability_missing` before the companion is consulted; no companion →
  `dependency_unavailable` (retryable).
- **Companion.** `CompanionRuntime` runs browser requests concurrently on their own tasks
  and answers through one sequenced writer (same `conn_id`/`seq` rules, one response per
  request; desktop requests unchanged). `BrowserWorkerHost` spawns the worker from
  configuration with redirected UTF-8 stdio, waits for its `hello` (20 s bound),
  correlates `exec`/`result` by `request_id`, honours `timeout_ms` (cancel forwarded,
  answer `timeout` retryable), pings for liveness, restarts with exponential backoff after
  an exit (in-flight requests fail `dependency_unavailable` retryable), sends `shutdown`
  then kills the process tree on stop, forwards worker stderr into the companion log,
  enforces the 48 KiB result cap (`internal_bug`) and the forbidden-key rule
  (`security_scope_error`), and writes one `browser_request` audit row per request
  (capability, request id, outcome class, duration — never payload/result text or URLs).
- **Configuration** (`PAGENTOS_AGENT_` prefix; nothing hardcoded to a machine):
  service `BrowserEnabled` (default false); companion `BrowserWorkerCommand` (executable,
  e.g. the provisioned venv's `python.exe`), `BrowserWorkerArgs` (e.g.
  `-m browser_agent.worker`), `BrowserDataDir` (default `<DataDir>\browser`),
  `BrowserProfileDir` (default `<BrowserDataDir>\profile`), `BrowserChannel` (`chrome`),
  `BrowserVisible` (true), `BrowserIdleTimeoutS` (600), `BrowserWorkerEager` (false — lazy
  start on the first request). The host appends `--data-dir --profile-dir --channel
  --visible|--headless --idle-timeout-s` to the configured arguments.
- **Installer.** `scripts/install-device-service.ps1` provisions the worker unless
  `-SkipBrowser`: copies `services\browser` into `<InstallRoot>\browser` through the same
  staging/publish swap as the binaries, runs `uv sync --frozen --no-dev --no-editable`
  there (uv resolved like `preflight.ps1`; interpreter under `<InstallRoot>\python`),
  proves the venv is relocatable, requires `python -m browser_agent.worker --self-check
  --channel chrome` to exit 0 in staging from a NEUTRAL working directory and to report the
  staged release from the staged venv (version, contracts, worker hash, package digest,
  `module.file` inside `.venv`; ADR-0050 item 16), writes the companion `BrowserWorker*` settings (data under
  `%ProgramData%\PagentOS\companion\browser`, granted to the owner SID explicitly) and the
  service `BrowserEnabled=true`, then re-runs the self-check from the hardened tree.
  `scripts/verify-device-service.ps1` repeats the self-check unelevated as the owner
  (6b.1) and compares the advertised manifest with the worker's hello (6b.2). Regression
  tests: `scripts/tests/installer-browser.tests.ps1`.
- **Developer run.** Point the companion at any worker:
  `PAGENTOS_AGENT_BrowserWorkerCommand=<venv>\Scripts\python.exe`,
  `PAGENTOS_AGENT_BrowserWorkerArgs=-m browser_agent.worker`, and start the service with
  `PAGENTOS_AGENT_BrowserEnabled=true`.

## Realtime voice client (M12 track C, additive, off by default)

The companion carries the desktop audio client of `docs/M12_REALTIME_VOICE_SPEC.md` but
runs it only when asked: `PagentOS.SessionCompanion.exe --voice`, or
`PAGENTOS_AGENT_VoiceEnabled=true` plus `PAGENTOS_AGENT_CloudCoreUrl=http://<tailnet-ip>:8001`.
Optional: `PAGENTOS_AGENT_VoiceCaptureDevice` / `VoiceRenderDevice` (WASAPI endpoint ids from
the bench's `--list-devices`), `PAGENTOS_AGENT_VoiceEndOfTurn=server|client`. The owner
session token is read from the existing store
`%LOCALAPPDATA%\PagentOS\secrets\PAGENTOS_OWNER_SESSION_TOKEN.dpapi`; without it voice stays
off and says so. The voice loop runs beside the pipe loop; its failure only logs. Every
session writes `voice_session_started` (ids, devices, and an honest AEC/NS statement),
`voice_device_switched`, `voice_network_lost/restored`, `voice_tool_silence` and
`voice_session_closed` rows to the companion audit log — never audio, never credentials.

Offline numbers for the gate (no microphone, no network):

```powershell
& $dotnet run --project src\PagentOS.Companion.Audio.Bench -- --turns 4 --eot client
& $dotnet run --project src\PagentOS.Companion.Audio.Bench -- --json
```

Real acceptance is the owner's microphone against the real provider and Cloud Core (spec
§10); nothing in this repository attempts it.

## Windows Service installation (owner action, deferred)

Development runs both processes as consoles. Installing the Device Service as a real Windows
Service requires an elevated (UAC) shell, e.g.:

```powershell
sc.exe create PagentOSDeviceAgent binPath= "<publish-dir>\PagentOS.DeviceService.exe run" start= auto
```

plus an auto-start entry for the Session Companion in the owner session (e.g. `shell:startup`).
This is an owner action to be recorded in `OWNER_ACTIONS_MINIMAL.md`; the service code is
already `UseWindowsService`-capable.
