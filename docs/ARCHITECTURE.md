# Architecture

## 1. High-level topology

```text
                         OWNER
              +-----------+-----------+
              |           |           |
           Windows       Web        Mobile
              |           |           |
              +-----------+-----------+
                          |
                    Tailscale/private
                          |
                 +--------v---------+
                 |    CLOUD CORE    |
                 +------------------+
                 | API Gateway      |
                 | Orchestrator     |
                 | Temporal Worker  |
                 | Memory Service   |
                 | Artifact Service |
                 | Voice Gateway    |
                 | Device Broker    |
                 | Model Gateway    |
                 | Evolution Ctrl   |
                 | Telemetry        |
                 +-----+--------+---+
                       |        |
                  PostgreSQL   S3
                  + pgvector  artifacts
                       |
                    Temporal

Windows PC
+---------------------------------------------+
| Device Service (background/system)          |
|  - updater/watchdog                         |
|  - machine operations                       |
|  - secure IPC                               |
|                                             |
| Owner Session Companion                     |
|  - UI Automation                            |
|  - Browser bridge                           |
|  - audio/microphone                         |
|  - screen observation                       |
|  - local artifact open                      |
+---------------------------------------------+
```

## 2. Technology choices

### Web/PWA

- TypeScript
- Next.js/React (current stable/LTS ecosystem selected and pinned during M0)
- responsive PWA first
- WebSocket/SSE for task progress
- Web Audio/WebRTC for voice where supported

### Cloud API/orchestration

- Python 3.12+ class runtime unless M0 research identifies a compelling compatibility reason
- FastAPI
- Pydantic schemas
- Temporal Python SDK for durable workflows

### Persistence

- PostgreSQL
- pgvector extension
- Redis for cache/short-lived coordination only
- S3 abstraction: MinIO in local development; Hetzner Object Storage in production

### Windows Agent

Preferred implementation split:

- .NET current LTS for service/companion/native integration
- Windows Service for machine/background responsibilities
- user-session companion for interactive UI/audio
- named pipes or local authenticated IPC
- outbound WSS connection to Device Broker
- no inbound public Windows port

Python/UFO components may run as a separately sandboxed integration where they add value.

### Browser

- Playwright
- Playwright MCP for development/integration
- extension/CDP bridge for current user session
- accessibility/DOM snapshot preferred over pixels

### Durable workflows

Temporal is used for:

- research jobs;
- long artifact generation;
- narration preprocessing;
- device commands with retry/idempotency;
- scheduled checks;
- incident repair pipeline;
- self-extension pipeline;
- canary evaluation.

A cloud process restart must not erase workflow intent.

### Telemetry

- structured JSON logs;
- OpenTelemetry traces/metrics;
- Prometheus metrics;
- Grafana dashboards;
- Loki logs initially;
- health endpoints and SLO counters.

## 3. Main services

### `api`

Owner-facing HTTP/WebSocket boundary. Does not contain domain logic that cannot be called by background workers.

### `orchestrator`

Converts owner intent into durable task plans and invokes capability registry.

### `temporal-worker`

Executes durable workflows and activities with idempotency.

### `device-broker`

Tracks enrolled devices, outbound sessions, capabilities and command acknowledgements.

### `memory`

Canonical owner memory service. PostgreSQL is source of truth. A Mem0 adapter can be evaluated, but memory semantics belong to this project.

### `artifact`

Stores canonical artifact metadata and S3 objects. Handles render jobs, versioning and cross-device presentation.

### `voice-gateway`

Provider-neutral STT/TTS/realtime abstraction. Does not own long-term voice preferences; those live in memory/settings.

### `narration`

Converts semantic document structure into spoken form, handles Turkish normalization, chunking and resume position.

### `model-gateway`

Provider-neutral reasoning/coding/embedding interface. Supports budget, latency and capability routing.

### `evolution-controller`

Detects capability/quality gaps and creates engineering work orders. It cannot directly overwrite production binaries.

### `recovery-supervisor`

Minimal independent process/service that validates health and can return to last-known-good release.

## 4. Task state model

Minimum states:

`CREATED -> PLANNED -> RUNNING -> WAITING_EXTERNAL -> RENDERING -> READY -> PRESENTING -> COMPLETED`

Error paths:

`RUNNING -> RETRYING -> FAILED_RECOVERABLE -> RUNNING`

or

`FAILED_TERMINAL`

A task may be `READY` while no presentation has occurred.

## 5. Artifact state model

`DRAFT -> CANONICAL_READY -> RENDERS_PENDING -> READY -> ARCHIVED`

Narration audio is a derived cache, not the only canonical representation.

## 6. Security identity

- one `owner_id` singleton;
- passkey/device session for app access;
- Tailscale/private network identity as network signal;
- device-specific asymmetric keypair;
- voice match as additional owner signal, never the only secret;
- signed commands with nonce/idempotency key.

## 7. Network rules

- Windows Device Agent initiates outbound connection.
- Cloud services bind to private network/localhost where practical.
- Owner web/PWA initially reachable over Tailscale Serve/private network.
- Do not expose PostgreSQL, Redis, Temporal internals, MinIO console or management endpoints publicly.
- Public domain/Cloudflare can be added later only if required.

## 8. Deployment model

Development:

`Windows + Docker Desktop/WSL2 + local containers + local Windows companion`

Production initial:

`1 Hetzner VM + Docker Compose + external S3 Object Storage + Tailscale`

Later scale only when metrics justify it. Do not introduce Kubernetes merely for conceptual elegance.

## 9. External component strategy

Evaluate and wrap, do not blindly fork:

- Microsoft UFO3/UFO2: Windows/multi-device agent reference and possible components.
- Playwright MCP: browser automation.
- Screenpipe: local screen/audio capture adapter candidate.
- Mem0: optional memory engine/backend candidate.
- Claude Agent SDK: coding backend for Evolution Engine v1.
- Faster-Whisper: local STT fallback candidate.

Each integration needs a version pin, license note, adapter boundary and fallback behavior.
