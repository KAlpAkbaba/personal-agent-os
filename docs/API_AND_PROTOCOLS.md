# API & Protocol Contracts

## 1. Owner API

Suggested versioned prefix: `/v1`.

Core resources:

- `/v1/conversations`
- `/v1/tasks`
- `/v1/artifacts`
- `/v1/devices`
- `/v1/voice/sessions`
- `/v1/narration/sessions`
- `/v1/memory`
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

Implemented in M1 as protocol v1 — the normative contract is `packages/protocol/DEVICE_PROTOCOL.md` with message schemas in `packages/schemas/device-protocol.schema.json` (hello → challenge → auth → welcome, ECDSA P-256 over `nonce||device_id`, heartbeat presence, at-least-once delivery with agent-side idempotency, redelivery on reconnect). Broker REST surface: `/v1/devices`, `/v1/devices/enroll`, `/v1/devices/enrollment-tokens`, `/v1/devices/{id}/commands[...]`, `/v1/devices/{id}/revoke`; WS endpoint `/v1/devices/connect`.

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
