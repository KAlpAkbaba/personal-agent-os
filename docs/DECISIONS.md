# Architecture Decision Log

Claude must append dated ADR-style entries here.

## ADR-0001 — Hybrid cloud + local device architecture

Status: Accepted

Decision: Cloud core persists tasks/memory/artifacts; Windows agent executes local privileged/interactive actions.

Reason: A pure cloud agent cannot directly and reliably control the owner's Windows desktop without a device-local component.

## ADR-0002 — Native Windows primary development environment

Status: Accepted

Decision: Develop primarily from native Windows Claude Code with Docker/WSL2 for Linux services.

Reason: Windows UI Automation, PowerShell and service/session integration are first-class requirements.

## ADR-0003 — No GPU requirement for initial development

Status: Accepted

Decision: M-1 through initial voice integration must not require a local GPU. GPU is an optional accelerator for local STT/TTS/vision/LLMs.

## ADR-0004 — Hetzner NBG1 initial cloud

Status: Proposed/Accepted for initial implementation, verify SKU before provisioning.

Decision: Use one Nuremberg cloud VM + Nuremberg S3-compatible Object Storage + Tailscale.

## ADR-0005 — Docker Compose before Kubernetes

Status: Accepted

Decision: Kubernetes is not an early milestone dependency.

## ADR-0006 — PostgreSQL is canonical memory/domain store

Status: Accepted

Decision: Use pgvector and optional Mem0 integration; preserve project-owned schemas and exportability.

## ADR-0007 — Deterministic line endings via .gitattributes (2026-08-31)

Status: Accepted

Decision: `* text=auto eol=lf` with CRLF overrides for PowerShell/batch scripts; common media/office formats marked binary.

Reason: The repo is edited on Windows but ships Linux containers and CI; LF-normalized sources keep Docker builds, hashes and diffs reproducible. PowerShell files stay CRLF because Windows PowerShell 5.1 handles CRLF most predictably.

## ADR-0008 — Preflight script hardened against hangs and PATH drift (2026-08-31)

Status: Accepted

Decision: `scripts/preflight.ps1` rewritten to run every external probe through `System.Diagnostics.Process` with a hard timeout, resolve tools via absolute fallback paths, and emit both JSON and a console summary.

Reason: The original version hung indefinitely on this machine (blocked child process, no timeout) and depended on a session PATH that was observed to be incomplete (`System32` missing in spawned shells). A preflight that can hang violates the deterministic-gate principle. Also fixed a latent bug: the old script used `$args` as a function parameter name, which collides with PowerShell's automatic variable.

## ADR-0009 — User-scope toolchain installs, no UAC (2026-08-31)

Status: Accepted

Decision: Missing dev tools are installed user-scope without elevation: `uv` 0.12.7 (winget `--scope user`), `pnpm` 11.24.0 (`npm install -g`, lands in `%APPDATA%\npm`), GitHub CLI 2.98.0 (winget `--scope user`). Deferred: PowerShell 7 (optional; scripts stay 5.1-compatible), .NET SDK (installed user-scope via official `dotnet-install.ps1` when M1 Windows-agent work starts), Tailscale (machine service + login → owner action at M1).

Reason: Keeps M-1 free of UAC interruptions per the owner-experience policy while still making all M0-required tools available. Owner actions are batched in `docs/LOCAL_ENV_REPORT.md`.

## ADR-0010 — M0 language/runtime pinning strategy (2026-08-31)

Status: Accepted

Decision: Python services use `uv` with a project-pinned interpreter (uv-managed CPython, `pyproject.toml` + `uv.lock`), independent of the machine's mixed system Pythons (3.8/3.12/3.14). Node/web workspace uses pnpm with `packageManager` pinning and lockfile. All service dependencies pinned via lockfiles from the first commit.

Reason: The machine has multiple global Pythons; reproducibility must not depend on machine state.

## ADR-0011 — M0 dev infrastructure port scheme and loopback binding (2026-08-31)

Status: Accepted

Decision: The compose project `pagentos` (infra/docker/docker-compose.dev.yml) publishes all services on 127.0.0.1 only, using a 1xxxx host-port scheme to avoid local collisions: PostgreSQL 15432, Redis 16379, MinIO 19000/19001 (API/console), Temporal 17233 (gRPC) and 18233 (UI). Images pinned: pgvector/pgvector:pg16, redis:7-alpine, minio/minio:RELEASE.2025-04-22T22-12-26Z, temporalio/auto-setup:1.27.2, temporalio/ui:2.34.0. Temporal shares the pgvector PostgreSQL instance with its own `temporal`/`temporal_visibility` databases (auto-setup).

