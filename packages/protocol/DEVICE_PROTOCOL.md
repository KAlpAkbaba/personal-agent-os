# Device Protocol v1

Contract between the cloud Device Broker and device agents (first: Windows Device Agent). Both sides validate messages against `packages/schemas/device-protocol.schema.json`. Protocol version is negotiated in `hello`; incompatible versions are rejected at handshake.

## 1. Transport

- WebSocket, **always initiated outbound by the device agent**. No inbound port is ever opened on the device.
- JSON text frames, UTF-8, one protocol message per frame.
- The transport endpoint is configuration, not code: dev `ws://127.0.0.1:8001/v1/devices/connect`; production later `wss://…` over Tailscale (private overlay) without agent code changes. TLS/mTLS termination is a deployment concern below the protocol layer.
- The agent treats any socket failure identically: reconnect with exponential backoff 1 s → 60 s (factor 2, full jitter), forever.

## 2. Identity and enrollment

- Each device generates an **ECDSA P-256 keypair** at first run; the private key never leaves the device (stored under the device account with OS file ACLs; DPAPI hardening is a later milestone).
- Enrollment (REST, one-time):
  1. Owner/operator obtains a one-time enrollment token: `POST /v1/devices/enrollment-tokens` → `{token, expires_at}`. Since M9 this requires an owner bearer session (`Authorization: Bearer <token>`, ADR-0027) **and** a loopback peer.
  2. Agent calls `POST /v1/devices/enroll` with `{token, name, platform, public_key_spki_b64, capabilities}` → `{device_id}`. This is the one broker REST endpoint that does NOT require an owner session: the enrollment token *is* its credential, and the enrolling agent has no owner session by construction. The trust chain still roots in the owner, because only an authenticated owner can mint that token.
  - Tokens are single-use, short-lived (default 15 min), stored hashed.
- In protocol v1, every enrollment creates a **new device identity** (the enroll request carries no device_id, so the broker cannot safely bind it to an existing row). Key rotation for an existing device is deferred to a dedicated authenticated rotate endpoint in a later protocol revision. Revocation: broker marks device revoked; all WS sessions close; the handshake is rejected thereafter; and since M9 every owner session bound to that `device_id` is revoked too, so the device's bearer token stops working on the REST API as well (M9 acceptance: *device revocation invalidates session*).
- Signature encoding: the broker accepts both DER and raw IEEE P1363 `r||s` (64-byte) ECDSA signatures.

## 3. Handshake (over WS)

```
agent  -> {"type":"hello","protocol_version":1,"device_id":"…","software_version":"…","capabilities":["desktop.open_application"]}
broker -> {"type":"challenge","nonce":"<b64 32 bytes>"}
agent  -> {"type":"auth","signature":"<b64 ECDSA-SHA256 over nonce_bytes||device_id_utf8>"}
broker -> {"type":"welcome","session_id":"…","heartbeat_interval_s":10}
```

Failure at any step: broker sends `{"type":"error","error":{"class":"auth_error",…}}` and closes. Unknown/revoked device or bad signature never reveals which check failed.

## 4. Heartbeat and presence

- Agent sends `{"type":"heartbeat","seq":n}` every `heartbeat_interval_s`; broker replies `{"type":"heartbeat_ack","seq":n}`.
- Broker marks the device **offline** when no frame arrives for `2.5 × heartbeat_interval_s`, and on socket close. Presence is observable at `GET /v1/devices`.
- Any frame refreshes liveness; heartbeats are only a floor.

## 5. Commands

Broker → agent:

```json
{"type":"command","command":{
  "command_id":"uuid","idempotency_key":"uuid",
  "capability":"desktop.open_application",
  "payload":{"application":"notepad"},
  "expires_at":"2026-08-31T12:00:00Z","trace_id":"…"}}
```

Agent → broker acknowledgements (`command_ack`), monotonic per command:

`accepted` → `running` → terminal `succeeded` | `failed`

```json
{"type":"command_ack","command_id":"…","status":"succeeded",
 "result":{"pid":1234},"error":null}
```

`failed` carries `error: {class, message, retryable}` using the taxonomy of `docs/API_AND_PROTOCOLS.md` (plus `command_expired`, `cancelled`).

