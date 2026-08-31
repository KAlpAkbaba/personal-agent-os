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
  1. Owner/operator obtains a one-time enrollment token: `POST /v1/devices/enrollment-tokens` → `{token, expires_at}` (dev: loopback-only, unauthenticated; production: owner-authenticated).
  2. Agent calls `POST /v1/devices/enroll` with `{token, name, platform, public_key_spki_b64, capabilities}` → `{device_id}`.
  - Tokens are single-use, short-lived (default 15 min), stored hashed.
- Re-enrollment with a new token rotates the key. Revocation: broker marks device revoked; all sessions close; handshake is rejected thereafter.

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
- **Cancellation:** broker sends `{"type":"cancel","command_id":"…"}`. If not yet terminal, agent aborts and acks `failed` with `error.class="cancelled"`; if already terminal, it re-sends the terminal ack.
- **Malformed frames:** receiver answers `{"type":"error","error":{"class":"validation_error",…}}` referencing `command_id` when parseable; the connection stays open.
- **Redelivery:** on (re)connect the broker re-delivers all non-terminal, non-expired commands for that device.

## 6. Capability: `desktop.open_application` (M1)

Payload: `{"application":"<name>","args":[…]?}`. The agent maintains a local **allowlist** (M1 default: `notepad`, `calc`); anything else fails with `capability_missing`. The application must start in the **interactive owner session** (executed by the session companion, not the background service). Result: `{"pid":<int>,"executable":"<path>"}`.

## 7. Audit

Broker persists an `audit_events` row for: enrollment, session start/end, command created, delivered, each ack transition, cancel, expiry. Events carry `trace_id`, `device_id`, `command_id`, never secrets or payload bodies larger than 4 KB.
The agent writes a local append-only JSONL audit log (`%ProgramData%`/configurable) for every executed command.

## 8. Broker REST surface (owner API)

- `POST /v1/devices/enrollment-tokens` → one-time token (dev: open on loopback; prod: owner auth)
- `POST /v1/devices/enroll`
- `GET /v1/devices` — includes `status: online|offline|revoked`, `last_seen_at`
- `POST /v1/devices/{device_id}/commands` `{capability, payload, idempotency_key?, timeout_s?}` → `202 {command_id,status}` (immediately durable; never long-polls execution)
- `GET /v1/devices/{device_id}/commands/{command_id}` → full status/result/error
- `POST /v1/devices/{device_id}/commands/{command_id}/cancel`
- `POST /v1/devices/{device_id}/revoke`

## 9. Windows agent process architecture

- **Device Service** (background; Windows Service-capable, Session 0): owns the keypair, the WS connection, idempotency store, local audit log, and machine-level capabilities. Never touches the interactive desktop.
- **Session Companion** (runs in the owner's interactive session): executes interactive capabilities (UI, `desktop.open_application`), connects to the Device Service over an authenticated local named pipe. If no companion is connected, interactive commands fail fast with `dependency_unavailable` (`retryable: true`).
- In development both run as console processes; installing the service (requires UAC) is an owner action recorded in `OWNER_ACTIONS_MINIMAL.md`.