Reason: 5432 is occupied by a locally installed PostgreSQL and 8000 by wslrelay on this machine; loopback-only binding enforces the "never expose infra publicly" network rule from day one. Dev credentials (pagentos/pagentos-dev, minioadmin) are dev-only, documented in .env.example, and never valid outside the local loopback stack.

## ADR-0012 — Health checks use sync DB driver in a worker thread (2026-08-31)

Status: Accepted

Decision: `/v1/system/health` probes PostgreSQL with the synchronous psycopg driver via `asyncio.to_thread` instead of SQLAlchemy's async psycopg support.

Reason: psycopg async mode requires a selector event loop; uvicorn on Windows runs the Proactor loop, which made the async DB check fail (`InterfaceError`) even with a healthy database. The threaded sync probe is loop-agnostic and behaves identically in Linux containers.

## ADR-0013 — Web shell stack pinning (2026-08-31)

Status: Accepted

Decision: apps/web uses Next.js 16.3.3 + React 19.2.8 + TypeScript 7.0.2, managed by pnpm 11.24.0 in a root workspace (`pnpm-workspace.yaml`, single root `pnpm-lock.yaml`). Exact versions pinned in package.json. The M0 shell is a single client page that polls `/v1/system/health` (default base `http://127.0.0.1:8001`, override via `NEXT_PUBLIC_API_BASE`) and renders per-dependency status with a graceful "API unreachable" state, tr locale.

Reason: Architecture doc mandates Next.js/React pinned during M0; latest stable resolved at implementation time and locked for reproducibility.

## ADR-0014 — Device broker is a module of the api service (2026-08-31)

Status: Accepted

Decision: The M1 device broker lives inside `services/api` as `app/broker/` (REST routes + WS endpoint + presence/dispatch), not as a separate process. `services/device-broker/` documents the pointer. Separation into its own service happens only when scale or isolation demands it.

Reason: One process, one DB, one migration chain and one health surface make the M1 vertical slice deterministic and avoid premature microservices (constitution: the owner is not an operator; fewer moving parts to keep healthy).

## ADR-0015 — .NET 10 LTS user-scope for the Windows agent (2026-08-31)

Status: Accepted

Decision: Windows Device Agent targets .NET 10 LTS (SDK 10.0.400 pinned via `global.json`), installed user-scope via the official `dotnet-install.ps1` to `%LOCALAPPDATA%\Microsoft\dotnet` (no UAC). Architecture: `PagentOS.DeviceService` (background, Windows-Service-capable) + `PagentOS.SessionCompanion` (interactive session) + authenticated local named pipe, per the Windows Agent rule. In development both run as console processes; actual service installation requires UAC and is deferred as an owner action.

Reason: Architecture doc prescribes .NET current LTS and the service/companion split; user-scope install keeps the no-UAC policy intact.

## ADR-0016 — Device identity and protocol v1 choices (2026-08-31)

Status: Accepted

Decision: Device identity is an ECDSA P-256 keypair generated on-device (SPKI public key registered at enrollment; signature over `nonce||device_id` at WS handshake). Enrollment uses single-use, 15-minute, SHA-256-hashed tokens. Transport is agent-initiated outbound WebSocket with JSON frames validated against `packages/schemas/device-protocol.schema.json`; delivery is at-least-once with agent-side idempotency (persistent `idempotency_key -> terminal ack` store), giving effectively-once execution. Error taxonomy extends `docs/API_AND_PROTOCOLS.md` with `command_expired` and `cancelled`. Full contract: `packages/protocol/DEVICE_PROTOCOL.md`.

Reason: P-256/SHA-256 is native to both .NET and Python `cryptography`; Ed25519 support in .NET still requires third-party packages. At-least-once + idempotency is the standard robust choice for command delivery over flaky links and satisfies the retry-must-not-duplicate rule.

## ADR-0017 — Windows agent implementation choices (2026-08-31)

Status: Accepted

