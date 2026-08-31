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
| `src/PagentOS.SessionCompanion` | Console app for the interactive owner session. Connects to the service pipe, executes `desktop.open_application` against a configurable allowlist (default: `notepad`, `calc`), reconnects with backoff. |
| `tests/PagentOS.Agent.Tests` | xUnit suite: schema fixtures, signature verification, idempotency/LRU, backoff bounds, allowlist, real named-pipe round trips, and a Kestrel fake broker covering handshake, duplicate delivery, cancel, malformed frames and broker-restart reconnection. |

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

## Windows Service installation (owner action, deferred)

Development runs both processes as consoles. Installing the Device Service as a real Windows
Service requires an elevated (UAC) shell, e.g.:

```powershell
sc.exe create PagentOSDeviceAgent binPath= "<publish-dir>\PagentOS.DeviceService.exe run" start= auto
```

plus an auto-start entry for the Session Companion in the owner session (e.g. `shell:startup`).
This is an owner action to be recorded in `OWNER_ACTIONS_MINIMAL.md`; the service code is
already `UseWindowsService`-capable.