Rules:
- **Expiry:** agent rejects a command whose `expires_at` has passed with `command_expired` (never executes). Broker also expires undelivered commands server-side.
- **Idempotency:** agent keeps a persistent map `idempotency_key → terminal ack` (bounded LRU, survives restarts). A duplicate delivery re-sends the recorded terminal ack and never re-executes. Broker dedups REST creation on `idempotency_key` as well. Delivery is therefore at-least-once, execution effectively-once.
- **Cancellation:** broker sends `{"type":"cancel","command_id":"…"}`. If not yet terminal, agent aborts and acks `failed` with `error.class="cancelled"`; if already terminal, it re-sends the terminal ack. A cancel for a command the agent has never seen is acked `failed`/`cancelled` so the broker can settle it (the agent records nothing, having no idempotency key for it).
- **In-flight duplicate delivery:** a duplicate of a command that is still executing is answered with a `running` re-ack and never re-executed (monotonicity preserved).
- **Malformed frames:** receiver answers `{"type":"error","error":{"class":"validation_error",…}}` referencing `command_id` when parseable; the connection stays open.
- **Redelivery:** on (re)connect the broker re-delivers all non-terminal, non-expired commands for that device.

## 5a. Sideband push: `voice_sideband` (M12, ADR-0039)

Broker → agent, additive to v1. Cloud Core's realtime-voice session service delivers
sideband messages for the owner-session companion (`say`, `tool_progress`,
`tool_completed`, `plan_changed`, `narration_cursor`, `leg_closed`) over the SAME
outbound-only device connection, as their own frame type:

```json
{"type":"voice_sideband","session_id":"uuid","event":"tool_completed",
 "payload":{"call_id":"call_1","status":"succeeded","result":{}},
 "at":"2026-09-02T12:00:00Z"}
```

Rules:
- **Opaque to the Device Service.** The service validates the envelope (uuid session id,
  event name, object payload, ≤ 16 KiB serialized) and forwards the frame unchanged over
  the authenticated local IPC (`voice_sideband` pipe frame, carrying the connection id and
  sequence every pipe frame carries). It never interprets the payload, never acts on it,
  and never replies to the broker for it.
- **Nothing from the command path applies:** no `command_ack`, no idempotency key, no
  expiry, no audit row per frame. A frame that cannot be forwarded (no companion
  connected, oversize) is dropped by the agent; Cloud Core already queues undelivered
  pushes on the session record and the client receives the backlog with its next
  `POST /v1/voice/realtime/sessions/{id}/events` response or on `attach`.
- **Older receivers:** an agent that predates this frame answers it with the standard
  `validation_error` error frame (§5, malformed frames); the broker logs that and the
  session continues. Newer agents with no companion connected simply drop it.

## 6. Capability: `desktop.open_application` (M1)

Payload: `{"application":"<name>","args":[…]?}`. The agent maintains a local **allowlist** (M1 default: `notepad`, `calc`); anything else fails with `capability_missing`. The application must start in the **interactive owner session** (executed by the session companion, not the background service). Result: `{"pid":<int>,"executable":"<path>"}`.

## 6a. Capability: `desktop.open_artifact` (M3)

Payload: `{"path":"<absolute local file path>","artifact_id":"<uuid>"?}`. The session companion opens the file with its OS-associated application (ShellExecute, never launching a program directly) and returns `{"opened":true,"path":"<canonical path>","handler":"shell-associated"}`. Interactive-session capability, executed by the companion.

Allowlist (checked in order): reject non-absolute/blank/UNC paths (`validation_error`); hard-deny executable extensions even inside a root (`security_scope_error`); require a document extension in the configurable allowlist, default `.pdf .docx .html .htm .txt .md` (`capability_missing`); require the canonical resolved path (symlinks/`..` collapsed) to lie under a configured artifact root, default `%LOCALAPPDATA%\PagentOS\agent\artifacts` plus configured extra roots (`security_scope_error`); require the file to exist (`dependency_unavailable`). Every attempt is written to the companion's local JSONL audit with path, artifact_id and result.

## 6b. Capability family: `browser.*` (M13)

The browser family rides this protocol unchanged — one command envelope, one
`accepted → running → succeeded|failed` lifecycle, one idempotency store, one audit log.
Names, payloads, results, sessions, risk classes and the companion ↔ worker stdio protocol
are specified in **`packages/protocol/BROWSER_CAPABILITIES.md`** (ADR-0050), which is the
binding contract; this section only states how the family is carried on the device side.

- **Advertisement.** The family marker `browser.chrome` and the per-operation names
  (`browser.session_open` … `browser.fetch_evidence`) appear in `hello.capabilities` and in
  the enrollment manifest **only when the device has a configured Browser Worker**:
  service option `BrowserEnabled=true` (written by the installer after the worker's
  self-check passed) and, on the companion, `BrowserWorkerCommand` set. The two desktop
  names are advertised unconditionally, exactly as before. A device never advertises a
  family it cannot execute; Cloud Core selects devices by capability (§8 of the browser
  contract), so an absent name means `no_capable_device`, not a hang.