Decision:
- Agent signs handshake with DER (RFC 3279) ECDSA signatures; broker accepts DER and raw P1363, so both encodings are protocol-legal.
- Private key: unencrypted PKCS#8 PEM in the agent data dir (`%LOCALAPPDATA%\PagentOS\agent` in dev) with NTFS ACL restricted to the current user; DPAPI/TPM hardening deferred to a later milestone per protocol §2.
- Service↔companion IPC: named pipe `pagentos-companion-{user-SID}`, PipeSecurity current-user-only, single instance, newline-delimited JSON internal messages (not part of the device protocol).
- Idempotency store: single JSON file, LRU 1000, atomic temp+move writes; terminal ack persisted before send so lost sends are recovered by broker redelivery; corrupt store quarantined to `.corrupt` and restarted empty.
- `devices/windows-agent/NuGet.config` pins nuget.org because the machine has no configured NuGet source.
- Presence tracking is process-local in the broker (valid single-process; revisit if broker scales out). Broker DB access is sync SQLAlchemy in `asyncio.to_thread` (same Proactor-loop rationale as ADR-0012).

Reason: Each choice keeps the M1 slice deterministic and reversible while matching the two-process Windows architecture rule; hardening steps that need UAC or new infrastructure are explicitly deferred, not skipped silently.

## ADR-0018 — Browser agent semantics (M2, 2026-08-31)

Status: Accepted

