# API & Protocol Contracts

## 1. Owner API

Suggested versioned prefix: `/v1`.

Core resources:

- `/v1/identity` — M9: owner authentication (bootstrap, sessions, refresh, revoke, panic, audit). Every other resource below requires `Authorization: Bearer <owner-session-token>`; see §4a.
- `/v1/conversations`
- `/v1/tasks`
- `/v1/artifacts`
- `/v1/devices`
- `/v1/voice/sessions`
- `/v1/narration/sessions`
- `/v1/memory` — M5: `POST /observe` (write policy: may decide "ignored"), `POST /remember` (explicit owner teach → durable), `GET /search` (hybrid semantic+structured retrieval with class/project/temporal filters), `GET /{id}` (inspection incl. why-it-exists: provenance/evidence/versions/audit), `PATCH /{id}` (owner correction), `POST /{id}/pin`, `POST /{id}/supersede`, `DELETE /{id}` (forget — hard removal incl. vector rows), `GET /audit`, plus minimal `/entities` graph CRUD
- `/v1/capabilities`
- `/v1/authorized-assets`
- `/v1/system/health`

Exact routes are implementation details but IDs and semantics should remain stable.

## 2. Task create

Request concept:

```json
{
  "conversation_id": "...",
  "input": "Son üç günde yapay zekâ ajanlarıyla ilgili gelişmeleri araştır.",
  "origin_device_id": "...",
  "modality": "voice"
}
```

Response immediately returns durable `task_id` and state, not a long-running HTTP request.

## 3. Events

WebSocket/SSE event types:

- task.status_changed
- task.progress
- artifact.ready
- narration.ready
- device.online
- device.offline
- incident.recovered
- evolution.release_promoted

Owner UI suppresses noisy events by notification policy.

## 4. Device connection

Windows device initiates outbound authenticated WSS session.

Handshake carries:

- device ID;
- software version;
- capability manifest;
- nonce challenge signature;
- session metadata.

Implemented in M1 as protocol v1 — the normative contract is `packages/protocol/DEVICE_PROTOCOL.md` with message schemas in `packages/schemas/device-protocol.schema.json` (hello → challenge → auth → welcome, ECDSA P-256 over `nonce||device_id`, heartbeat presence, at-least-once delivery with agent-side idempotency, redelivery on reconnect). Broker REST surface: `/v1/devices`, `/v1/devices/enroll`, `/v1/devices/enrollment-tokens`, `/v1/devices/{id}/commands[...]`, `/v1/devices/{id}/revoke`; WS endpoint `/v1/devices/connect`. Since M9 the whole broker REST surface requires an owner session (§4a); `/v1/devices/enroll` and the WS handshake carry their own credentials instead.

## 4a. Owner identity and API authentication (M9, ADR-0027)

One owner, no accounts, no roles. `POST /v1/identity/bootstrap` is a one-time,
loopback-only owner action that mints the single owner credential (returned
once, stored only as a SHA-256 hash in the identity root **file**, not the
database). That credential is exchanged at `POST /v1/identity/sessions` for an
opaque bearer session token (`secrets.token_urlsafe(32)`, stored only hashed,
compared in constant time), which every other endpoint requires as
`Authorization: Bearer <token>`.

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/identity/bootstrap` | one-time owner credential mint (loopback) |
| `POST /v1/identity/sessions` | credential → session token |
| `POST /v1/identity/sessions/refresh` | rotate the token, extend the window |
| `GET /v1/identity/sessions/current` | who am I (native-client connect check) |
| `GET /v1/identity/sessions` | list active sessions |
| `DELETE /v1/identity/sessions/current` | sign this client out |
| `POST /v1/identity/sessions/{id}/revoke` | revoke another client's session |
| `POST /v1/identity/panic` | kill control: revoke ALL sessions |
| `GET /v1/identity/events` | append-only authentication audit |

Sessions have an absolute TTL (`PAGENTOS_SESSION_TTL_S`, default 30 days) and
an idle timeout (`PAGENTOS_SESSION_IDLE_TIMEOUT_S`, default 7 days); refresh
rotates the token so a leaked one dies at the next refresh. Refusals are coarse
to the caller (401 `unauthorized` / 403 `forbidden` / 429), precise in the
audit. **Fail closed**: with no owner credential bootstrapped, everything
refuses — there is no default credential.

The only unauthenticated surfaces are `GET /v1/system/health`,
`POST /v1/identity/bootstrap`, `POST /v1/identity/sessions` (the credential
exchange itself), `POST /v1/devices/enroll` (owner-minted enrollment token) and
the `/v1/devices/connect` WebSocket (ECDSA device authentication). A lost
credential is recovered on the host with `python -m app.identity.recover
--rotate`, never through this API.

## 5. Device command envelope

```json
{
  "command_id": "uuid",
  "idempotency_key": "uuid",
  "expires_at": "timestamp",
  "capability": "desktop.open_artifact",
  "payload": {},
  "trace_id": "..."
}
```

Device returns accepted/running/succeeded/failed with structured error taxonomy.

Retries must not duplicate destructive actions when idempotency can prevent it.

## 6. Artifact presentation

Presentation request specifies intent, not hardcoded implementation:

```json
{
  "artifact_id": "...",
  "action": "open",
  "target": "current_desktop",
  "preferred_format": "auto"
}
```

## 7. Narration cursor

Semantic cursor should survive regenerated audio:

```json
{
  "section_id": "s3",
  "paragraph_id": "p4",
  "sentence_index": 2,
  "char_offset": 0
}
```

Audio time is secondary cache metadata.

## 8. Error taxonomy

At minimum:

- validation_error
- auth_error
- device_offline
- capability_missing
- dependency_unavailable
- provider_rate_limited
- provider_error
- ui_target_not_found
- ui_state_changed
- timeout
- command_expired
- cancelled
- retry_exhausted
- artifact_render_error
- voice_provider_error
- security_scope_error
- internal_bug

Error class drives retry/evolution behavior.