- **Routing.** The Device Service (Session 0) forwards every `browser.*` command over the
  authenticated companion pipe like `desktop.*`; the Session Companion (owner session)
  hands it to the Browser Worker child process it owns. With `BrowserEnabled=false` the
  service answers `capability_missing` (not retryable) before consulting the companion.
  With no companion connected the answer is `dependency_unavailable` (retryable), as for
  the desktop family. A companion without a configured worker answers `capability_missing`.
  Unknown names in the `browser.` namespace, and the marker `browser.chrome` itself, are
  `capability_missing`.
- **Timeout cap.** The service caps the time one command may hold the companion per family:
  **60 s for `desktop.*`** (unchanged), **120 s for `browser.*`**. The pipe request carries
  `timeout_ms` (the remaining command life clamped to [1 s, cap]); the companion forwards
  it to the worker (less a 500 ms headroom so the typed `timeout` arrives before the
  service synthesises one), cancels the worker's request when it elapses, and answers
  `timeout` (retryable).
- **Concurrency.** Browser requests execute concurrently on the companion and their
  responses may interleave on the pipe; `request_id` correlates them, every frame still
  carries the connection id and a strictly increasing sequence (ADR-0028), and one
  `exec_response` answers one `exec_request`. Desktop requests are unaffected.
- **Companion-side checks.** Before a worker result crosses the pipe the companion enforces
  the contract's result cap (48 KiB → `internal_bug`; the worker must truncate) and the
  forbidden-key rule (`cookie`, `authorization`, `set-cookie`, `localstorage`,
  `sessionstorage`, `password`, `token`, `secret`, `apikey` — case-insensitive substring of
  any key at any depth → `security_scope_error`). Error classes outside §8's taxonomy are
  reported as `internal_bug`. The worker's stderr goes to the companion log, never into a
  result.
- **Worker lifecycle.** Started lazily on the first `browser.*` request (or eagerly with
  `BrowserWorkerEager=true`), one at a time; restarted with exponential backoff after an
  exit, with in-flight requests failed `dependency_unavailable` (retryable) immediately;
  pinged for liveness; told `shutdown` and then killed (process tree) when the companion
  stops.
- **Audit.** The companion writes one `browser_request` row per request — capability,
  `request_id`, outcome class, `duration_ms`, never payload or result text, never a URL —
  plus `browser_worker_started` / `browser_worker_exited` rows; the service writes its usual
  per-command rows.

Configuration (all `PAGENTOS_AGENT_`-prefixed, nothing machine-specific): service
`BrowserEnabled` (default false); companion `BrowserWorkerCommand`, `BrowserWorkerArgs`,
`BrowserDataDir` (default `<companion DataDir>\browser`), `BrowserProfileDir` (default
`<BrowserDataDir>\profile`), `BrowserChannel` (default `chrome`), `BrowserVisible` (default
true), `BrowserIdleTimeoutS` (default 600), `BrowserWorkerEager` (default false).

## 7. Audit

Broker persists an `audit_events` row for: enrollment, session start/end, command created, delivered, each ack transition, cancel, expiry. Events carry `trace_id`, `device_id`, `command_id`, never secrets or payload bodies larger than 4 KB.
The agent writes a local append-only JSONL audit log (`%ProgramData%`/configurable) for every executed command.

## 8. Broker REST surface (owner API)

Since M9 every endpoint below requires `Authorization: Bearer <owner-session-token>` (ADR-0027) except `POST /enroll`, which is authenticated by the owner-minted enrollment token.

- `POST /v1/devices/enrollment-tokens` → one-time token (owner session + loopback peer)
- `POST /v1/devices/enroll` (enrollment token only — see §2)
- `GET /v1/devices` — includes `status: online|offline|revoked`, `last_seen_at`
- `POST /v1/devices/{device_id}/commands` `{capability, payload, idempotency_key?, timeout_s?}` → `202 {command_id,status}` (immediately durable; never long-polls execution)
- `GET /v1/devices/{device_id}/commands/{command_id}` → full status/result/error
- `POST /v1/devices/{device_id}/commands/{command_id}/cancel`
- `POST /v1/devices/{device_id}/revoke`

## 9. Windows agent process architecture

- **Device Service** (background; Windows Service-capable, Session 0): owns the keypair, the WS connection, idempotency store, local audit log, and machine-level capabilities. Never touches the interactive desktop.
- **Session Companion** (runs in the owner's interactive session): executes interactive capabilities (UI, `desktop.open_application`), connects to the Device Service over an authenticated local named pipe. If no companion is connected, interactive commands fail fast with `dependency_unavailable` (`retryable: true`).
- In development both run as console processes; installing the service (requires UAC) is an owner action recorded in `OWNER_ACTIONS_MINIMAL.md`.