Decision: `services/browser` (Python 3.12, Playwright 1.62.0 pinned) is the runtime browser adapter, enforcing the semantic-control hierarchy at the type level: `TargetSpec` expresses role+name / text / label / placeholder / test-id only — CSS, XPath and coordinates are not expressible in the public API. Two session routes: `launch_dedicated()` (managed Chromium) and `connect_existing_cdp()` (existing-session route, E2E-tested against a throwaway Chromium with `--remote-debugging-port`; attaching the owner's real Edge/Chrome is documented for a later milestone via companion-managed loopback CDP or an extension bridge). Typed `BrowserError(error_class, retryable, evidence)` maps Playwright failures onto the project taxonomy (resolve-phase timeout → `ui_target_not_found`; actionability/race failures → `ui_state_changed`, retryable; connection failures → `dependency_unavailable`, retryable). `with_retry` retries retryable classes only and preserves the original error_class on exhaustion (annotating attempts in evidence) — collapsing to `retry_exhausted` is the orchestrator's job, which owns the whole task retry budget. Ambiguous locators resolve `.first` and report `match_count` instead of strict-mode errors. A single `escape_hatch_click_xy` exists as an explicitly warning-logged last resort; it is not reachable via `TargetSpec` and is exercised by exactly one marked fallback test. Playwright MCP remains development-time tooling, distinct from this runtime adapter.

Reason: Implements the constitution's browser rule (highest semantic surface, coordinates last resort) as API shape rather than convention, and keeps retry-budget ownership in one place.

## ADR-0019 — Browser backend adapter model and owner-browser enrollment (M2 delta, 2026-08-31)

Status: Accepted (owner architecture directive)

Decision:
- A `BrowserBackend` adapter interface separates transport from semantics. Backends: `ManagedBackend` (Playwright-managed dedicated/persistent profile — CI, fixtures, deterministic E2E; zero dependency on owner browser state), `ExistingSessionBackend` (attach to an already-running owner Chrome/Edge preserving logins/cookies/tabs/extensions), and a future `VisualFallbackBackend` (explicitly separate adapter; never mixed into the semantic engine). Higher-level browser commands are transport-agnostic.
- Owner-browser attachment is modeled as **browser enrollment**: an explicit authorization record (browser identity, transport, granted capability set) created once by the owner, analogous to device enrollment — never ad-hoc debug-port exposure. The enrollment transport for the real owner browser is a companion-managed loopback attach (extension bridge or supported channel mechanism when it ships); raw `--remote-debugging-port` against the owner's default profile is NOT the production dependency and any CDP endpoint must remain loopback-only. In M2 the existing-session backend is exercised against throwaway browsers.
- Every backend declares a `BrowserCapabilities` record: `authenticated_session`, `downloads`, `uploads`, `extensions`, `existing_tabs`, `multiple_windows`, `visual_fallback` — queryable by the orchestrator before dispatching work.
- A browser-command layer adds cancellation and idempotency (command_id/idempotency-key dedup) mirroring device-protocol semantics, so browser work is safely retryable end to end.
- Semantic priority remains `API/structured data -> DOM/role/accessibility -> text/semantic locator -> browser-level action`; coordinates stay prohibited in the M2 layer.

Reason: Deterministic tests must never depend on (or endanger) the owner's live browser; the owner path needs session preservation and explicit authorization; a stable adapter seam lets transports evolve (extension bridge, visual fallback) without rewriting agent logic.

## ADR-0020 — Research + Artifact model (M3, 2026-08-31)

Status: Accepted

Decision: The research command is realized as `Task -> Artifact -> Presentation`. A durable Temporal `ResearchWorkflow` (idempotent activities plan → gather → compose → render) survives worker/broker restart so task intent is never lost. Research sources come through a provider-neutral `ResearchProvider` interface; M3 ships a `DeterministicResearchProvider` (seeded, offline — this is what the acceptance gate uses) with `WebResearchProvider` left as an explicit unimplemented network seam. The canonical artifact body is Turkish-first Markdown stored in Postgres (`artifact_versions.canonical_body`; `canonical_object_key` reserved for future large-body offload to object storage); renders (PDF/DOCX/HTML/TXT) are derived, content-hashed, and stored in MinIO via the ObjectStore, recorded in `artifact_renders`. The Executive layer is realized by storing `executive_summary` in its own `artifacts` column so a task ends `READY` and can be presented (notify-and-wait) without the workflow or the `GET /v1/artifacts/{id}` response ever returning the full body (body only via `?include=body` or `/canonical`). Renderers are deterministic (pinned timestamps, normalized OOXML zip) so content hashes are stable. New columns beyond DATA_MODEL.md: `artifacts.executive_summary`. New capability `desktop.open_artifact` (ADR references §6a of the protocol) lets an enrolled Windows device open a rendered artifact.

Reason: Durability and provider-independence are constitutional; deterministic renderers keep the gate reproducible and offline; the executive-summary column is what makes "notify briefly and wait" real rather than dumping long output onto the owner.

## ADR-0021 — PDF embeds a bundled Unicode font; scoped CORS (M3, 2026-08-31)

Status: Accepted

Decision: (a) The PDF renderer embeds a bundled DejaVu Sans subset (regular+bold, permissive license, under `services/api/app/artifacts/fonts`) instead of a latin-1 core font, so full Turkish orthography renders identically on every host — Turkish is first-class and PDF is the default mobile presentation format, so silent ASCII-folding was a real defect, not a cosmetic follow-up. (b) The API declares a scoped CORS allowlist (`web_origins`, loopback dev origins by default, never `*`, production overrides via `PAGENTOS_WEB_ORIGINS`) because the web shell is a separate origin from the API — this resolves the deferred M0 security review finding #3 now that the Artifact Inbox performs cross-origin fetches. The M3 end-to-end flow (research → READY → executive-summary inbox → PDF/DOCX download) was verified live in a real browser.

Reason: Both are correctness requirements surfaced by exercising the real M3 vertical slice, fixed rather than deferred.

## ADR-0022 — M4 voice: local-first, provider-neutral, owner-audio deferred (2026-08-31)

Status: Accepted

Decision: M4 is split into a deterministic local core (buildable and gatable now with no owner action) and a real-audio/real-provider layer (owner action). The local core: a deterministic tr-TR narration normalizer (dates/clock/decimal/percentage/lira+currency/thousands/ordinals/phone/IP-CIDR/version/email/URL/path/abbreviations/mixed TR-EN, driven by a machine-readable eval dataset under `evals/voice`), semantic table/code narration, a narration engine (stable section/paragraph/sentence cursor IDs per API_AND_PROTOCOLS §7, chunk planning + cancellation, cross-device cursor persistence via `narration_sessions`), the "oku/dur/devam/tekrar/jump/explain" command state machine ("dur" top priority; explain returns to the exact cursor), provider-neutral `TTSProvider`/`STTProvider`/`RealtimeProvider` interfaces with deterministic fakes and HTTP-mocked real adapter skeletons (ElevenLabs/Azure/OpenAI, Faster-Whisper local fallback), a benchmark harness comparing ≥2 providers producing STT+TTS reports, a provider fallback router, an OWNER/NOT_OWNER/UNCERTAIN speaker-verification classifier (cosine similarity + threshold band + device-trust signal; voice never the sole secret) tested with fixture embeddings, and voice preferences (VOICE_SPEC §12). The migration is shared (`0004_voice`, lead-authored) so the two workstreams don't race the alembic chain. Owner actions batched: real voice-provider API keys and owner speech samples for the real-audio A/B quality benchmark and real speaker enrollment; these plug into the same interfaces without code changes.

Reason: Voice quality is a core acceptance criterion, but the constitution forbids vendor lock-in and requires deterministic gates. Building the interfaces, deterministic engine, and harness first means the owner's one-time audio/key actions later slot into a proven, tested skeleton rather than blocking all M4 progress.

Implementation addenda (2026-08-31): Spoken separators follow Turkish convention — `@`→"et", `.`→"nokta", `/`→"bölü" (CIDR mask and URL path), `\`→"ters bölü". Per constitution §5 (don't encode pronunciation guesses), abbreviations/acronyms are read from the owner pronunciation dictionary; a mechanical Turkish letter-spelling fallback exists but is opt-in (`spell_acronyms=True`, default off) so "API"/"IP" pass through untouched until the owner supplies a form. Ordinal detection is conservative (`N'nci` apostrophe forms and `N. <noun>` only) to avoid misreading a sentence-final "N." as an ordinal; in `technical` mode grouped-thousands collapsing is disabled so `192.168.100.200` never reads as a magnitude. Speaker-verification thresholds: owner_accept≥0.75, not_owner≤0.45 (the gap is the UNCERTAIN band), with an untrusted-device penalty that caps an untrusted session at UNCERTAIN regardless of voice similarity (voice is never the sole secret, VOICE_SPEC §10). Benchmark reports are stored in the object store (no benchmark table) reusing the render/object-store seam.

## ADR-0023 — Memory as a first-class subsystem (M5, 2026-08-31)

Status: Accepted (owner directive)

Decision: Memory is implemented as a first-class subsystem, not a vector-store wrapper. Six memory classes (preference, episodic, project, semantic, procedural, voice_preference) live in a lead-frozen canonical PostgreSQL schema (migration `0005_memory`). A Memory Write Policy grades every observation `ignore -> session -> candidate -> durable`; explicit owner instructions carry Actor.OWNER authority, enter durable directly, and can never be silently rewritten by inference (contradicting inferred evidence records a conflict instead). Inferred memories require both evidence-count (≥3) and confidence (≥0.7) thresholds to promote; a single observation is capped at 0.4 confidence. Forgetting is a hard delete cascading to versions, evidence and pgvector rows — nothing resurfaces from any index, and the audit trail keeps identifiers/reasons but never content post-forget. Retrieval is hybrid: pgvector (hnsw, cosine) + structured relational + deterministic weighted reranking with temporal filters and strict project isolation, always excluding superseded rows. Embedding rows carry (model_id, model_version, dim) so re-embedding is an index rebuild, never a canonical-data migration; the `Embedder` seam ships with a deterministic offline n-gram hash embedder for tests/eval (semantic-grade embeddings are a provider plug-in later). The whole API sits behind a `MemoryBackend` abstraction so Mem0 or another engine can be evaluated as an adapter without becoming an opaque source of truth (MEMORY_SPEC §3). Secrets/credentials/tokens are refused as memory content. A seeded deterministic retrieval evaluation (corpus + known queries) gates precision and requires zero cross-project contamination. Foundational constraint for the Evolution Engine (recorded ahead of M6/M7): it may consume this subsystem but must not alter explicit owner preferences or recovery/security roots.

Reason: Memory feeds future self-learning and evolution; retrofitting provenance, authority ordering, forget semantics or backend independence onto a naive vector store would be far costlier than building them in from the start.

Security addendum (2026-08-31, M5 review): OWNER authority in the write policy requires the caller-asserted `explicit` flag from a trusted owner surface — a trigger phrase inside arbitrary text is never sufficient by itself (prompt-injection defense per SECURITY_MODEL §6; phrase-matched text becomes at most a strong candidate signal). The owner-only guard covers edit, supersede, pin AND forget (the irreversible path), plus corroboration bookkeeping; the secrets guard covers edit/supersede and the provenance/source payload. Inferred episodic candidates carry SHORT retention and are swept.

Implementation addenda (2026-08-31): Episodic memories are excluded from semantic dedup — episodes are time-anchored distinct events, and merging similar workflow runs would starve procedural detection. Project-scoped retrieval is strict (only that project's rows; global/NULL-project rows excluded), which is what guarantees contamination = 0. An owner explicitly confirming an existing inferred memory upgrades it in place (explicit=True, durable, confidence 1.0, `confirmed_explicit` audit) instead of duplicating. Procedural proposals carry full supporting-episode evidence but are clamped to candidate/0.4 confidence — promotion requires owner/behavioral confirmation, never automatic. REST mutations act with owner authority by design (single-owner surface); other actors exist only at the service layer. Hybrid rerank weights (0.55 semantic / 0.15 recency / 0.15 confidence / 0.10 explicit / 0.05 project) are module constants, not runtime settings — changing them is a reviewed code change because it moves the deterministic eval. Confidence formula: `min(0.95, 0.3 + 0.2·(n−1))`, so promotion lands exactly at 3 corroborating observations.

## ADR-0024 — Self-healing pipeline with deterministic gate backend (M6, 2026-08-31)

Status: Accepted

Decision: M6 realizes the constitution's self-development rule as: **recovery first, coding second**, with the Recovery Supervisor strictly outside the blast radius. The supervisor (`services/recovery-supervisor`) is a stdlib-only, model-reasoning-free standalone process managing a versioned release workspace (release directories + `current`/`last_known_good` pointer FILES — Windows-friendly, never in-place overwrite), a transient-tolerant health policy (N sustained failures in a window; one blip never rolls back), automatic rollback to last-known-good, and file-outbox incident reports that the API ingests (outbox is source of truth if the API is down). Incidents carry a stable fingerprint (sha256 of component + error class + failing check) with dedup/occurrence counting (migration `0006_selfhealing`). The engineering loop implements EVOLUTION_ENGINE_SPEC §7 behind a `CodingBackend` interface: the acceptance gate runs a `DeterministicCodingBackend` that fixes the CONTROLLED injected-bug class offline (proving the pipeline mechanics: reproduce → regression-test-fails-before/passes-after → candidate → staging/canary → health → promote-or-reject), while `ClaudeCodingBackend` (Claude Agent SDK, the real v1 coding backend) plugs into the same seam and stays inert without configuration — the same provider-independence pattern as research/voice. Builder/reviewer separation is enforced by a separate deterministic `review_change` validator gate; an implement step alone can never promote. Per ADR-0023, the pipeline may read memory but is structurally barred from owner-actor memory mutations (guard test). The injected M6 bug targets a supervised browser-agent-shaped demo service under `staging/` (real processes, temp workspaces, free ports) so the whole story — inject → detect → fingerprint → auto-rollback → reproduce → fix → canary → promote, plus bad-candidate auto-rollback — runs deterministically in CI with no model calls.

Reason: The acceptance criteria demand deterministic proof of the loop, not of a model's coding luck; separating the pipeline (deterministic, testable) from the coding backend (pluggable, non-deterministic) satisfies both the gate and the constitution's provider-independence and recovery-survivability rules.

Implementation addenda (2026-08-31): `promote` marks only the health-verified ACTIVE release as last-known-good (an unverified release can never become the rollback target). The supervisor's ordering invariant is tested: rollback repoint → process-tree restart → recovery re-verify → THEN incident outbox write (recovery before reporting; outbox persists even when the API POST succeeds). An incident ingested after a supervisor rollback starts as `recovered`, not `open` — recovery already happened; `open` is reserved for unrecovered failures. The manifest-digest formula is deliberately duplicated between the stdlib supervisor and the API (the supervisor cannot import app code) and pinned equal by an E2E assertion. Selftest expectations live in the monitor, never in the release, so a broken release cannot redefine success. Pipeline/route workspace paths are confined to the configured workspace root. The reviewer gate is a separate authority: a backend whose own self-review would approve an unfixed patch is never consulted (LyingBackend test). Temporal durability was skipped for the orchestrator (plain deterministic class; a durable wrapper can be added around `pipeline.run` later). Self-healing config lives in `PAGENTOS_SELFHEALING_*` env vars.

## ADR-0019 addendum / M2 (2026-08-31): dead-endpoint navigation types as `dependency_unavailable` (retryable, `net::ERR_*` marker rule); cancellation is a recorded terminal state — duplicates of a cancelled command replay `cancelled` without re-execution (mirrors ADR-0017 idempotency semantics); iframe addressing is semantic-only (`frame="<name>"`, injection-rejecting); `ManagedBackend` rejects profile paths inside real Chrome/Edge/Chromium/Brave `User Data` trees as defense-in-depth; `EnrollmentRegistry` is in-process/file-backed in M2 with broker/DB persistence and the owner approval flow explicitly deferred; closing the last tab is refused (`validation_error`) in favor of closing the session.
