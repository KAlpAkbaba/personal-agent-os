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

Security addendum (2026-09-01, M6 review): any incident-supplied value that reaches GENERATED SOURCE must be validated to a strict token shape at a single choke point and re-validated at the splice point — the Critical finding was an unescaped evidence value spliced into a generated Python literal, which (with the unauthenticated ingest + pipeline endpoints) formed a network-to-promoted-code path. Evidence of an incident already in repair is immutable to later duplicate reports. Incident reports are size-bounded. The self-healing endpoints inherit the standing API-auth hard gate: they must be owner-authenticated before running outside a trusted loopback/private network and before any non-deterministic coding backend is enabled.

Implementation addenda (2026-08-31): `promote` marks only the health-verified ACTIVE release as last-known-good (an unverified release can never become the rollback target). The supervisor's ordering invariant is tested: rollback repoint → process-tree restart → recovery re-verify → THEN incident outbox write (recovery before reporting; outbox persists even when the API POST succeeds). An incident ingested after a supervisor rollback starts as `recovered`, not `open` — recovery already happened; `open` is reserved for unrecovered failures. The manifest-digest formula is deliberately duplicated between the stdlib supervisor and the API (the supervisor cannot import app code) and pinned equal by an E2E assertion. Selftest expectations live in the monitor, never in the release, so a broken release cannot redefine success. Pipeline/route workspace paths are confined to the configured workspace root. The reviewer gate is a separate authority: a backend whose own self-review would approve an unfixed patch is never consulted (LyingBackend test). Temporal durability was skipped for the orchestrator (plain deterministic class; a durable wrapper can be added around `pipeline.run` later). Self-healing config lives in `PAGENTOS_SELFHEALING_*` env vars.

## ADR-0025 — Evolution Engine boundaries and gate design (M7, 2026-09-01)

Status: Accepted

Decision: The Evolution Engine reuses the M6 machinery (CodingBackend seam, independent reviewer separation, release/manifest helpers, sandboxed workspaces) rather than duplicating it, and adds: a machine-readable **capability registry** (migration `0007_evolution`) where `resolve()` returns only `production` capabilities backed by a `registered` skill version, and registration is code-enforced to require a version that reached `evaluated` with passing gates; a **gap-detection decision tree** (existing → compose → configure → extend → generate → product/core change) whose trail is persisted to `capability_gaps.decision_trail_json`, so "composition was attempted first" is auditable evidence rather than a claim, and generation is unreachable until composition has genuinely been tried and reported insufficient; **generated skills** in the §10 layout produced by a `SkillGenerator` seam (deterministic generator for the gate, Claude Agent SDK generator inert without configuration); **evaluation that actually runs** the generated tests and evals in an isolated subprocess and scores them per §9 — no promotion from reviewer approval alone; and **original-task resumption** after registration.

**Boundary verification (explicit owner requirement).** Before implementing the engine, three constraints were verified as design preconditions and are enforced by guard tests, not convention:
1. *Memory*: the evolution modules contain no owner-actor memory mutation path — the engine may read memory and write inferred observations, but explicit owner preferences are structurally unreachable (ADR-0023's owner-only gate on edit/supersede/pin/forget/corroborate).
2. *Recovery/security roots*: the sandbox root excludes `services/recovery-supervisor` and the repo's own source; a gap whose resolution would require changing core or recovery components is refused with `product_change_required` and never auto-generated (EVOLUTION_ENGINE_SPEC §12, constitution §6 — a new recovery version may be *developed*, but activation must preserve an independently runnable previous recovery path, which is a separate owner-gated step).
3. *Generated source*: every value from a request/observation that reaches generated code is validated to a strict token shape at the parsing choke point AND at each splice point (the M6 Critical lesson).

Reason: Self-extension is the highest-blast-radius capability in the product; the acceptance criteria demand auditable evidence for each guard, and the constitution demands that evolution can never silently rewrite what the owner explicitly stated or the recovery path the system falls back to.

### ADR-0025 security addendum (2026-09-01) — evidence must be verified, never self-reported

Two independent gates found the same class of defect: **trusting evidence the requester supplied**.

1. Permission approval keyed on a caller-asserted `authorized_asset` string with nothing to verify it against. Fixed with an `AuthorizationProvider` seam: approval requires a *verified* authorization whose recorded scope covers the requested grants. The default provider verifies nothing, so until M8's Authorized Asset Registry exists every grant is refused and generated skills reach production with empty grants — deny-by-default holds, and a false "owner-authorized, independently reviewed" record can never be written. M8's registry implements the same interface, so the owner-policy path (granting powerful tools to explicitly authorized assets) is preserved without Evolution touching the security root.
2. The composition-first gate validated the decision trail's *shape* rather than its truth, so a fabricated entry unlocked generation. Fixed by **re-deriving** the claim: the composer re-runs against live registry state and must independently agree composition cannot satisfy the request. This also closes the race where a capability registered after the trail was written makes composition sufficient.

General rule adopted for this subsystem: any gate whose evidence originates outside the gate must re-derive that evidence, not pattern-match a record of it. Runtime confinement remains a prerequisite before enabling an I/O-capable generated operation or a non-deterministic generator (see docs/reviews/M7_SECURITY_REVIEW.md verdict (d)).

### ADR-0025 delta (owner directive, 2026-09-01) — proving *true* self-extension

The engine must demonstrate capability self-extension, not registry CRUD plus a code-generation demo. Accepted extensions:

- **Six-step resolution order**, auditable per gap: existing → compose → configure/extend → install/adapt a compatible reusable component → generate → product/core change (last resort). Generation is refused while composition suffices.
- **Versioned capability manifest** carrying id, purpose, version, I/O schema, dependencies, network/filesystem/device permissions, secret requirements, external providers, expected side effects, risk classification, tests, evaluation metrics, provenance, builder identity, creation reason/task, and rollback version. Generated-skill permissions are **deny-by-default**.
- **Lifecycle** `candidate → sandbox → validated → shadow → canary → active → deprecated/rolled_back`, where `active` is unreachable on generator self-assertion; independent validation evidence is required at each promotion edge.
- **Isolation**: builds/executes in an isolated workspace/worktree/container, no production secrets by default, capability-scoped network/filesystem/device access.
- **Supply chain**: no blind installs; dependency name/version/source recorded and pinned; dependency and security scanning; install scripts cannot silently expand privileges.
- **Resource controls**: configurable execution timeout and CPU/memory/disk/network budgets, retry limit, and a recursion/self-extension depth limit that structurally prevents infinite agent→agent capability creation.
- **Failure matrix**: non-compiling code, failing tests, reviewer rejection, candidate crash, worse canary, unavailable dependency, and timeout each leave production on last-known-good with the system usable.
- **Existing-skill improvement**: telemetry-detected weakness → candidate → old-vs-new benchmark → promote only when objectively superior, old version rollback-capable.
- **Boundary clarification**: the recovery/security-root restriction constrains **self-modification authority only**. It must not block the owner policy subsystem from granting powerful tools to explicitly authorized devices/assets — Evolution never needs to weaken or rewrite the security root to enable owner-authorized operational capability.
- **Auditability**: each evolution answers why the capability was needed, what triggered it, what changed, which code/dependencies were introduced, which permissions were granted, which tests ran, who reviewed it, why it was promoted, and what the rollback target is.

## ADR-0026 — Authorized Asset Registry as the single scope authority (M8, 2026-09-01)

Status: Accepted

Decision: The Authorized Asset Registry (migration `0008_authorized_assets`) is the single source of truth for the constitution's enforcement question — *is this target/action within the owner's stored authorization scope?* An asset records what is authorized (locator/CIDR/device identity), which testing classes are allowed, the disruption/maintenance constraints, the authorization evidence, and a validity window. `app/security/scope.py` is the one enforcement point: it resolves a requested target against enrolled assets using real `ipaddress` membership (never string prefixing, so `10.20.30.40.evil.com` cannot masquerade as an in-scope host), verifies the testing class, status and validity window, and is fail-safe — unknown, ambiguous, expired, suspended or revoked resolves to refused. Every refusal writes an append-only `authorization_events` row, so out-of-scope targets can never be silently added and scope drift is impossible to hide. Within a recorded scope, assessments run without repeated approval prompts (authorization is scope-based, per SECURITY_MODEL §8), and remediation is gated on the asset's stored constraints. Assessment results become ordinary artifacts through the existing M3 artifact service rather than a parallel reporting path, and all collected evidence passes a redaction pass so findings can name an exposure without reproducing the secret.

Critically, this registry is also the verification source the M7 evolution permission model already depends on: `app/security/provider.py` implements the `AuthorizationProvider` interface introduced by the ADR-0025 security addendum, replacing the deny-everything default. Owner-authorized operational capability grants become approvable **because a registry entry proves the authorization**, not because a requester asserted an asset name.

Reason: The constitution grants the owner root authority over explicitly authorized assets and forbids scope drift; making one table the authority for both security testing and evolution permission grants means there is exactly one place to audit, and no second mechanism that could disagree with it.

Security addendum (2026-09-01, M8 review): the first implementation built `RegistryAuthorizationProvider` correctly and unit-tested it, but never wired it into `EvolutionRuntime` — so this ADR's claim that the registry backs evolution permission grants was false of the running system while looking true in the code. **A component that is built and tested in isolation but not wired changes nothing; regression tests for an integration point must assert the wiring, not the class.** Fixed, with such a test. Also fixed: the assessment collector decided containment with `is_symlink()`, which does not detect a Windows NTFS junction — the third occurrence of this bug class in the project (M3 `ArtifactOpener`, M6 ancestor walk, M8 collector), now recorded as a standing rule: *`is_symlink()` is not a containment check; resolve the path, then verify containment.* Finally, network locators broader than /16 (IPv4) or /48 (IPv6) and multicast/reserved/unspecified blocks are refused at enrollment: the owner may authorize wide ranges, but as deliberate, auditable entries rather than one silent `0.0.0.0/0`.

## ADR-0027 — Owner identity and API authentication (M9, 2026-09-01)

Status: Accepted

Decision: M9's first acceptance bullet ("authenticated native client connects") is the same API authentication/identity layer that has been the standing hard gate since M0, recorded against M4 (speaker verification must not gate a privileged action), M5 (memory owner-authority mutations and the explicit flag), M6 (self-healing ingest and pipeline runs), M7 (evolution endpoints create and promote code) and M8 (security endpoints enroll assets, i.e. define what the agent may touch). It is built here, and building it closes all five.

Shape, constrained by the constitution's single-owner model: **no accounts, roles, RBAC, signup or user management**. A one-time bootstrap mints the owner credential (an owner action); that credential is exchanged for opaque bearer sessions. Sessions are high-entropy tokens stored only as SHA-256 hashes and compared in constant time, scoped to a client kind, optionally bound to an enrolled device, with an absolute TTL and an idle timeout; every issuance, refresh, revocation, expiry and rejection is appended to `session_events` with a reason and never the token. `require_owner_session` is applied to every mutating or sensitive endpoint across memory, voice, narration, artifacts/tasks, device broker, self-healing, evolution and security; the only unauthenticated surfaces are `GET /v1/system/health` and the device WebSocket handshake, which carries its own ECDSA device authentication. **Fail closed**: with no owner credential bootstrapped, protected endpoints refuse everything — there is no default credential.

Revoking an enrolled device revokes every session bound to it (M9 acceptance). Kill/recovery controls per SECURITY_MODEL §10: a panic endpoint revoking all sessions, and an owner-recovery path that runs on the host rather than through the API, since the owner controls the machine and a lost credential must not require the very API it protects.

Reason: Every deferral of this layer was justified only by "nothing calls it yet". M9 finally does, and shipping a mobile client against unauthenticated endpoints that can promote code, enroll assets and mutate owner memory would be the single largest security regression available to this project.

### ADR-0027 implementation notes (M9, 2026-09-01) — what was built, and the three judgement calls

Built: `app/identity/` (models matching frozen migration `0009_identity_mobile`;
`tokens.py`; a file-backed `root.py`; `service.py`; `dependencies.py`;
`routes.py`; `recover.py`), `require_owner_session` applied across the API, and
device revocation wired into session revocation.

Token/session semantics: `secrets.token_urlsafe(32)` (256 bits, OS CSPRNG),
prefixed `pagentos_st_` / `pagentos_ok_` so a leak is identifiable by a scanner;
persisted only as SHA-256 (plain SHA-256, not a password KDF — key stretching
protects low-entropy human secrets and buys nothing against a full-entropy
random preimage, while costing a hash on every request); compared with
`hmac.compare_digest`; absolute TTL 30 days and idle timeout 7 days by default;
**refresh rotates the token on the same session row**, so a token that leaked
into a log or a backup dies at the next refresh while the session's identity,
device binding and audit history stay stable.

Three judgement calls worth recording:

1. **The identity root is a file, not a table.** Constitution §6 names "owner
   identity root" as part of the minimal recovery root that must remain usable
   when the application is broken. A credential hash that lives only in
   PostgreSQL makes owner recovery depend on the database *and* on the API the
   credential protects. The file (0o600 plus an inheritance-stripped Windows
   ACL, outside the tracked tree) is what makes `python -m app.identity.recover`
   possible at all — and the recovery path being host-side, not an endpoint, is
   the point: a "forgot my credential" endpoint is by construction an
   unauthenticated way to mint owner authority.

2. **`POST /v1/devices/enroll` stays owner-session-free** — a third deliberate
   exception alongside health and the WS handshake, not an oversight. The
   enrolling agent has an owner-minted, single-use, 15-minute enrollment token
   and, by construction, no owner session; requiring one would make enrollment
   impossible. It is the same shape as the WS handshake exception: a surface
   with its own credential whose trust chain roots in an owner action, since
   minting that token now requires an owner session *and* a loopback peer. A
   route-table sweep test asserts the complete open set, so a future endpoint
   added without the dependency fails the suite instead of shipping open.

3. **Scopes narrow, they never elevate.** A session with no scopes has full
   owner authority; a non-empty scope list restricts that client. This keeps
   the single-owner model intact (no roles, no RBAC, nothing to escalate to)
   while letting a constrained client — a phone, a device-bound session — be
   issued with less than everything. `require_scope` refuses with 403, a
   different coarse class from 401 because it is not fixable by
   re-authenticating.

Also decided: `/docs`, `/redoc` and `/openapi.json` are served only when
`PAGENTOS_ENVIRONMENT=dev`; enumerating every endpoint and request shape to an
unauthenticated caller is a gratuitous leak once the API is reachable over a
network. Rejection auditing is bounded by an in-process sliding-window limiter
so a scanner cannot flood `session_events`, and credential exchange is
throttled to 429 after 10 failures in 60 s.

Known follow-up, not in this change: `apps/web` fetches `/v1/artifacts` and
`/v1/tasks` with no Authorization header and will now receive 401. The web
shell needs an owner sign-in that stores a session token and sends it; that is
UI work outside this layer's ownership and is recorded as the immediate next
M9 task rather than silently patched here.

Security addendum (2026-09-01, M9 review — full record in
`docs/reviews/M9_SECURITY_REVIEW.md`): **"scopes narrow, never elevate" is now
enforced structurally rather than by convention.** As first written,
`require_scope` existed and was unit-tested but no shipped route used it, so a
scoped session had full owner authority everywhere — the M8 lesson repeating
itself one milestone later. Bare `require_owner_session` therefore now *refuses*
any session carrying scopes (403, audited as `scope_missing:<unrestricted>`);
a narrowed credential can only ever reach a route that explicitly declares a
scope, and forgetting `require_scope` on a route now fails closed instead of
open. Also decided in the same pass: **a corrupt identity root is not an absent
one** — `CorruptIdentityRoot` refuses credential exchange with the standard
coarse 401 and makes `POST /bootstrap` return 409 pointing at host recovery,
because treating an unreadable root as "not bootstrapped" would make damaging
the file a way to seize ownership. **Device revocation revokes sessions first
and the device row second**: the two writes cannot share a transaction, so the
ordering is chosen to fail towards "the revoke did not take, retry it" rather
than towards a revoked device whose bearer token still works. And the
artifact-ready announcer **delivers before it stamps** `announced_at`; the
guarantee is at-least-once with collapse-key idempotency, which is what
migration 0010 now says instead of the exactly-once it previously claimed.

## ADR-0019 addendum / M2 (2026-08-31): dead-endpoint navigation types as `dependency_unavailable` (retryable, `net::ERR_*` marker rule); cancellation is a recorded terminal state — duplicates of a cancelled command replay `cancelled` without re-execution (mirrors ADR-0017 idempotency semantics); iframe addressing is semantic-only (`frame="<name>"`, injection-rejecting); `ManagedBackend` rejects profile paths inside real Chrome/Edge/Chromium/Brave `User Data` trees as defense-in-depth; `EnrollmentRegistry` is in-process/file-backed in M2 with broker/DB persistence and the owner approval flow explicitly deferred; closing the last tab is refused (`validation_error`) in favor of closing the session.

## ADR-0028 - Session-0 IPC identity: the kernel decides who the companion is (2026-09-01)

Context: the M1 security review left one Medium finding open, and it was the gate on
installing the Windows Service. The service/companion named pipe was ACLed to
`WindowsIdentity.GetCurrent()` - correct while both halves ran as the same interactive
user, and wrong the moment the service becomes LocalSystem in Session 0, because then
"the creating process's account" is not the owner's account. There was also no mutual
authentication beyond that ACL: an ACL says who may open a pipe, never who did.

Decision: four independent layers, each with its own refusal reason in the audit trail.

1. **DACL** - SYSTEM keeps full control; the *configured* owner SID gets read/write. The
   owner SID is configuration (`PAGENTOS_AGENT_CompanionSid`), not an inference from the
   running process. When it is absent the service falls back to its own SID and says so
   loudly at startup, so a service install that forgot to set it is visible rather than
   silently self-authorizing.
2. **First instance** - the pipe is created with `FILE_FLAG_FIRST_PIPE_INSTANCE`. If
   anything already holds the name, creation fails and the service retries; it never
   becomes a second instance behind a squatter.
3. **Peer admission** - the connected process's account (`RunAsClient`), Windows session
   (`GetNamedPipeClientProcessId` -> `ProcessIdToSessionId`) and image path
   (`QueryFullProcessImageName`) are read from the kernel and judged by
   `CompanionAdmissionPolicy`. Session 0 is refused outright. The binary is pinned in a
   service install, because the owner's own account can start any program - without that
   check, "runs as the owner" would be the whole bar.
4. **Channel freshness** - the service issues a random connection id and nonce per accepted
   connection; every frame carries that id and a strictly increasing sequence
   (`IpcChannelGuard`). A frame from a retired connection, or one already seen, is refused
   and does not advance the window.

In the other direction the companion refuses a pipe whose **owner SID** is not an account
trusted to host the service. A standard user cannot create a SYSTEM-owned kernel object, so
this is the one claim an impostor on the same machine cannot make. The companion connects at
`TokenImpersonationLevel.Identification`: the service may identify it, never impersonate
it.

Judgement calls:

- **Identity comes from the kernel, never from a frame.** The handshake carries no
  credential, because any credential a same-user process could present, a same-user attacker
  could also present. What the handshake establishes is freshness, not identity.
- **A refused peer is told nothing** - the connection simply closes. The reason goes to the
  log and the audit trail, where the owner can see it and a local attacker probing which
  check it failed cannot.
- **"I could not check" is refusal, not a pass.** An unreadable image path under a pinned
  binary, an unidentifiable peer, an unreadable pipe descriptor: all refuse.
- **Developer mode is labelled, not hidden.** `ServiceAdmissionPolicy.DeveloperMode` also
  trusts a pipe owned by the owner, because in a dev run the "service" is a process the owner
  started; `RequiresElevatedOwner` reports which posture produced a result so a
  developer-mode pass is never written up as a production one.

Consequence for testing: the adversarial cases that need a second account or a second logon
session are proven against an injected kernel answer at the one seam where that answer
enters the system, and are recorded as `PROVEN_PROXY` in `docs/QUALIFICATION.md` until
the service actually runs under LocalSystem. Pipe squatting, impostor-pipe refusal, replay
and stale-connection refusal are proven for real today.

### ADR-0028 security addendum (2026-09-01) - the check that shipped turned off

The independent review of this ADR found a **Critical** in the fix itself, and it is the
same defect class this project has now hit three times: the component was written, the
component was tested, and the binary that ships never used it.

`SessionCompanion/Program.cs` constructed `CompanionRuntime` with four positional
arguments and no `ServiceAdmissionPolicy`. The constructor default was
`DeveloperMode(currentUserSid)`, which trusts a pipe owned by the owner's own account in
addition to SYSTEM. UAC splits integrity level, not identity, so on an installed agent every
ordinary process in the owner's session shares that SID: any of them could have squatted the
pipe name during a service restart and driven `desktop.open_application` on the owner's
desktop. `ServiceMode()` was constructed nowhere outside the test suite - the tests passed
a policy the real program never passed, so a green suite said nothing about the shipped
posture.

Fixed, and the fix is about defaults rather than about remembering:

- `CompanionRuntime`'s own default is now `ServiceMode()`. Whatever a caller forgets, the
  safe posture is what it gets.
- `SessionCompanion.Program.BuildServicePolicy(mode, ownerSid)` is public and directly
  tested. It returns service mode for null, for an unrecognized value, **and** for
  `developer` without an owner SID - asking to widen trust without saying whose pipes to
  trust is not a request that can be honoured safely.
- The active posture is logged at startup every time, at warning level for developer mode.
  ADR-0028 claimed "developer mode is labelled, not hidden"; it was hidden, and now it is not.
- Developer runs opt in out loud: `--dev-trust` or `PAGENTOS_AGENT_ServiceTrustMode`.
  `scripts/e2e-m1-device.ps1` and the round-trip tests do exactly that, because in both the
  "service" is a process the owner started.

Two Mediums from the same review, also fixed. A missing `CompanionImagePath` now produces
the same loud startup warning as a missing `CompanionSid` - without the binary pin, any
process running as the owner in an interactive session is admitted, which is precisely the
gap the pin exists to close. And the pipe name is now derived from the OWNER
(`PipeNaming.ForOwnerSid`, with the service defaulting from `CompanionSid`) rather than
from the calling process: a Session-0 service naming its pipe after "the current user" would
listen on `pagentos-companion-S-1-5-18` while the companion waited on
`pagentos-companion-{ownerSid}` - two healthy-looking halves that never meet.

`docs/QUALIFICATION.md` 1.3 was corrected: its evidence line described the production
policy while the shipped binary ran developer trust. New criteria 1.12 and 1.13 track the
wiring and the pipe-name agreement, because the thing that went unnoticed was not covered by
any numbered criterion - which is how it went unnoticed.

Standing lesson, restated because restating it has not yet been enough: **a regression test
for an integration point must assert the wiring, not the class.** Seen in M8 (authorization
provider), M9 (`require_scope`), and now here.

## ADR-0029 — An elevated updater may read service-owned device state directly (2026-09-01)

Status: Accepted

Context: the real finalize run failed at `Get-Content state.json` — access denied even to an
elevated administrator. The owner's read-only inspection showed `state.json` with a
protected, EMPTY DACL (`D:PAI`, no ACEs) while `device.key` (explicit SYSTEM-read +
Administrators-full ACEs) and the `agent` directory (SYSTEM + Administrators) were intact.
Root cause, closed by timeline: `state.json` was rewritten by the *old installed binary's*
`AgentState.Save` at enrollment — atomic tmp→move, so the fresh file held only INHERITED
ACEs — and the later install run still carrying the pre-fix `/inheritance:r ... /T` icacls
stripped inherited ACEs tree-wide, its `(OI)(CI)`-flagged grants applying nothing to files.
Explicit ACEs survived, inherited ones died: the exact signature of the twice-seen
empty-DACL bug class, inflicted by a run that predated its fix. Not a new bug; one damaged
file plus an unanswered design question.

Decision — the question the incident forces: should an elevated owner/update process ever
read service-owned device state directly? **Yes, for `state.json` specifically**, because:

- `AgentState` is documented and enforced secret-free (device id, name, broker URL,
  enrolled-at). The device private key lives in a different file under a strictly narrower
  DACL (SYSTEM read-only), and no deployment flow reads it.
- Administrators-as-recovery-authority is already the model: the intended machine-state DACL
  is explicit `SYSTEM FullControl + Administrators FullControl`, so the elevated read needs
  no new grant. The minimum right the updater uses is Read; it arrives via the existing
  Administrators ACE. A separate metadata-broker service would add a privileged interface
  without removing any authority Administrators already hold on this machine.

Consequences, all implemented:

1. **ACL correctness is part of the write primitive.** `AgentState.Save` and
   `IdempotencyStore` apply `MachineMaterial.Protect(State)` explicitly after every atomic
   replace — audited as the only writers of these files; no script writes them. A new agent
   test replaces the state file twice and asserts explicit (never inherited) SYSTEM +
   Administrators ACEs survive the replace. The old binary — the one writer without this
   property — is exactly what the pending deployment replaces.
2. **Targeted repair, never a sweep**: `Restore-MachineStateAcl` repairs exactly the named
   state files (`state.json`, `idempotency.json`, `device.key`) and only when their DACL is
   empty or unreadable — no recursion, no takeown, no `/reset /T`, no principal beyond
   SYSTEM and Administrators, `device.key` keeping SYSTEM read-only. Healthy files are
   untouched (asserted by SDDL equality). `finalize-qualification.ps1` runs it as a guard
   before its state read.
3. **The posture is not widened**: a non-elevated caller is still denied after repair — a
   test asserts the denial. Users/Authenticated Users/Everyone appear nowhere.
4. **One strict read primitive, everywhere**: `Get-MachineStateDocument` decides existence
   by *directory listing* — because `Test-Path`/`File.Exists` answer FALSE for an
   access-denied file, and "not enrolled" is the answer that leads a bring-up script to
   RE-ENROLL a machine that already has a device identity. It guards with the targeted
   repair, throws a self-explaining error on denial, and refuses `device.key` by name. All
   four elevated readers (`finalize-qualification`, `qualify-device`,
   `rotate-owner-credential`, `complete-device-enrollment` — including its
   enrolled-already probe and its post-enroll verification) now go through it; no script
   reads `state.json` raw any more.

Tests: 7 new PS5.1 cases in `installer-acl.tests.ps1` (26/26) reproducing the empty-DACL
denial on a state file, repairing it, proving no-op on healthy files, idempotence, and the
reader's absent/denied/key-refusal semantics; 1 new agent test (`134/134`) proving
protection survives `Save`-over-`Save` atomic replacement.

## ADR-0030 — Scripts never touch cryptographic key APIs; the `identity` verb (2026-09-01)

Status: Accepted

Context: the finalize run got through the transactional deployment (journal committed,
service + companion + pipe healthy) and died in broker-registration restore on
`ECDsa.ImportFromPem` — a .NET Core 3+ API that Windows PowerShell 5.1's .NET Framework
does not have. The script was deriving the public SPKI from the device key to rebuild the
broker row. Fourth member of the same defect family (native quoting, StrictMode
cardinality, `??`): code that parses fine on 5.1 and fails only at the call, only on the
owner's machine.

Decision: **Windows PowerShell scripts never touch cryptographic key material or key APIs
— all key handling goes through the agent's own .NET implementation.** Concretely, a new
load-only verb: `PagentOS.DeviceService identity` prints exactly one JSON document
(device_id, name, broker URL, enrolled_at, `public_key_spki_b64`) on stdout, diagnostics
on stderr, per the machine-readable child protocol. The private key never crosses stdout;
the SPKI comes from the same `DeviceIdentity` the agent signs with, so enrolled key and
restored row cannot drift. Distinct exit codes: 3 = not enrolled, 4 = state exists but the
key is missing — opposite recovery situations a caller must not conflate. The verb never
creates anything: an identity question must not mint an identity.

Judgement calls:

- The helper is the **repo build run as a tool** against the installed data dir
  (`PAGENTOS_AGENT_DataDir` override), not the installed service exe — the committed
  runtime may predate the verb, and reading state never justifies touching it.
- No PS5.1-native fallback: producing SPKI from a PKCS#8 PEM on the Framework host means
  hand-parsing DER in script, which is crypto reimplementation and forbidden outright.
- PowerShell 7 was not made a requirement; the installed product's script surface stays
  Windows PowerShell 5.1.
- `finalize-qualification.ps1` gained phase resumability (`-StartPhase
  Auto|Deploy|RestoreBrokerRegistration`): Auto skips deployment when the journal says
  committed and nothing is staged, but only over a runtime re-verified healthy
  (service AND companion AND pipe — Running alone is not health).

Enforcement, both directions:

- `script-syntax.tests.ps1` now scans every script for .NET-Core-only crypto calls
  (`ImportFromPem`, `ImportPkcs8PrivateKey`, `ExportSubjectPublicKeyInfo`, …) and fails
  the gate on any hit — the class is detected before a real run, not during one.
- `identity-restore.tests.ps1` (new gate step, after the agent build): under actual
  Windows PowerShell 5.1, the REAL service exe performs a REAL enrollment against a raw
  loopback TCP listener the test operates (no HttpListener URL ACLs, no elevation, chunked
  encoding handled), capturing the registered public key; then the `identity` verb must
  return that key **byte-for-byte** — the exact property broker restoration depends on.
  Refusal legs prove exit 3 creates nothing and exit 4 never re-mints a key whose loss is
  a recovery situation. First test asserts the host really lacks `ImportFromPem`, so the
  premise is re-examined if the platform ever changes.

## ADR-0031 — Optional-property accessors, and broker reuse only on proof (2026-09-01)

Status: Accepted

Context: the next real finalize run got past deployment and Cloud Core startup, then died
in `dev-broker.ps1` on `$health.dependencies` — `PropertyNotFoundStrict`. The real health
schema is `{status, version, checks}` where `checks` maps subsystem→check; a top-level
`dependencies` array never existed. The script had also never declared StrictMode — it
inherited it from dot-sourced libraries, which is how an imagined property parsed fine and
died at the read, on the owner's machine, after the broker was already healthy.

Decisions:

1. **Optional JSON properties go through explicit accessors** — `Test-ObjectProperty`
   (presence; distinguishes absent from present-but-null, takes no default so the caller
   owns what absence means) and `Get-OptionalProperty` (value-or-null, display reads only).
   Properties the schema REQUIRES stay direct accesses on purpose: their absence should be
   loud. StrictMode is declared per script, never merely inherited. Covered by 5 new
   StrictMode cases (absent/null/scalar/empty/one/many, plus the real health shape and the
   checks-less document) in `installer-strictmode.tests.ps1` (21/21).
2. **A running Cloud Core is reused only when its DATABASE is proven.** dev-broker records
   `{pid, port, database, started_at}` in a marker when it starts an instance (removed on
   -Stop); `Get-DevBrokerDatabase` vouches for a database only when the marker matches a
   live broker process — dead pid, recycled pid, missing/unreadable marker, wrong image
   name all mean *unknown*, and unknown is not suitable. finalize reuses on
   `pagentos_prod` proof and restarts otherwise. Health alone never qualifies: the
   enrollment row was once destroyed by a healthy-looking broker on the wrong database.
3. Proving the fix against the real machine surfaced a third defect, fixed in the same
   change: an **elevated**-started broker hides its command line from a non-elevated
   querier, so process-match detection missed the live instance and a duplicate uvicorn
   was spawned (it died on the bound port while riding the existing instance's health).
   "Already running" is now process match OR a live listener on the port, and the marker
   liveness check falls back to pid+image-name for exactly this asymmetry.

Verified on the real machine: the fixed dev-broker detects the owner's elevated broker
(pid 46856) via its listener, spawns nothing, warns that its database cannot be proven
(it predates markers — the honest answer), and renders all 13 real subsystem checks from
the exact line that crashed.

## ADR-0032 — RQ-2 cloud bring-up shape (2026-09-01)

Status: Accepted

Context: local Windows qualification closed; the next milestone moves the Cloud Core to
the real Hetzner + Tailscale environment. These are the initial-bring-up decisions, each
reversible by configuration and each biased toward "prove the path first, optimize never
until RQ-2 closes":

1. **Artifact store: MinIO on the VPS first.** `CLOUD_INFRASTRUCTURE.md` targets Hetzner
   Object Storage; the app speaks S3 either way, so the migration is a `PAGENTOS_S3_*`
   config change later. Bringing up qualification must not wait on a second provider
   surface, and MinIO stays compose-internal (no published port).
2. **Image built on the host first, GHCR later.** The release model (CI → GHCR → pull) is
   the destination, but it needs the private repo + CI to exist. `deploy-cloud-core.sh`
   builds from the checked-out source; swapping `build:` for a GHCR `image:` later
   changes nothing else.
3. **The API binds the tailnet IP + loopback, nothing else.** Compose refuses to start
   with `PAGENTOS_BIND_IP` unset (`:?`), so forgetting the variable cannot fall open to
   0.0.0.0. Loopback exists for the one-time identity bootstrap (loopback-only guard
   stays on) and host-side recovery. Infra services publish no host port at all.
4. **The cloud identity root is a NEW bootstrap on the VPS**, on the persistent volume
   (`/mnt/pagentos-data/identity`), minted once by the owner through the loopback guard.
   The local root keeps serving the local dev broker; two instances, two roots, neither
   able to impersonate the other. Secrets in `/opt/pagentos/.env` are GENERATED on first
   deploy (root-owned 0600, never printed, never in git).
5. **The Windows device is NOT re-enrolled.** Its identity (id + P-256 key) is
   cryptographically valid for any broker; the cloud database gets the existing
   registration via `restore-device-row.sh` from the identity helper's non-secret
   document, and `switch-agent-broker.ps1` changes only the broker URL — atomically
   (`File.Replace`; a 3-arg `File.Move` is .NET-Core-only, the ImportFromPem class),
   with health-probe-before-switch, symmetric restart, and one-command rollback.
6. **Hetzner SKU is decided at provisioning**, not now: verify current NBG1 availability
   against the CPX42-class target in `CLOUD_INFRASTRUCTURE.md` §1 and record the choice
   here when the API token exists.

## Security investigation — `pagentos_ok_` in Git history (2026-09-01)

Status: Closed — no real credential ever entered Git; no history rewrite performed.

Question: `git log --all -S"pagentos_ok_"` names four commits (`bacda78`, `23df90b`,
`75d8fad`, `b2d2c29`). Did actual owner-credential material ever enter the repository?

Method: every occurrence in each of the four commits, in HEAD, and in the repository's one
unreachable commit (`5191d84`, no occurrences) was classified STRUCTURALLY — file, line,
and shape only; no matched value was ever printed. Shapes: bare prefix; short suffix
(<30 chars); token-shaped (≥30 urlsafe chars, identified by SHA-256 fingerprint only).

Findings:

1. **Every occurrence is prefix, placeholder, format-check, or off-shape fixture.** The
   `PREFIX` constant in `app/identity/tokens.py`; ADR-0027 prose; the web `OwnerGate`
   input placeholder; the reference client's format check; `startswith`-style assertions
   and bare-prefix fixtures across the identity unit tests; one 6-char-suffix fixture
   (`test_identity_root.py`); and one 33-char-suffix marker in
   `machine-readable.tests.ps1` whose declared purpose is asserting that error paths never
   quote a secret.
2. **Nothing could ever have authenticated.** A real mint is `token_urlsafe(32)` — a
   43-char suffix; nothing 40+ shaped ever appears in history. The single ≥30 candidate is
   33 chars, contains dictionary words ("test", "secret", "not" — hand-authored, not
   CSPRNG), and its SHA-256 does not match the real root's `credential_hash`.
3. **Fixtures are environmentally isolated**: identity unit tests run against temp roots
   and the test database; the machine-readable suite drives fake child processes in temp
   dirs. None touches the real root or `pagentos_prod`.
4. **No plaintext credential ever entered Git by any path**: the real root
   (`services/api/var/identity/owner_credential.json`) stores only a SHA-256, is
   gitignored (`services/api/var/`), and `git log --all` over that path is empty — never
   tracked, not even once.
5. **Never pushed anywhere**: the repository has NO remote configured (GitHub setup is
   still pending), one branch, no tags, no stashes.

Consequences: per the investigation's own rule, prefixes and off-shape fixtures do not
warrant a history rewrite — none was performed. Permanent enforcement added to the gate's
Secret hygiene step: a SHAPE-based scan fails on `pagentos_(ok|st)_` + 40 or more urlsafe
chars anywhere in tracked files, with **no allowlist** — synthetic fixtures stay
legitimate exactly as long as they stay off-shape, because a fixture that perfectly
imitates a production credential is indistinguishable from a leak. The scanner (and the
pre-existing generic one, fixed here) reports file:line only; a scanner that echoes what
it matched would be the leak it exists to prevent. Verified both ways: the current tree
passes; a 43-char probe is caught.

## First real CI run — four defects the local gate could not see (2026-09-01)

The private GitHub repository was created and `main` pushed; the first CI run failed three
jobs. Every failure was an assumption about the *environment* that the local gate satisfies
by accident, which is the same shape as every real-machine defect in RQ-1 — only this time
the "real machine" is a Linux runner and an elevated Windows runner.

1. **CI was testing nothing at all.** `tests/integration/conftest.py` implemented
   `pytest_collection_modifyitems` as an unscoped loop. Pytest hands a subdirectory
   conftest's hook the WHOLE session's collected items, not just the ones beneath it, so
   every unit test was marked `integration` too: `pytest -m "not integration"` deselected
   all 1418 and exited 5 having asserted nothing. The local gate never saw it because it
   scopes by path (`pytest tests/unit`). The hook now marks only items under its own
   directory — 1324 selected / 94 deselected, and the inverse for `-m integration`. This is
   the worst class of defect in the project's history: a gate that looks green and asserts
   nothing.

2. **The real-browser-profile guard was a no-op on Linux** — the platform the browser agent
   actually runs on in production. `_reject_real_profile_dir` scanned `Path(...).parts`, and
   a Windows-shaped string is a SINGLE segment on POSIX because a backslash is not a
   separator there, so the marker scan matched nothing. The markers themselves were also
   Windows-only, while a real Chrome profile in the container lives at
   `~/.config/google-chrome`. Fixed on both axes: Linux and macOS markers added, and every
   path is now read natively AND as a `PureWindowsPath`, so the guard answers the same way
   whichever OS is asked. The test carries all nine shapes.

3. **Two Windows agent tests asserted the host's elevation, not the policy.** The owner of a
   new kernel object comes from the creating token's default owner, and for a member of
   Administrators running elevated that is `BUILTIN\Administrators` — an account
   `ServiceAdmissionPolicy.ServiceMode` deliberately trusts. So on every GitHub runner (and
   in the owner's own elevated console) the "standard user" pipe those tests build was
   really owned by Administrators, the policy correctly accepted it, and the refusal under
   test never happened. The product was right; the tests were wrong. They now STATE the
   pipe's owner as the account's user SID via `NewPipeOwnedByCurrentUser`, so the adversarial
   condition is staged identically on every host instead of being skipped or assumed.

4. **Every CI job is now bounded** (`timeout-minutes`). A job that hangs otherwise burns
   hours of runner time and hides the real signal.

Also recorded, because it cost time and is worth recognising instantly next time: with
Docker Desktop stopped on Windows, loopback connections to the compose ports are refused in
~2 s each rather than instantly, which makes the dependency-probing unit tests look hung.
On a Linux runner with nothing listening the same connections are refused in ~0 ms. The
symptom looked like a product hang and was a stopped container engine.

## ADR-0033 — Hetzner SKU: CPX32, not CPX42 (2026-09-01)

Status: Accepted (owner decision)

`CLOUD_INFRASTRUCTURE.md` §1 targets a CPX42-class host (8 vCPU / 16 GB) and requires the
current price to be verified before provisioning. Verified: CPX42 exists in NBG1 with 8
vCPU / 16 GB / 320 GB NVMe / 20 TB traffic — but Hetzner's price adjustment of 15 June
2026 moved it from €25.49 to **€69.49/month**, with CPX32 (4 vCPU / 8 GB / 160 GB) going
from €13.99 to €35.49.

Decision: provision **CPX32**, and keep the separate 100 GB data volume.

Reason: the original recommendation was made against a price that no longer exists, and the
requirement behind it was headroom, not a SKU. The stack actually deployed is five
containers — PostgreSQL, Redis, MinIO, Temporal, the API — which sit around 2–3 GB in
practice, so 8 GB leaves genuine margin. Hetzner supports rescaling CPU/RAM upward in
place, so this is reversible from measured need rather than guessed at up front, which is
what the constitution asks for ("scale from measured need, not speculation"; "do not
pre-emptively create cluster complexity"). Revisit if real telemetry shows memory pressure
— that is an M10 input, and M10 is gated on RQ-2 completing.

The data volume is kept despite the extra ~€4–5/month because it is what makes the host
disposable: PostgreSQL, the artifact store and the owner-identity root live on it, so the
server can be rebuilt, resized or replaced without taking them along.
`scripts/cloud/deploy-cloud-core.sh` refuses to start if that data would land on the boot
disk instead, so the property is enforced rather than assumed.

Prices move: this one changed once already between the plan being written and the machine
being ordered. Verify in the console at provisioning time rather than trusting this ADR.

## Incident — the provisioning run that said it stopped, and had not (2026-09-01)

`scripts/cloud/provision.ps1 -Apply` ended with:

    Cannot convert value "System.Management.Automation.PSCustomObject"
    to type "System.Management.Automation.SwitchParameter"

and the natural reading — that it failed on the way into the apply phase — was wrong.

The script declared `[switch]$Apply` and later wrote `$apply = Invoke-NativeProcess ...`.
PowerShell variable names are case-INSENSITIVE, so that is not a new variable: it is an
assignment to the parameter, and the result object cannot become a `SwitchParameter`. The
assignment is evaluated **after** the child process returns, so `tofu apply` had already
run to completion. Four billable resources existed — server `164238173` (`pagentos-core`,
cpx32, nbg1, running), a 100 GB volume, a firewall and an SSH key — while the operator
believed nothing had been created. Verified from local state before saying so, exactly as
the owner required: `tofu state list` plus the declared outputs, never `tofu show` of the
whole state, because the server's `user_data` carries the Tailscale auth key.

Fixes:

- intent is captured once as `$shouldApply = [bool]$Apply` and every result object has a
  name no parameter wants (`$planResult`, `$applyResult`, `$initResult`, `$outputResult`,
  `$tofuVersionResult`);
- a **parser lint over every script** for the whole class: an assignment to a `[switch]`
  parameter, or to any parameter under different casing. It immediately found a second,
  live instance — `bootstrap-owner-credential.ps1` declared `[switch]$Status` and then did
  `$status = $_.Exception.Response.StatusCode.value__`. An int coerces to a
  `SwitchParameter` instead of throwing, so nothing looked wrong, but `$status -eq 409`
  then compared switch-to-switch and was true for **any** non-zero HTTP status: the
  "credential already exists" and "non-loopback refused" branches both fired on every
  error alike, in the owner-credential bootstrap. Renamed to `$statusCode`;
- `provision.ps1` is now driven end-to-end in tests by a fake `tofu`, so the apply path is
  exercised without paid infrastructure. 12 tests: plan-only never applies, `-Apply`
  applies exactly the plan it just saved and showed, the saved plan is deleted afterwards,
  `-ExpectAdd/-ExpectChange/-ExpectDestroy` refuse a plan that is not the reviewed one, a
  plan containing any delete refuses without `-AllowDestroy`, a no-op plan does not churn,
  and no credential value is ever echoed;
- the saved plan moved out of the repository into a temp file deleted in a `finally`, and
  `tfplan.binary` is gitignored. A saved plan embeds the variable values it was planned
  with — including the Tailscale auth key — so it is secret-bearing at rest exactly like
  state, and it was sitting untracked-but-not-ignored, one `git add -A` from being
  committed.

Two lessons worth keeping. A crash after a side effect is not evidence the side effect did
not happen — check the world, not the traceback. And an assertion anchored on an error
*message* is anchored on the host's display language: the first version of the collision
test matched "SwitchParameter" and failed on this Turkish-language machine, so it now
matches the exception type instead.

## Incident — `File.Replace` and the empty third argument (2026-09-02)

The real agent migration got through cloud health, the identity read and the existing-device
registration, then died switching the broker endpoint:

    Exception calling "Replace" with "3" argument(s): "The path is not in a valid format."

Diagnosed by probing the call before changing anything. The overload is
`System.IO.File.Replace(String, String, String)` — there is only one with three parameters.
The source and destination were absolute, rooted, existing, and in a directory containing a
space; both fine. The third argument was the defect: **PowerShell binds `$null` to a
`[string]` parameter as an empty string**, and `File.Replace` refuses `""` as a path.
Passing `[NullString]::Value` succeeds; passing a real backup path on the same volume
succeeds *and* produces the rollback copy inside the same atomic NTFS operation.

State on the machine when it failed, established before touching anything: the live
`appsettings.json` still pointed at `127.0.0.1`, untouched; the staging file and the
backup copy had been written beforehand and were harmless. The failed call had no side
effect on the live file — `File.Replace` validates its arguments first.

Decisions:

1. **The transactional primitive is a library, not inline code** —
   `scripts/lib/ConfigSwap.ps1`. It never passes null or empty for the backup; every path
   is `GetFullPath`-normalised and required to be rooted (nothing depends on the current
   directory); staging file and backup must live in the destination's own directory, so
   all three are on one volume and the replace is atomic rather than a copy; a stale
   staging file from an interrupted attempt is overwritten, not trusted; an existing
   backup is overwritten; the staged content is validated (JSON, the runtime identity
   keys preserved, endpoints well-formed) **before** it goes live and read back
   **after**, byte for byte; the DACL is checked afterwards rather than assumed — an
   empty DACL on a config file has already taken this service down once; and applying
   the same bytes twice is a no-op that reports `Replaced = $false`.
2. **`switch-agent-broker.ps1` is a transaction with a journal**: probe the new broker →
   read → stage → validate → stop runtime → atomic replace → verify ACL → start runtime →
   verify (service + companion + pipe, and the cloud still answers, and the live file says
   the new URL) → commit. Any failure after the replace restores the previous
   configuration atomically from the backup, restarts, re-verifies, and only then throws.
   `-Rollback` does the same on demand; the failed configuration is kept aside as
   `appsettings.json.failed` for diagnosis.
3. **Only the broker keys change.** `DataDir`, `PipeName`, `CompanionSid` and
   `CompanionImagePath` are the qualified runtime's identity and are preserved verbatim;
   a configuration missing any of them is refused as "not the installed agent's".
4. `migrate-agent-to-cloud.ps1 -StartPhase SwitchBroker` resumes at the switch without
   re-reading machine material or re-registering — the completed cloud steps stay done.

13 tests in `config-swap.tests.ps1`, all against real files in a directory whose name
contains a space: the incident reproduced (`$null` → `ArgumentException`, live file
untouched), absolute-path and same-volume enforcement, the replace itself, a stale staging
file, an existing backup, validation before going live, the caller's validator refusing a
config that lost its identity, rollback, idempotence, and the post-replace DACL.

## Incident — cloud owner bootstrap returned 403 (2026-09-02)

The migration reached the cloud owner-credential bootstrap and the mint failed with HTTP 403
before any credential was shown. Diagnosed from the guard, the deployment shape and the
root's state — not by retrying:

- the cloud identity root was **absent** (`/mnt/pagentos-data/identity/owner_credential.json`
  does not exist), so this was never a "second owner" 409; no owner exists yet;
- `_require_loopback` accepts only `request.client.host` ∈ `{127.0.0.1, ::1, localhost,
  testclient}` and refuses with a coarse 403, fail-closed (M9 review #3);
- the request was `curl -X POST http://127.0.0.1:8001/...` run **on the host** against the
  **published** container port. Through Docker port publishing the API sees that request
  arriving from the bridge gateway (`172.x`), not from 127.0.0.1. The guard cannot tell NAT
  from a stranger and refused — which is the guard working, not failing.

Decision: **the guard is not loosened.** The canonical host-side bootstrap for a containerised
Cloud Core is made *from inside the API's own network namespace*:

    docker exec pagentos-prod-api curl -fsS -X POST http://127.0.0.1:8001/v1/identity/bootstrap

`docker exec` requires root on the host (Tailscale SSH), which is exactly the trust boundary
bootstrap is defined by — "the owner is on the machine". The request is made from where the
guard can see it is local; nothing about who may mint owner authority changed. This is the
containerised form of ADR-0027's rule that owner recovery happens on the host, never through
the network-facing API.

A second defect in the same script, fixed alongside: a native command's non-zero exit does
not throw in PowerShell, so the first run sailed past the 403 straight into "paste the
credential" with nothing to paste. The mint's exit code is now checked and the script stops
there, with the meaning of 403 vs 409 spelled out. `-StartPhase Bootstrap` resumes at this
exact phase and verifies the completed broker switch in place instead of redoing it.

Also observed: Tailscale SSH's periodic re-authentication check for root sessions surfaced
during diagnosis ("Tailscale SSH requires an additional check"). It is a browser link only
the owner can open; it gates the agent's host access but not the owner's own run, where the
link appears in their console.

## Incident — the audit row that was there all along (2026-09-02)

The cloud qualification driver closed every runtime and recovery row `PROVEN_REAL` and left
exactly one open: "agent audit trail records the command" — *no command row found in
agent-audit.jsonl since run start* — while, on the same screen, real commands opened Notepad
and were ACKed from the Hetzner broker.

Diagnosed from the writer, not by retrying. `AuditLog` emits `ts`, `event`, and (when
present) `device_id`, `command_id`, `trace_id`, `capability`, `status`, `detail`; the
dispatcher — the same one whichever broker is dialled — writes `command_received` and then
`command_ack` for every command. The verifier read the timestamp from a field named `at`,
which the writer has never emitted, so the time filter rejected every row that ever existed.
It also matched rows loosely (`event -match "command"`) and read only a 200-line tail. **The
writer was right; the verifier was wrong.** The product path was never in doubt — the
evidence path was.

Decisions:

1. **Correlate by identity, not by vocabulary.** `scripts/lib/AgentAudit.ps1` reads the whole
   file, parses `ts`, and proves a command only by rows carrying THAT `command_id` (and
   `trace_id`) with both `command_received` and `command_ack` present. Rows older than the
   run, a different command's rows, or a trace mismatch are reported as such — never
   counted. Unparseable lines are counted, not hidden.
2. **The schema is pinned where it is produced.** `AuditLogSchemaTests.cs` writes real rows
   through the real `AuditLog` and asserts the exact key set (and that `at` is absent).
   The PowerShell fixture is documented as the same contract; if the writer changes, the C#
   test fails first.
3. **Non-fatal is not silent.** The writer still never throws into the audited code path
   (a throwing logger once killed the pipe server), but a failed audit write is now counted
   (`FailedWrites`) and each new failure message is written once to stderr, which the
   service host captures. A trail that has quietly stopped is the exact shape of this
   incident, and it must be observable.
4. The driver's baseline and a new `-Scenarios audit-only` mode carry the ACK's
   `command_id`/`trace_id` into the audit check and print the correlated evidence —
   service pid, companion pid, the rows' `ts` — so the matrix row is closed by a persisted
   row that matches the broker's ACK, and by nothing less.

## RQ-2 closed — the cloud is a proven baseline (2026-09-02)

Status: Closed on real evidence, on the owner's actual machines.

`pagentos-core` (Hetzner cpx32, nbg1) runs the production Cloud Core on `pagentos_prod`,
reachable only over the tailnet — every public port refused, proven from outside. The
Windows agent was moved to it without reinstall or re-enrolment. The headline path —
Hetzner Cloud Core → Tailscale → DeviceService (LocalSystem, Session 0) → Session Companion
(Session 1) → real Notepad → ACK — is `PROVEN_REAL`, and `command_received`/`command_ack`
are persisted in the Windows audit JSONL correlated to the real `command_id`/`trace_id`.
Recovery is `PROVEN_REAL` without owner intervention for Cloud Core process restart, VPS
reboot (tailnet address, `/mnt/pagentos-data` and the device row all survived), Tailscale
disconnect/reconnect, Windows-side network loss and DeviceService restart.

Decision: the cloud/device infrastructure is **frozen** as a proven baseline. No reinstall,
re-enrolment, device or owner recreation, or broker redesign unless a real observed defect
requires it. Security evidence of record: the pipe DACL from the live handle, kernel-sourced
admission, SYSTEM+Administrators machine material, the shape-based credential scanner, the
in-namespace-only owner bootstrap, no public port anywhere. Recovery evidence of record: the
five scenarios above and the qualified deployment/rollback engines (`Deployment.ps1`,
`ConfigSwap.ps1`, `breakglass-ssh.ps1`).

## ADR-0034 — Realtime voice: native speech-to-speech behind a capability-driven abstraction (2026-09-02)

Status: Accepted

Context: the owner's primary interface is voice, and the standard is ChatGPT-Voice-class
interaction — behavioural and perceptual parity as far as publicly available APIs and this
architecture allow, never a claim of an identical backend. M4 built provider-neutral TTS,
STT and a `RealtimeProvider` protocol with a control-only session FSM (barge-in, stop
words, tool-call states, network loss), a Turkish normaliser, a narration engine with a
durable cursor, and speaker classification. It did not build a native full-duplex
speech-to-speech conversation path; its realtime is a protocol and a fake, and there is no
audio transport or client audio code at all. A traditional STT → text LLM → TTS chain is
explicitly NOT sufficient for the primary conversation.

Decisions:

1. **The primary conversation path is a native realtime speech-to-speech provider**,
   selected by declared capabilities (speech-to-speech, full-duplex, server VAD with
   semantic end-of-turn, barge-in, tool calling on a sideband, Turkish, streaming audio
   in/out, supported transports), never by model name. OpenAI Realtime is the primary
   candidate where it provides the best experience; its adapter is one implementation of
   the `ConversationRealtime` capability.
2. **Direct media path, sideband brain.** Microphone audio flows owner ↔ provider over a
   realtime transport (WebRTC where available) without detouring through Hetzner when
   that lowers latency. Hetzner remains the authoritative orchestration, tool and memory
   brain over a sideband control channel: the client relays the provider's tool calls to
   Cloud Core over the existing authenticated API, Cloud Core executes them against
   Memory / Browser / Research / Windows Agent / Artifacts / Evolution and returns the
   result for submission back to the provider. The provider never holds owner authority;
   it holds a short-lived session credential minted by Cloud Core for one session.
3. **Explicit modes**: `ConversationRealtime`, `Narration` (durable cursor, semantic
   navigation, adjustable speed; never pre-synthesises a whole document),
   `Transcription` (accurate STT for notes, meetings, videos and commands),
   `VoiceIdentity` (speaker verification and personalisation; never a sole root of
   authentication — it may augment device trust + owner session, per M4's standing rule).
4. **Turkish voice control is semantic, not keyword-only**: dur / devam / tekrar oku /
   ikinci maddeyi tekrar oku / biraz daha yavaş / hızlı / özet geç / detaya gir / burayı
   atla are intents resolved against conversation and narration state, and normal Turkish
   hesitation must not be cut off — end-of-turn is semantic, not silence-only.
5. **Latency is measured, not felt.** The foundation ships a benchmark harness recording
   mic → uplink, end-of-turn → first audible response, barge-in → playback stopped,
   tool-call preamble, and tool completion → resumed speech, against the real environment.
   Targets (PersonalAgentOS's own): barge-in-to-stop under ~150 ms and short-turn first
   response under ~500–700 ms where technically achievable; no unexplained silence during
   long tools — a short natural preamble, the session alive, the plan redirectable.
6. **Real-only acceptance**: the owner's real Windows machine, microphone, headset,
   Turkish speech, network and Hetzner Cloud Core. Fakes remain gates.
7. Provider credentials are owner-provisioned and stored in the existing secret store,
   never in the repository; the credential ask is deferred until the real-provider adapter
   exists behind the abstraction and a fake-provider run already passes.

## ADR-0035 — M13 browser-research pipeline: shape, dispatch path, and owner-browser authorization (2026-09-02)

Status: Accepted (design + offline-testable skeleton; real E2E deferred to owner qualification)

Context: M13's goal is the real end-to-end chain — voice/text request → Hetzner planner →
research plan → Tailscale → real Windows Browser Agent → real Chrome → multiple live
sources → evidence extraction → dedup/ranking → synthesis → executive summary → expandable
detail → durable artifact → memory → voice presentation — built now as far as it can be
proven offline, with the genuinely owner-gated real-browser step qualified later on the
owner's machine (mirrors ADR-0022/ADR-0034's local-first/real-audio-deferred split).

**1. A separate pipeline from M3, not an extension of it.** M3's `ResearchProvider`
contract (`gather() -> list[SourceRecord]`, composed into flat Markdown via
`app.research.compose`) is what the M3 acceptance gate exercises and must keep passing
unmodified — the M13 output shape (provenance-complete evidence, labelled statements,
Executive Summary → Why it matters → Recommended action → Details on demand) is richer and
different, so it is built as a new module set (`app.research.dates` / `plan` / `evidence` /
`synthesis` / `executive` / `browser_gateway` / `browser_provider`) rather than overloading
`SourceRecord`/`compose.py`. `BrowserResearchProvider.run()` composes them:
plan (`build_plan`, which parses a Turkish relative-date phrase — "son üç gün", "son 24
saat", "son bir hafta", "bugün", "dün" — out of the topic text itself via
`app.research.dates.parse_recency_window`, falling back to a documented 3-day default
rather than guessing) → gather (`BrowserGateway.fetch_evidence`, query-shaped not
URL-shaped: discovering which URLs answer a query is the browser agent's job, not the
API's) → dedup/rank (`app.research.evidence.dedup_and_rank` — deterministic: normalized-URL
dedup keeping the richer excerpt, score = 0.6·source-class-weight + 0.25·keyword-overlap +
0.15·in-recency-window-bonus, ties broken by URL) → synthesize (`SynthesisProvider` seam)
→ `ExecutiveReport`. Every emitted claim is an `app.research.evidence.LabelledStatement`
carrying exactly one of `source_fact | model_inference | recommendation | uncertainty`; the
shipped `DeterministicSynthesisProvider` never emits a `source_fact` without a matching
`evidence_urls` citation (unit-tested), and `uncertainty` is the only label allowed to carry
no citation (an absence of sources has nothing to cite).

**2. Synthesis is behind a provider seam, Claude is the default, nothing is hardcoded.**
`SynthesisProvider` (`app.research.synthesis`) follows the same pattern as
`CodingBackend`/M6, `SkillGenerator`/M7, `TTSProvider`/M4: `DeterministicSynthesisProvider`
is fully offline/seeded and is what tests and the acceptance gate run;
`ClaudeSynthesisProvider` is the real backend and is INERT — raises
`SynthesisNotConfiguredError` before any I/O — until the owner sets
`PAGENTOS_RESEARCH_CLAUDE_CLI`, mirroring `ClaudeCodingBackend`'s
`backend_not_configured` discipline exactly. Claude models are this project's stated
default choice for real backends once configured; the seam is what lets that choice change
without touching `BrowserResearchProvider`.

**3. Browser dispatch reaches the Windows Browser Agent by extending the already-proven
device/broker command path, not by inventing a second one.** M1's `desktop.*` capability
path (hello → challenge → auth → welcome handshake, ECDSA device auth, command
envelope with `command_id`/`idempotency_key`/`expires_at`, Device Service in Session 0 +
Session Companion for interactive work, `packages/protocol/DEVICE_PROTOCOL.md`) is
PROVEN_REAL end to end (state/BUILD_STATE.json qualification log). The decision is to
extend it with `browser.*` capabilities (e.g. `browser.fetch_evidence`) dispatched to
`services/browser` running as a companion-adjacent process on the owner's enrolled Windows
machine, rather than modelling the browser agent as a wholly separate device-capability
system — one enrollment/heartbeat/idempotency/audit mechanism for both, and `browser.*`
commands get the same at-least-once delivery, expiry and cancellation semantics
`desktop.open_application`/`desktop.open_artifact` already have. `app.research.browser_gateway`
is the API-side seam this decision implies: `BrowserGateway.fetch_evidence()` is what a
`browser.fetch_evidence` command's response would satisfy; `UnwiredBrowserGateway` is the
inert default (raises before any I/O, exactly like `WebResearchProvider`/M3), and
`FakeBrowserGateway` is the deterministic offline stand-in tests use.

**Exactly what is missing to make this real** (none of it touched in M13's scope — `devices/`,
`infra/`, `scripts/cloud/` and the qualified Windows/cloud runtime were explicitly
off-limits for this pass):

- a `browser.fetch_evidence` (and likely `browser.close_session`) entry in the Windows
  agent's capability manifest/allowlist (`packages/protocol/DEVICE_PROTOCOL.md` §capability
  list, `ProtocolConstants.cs`) and a handler, analogous to `AppLauncher.cs`/
  `ArtifactOpener.cs`, that constructs a `browser_agent.BrowserSession` (via
  `ManagedBackend` by default) and calls `browser_agent.research.gather_evidence`;
- the async command-dispatch plumbing on the Cloud Core side to await a
  potentially-slow, multi-page browser result over the existing command-envelope
  accepted/running/succeeded/failed lifecycle (today's `desktop.*` commands are all
  fast/synchronous-feeling; a multi-source research fetch is not);
- a chosen mechanism for query → candidate-URL discovery (which search engine or index the
  browser agent visits and reads result links from, itself via the semantic DOM/
  accessibility surface, never coordinates) — deliberately left unresolved in this ADR
  because it is a product/provider choice (which search surface, ToS considerations), not
  an architecture one;
- wiring `BrowserResearchProvider` into a durable Temporal workflow mirroring
  `ResearchWorkflow`'s plan/gather/compose/render activities, so a worker restart cannot
  lose an in-flight browser research task either (M13's pipeline module is written to be
  that workflow's activity bodies, but no workflow class exists yet);
- the memory write (M16 depends on this pipeline's output) and voice-presentation (M14)
  integration points are intentionally not built here.

**4. The owner's real Chrome session requires an explicit, separate research grant —
never inferred from mere enrollment.** ADR-0019's `BrowserEnrollment` authorizes *attaching*
to an existing browser (including throwaway dev/test browsers); it says nothing about
whether an *autonomous* task (no owner approving each page) may drive that session. M13
adds `BrowserEnrollment.owner_authorized_for_research: bool = False` — a separate,
narrower, explicit grant, defaulting False so a plain attach enrollment never silently
widens into autonomous-research use, and a pre-M13 file record with the field entirely
absent also defaults False (never inferred True from a missing key; unit-tested).
`browser_agent.enrollment.require_research_authorization` /
`get_research_authorized_enrollment` are the enforcement points: unknown enrollment id →
`validation_error` (unchanged M2 behavior); known but unauthorized → the new
`ErrorClass.SECURITY_SCOPE_ERROR` (added to the browser taxonomy — it is a scope/
authorization refusal, not a UI or transport failure, so it does not overload
`capability_missing`/`validation_error`). This is a narrower, ADR-0019-native gate,
deliberately NOT layered onto ADR-0026's `AuthorizedAssetRegistry`/`ScopeGuard`: that
registry's `asset_kinds` and `testing_classes` are frozen to migration `0008_authorized_assets`
(security-assessment scope — configuration audits, vulnerability scans, remediation), and
neither vocabulary is shaped for "may this task drive the owner's logged-in browser session
autonomously" — extending it would mean widening a frozen security-scope migration for a
concern ADR-0019 already owns. The default when no explicit grant exists remains
`ManagedBackend` with a dedicated profile (never the owner's real profile), per M13 spec
item 4; the refusal path is unit-tested (`services/browser/tests/unit/test_enrollment.py`).

Reason: the constitution requires third-party/owner-session use to be explicit and scoped,
not inferred, and requires deterministic gates before real-provider/real-browser work is
qualified on the owner's machine; splitting "design + offline-testable pipeline" from "real
browser + real search + real Temporal workflow + real owner-Chrome authorization" lets M13
proceed in parallel with M12 without risking either milestone's acceptance gate, and leaves
an explicit, itemized list of exactly what remains for the owner-qualified follow-up.

## ADR-0036 — M12 tracks A+E: session service, simulator and harness decisions (2026-09-02)

Status: Accepted (reversible implementation decisions under ADR-0034)

Context: M12 tracks A (server core) and E (intent resolver, narration bridge,
benchmark harness) were built offline against a deterministic full-duplex simulator
before any real speech-to-speech adapter exists. Several choices were not fixed by
the spec; they are recorded here so tracks B/C/D and the owner can revisit them.

Decisions:

1. **Capability defaults are "not offered".** The M12 fields on
   `ProviderCapabilities` default to false/empty/`silence`/`n/a`, so every M4
   TTS/STT adapter keeps working unchanged and can never be selected for
   `ConversationRealtime` by accident. Selection (`app/voice/selection.py`) is a
   pure function: hard requirement first, then semantic end-of-turn, then WebRTC,
   then the configured `voice_realtime_provider_preference`, then the name.
2. **The simulator is a real provider, not a mock.** `app/voice/simulator.py`
   implements the full `RealtimeProvider`/`RealtimeSessionHandle` contract on a
   virtual clock and embeds the M4 control FSM, so its ordering semantics and
   `STOP_WORDS` are reused rather than re-implemented. It declares transport
   `simulated` only, so a WebRTC-capable real adapter outranks it automatically.
3. **Audit goes to the shared `audit_events` table** (category `voice_realtime`,
   actions `voice_*`), not a new table: one place to query, the existing 4 KB bound,
   plus a scrubber that refuses audio/credential/transcript-shaped keys. Transcript
   text is never audited — only the resolved intent and character count.
4. **`call_id` is unique per session**, not globally: provider call ids are
   provider-scoped, and a global constraint would let one session's id collide with
   another's. The API is idempotent on `(session_id, call_id)`; PostgreSQL enforces it.
5. **The media leg is owned by one owner API session at a time.** Tool calls and
   events are accepted only from the owner session holding the current leg; `attach`
   moves the leg, mints a fresh credential, tells the previous client (`leg_closed`,
   best effort) and replays queued sideband messages. A web client with no device
   socket receives sideband messages in its next `/events` response or on `attach`
   (queued on the session record, bounded at 50).
6. **One `context_json` column** carries the open plan, the pending sideband queue,
   last intent, presentation level and counters, rather than a column per concern;
   `plan_id` and `narration_session_id` stay as first-class columns for queries.
7. **Provisional targets for the metrics the spec leaves open**: mic→uplink
   ≤ 120 ms, tool preamble ≤ 1000 ms, tool-done→speech ≤ 1000 ms, audible gap
   ≤ 300 ms, tool silence ≤ 3000 ms. They are recorded in every report with their
   basis and are PersonalAgentOS targets, never claims; the two spec targets (150 ms
   barge-in, 500–700 ms first audio) are labelled as ADR-0034.
8. **"ikinci madde" counts content items, not blocks.** Headings are navigation
   structure; the intent bridge maps the n-th item to the n-th non-heading paragraph.
   The M4 `MADDEYE_GEC` command (which counts blocks) is unchanged for its callers.
9. **Hesitation guard is provider-side in the simulator and client-side for real
   providers** (spec §5); the harness measures the false-barge rate on the
   hesitation set in both cases.

Consequences: track B registers its adapter in `RealtimeVoiceRuntime.providers`
and is selected only if its declared capabilities beat the simulator; tracks C/D
implement the create → media → tool relay → events → attach contract in
`app/voice/realtime_sessions/routes.py`; the owner's acceptance numbers come from
`GET /v1/voice/realtime/sessions/{id}/benchmark`, never from the simulator.

### ADR-0036 addendum — independent review of A+E and M13 prep (2026-09-02)

Fixed on `main` before tracks B/C/D merged:

1. **`POST …/tool-calls/{call_id}/complete` skipped `require_leg` and `require_live`**
   while its two siblings enforced them. A media leg superseded by `attach` still
   holds a valid owner bearer, so it could have injected a tool result into the live
   conversation, and a closed session could still be written to. Now gated exactly
   like `/tool-calls` and `/events` (409 stale leg, 410 dead session), with a
   regression test that completes from the pre-attach leg. A future worker/pipeline
   that completes tool calls without a media leg gets its own non-owner credential;
   it never comes through the owner router.
2. **Forbidden-key matching was a literal substring (`api_key`)** in both the route
   validator and the audit scrubber, and the two lists had diverged; `apiKey` and
   `api-key` passed both and would have landed verbatim in an audit row. One
   normalized blocklist (`service.FORBIDDEN_KEY_PARTS`, case and separators dropped)
   now serves both layers.
3. **`source_fact` provenance was a property of one synthesis provider, not of the
   pipeline.** `BrowserResearchProvider.run()` now re-derives it for the output of
   ANY `SynthesisProvider` (`require_source_fact_provenance`, `ProvenanceError`):
   every `source_fact` must cite a non-empty subset of the evidence actually
   gathered. Page excerpts are untrusted text; this is the gate that keeps a
   planted instruction from reaching the owner as a labelled "fact" once a
   model-backed provider is wired.

Carried forward, recorded here so they are not lost (both inert today - the
gateway is `UnwiredBrowserGateway` and query→URL discovery is unimplemented):

- Autonomous research navigation of the owner's REAL authenticated browser needs a
  destination policy beyond the http/https scheme allowlist before
  `browser.fetch_evidence` is wired for `ExistingSessionBackend` (a
  search-result-driven GET rides the owner's cookies). Default: `ManagedBackend`
  for any target not evidenced from a prior trusted step.
- The file-backed `EnrollmentRegistry` carries `owner_authorized_for_research`
  grants with no ACL tightening; apply the ADR-0029 discipline when the durable
  store lands.


## ADR-0037 — M12 track C: the desktop audio client is an additive companion library (2026-09-02)

Status: Accepted (track C of ADR-0034; server tracks A/B/E are built in parallel from the
same `docs/M12_REALTIME_VOICE_SPEC.md` contract)

Context: the Session Companion is the only PagentOS process in the owner's interactive
session, so it is the only place microphone and speaker access can live (the DeviceService
is LocalSystem in Session 0 and must never touch audio). The companion, its named-pipe IPC,
DACLs, admission policy and the installer are qualified and FROZEN (QUALIFICATION Stages
1–2, 5). The audio client must therefore be additive: it may ship inside the companion
binary through the existing deployment engine, and it may change nothing the owner
qualified.

Decisions, each reversible at the seam named:

1. **A separate class library, `devices/windows-agent/src/PagentOS.Companion.Audio`,
   referenced by `PagentOS.SessionCompanion`.** The installer publishes the companion
   csproj, so a `ProjectReference` ships with it and `scripts/` is untouched. Alternative
   rejected: a folder inside the companion project — it would put NAudio and the voice
   surface into the qualified project's own compilation unit for no deployment gain.
2. **Voice is OFF unless asked for, and cannot take the pipe loop down.** The companion's
   `Program.cs` gains one flag-gated hook (`--voice`, or `PAGENTOS_AGENT_VoiceEnabled=true`
   plus `PAGENTOS_AGENT_CloudCoreUrl`); the shipped `appsettings.json` says `false`, and a
   test parses that shipped file and asserts the posture (the ADR-0028 addendum lesson:
   the default that ships is the one that must be tested). Voice runs beside
   `CompanionRuntime.RunAsync`, never inside it, and any failure only logs.
3. **NAudio 2.2.1 (MIT) for WASAPI**; only the `Wasapi/` folder references it. Capture
   is opened at the engine mix format and converted by a dependency-free
   `PcmConverter`; playback is a `BufferedWaveProvider` under `WasapiOut` that stays
   open between responses, and `StopImmediately` is a buffer clear so the residual audible
   audio is exactly the device period (50 ms), which is reported rather than hidden.
4. **Echo cancellation / noise suppression — what is real and what is deferred.** Real
   now: (a) the capture stream is opened with `IAudioClient2::SetClientProperties`
   category `Communications`, the one Windows 10 (19045) lever that engages a driver's own
   voice-processing APOs (AEC/NS/AGC on most laptop microphone arrays), with a watchdog
   fallback to a plain stream if the category path yields nothing; (b) a client-side DC
   blocker and energy noise gate; (c) an echo-aware VAD: while the assistant is audible
   through loudspeakers (render device not headset-like) the onset threshold rises by a
   margin so the speakers do not barge in on themselves. Deferred, and written into the
   session's audit row as such: software AEC/NS of WebRTC APM class (no maintained managed
   port; native build later), Windows 11's `IAcousticEchoCancellationControl` (needs build
   22621+), and provider-side noise reduction (Cloud Core's session config, track A/B).
5. **WebSocket media leg first; WebRTC is a separated, explicitly deferred adapter.**
   `IMediaLeg` is the seam; `WebSocketMediaLeg` is real (PCM16 base64 over the provider's
   JSON events, proven against a loopback Kestrel provider); `WebRtcMediaLeg` throws
   `NotSupportedException` and `MediaLegFactory` refuses a `webrtc` grant loudly instead of
   downgrading, while the client asks Cloud Core for `websocket` in its transport
   preference. SIPSorcery (BSD-3, maintained, ICE/DTLS-SRTP/data channel) was evaluated
   as the candidate and not added: it needs an Opus codec (Concentus or a native encoder)
   and a live provider to validate, neither of which exists offline; the WebSocket leg
   reaches the same session contract today.
6. **Provider wire mapping is a pure codec (`OpenAiRealtimeWireCodec`), no model names.**
   Cloud Core's grant is already bound to a model; any provider-specific session settings
   arrive in the grant's `provider_session_config` and are forwarded verbatim. A tool's
   final result after a provisional "running + preamble" output is submitted as a
   follow-up conversation item that names the `call_id`, because a function output cannot
   be amended once sent.
7. **Barge-in order is enforced by construction**: `BargeInController` stops playback
   before any await, records the cut, cancels the provider, latches, then reports
   `barge_in` with the measured `playback_stopped_ms` and `playback_stopped`; the FSM
   refuses to latch without a recorded cut. The M4 FSM (`realtime.py`) is mirrored state
   for state and event for event, plus timestamps.
8. **Turkish hesitation guard is a pure function of evidence** (`HesitationGuard`):
   fillers (şey/yani/hani/ııı/eee/hmm, elongated tokens), connectives (ve/ama/çünkü…) and
   an acoustic prolongation heuristic (a steady final voiced run ≥ 350 ms) each extend the
   trailing-silence requirement, additively and capped. In `Server` end-of-turn mode
   (default, per ADR-0034 semantic VAD) it times events and reports its verdict; in
   `Client` mode it is decisive and the client commits the turn.
9. **The sideband reuses the owner's existing DPAPI secret store, not a new one.** The
   companion reads `%LOCALAPPDATA%\PagentOS\secrets\PAGENTOS_OWNER_SESSION_TOKEN.dpapi`
   (written by `complete-device-enrollment.ps1` / `rotate-owner-credential.ps1` via
   `ConvertFrom-SecureString`) through crypt32 directly, in the owner's session — the
   only place it decrypts. A test round-trips through the real `powershell.exe` in both
   directions. The two-domain rule from QUALIFICATION 2.6 holds: owner material stays
   owner-scoped, and the Session-0 service still uses no DPAPI.
10. **Event reporting is at-least-once with a per-session `client_seq`**, one event per
    `POST .../events`, queued while offline and re-sent unchanged (never renumbered) on
    `network_restored`, so Cloud Core can make it exactly-once. Tool-call relay is
    idempotent on `call_id` at both ends: a duplicate provider event joins the in-flight
    call; a transient failure retries with the same id.
11. **Latency is measured by the client, offline first.** `LatencyRecorder` stamps the
    five §8 metrics from monotonic timestamps; `OfflineVoiceBench` (and the
    `PagentOS.Companion.Audio.Bench` console) runs the real orchestrator against fake
    devices, a scripted provider and an in-process fake Cloud Core and prints them with an
    explicit disclaimer that they are gate numbers, not the owner's machine.

Open questions for the integrator (server track A), recorded rather than guessed at
silently: the exact body of `POST .../events` (this client sends one
`{event, client_seq, client_ts_ms, data}` per call); the credential object's key (this
client accepts `value`, `client_secret[.value]`, `token`, `key`, `secret`,
`ephemeral_key`, plus an optional `url`); whether `POST .../attach` exists in the first
server cut (this client treats 404/410 as "session gone"); and which authenticated WS
surface carries the sideband pushes to the companion — track C defines
`ISidebandPushSource` with a fake and leaves the real transport unwired.

Consequences: the qualified runtime is unchanged in behaviour; the companion binary gains
one assembly and NAudio, which the deployment engine redeploys as any other companion
change. Real acceptance (spec §10 order of proof) still needs the owner's microphone,
which nothing here attempted.

## ADR-0038 — M12 track B: OpenAI Realtime adapter shape and simulator eligibility (2026-09-02)

Status: Accepted (reversible implementation decisions under ADR-0034/ADR-0036)

Context: the first real native speech-to-speech adapter
(`app/voice/providers_openai_realtime.py`) now sits behind the capability abstraction
track A merged. The vendor facts it relies on are the citation-backed survey in
`docs/research/realtime-providers-2026-09.md`; several of them are marked UNVERIFIED
there and stay so here. No network call and no key are needed by any test.

Decisions:

1. **The adapter mints credentials and defines the client contract; it never holds
   the media leg.** `mint_credential` is the M4 `ProviderRequest` pattern (pure request
   builder, lazily-sent, inert without a key). `open_session` raises
   `CAPABILITY_MISSING` deliberately: the media session is the client's (spec §1), and
   a Cloud Core relay would put audio through Hetzner. The credential carries a
   **transport descriptor** (`EphemeralCredential.transport_descriptor`: SDP endpoint,
   `oai-events` data channel, WebSocket URL, pcm16/24 kHz/mono) so Companion and web
   clients open the leg from data, not from vendor knowledge baked into client code.
   `to_client_dict` includes the key only when the descriptor is non-empty, so the
   simulator's client shape is unchanged.
2. **Session configuration is baked in server-side at mint time.** The
   `RealtimeProvider.mint_credential` protocol gained an optional
   `session_config: RealtimeSessionConfig` (language, persona `instructions`, tool
   manifest, optional voice). The session service builds it once and hands the *same*
   object to the adapter and to the client payload, so the persona/tools a client sees
   can never differ from what the vendor session was created with, and a client cannot
   substitute its own. The simulator and the M4 fake accept and ignore it.
3. **`end_of_turn = semantic` maps to `turn_detection.type = semantic_vad` with
   `eagerness = low` by default** (setting `PAGENTOS_VOICE_REALTIME_OPENAI_EAGERNESS`),
   per the survey's recommendation for the Turkish hesitation guard (spec §5);
   `interrupt_response` stays on so server-side barge-in truncation is active. Input
   transcription is enabled with the `tr` language hint so clients can report
   utterances for Cloud Core's intent resolver.
4. **tr-TR is declared for eligibility, not quality.** The vendor publishes no Turkish
   statement for the conversational model (survey §1.5). The declaration carries a code
   comment saying so; Turkish quality is measured on the owner's machine (ADR-0034 §6).
   `interrupt_latency_class` is `medium`, not `fast`, until the harness measures it.
5. **Tool calls are emitted once per stream**, from
   `response.function_call_arguments.done`; `response.done` only lists the call ids it
   closed. Cloud Core's `/tool-calls` is idempotent on `(session, call_id)` regardless.
   Barge-in on the provider side is `response.cancel` → `output_audio_buffer.clear`
   (WebRTC only) → `conversation.item.truncate` (when the client knows the item and how
   much was heard); the client's local playback stop stays first (spec §5). Tool results
   are `conversation.item.create(function_call_output)` → `response.create`; a
   long-running tool's Turkish preamble is an explicit client-driven `response.create`
   because the survey found no vendor "keep talking" primitive.
6. **The simulator is a candidate only in `environment=dev`** or behind
   `PAGENTOS_VOICE_REALTIME_SIMULATOR_ENABLED=true`. Track A flagged that the gate could
   otherwise answer a production session when the real adapter lacks its key. Outside
   dev the runtime neither registers a simulated-only provider nor accepts one handed to
   it explicitly (reported as rejected with `simulated_transport_disabled_outside_dev`),
   and the health check lists **inactive** adapters with the reason
   (`provider_auth_missing` → the owner action). The real adapter is registered only
   when its key is present, so selection can never pick a provider that would fail
   every mint.
7. **Model names live in the adapter and `Settings` only** (`gpt-realtime` default,
   unversioned alias per the survey's advice against pinning a dated snapshot;
   transcription model likewise a setting). The standing key is used for one header,
   is never returned or logged, and every error from the mint path is scrubbed of it
   (tested against a transport error that echoes the header).
8. **The opt-in smoke (`scripts/realtime_smoke.py`) is the owner's credential step.**
   With no key it exits 3 without attempting anything; with a key it mints one
   credential and prints expiry, the transport descriptor and the vendor's *scrubbed*
   session echo — never a value — so the GA `audio.input/output` field names the
   clients will be built on are confirmed live (survey §4 item 7) before track C/D
   code depends on them.

Consequences: tracks C/D open the media leg from `credential.transport_descriptor` and
drive the data channel with `map_server_event` / `barge_in_commands` /
`tool_result_commands` semantics (ported, not re-invented). The owner credential ask
(ADR-0034 §7) is now unblocked: provision `PAGENTOS_VOICE_OPENAI_API_KEY` and run the
smoke. Azure Voice Live remains the documented second adapter candidate if measured
Turkish quality is insufficient.

## ADR-0039 — M12 integration: the companion speaks the server's contract, and the sideband push rides the device protocol (2026-09-02)

Status: Accepted (integration of track C, ADR-0037, with tracks A+E, ADR-0036, and B,
ADR-0038; reversible at the seams named)

Context: the track C handoff recorded two open questions honestly and both turned out to
be defects. (1) The companion posted one `{event, client_seq, client_ts_ms, data}` object
per call to `POST .../events`, with its own event vocabulary, a guessed credential key
list, a `client_kind` outside the server's pattern and a create body with three fields the
server forbids — the server would have answered 422 to every single request, and the
in-process fake that stood in for Cloud Core accepted all of it because it was looser than
the server. (2) Cloud Core's sideband pushes (`say`, `tool_completed`, `plan_changed`,
`narration_cursor`, `leg_closed`) were delivered as a `voice_sideband` frame over the
device broker connection, but that frame existed in no protocol definition, and the
qualified Device Service/Companion (frozen, `docs/QUALIFICATION.md`) had no path for it, so
the companion consumed a `NullSidebandPushSource`.

Decisions:

1. **The server is the single source of truth and the fake is its mirror.**
   `routes.py`/`service.py` define the contract; `RealtimeContract` in the companion
   copies it verbatim (batch shape `{"events":[{kind,t_ms,turn,payload[,text]}]}`, 1..200
   per request, 4 KiB payloads, 16 KiB arguments, `CLIENT_EVENT_KINDS`, the normalized
   forbidden-key rule with `FORBIDDEN_KEY_PARTS`, the `client_kind`/`call_id`/tool-name
   patterns, `TRANSPORTS`), and `InProcessFakeCloudCore` enforces every one of them with
   the server's status codes (201/404/409/410/422) and pydantic-shaped 422 details. Tests
   post RAW bodies to the fake so it is judged on its own: the old per-call object is a
   422, `apiKey`/`api-key`/`API_KEY`/`audioPcm` in a payload are 422s, the old create
   body is a 422. The client also checks its own payloads against the same rule before
   posting, so a forbidden key throws at the call site in a test rather than 422ing in
   production. That check caught this integration's own first bug: `eot_to_first_audio_ms`
   contains `audio` and is refused by the server's rule; the client key is
   `eot_to_first_ms`. **Consequence to keep:** any new client payload key is checked
   against `RealtimeContract.IsForbiddenKey` — `text`, `token`, `audio`, `pcm`, `wave`
   inside a longer word are all refused (`context`, `next_item`, `waveform`).
2. **Event kinds are the server's benchmark/state vocabulary; `client_seq` is client-side
   bookkeeping only.** `mic_speech_start`, `uplink_first_packet`, `end_of_turn`,
   `first_audio`, `barge_in_start`, `playback_stopped`, `tool_call`,
   `preamble_audio_start`, `tool_done`, `speech_resumed`, `response_done`,
   `network_lost/restored`, plus `utterance` (final transcripts, in `text`, for track E's
   resolver), `error` (provider errors). `t_ms` is integer client-monotonic ms since
   session start; `turn` is the latency recorder's turn index. Not reported: `audio_frame`
   (one HTTP event per 20 ms frame is noise; gap detection stays client-side in the
   latency recorder). Delivery is at-least-once: the reporter batches everything pending
   (≤ 200) into one request, keeps the head on a transient failure and re-sends it
   unchanged; the server has no dedup, so a lost 2xx can duplicate a timing event — accepted
   for the gate, revisit if the benchmark's percentiles ever look bimodal.
3. **The server's verdicts are handled as verdicts, not retries.** 422 = a client bug:
   logged at Error with the server's detail, the batch/tool call is dropped (a refused
   head must not block every later event), counted (`RefusedBatches`, `RefusedRelays`),
   never retried; for a tool call the provider still receives an error output so the
   conversation does not stall. 409 = another client holds the media leg: the leg is
   closed, playback stopped, events held, and the leg is reclaimed (`attach`) on the
   owner's **next speech onset on this device** — not on the 409 itself, which would have
   two clients stealing the leg from each other forever (spec §7 makes the last attach the
   winner). A `leg_closed` push is the same signal, handled the same way. 410/404 = the
   session is gone: the orchestrator ends with `StopReason = session_gone` and
   `VoiceCompanionHost` opens a fresh session after a backoff, so an expired TTL never
   needs the owner. The `pending_sideband` backlog in every `/events` ack and `attach`
   response is applied exactly like a push.
4. **Credential and transport come from the documented contract.** `credential.secret`
   is the only key read (the guess list is gone; a bare-string or `value`/`client_secret`
   credential is a `FormatException`); `credential.transport_descriptor` (ADR-0038:
   `websocket_url` + `query`) is where the OpenAI codec connects, falling back to the
   default endpoint when absent (the simulator). `client_kind` is `windows_desktop`
   (pattern `^[a-z][a-z0-9_]{0,15}$`). The create request names ONE transport (the first
   this build supports); a 422 naming the provider's transports is answered once with the
   first preference the provider offers, and a provider offering none of ours is an error
   said out loud rather than a WebRTC grant this build cannot open.
5. **`voice_sideband` is an additive device-protocol frame, forwarded opaquely.** Declared
   in `packages/schemas/device-protocol.schema.json` (`$defs/voice_sideband`, appended to
   `oneOf`; every v1 definition untouched), `packages/protocol/DEVICE_PROTOCOL.md` §5a,
   and `app/broker/frames.py` (`VoiceSidebandFrame`, outbound-only; a unit test pins the
   service's `sideband_frame()` output, the schema and the model to each other).
   `BrokerSideband.push` refuses a frame over 16 KiB so it stays on the session's HTTP
   backlog instead of being dropped at the pipe. On the Windows side, Agent.Core gains
   `VoiceSidebandMessage` (validated for a uuid session id, an event name by *shape*, and
   the 16 KiB bound — the payload is never read), `AgentConnection` gains an optional
   `ISidebandFrameSink` (with none, the frame is logged and dropped), the pipe protocol
   gains a `voice_sideband` frame carrying the same `conn_id`/`seq` as every pipe frame,
   `CompanionPipeServer.ForwardSidebandAsync` writes it one-way with no pending entry, no
   response, no audit row, and `CompanionRuntime` hands an accepted (fresh, this-connection)
   forward to an optional `ISidebandForwardSink`; a stale or replayed forward is refused by
   the existing `IpcChannelGuard` like a replayed `exec_request`. `PipeSidebandPushSource`
   in the companion library is the real `ISidebandPushSource` (bounded, drop-oldest, since
   Cloud Core keeps its own backlog). Nothing on the command/ack/idempotency/audit path
   changed, the pipe DACL and admission logic are untouched, and the companion gained no
   authority — it can only receive this frame and never answers it. All 138 pre-existing
   agent tests pass unmodified; 7 new ones prove the forward, the bound, the no-sink drop,
   the malformed-frame error reply and the freshness refusal.
6. **Older receivers.** An agent that predates the frame answers it with the standard
   `validation_error` error frame (§5 malformed-frame rule), which the broker already logs
   and ignores; a newer agent with no companion connected drops it and returns false to
   the sink. Both are harmless because a push that does not arrive is re-delivered over
   HTTP on the next `/events` ack or `attach`.

Also fixed on the way: `BrokerSideband.push`'s failure warning passed `event=` to structlog,
which reserves that name — a dead socket would have raised `TypeError` inside the handler
and failed the tool call it was meant to protect. Both warnings now use `sideband_event`.

Not done, recorded rather than implied: the OpenAI codec's `session.update` still uses the
beta field names (`modalities`, `input_audio_format`) and the `OpenAI-Beta: realtime=v1`
header; ADR-0038 item 8's smoke confirms the GA `audio.input/output` names before any
client depends on them, and that is track B/C's next real step. The Session Companion's
composition (`Program.cs`) wires the pipe source into both the pipe loop and the voice
host, but there is no wiring test for it beyond the two halves' own tests. Real acceptance
still needs the owner's microphone, the real provider credential and the real broker; every
number here is offline gate evidence.

Consequences: `devices/windows-agent` builds 0/0; PagentOS.Agent.Tests 138 → 145 (138
unmodified), PagentOS.Companion.Audio.Tests 110 → 134; API unit tests +10
(`test_voice_sideband_frame.py`), ruff clean. The companion posts to the real
`/v1/voice/realtime/...` unchanged from what the fake accepted.

## ADR-0040 — M12 track D: browser realtime voice client decisions (2026-09-02)

Status: Accepted (reversible implementation decisions under ADR-0034 / ADR-0036)

Context: the web shell (`apps/web`) needed the browser leg of a
`ConversationRealtime` session against the contract that landed with tracks A+E
(`services/api/app/voice/realtime_sessions/routes.py`). The shell had no test
framework, and its `next lint` script had silently stopped working when Next.js
16 removed that command. Nothing below touches `services/`, `devices/`, `infra/`
or `scripts/`.

Decisions:

1. **The browser knows a transport descriptor and a wire dialect, never a
   vendor.** `RealtimeTransport` (`app/lib/voice/transport.ts`) is opened from a
   `TransportDescriptor` read off the session payload (`transport_descriptor`, or
   `credential.transport_descriptor`): `sdp_exchange_url`, `sdp_content_type`
   (`application/sdp` default, `multipart/form-data` + `session_config` when the
   adapter needs it), `data_channel`, `dialect`, `audio` formats, optional
   `headers`. The WebRTC implementation refuses to connect when the endpoint, the
   channel name or the dialect is missing rather than falling back to a built-in
   vendor table. **Track B must return this descriptor** for its WebRTC transport;
   until it does, the page cannot open a real media leg (the server payload today
   carries only the `transport` string).
2. **Provider event names live in a dialect module selected by name**
   (`dialects/openai-realtime`), a pure mapping to normalized `TransportEvent`s.
   Both the GA and the earlier beta event names are accepted. A dialect is a
   protocol family, not a model: no model, voice or endpoint appears in the
   client bundle.
3. **Barge-in order is fixed and tested**: local playback is silenced first
   (WebAudio gain → 0 on the audio thread, synchronous from the page's point of
   view), then the provider cancel goes out on the data channel, then
   `barge_in_start` (with `playback_stopped_ms`) and `playback_stopped` are
   reported. The trigger is whichever comes first of a **local RMS speech
   detector** on the microphone and the provider's `speech_started`; the second
   signal for the same speech is not a second barge-in but is reported as
   `uplink_first_packet` (payload `basis: provider_speech_started`) — the closest
   thing a WebRTC client has to an uplink acknowledgement, labelled as such so the
   harness never mistakes it for a wire measurement.
4. **The client reports the server's event vocabulary verbatim**
   (`realtime_bench.TIMING_EVENT_KINDS` + `service.STATE_EVENT_KINDS`); the
   task's working names (`mic_uplink`, `speech_ended`, `tool_call_relayed`,
   `tool_result_submitted`, …) map onto `mic_speech_start` / `uplink_first_packet`
   / `end_of_turn` / `tool_call` / `tool_done` / `preamble_audio_start` /
   `speech_resumed` / `state`. Timestamps are `performance.now()` relative to the
   session clock, integer ms. Payloads are scrubbed of audio/credential-shaped keys
   client-side and bounded to the server's 4 KB before they are sent.
5. **Hesitation guard is client-side and deterministic**
   (`app/lib/voice/hesitation.ts`, filler set mirrors `intents.FILLERS`):
   `speech_stopped` opens a hold of `base 200 ms` (+ `700 ms` after a filler or a
   drawn-out vowel, capped at `1500 ms`); speech inside the hold is a continuation
   (no new turn, no `end_of_turn`), and a response the provider started during
   the hold is cancelled through the barge-in path with `hesitation_resume: true`
   so the harness can count false barges. The guard cannot stop the provider's
   VAD from committing; it corrects it. Provider-side tuning (semantic VAD
   eagerness) stays in the adapter's session config.
6. **Long-running tools**: the `running` result (with the Turkish preamble) is
   submitted to the provider immediately as the function output; the final
   outcome from the replayed `tool_completed` sideband frame is injected as a
   system message followed by a new response. Whether the provider interleaves
   tool latency with speech on its own is unverified (research §1.4), so the
   client never depends on it.
7. **Web sideband is pull-only**: a web client has no device socket, so frames
   are received on every `/events` response and on `attach` (ADR-0036 §5). The
   reporter flushes every 250 ms while a session is open; `leg_closed` ends this
   leg without closing the session.
8. **Network loss**: `network_lost` is queued with its original timestamp,
   the leg is torn down, and restore goes through `POST .../attach` (fresh
   credential, replayed sideband, same microphone) with exponential backoff; a
   409 on a relay or an events post is treated the same way; 410 ends the session.
9. **Test framework: vitest** (`apps/web/vitest.config.ts`, Node environment,
   `tests/**`), with deterministic fakes for the transport, playback, scheduler,
   microphone, speech detector, network and an in-memory Cloud Core that mirrors
   the route semantics (idempotent tool calls, queued sideband, attach, 409/410).
10. **Lint: oxlint, not `eslint-config-next`.** `next lint` no longer exists in
    Next.js 16, and `eslint-config-next@16.3.3` requires `typescript-eslint`,
    which refuses the TypeScript 7.0 compiler the shell pins (support is tracked
    for TS ≥ 7.1). The sanctioned alternative — aliasing `typescript` to
    `@typescript/typescript6` — would change the compiler `next build` uses.
    oxlint has no TypeScript-version coupling, no install scripts and ships the
    Next/React/hooks rules; `react/react-in-jsx-scope` (automatic runtime) and
    `react/set-state-in-effect` (false positive on the async fetch-then-set
    pattern the pages use) are off. Revisit when typescript-eslint supports TS 7.
11. **When Cloud Core selects a provider whose transport is `simulated`** (no
    real adapter configured), `/voice` drives a scripted fake transport so the
    session, events, tool relay and close paths run against the real API, and
    says so on the page; it is not a media path.

Consequences: track B returns a `transport_descriptor` with its credential;
the owner's quality evaluation of the web leg waits on that plus the provider
credential; the desktop client (track C) can reuse the same descriptor contract
and event mapping.

## ADR-0041 — Provider credentials reach the cloud host over Tailscale SSH stdin; secret-name checks are ordinal (2026-09-02)

Context: M12 track B made the realtime adapter key-gated (ADR-0038), which makes the
first product-phase owner action "provide `PAGENTOS_VOICE_OPENAI_API_KEY`". The
constitution forbids pasting a secret into chat; `scripts/secret-store.ps1` already gives
a masked, DPAPI-encrypted local entry - but the key has to run on the Hetzner host, and
the production compose passed no such variable to the api container.

Decisions:

1. **`scripts/cloud/set-cloud-secret.ps1` ships one stored value to `/opt/pagentos/.env`
   over the existing Tailscale SSH path, on STDIN only.** The ssh argv, the remote bash,
   this script's output and every log line carry the name. On the host the value is read
   with `read -r`, refused if it contains whitespace/quotes/`#`/`$`/backslash, written by
   a temp-file + `mv` rewrite (0600 root, replacing an existing line), then the api is
   restarted through the deployment's own compose invocation and the health document is
   checked from Windows for the provider. `-DryRun` shows the plan by name; no other
   transport (scp of a file, an argument, a here-string) is offered.
2. **Windows PowerShell 5.1 native-argument quoting is handled explicitly and proven.**
   5.1 does not escape embedded double quotes when it hands an argument to a native
   .exe, so a remote command containing `"$name"` would reach `ssh.exe` torn apart.
   `ConvertTo-NativeCallArgument` (scripts/lib/SecretStore.ps1) applies the C-runtime rule,
   and cloud-secret.tests.ps1 proves it byte for byte against a real native process
   compiled in the test (`Add-Type -OutputType ConsoleApplication`), including the
   CR that a Windows pipe appends to the value (stripped on the host, not trusted away).
3. **Secret-name validation is ordinal (`-cmatch`), in `secret-store.ps1` too.** On this
   tr-TR machine a case-insensitive `-match` folds `I` through the culture into dotless
   `ı`, which falls outside `[a-z]`, so every name containing an I - `…_API_KEY`, `…_ID` -
   was refused by the existing store on exactly the machine that matters. The suite pins
   the tr-TR case. Same pattern class as ADR-0025's addendum: verified only where it
   does not run.
4. **`docker-compose.prod.yml` passes `PAGENTOS_VOICE_OPENAI_API_KEY` (optional, empty
   default) and `PAGENTOS_ENVIRONMENT` (default `prod`).** Absent key = adapter not
   registered, nothing else changes; the explicit environment keeps the simulated
   provider barred from selection in production (ADR-0038). This is the one additive
   change to the frozen cloud baseline, made because the product cannot otherwise reach
   its provider; the qualified paths are untouched.

Consequences: the owner action is two local commands and never shows a value
(`docs/OWNER_ACTIONS.md`); the gate and CI run the new suite; a future secret for any
provider goes through the same two commands.

### ADR-0038 addendum — the REAL client_secrets contract, proven live (2026-09-02)

The owner's first real smoke returned `401`, then, with a valid key, `400 Bad Request`
with the body discarded by the shared HTTP helper. Diagnosed and closed on the live API,
not from documentation:

1. **Non-2xx responses now carry the vendor's error object.** `_send` keeps
   `{"http_status", "vendor_error": {type, code, param, message}}` in the typed error's
   details (bounded, scrubbed of the key; 4xx other than 429 are not retryable). The next
   400 names its field instead of being a blind status code.
2. **The session is built in layers.** `MINIMAL_LAYERS` yields exactly the current
   contract, `{"session": {"type": "realtime", "model": ..., "audio": {"output":
   {"voice": ...}}}}`; each M12 option (`expires_after`, `output_modalities`,
   `audio_formats`, `transcription`, `turn_detection`, `instructions`, `tools`) is one
   layer. `scripts/realtime_smoke.py --mode probe` mints the minimal contract first and
   then adds one layer at a time, naming any the vendor refuses. Contract tests pin the
   minimal body byte for byte and refuse every beta-era top-level key (`modalities`,
   `voice`, `input_audio_format`, `turn_detection` at the top level, ...).
3. **What the live probe proved with `gpt-realtime-2.1`:** minimal OK; every layer OK;
   the vendor echo confirms the GA nesting exactly as sent - `audio.input.format
   {type: audio/pcm, rate: 24000}`, `audio.input.transcription {gpt-4o-transcribe,
   language: tr}`, `audio.input.turn_detection {semantic_vad, eagerness: low,
   create_response, interrupt_response: true}`, `audio.output {voice, format, speed:
   1.0}`; `expires_after` is honoured (observed TTL 59 s for 60 requested; 600 s
   without it); `cedar` is accepted as the comparison voice. The "GA audio-format
   nesting assumed" caveat of this ADR is closed.
4. **The one incompatibility was tool names.** OpenAI enforces
   `^[a-zA-Z0-9_-]+$` on `session.tools[].name`; Cloud Core's tools are dotted
   (`research.start`). `vendor_tool_name`/`cloud_tool_name` (`app/voice/providers.py`)
   map `.` <-> `__` reversibly (a Cloud Core name may not contain `__`, enforced),
   applied outbound in `map_tools` and inbound on `response.function_call_arguments.done`;
   `ToolRegistry.get` also resolves the vendor spelling because the desktop and web
   clients relay the provider's name verbatim, and the service records the canonical
   name. Nothing in the registry, intents or spec renamed.
5. **Model choice.** Live discovery on the account lists `gpt-realtime`, `-1.5`, `-2`,
   `-2.1`, `-2.1-mini`, dated `-2025-08-28`, `-mini`, `-translate`, `-whisper`; nothing
   newer than 2.1, so `gpt-realtime-2.1` is the default
   (`PAGENTOS_VOICE_REALTIME_OPENAI_MODEL` overrides). Voice `marin` default, `cedar`
   retained as the immediate comparison candidate for the real-microphone session.

Still open, honestly: the companion's `session.update` codec still uses beta-era field
names for mid-session updates (credential-time configuration is what the probe proved);
it is exercised only by the real-microphone session, which is the next owner step.

## ADR-0042 — Cloud Core releases are a transaction; a secret is live only when the running workload proves it (2026-09-02)

Context: after the owner shipped `PAGENTOS_VOICE_OPENAI_API_KEY` to the host, the file had
it and the running `pagentos-prod-api` did not. Diagnosed read-only on the host: `/opt/
pagentos/app` is a COPY of the tree from the first deployment (1 Sep), not a git checkout;
its compose had no wiring for the variable; the running image was built before M12
existed (no adapter, no realtime routes, no `voice_realtime` health check); the database
was at migration 0010. A restart of an unchanged definition changes nothing, and the
old `set-cloud-secret.ps1` accepted that as success. The credential, the DPAPI store and
the transfer were never the problem.

Decisions:

1. **A release is `git archive HEAD` → scp → `app.next` → host transaction.** Only
   committed content ships (no `.env`, no local files). `scripts/cloud/release-cloud-core.sh`
   validates the NEW tree's compose against the host env file and the env posture BEFORE
   touching anything (`--preflight` stops here and leaves nothing behind), keeps the previous
   image as `pagentos/cloud-core:prev` and the previous tree at `app.prev`, swaps, builds
   the api image, runs `alembic upgrade head`, recreates ONLY the api workload
   (`--no-deps --force-recreate --wait`; PostgreSQL/Redis/MinIO/Temporal are never rebuilt or
   recreated), checks health, and — when the provider key is on the host — proves it is
   PRESENT inside the running container (length + 12-hex SHA-256 fingerprint only), that
   health lists `openai-realtime`, and that one real client-secret mint from the host
   succeeds. Any failure after the swap rolls back tree and image and recreates the api;
   additive migrations are not downgraded. `RELEASE` in the tree records the sha and the
   driver verifies it from Windows over the tailnet.
2. **A secret change is the same transaction minus build/migrate, and it FAILS unless the
   workload proves it.** `scripts/cloud/install-env-secret.sh` (value on stdin only):
   atomic env-file update → posture 600 root → compose config validation → the compose
   must WIRE the name (exit 67: "release first") → recreate only the api → PRESENT inside
   the actual container or exit 68 → health lists the expected provider or exit 69 → one
   real provider call inside the container or exit 70. `set-cloud-secret.ps1` maps each
   code to its remedy and never shows a value; runtime verification reports
   PRESENT/MISSING, length and fingerprint.
3. **The host-side scripts are tested as transactions, on Windows, under Git Bash.**
   `cloud-release.tests.ps1` and `cloud-secret.tests.ps1` run the real bash scripts with a
   fake `docker` whose container exposes the variable only after `up --force-recreate`
   and a fake `curl`, and drive the Windows scripts through a real native fake ssh/scp
   (5.1 quoting, stdin-only value). Covered: the incident itself (unwired compose → 67,
   nothing recreated), "file has it, workload never does" → 68, provider absent → 69,
   self-test failure → 70 with the vendor line scrubbed, invalid compose → 71 before any
   change, rollback restoring tree and image, preflight leaving nothing behind, and that
   no `up` ever names a dependency.
4. **Lessons that became code.** `Console.In.ReadToEnd()` in a native fake blocks forever
   on an inherited pipe: the drivers now hand children a closed stdin (`$null |`, `ssh -n`);
   MSYS bash wants `/c/...` inside `PATH` while accepting `C:/...` for file arguments; a
   fake whose `" run "` pattern matched `sh -c "uv run …"` silently hid a self-test
   failure — case order is part of a fake's contract.

Consequences: the owner's command to put the existing stored key live is the release
(`scripts/cloud/release-cloud-core.ps1`), which ships the M12 Cloud Core and verifies the
key end to end; `set-cloud-secret.ps1` is the tool for the NEXT secret. Nothing in the
PROVEN_REAL device/cloud baseline changed: the same host, env file, database, dependency
containers, tailnet path and device registration; only the api tree/image moves.

### ADR-0042 addendum — the real release succeeded; the local report did not (2026-09-02)

The owner's release run committed on the host exactly as designed (env key PRESENT,
compose wired, api recreated, migration 0011 applied, container Healthy, key PRESENT in
the container, `openai-realtime` in health, a real client-secret mint from Hetzner). The
only failure was in the local report: `Get-OptionalProperty -InputObject$doc` — a
search-and-replace had eaten the space, and Windows PowerShell 5.1 reads that as one
parameter name and fails only at bind time.

1. **The exact defect is now a lint.** `script-syntax.tests.ps1` walks every script's AST
   and fails any `CommandParameterAst` whose name is not an identifier (a parameter token
   glued to its value); the lint proves itself on `-InputObject$doc` vs `-InputObject $doc`
   before checking the tree. A repo-wide audit found no other instance.
2. **Releases are idempotent.** The driver first reads the host's `RELEASE` marker; when it
   equals HEAD the archive/upload/host transaction is skipped and only the local
   verification runs (`-VerifyOnly` forces that path and does not need a clean tree;
   `-Force` repeats a release). The owner's committed release was therefore verified from
   this machine without being repeated: `RELEASE` = fb9d52e, health `ok`, providers
   `['openai-realtime']`. QUALIFICATION row 6.0 is PROVEN_REAL on that evidence.
3. **The microphone session does not touch the cloud baseline.** The browser would need
   its origin in the API's CORS allowlist; instead `apps/web/next.config.ts` rewrites
   `/api/*` server-side to `PAGENTOS_API_UPSTREAM` (the tailnet host) and the page runs with
   `NEXT_PUBLIC_API_BASE=/api`, so every Cloud Core call is same-origin; the WebRTC leg
   still goes straight to the provider with the ephemeral credential.
   `scripts/voice/start-web-voice.ps1` checks the provider is listed and starts the shell;
   `scripts/voice/fetch-benchmark.ps1` pulls the session state and the five-metric
   benchmark afterwards (credential in a masked prompt, one session minted and revoked,
   ids and timings only).

## ADR-0043 — Voice target "Arbor" as a perceptual profile; background noise as a first-class acceptance defect (2026-09-02)

Context: the first real microphone/WebRTC session (docs/VOICE_OWNER_FEEDBACK.md). The
owner's verdict: generally good, do not redesign; preferred voice is ChatGPT's *Arbor*;
the one major defect is that a very sensitive microphone lets ambient sound drive turns.

Decisions:

1. **Arbor is a profile, not a provider voice id.** Live discovery on 2026-09-02 (one
   real client-secret mint with `voice: arbor`) was refused with the vendor's list:
   `alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar`. The system keeps
   `owner_target_voice_profile = arbor` and never passes an unsupported id. The profile is
   realised through what the provider does expose: the closest supported voice (candidates
   `marin`, `cedar`; the winner is the owner's A/B verdict in the combined qualification),
   the Arbor style block in the persona instructions (relaxed, warm, conversational, not
   announcer-like, confident not formal, low theatricality, natural Turkish prosody,
   moderate pace, smooth transitions, natural pauses, low fatigue, no exaggerated
   cheerfulness, no "AI assistant" cadence) and output pacing (`audio.output.speed`).
   `supported_voices()` on the adapter pins the vendor list with its discovery date, and a
   session may request a voice only from it. If the provider later exposes Arbor, the
   switch is configuration (`PAGENTOS_VOICE_REALTIME_OPENAI_VOICE=arbor`) followed by
   qualification. No cloning or imitation of a proprietary voice.
2. **Background noise is an acceptance defect with a layered, capability-driven fix on
   the client** (ADR-0044 for the implementation): browser processing verified by
   read-back, AGC benchmarked rather than assumed, non-invasive noise-floor calibration
   with recalibration, local speech gating that combines energy, spectral cues and
   temporal consistency but never hard-cuts quiet speech, the provider's semantic VAD left
   in charge of end-of-turn, advanced denoising only behind a seam and measurements,
   per-device microphone profiles, owner-facing modes with `Otomatik` default. Noise
   reduction cleans input; local VAD decides whether speech is occurring; semantic VAD
   decides whether the owner finished. Speech preservation outranks denoising: a
   configuration that clips onsets, swallows Turkish consonants, pumps, chatters or adds
   turn latency is rejected.
3. **Echo is never solved by muting.** The assistant's own output is a feature to the
   gate, not a reason to close the microphone; barge-in must work in headset and
   open-speaker modes.
4. **Voice character and noise processing are qualified together**, on the owner's
   machine, with the 14-scenario matrix in docs/OWNER_ACTIONS.md. The revised voice target
   is PROVEN_REAL only on the owner's confirmation of the six acceptance points in
   VOICE_OWNER_FEEDBACK.md. Background human speech (TV, people) is tested honestly: generic
   suppression cannot fully solve it; an owner-directed strategy (proximity/level,
   conversational context, optional VoiceIdentity confidence that is never sole
   authentication, trusted device/session context) is evaluated, not assumed.

Consequences: server changes are small (voice request validated against the pinned list,
speed, style block, the profile recorded on the session); the working realtime path is
untouched; the owner never tunes DSP parameters - the system converges from calibration
and the owner's verdicts.

## ADR-0044 — Web microphone / noise work package: layered input pipeline, calibrated local speech gate, per-device profiles (2026-09-02)

Context: the owner's first real K66 session (VOICE_OWNER_FEEDBACK.md, ADR-0043) was good
except for one defect — the unusually sensitive microphone let fan, keyboard, clicks,
knocks, TV, music, street, chair and room echo trigger conversational turns. The WebRTC
architecture, provider, low-latency path, session handling, barge-in ordering and the
sideband are kept; this ADR is surgical optimisation of the input side in `apps/web`.

Decisions:

1. **Layer 1 — browser processing is requested AND read back.** `BrowserMicrophone`
   asks for `echoCancellation`, `noiseSuppression`, `channelCount: 1`, and (only when
   `getSupportedConstraints()` lists them) `voiceIsolation` / `suppressLocalAudioPlayback`
   as ideal constraints; after `getUserMedia` it records `track.getSettings()`,
   `track.getCapabilities()` and the list of requested booleans that were NOT honoured
   (`AppliedInputSettings`). `autoGainControl` is never blindly on: it is the profile's
   `agcPreference` (default `auto` = off unless the A/B benchmark says otherwise). The
   diagnostics view shows the read-back verbatim (bounded) with a copyable JSON, because
   the real K66 settings can only be read in the owner's own browser.
2. **Layer 2 — non-invasive noise-floor calibration.** `NoiseFloorCalibrator` consumes
   1.8 s of frame features at microphone/session start (frames skipped while the assistant
   is audible or the gate is open) and keeps numbers only: median floor (dBFS RMS), p20
   stationary estimate, p90−p10 spread, peak, clip risk, hum ratio, speech-like ratio.
   Class: floor < −60 quiet, < −48 normal, < −38 noisy, else very noisy; spread > 12 dB
   bumps one class. Sensitivity: floor ≥ −40 high, ≥ −55 normal. A window contaminated
   by speech (speech-like ratio > 0.3) is measured again (twice at most) instead of
   trusted. Recalibration: manual ("Yeniden ölçümle") and automatic when
   `RunningNoiseFloor` (non-speech frames only; rise τ 3 s, fall τ 0.5 s) drifts more than
   8 dB for 5 s, with a 30 s cooldown. No raw audio is retained anywhere.
3. **Layer 3 — local speech gate before the controller's turn logic.** Per 21 ms frame:
   `energy = sigmoid((rms − floor − openMargin [− echoExtra while playback]) / 3 dB)`,
   `spectral = 0.4·speechBandRatio(200–3400 Hz) + 0.4·(1 − flatness) + 0.2·zcrWindow`,
   `prob = energy · (0.35 + 0.65 · spectral)`. Energy alone caps at 0.35, below every
   mode's open threshold. Temporal: an onset must persist `minOnsetMs`; a burst above the
   margin shorter than 40 ms is a click; the open gate hangs `hangMs` after the last
   voiced frame (voiced = prob ≥ closeProb, or level above closeMargin = openMargin − 4 dB
   with a speech-like spectrum); the reported start is backdated by `preRollMs`. The
   documented mode table (`MODE_PRESETS`, pinned by tests):

   | class / mode | openMargin dB | openProb | minOnset ms | hang ms | preRoll ms | echoExtra dB |
   |---|---|---|---|---|---|---|
   | quiet ("Sessiz ortam") | 8 | 0.55 | 50 | 500 | 150 | 4 |
   | normal ("Otomatik" base) | 10 | 0.60 | 70 | 450 | 120 | 6 |
   | noisy ("Gürültülü ortam") | 14 | 0.65 | 90 | 400 | 100 | 8 |
   | very noisy ("Çok gürültülü ortam") | 18 | 0.70 | 110 | 350 | 100 | 10 |

   "Otomatik" takes the row of the calibrated class and adds `clamp((spread − 6)·0.5, 0, 3)`
   dB; the profile's sensitivity preference shifts ±2/+3 dB and ∓20 ms; bounds: onset
   40–140 ms, hang ≥ 300 ms. The gate governs only what the CLIENT treats as owner-speech
   start (barge-in / hesitation / counters); the uplink track is untouched and the
   provider's semantic VAD and `interrupt_response` are unchanged (Layer 4, server side).
   Speaker playback is a FEATURE (extra margin), never a mute: barge-in keeps working in
   headset and open-speaker modes. Added local barge-in latency vs. the old RMS detector
   (40 ms attack) is +10…+70 ms by mode, and the timestamp is backdated by the pre-roll.
4. **Two seams, both passthrough by default.** `Denoiser` (denoiser.ts) with the
   promotion checklist (better separation on the 14 scenarios, no unacceptable latency,
   no metallic Turkish, no lost quiet syllables, no harm to barge-in, bypass kept) — no
   RNNoise/APM in this task. `UplinkShaper` (uplink.ts): the gate computes a gain target
   (< 1 only during floor-level background, never during an onset candidate, an open gate
   or any energy rise > 3 dB above the floor; depth −9 dB, release 5 ms, attack 50 ms). The
   Web Audio implementation exists but is opt-in per profile from diagnostics
   (`inputGainStrategy: "gated_attenuation"`) because it re-routes the proven low-latency
   track; the gate analyses the raw capture so it never sees its own attenuation.
5. **MicrophoneProfile per device, not a global constant.** Keyed by
   FNV-1a(label|groupId) so the K66 keeps its profile across deviceId rotation; fields:
   deviceId, fingerprint, friendlyName, inputGainStrategy, agcPreference,
   noiseSuppressionMode, measuredNoiseFloorDb, preferredVadSensitivity, lastCalibratedAt,
   qualificationScore, environmentMode, agcBenchmark, appliedVoiceIsolation. Stored in
   localStorage behind `MicrophoneProfileStore` (every access wrapped; a throwing storage
   degrades to an in-memory mirror) so it can later be mirrored to Cloud Core owner
   preferences. Switching devices calibrates the new one independently. The AGC A/B
   benchmark (diagnostics, not live): two 2 s ambient measurements with AGC off/on on a
   temporary capture, scored `−floor − 1.5·spread − 20·clipRisk`; AGC must win by ≥ 1
   point to be recommended on.
6. **The controller learns two things it was missing, nothing else.** (a) It subscribes
   to the local gate's end: a locally opened turn the provider never confirms is released
   after a 700 ms grace as a *false start* (a *false barge-in* when it had already stopped
   the assistant, returning to LISTENING) — previously such a turn stayed "speaking" and
   silently disabled the next barge-in. (b) A provider-confirmed turn with no transcript
   is a *false turn*, judged when the next turn starts or at close (the transcript arrives
   after `speech_stopped`, so it cannot be judged at end-of-turn). Barge-in ordering,
   hesitation guard, tool relay, reattach and the event vocabulary are untouched.
7. **Metrics through the existing `/events` contract, numbers only.** `state` events with
   `mic_calibration: 1` (floor, stationary, spread, peak, clip risk, hum ratio, class,
   sensitivity, applied margin/onset/hang/pre-roll, trigger) and `mic_metrics: 1`
   (gate_opens, gated_out, click_rejects, speech_ms, calibrations, false_starts,
   false_barge_ins, false_turns, plus the marker of what just happened; `session_end: 1`
   at close). The client's forbidden-key rule now mirrors `service.py` exactly
   (`audio pcm wave secret credential token apikey password text transcript`, normalized
   before matching — so a key like `context` is also refused) and `numbersOnly()` drops
   everything that is not a finite number under a safe name. The server is unchanged.
8. **Owner-facing page.** "Mikrofon: <name>", "Ortam", "Gürültü bastırma", "Ses algılama"
   (sahibin sesi / arka plan / sessiz), a live level meter with the floor marker, the four
   mode buttons, "Yeniden ölçümle" and a "Tanılama" toggle; all DSP numbers live only
   behind the toggle. The ADR-0043 A/B gets a two-candidate "Ses" selector (Marin / Cedar,
   default Marin, persisted per owner, unknown values impossible) and the status line
   shows `ses: <voice> · profil: arbor` from the server's create payload.

What this cannot do without server changes: stop the PROVIDER's VAD from opening a turn on
loud non-speech. The audio still flows raw to the provider (by design, for quiet syllables);
provider-side VAD threshold / eagerness live in the session config Cloud Core sends with
the descriptor (Layer 4). The client now measures how often that happens (`false_turns`)
so the server-side tuning can be driven by numbers from the 14-scenario matrix.

Consequences: `apps/web` tests 36 → 70 (synthetic silence, −50 dBFS hum, click bursts,
band-limited modulated speech, −35 dBFS quiet speech; calibration, click rejection, bounded
open time, pre-roll, hang through a 250 ms pause, no chatter, playback-as-feature, profile
round-trips, mode table, false start / false barge-in / false turn, forbidden-key sweep);
`RmsSpeechDetector` kept as the Layer 3 bypass. The K66 read-back, the AGC A/B and the
14-scenario matrix are owner actions on the real device.

## ADR-0045 — The web voice client honours the server's realtime-session contract version; a 422 is shown field by field (2026-09-03)

Status: accepted. Owners: voice-engineer, lead-architect.

Context: the owner's first real qualification run against the deployed Cloud Core clicked
Connect and saw only `Oturum oluşturulamadı: HTTP 422`. The page at HEAD (ADR-0044 §8)
sends `voice: "marin"` when a voice is selected; the deployed API is one release older and
its `CreateSessionRequest` is `extra="forbid"` — it answered
`{"detail":[{"type":"extra_forbidden","loc":["body","voice"],"msg":"Extra inputs are not permitted"}]}`.
`VoiceApiError` already carried that body as `detail`; the page rendered the status and
nothing else. Two defects, both client-side: the client guessed the server's version, and
it threw the server's explanation away. The server was right to refuse, and stays so.

Decision:

1. **One canonical, versioned contract, generated from the models that validate.**
   `packages/protocol/realtime-session-contract.json` (JSON Schema per request body,
   `contract_version`, and `legacy.1.create_session` — the field list a v1 server accepts)
   is exported from the API's Pydantic request models by
   `services/api/scripts/export_realtime_contract.py`; a unit test fails on drift. The
   same document is served at `GET /v1/voice/realtime/contract` (owner-gated like the
   rest of the router). Version history lives in `realtime_sessions/contract.py`
   (1 = first shipped M12 A+E; 2 = ADR-0043 `voice` on create). Bump on every change to
   a request model's accepted fields.
2. **The client asks, then sends only what that version accepts.** Before creating a
   session the web client (`app/lib/voice/session-contract.ts`, `api.ts`, `controller.ts`)
   probes the contract endpoint through the authenticated `apiFetch` and maps the
   outcome — proven against the real deployment:
   `200` → the served document is the source of truth (it may be newer than the bundled
   one); `404` → the route does not exist there: a contract v1 server;
   `401` → not signed in — says nothing about the version, so the client does NOT enter
   v1 mode and surfaces the sign-in state instead; network failure or an unusable body →
   the bundled version is assumed but SHOWN as unknown and Connect is never blocked.
   Answers 200/404 are cached for the page load; 401/unknown are asked again on the next
   Connect. `validateCreateBody(body, version, schema)` restricts the real payload to the
   accepted fields (returning `dropped`) and checks values against exactly the schema
   subset this document uses — `type`, `pattern`, `anyOf` with `null`, `enum`, integer
   bounds, `format: uuid`, string length — no general JSON-Schema library. A value that
   breaks the honoured schema is refused before anything is sent.
3. **Dropped fields are explained in the owner's language, not silently swallowed.**
   Against a v1 server the page shows
   `Sunucu sözleşmesi v1: 'voice' alanı bu sürümde yok; varsayılan ses kullanılacak
   (marin/cedar seçimi için Cloud Core güncellenmeli)`, the status row reads
   `Sözleşme: v1 (eski sunucu; ses seçimi yok)`, and the A/B "Ses" selector stays visible
   but is disabled and labelled `Ses (sunucuda kullanılamaz)`. The session is created with
   the server's default voice; nothing else in the payload changes.
4. **A 422 is rendered structurally.** FastAPI's validation list (`loc`, `type`, `msg`)
   becomes `alan: body.voice · neden: extra_forbidden · Extra inputs are not permitted`;
   the API's own VoiceError shape (`error_class`, `message`, `details`) becomes
   `neden: <class> · <message> · ayrıntı alanları: <names>`. `input`, `ctx` and `url` of a
   validation item are never rendered, and detail VALUES are never rendered — field
   names and reasons only. The lines appear under the error headline and in the
   diagnostics request log.
5. **A scrubbed structured request log in diagnostics.** The last 20 outgoing Cloud Core
   requests (method, path, sanitized body, status, parsed detail; a network failure as
   `status: null` + the fetcher's message). The sanitizer applies the server-identical
   `isForbiddenKey` rule plus `authorization/credential/secret/token` under any spelling,
   recursively; headers are never captured at all, so the bearer cannot appear. In
   development the same object is logged once per request to the console; in production
   nothing is logged.
6. **Not done, on purpose.** The backend validation is not loosened (`extra="forbid"`
   stays — it is what made the drift visible), the cloud is not redeployed for this, and
   the client does not invent fields a served contract does not list.

Consequences: `apps/web` tests 71 → 90 in 8 files (bundled document shape; the real create payload
clean at v2 and with exactly `voice` dropped at v1; pattern / null-ability / bounds / uuid
/ enum / required problems by field and reason; the four probe outcomes; a newer served
property list honoured over the bundle and cached per page load; the owner's real 422
rendered in the error area and the request log; a schema-breaking value refused before
sending; the log free of Authorization/bearer/ephemeral credential and bounded to 20;
Turkish wording). Two existing assertions moved from `requests[0]` to the POST because the
probe now precedes the create. `pnpm --dir apps/web test`, `lint`, `build` green.
Redeploying Cloud Core (contract v2) restores the marin/cedar choice with no client change.

### ADR-0045 addendum — the owner copies the canonical session id; nobody transcribes a UUID (2026-09-03)

Context: during the same qualification the page showed only the first 8 characters of
the session id. The owner transcribed the full UUID from network traffic, transposed two
hex characters, and `fetch-benchmark.ps1` answered "unknown realtime session" — the row
existed; the id was wrong.

Decision: the status line keeps the 8-character short form (`11111111…`) but carries the
full id as its hover/title, and a **"Session ID kopyala"** button next to it copies the
CANONICAL full UUID (lower-case, hyphenated, exactly as minted) with
`navigator.clipboard.writeText`, falling back to selecting a read-only input plus the
legacy copy command when the async API is missing or refused (the selection stays for
Ctrl+C; the button then reads "Kopyalanamadı (seçildi)"). The diagnostics view shows the
full id as selectable text with the same button. The controller keeps the last session id
after disconnect, a failed reconnect and a failed create until a NEW session is created,
so the copy works once the session is over — which is when the benchmark is fetched.
`app/lib/voice/session-id.ts` holds the pure parts (`shortSessionId`, `canonicalSessionId`,
`copyText`, `copySessionId`); `tests/voice/session-id.test.ts` pins that the id is retained
after disconnect and through a failed create, that the copy handler receives the 36-char
UUID and never the short form, that the fallback gets the same text when the clipboard
API refuses, and that a non-UUID is never copied. `apps/web` tests 90 → 96.

## ADR-0046 — The voice evidence record is durable, self-describing and never transcribed by hand (2026-09-03)

Context: after a real qualification session the owner's benchmark fetch returned
`unknown realtime session`. Traced on the real host: the row `2b3517ed-…-47898ea23334`
exists in `pagentos_prod` (state `closed`, client events and the close audit row present;
the api log shows 200s for its `/events` and `/close`), and the one 404 in the log is for
`…47898e2a3334` — two hex characters transposed while the owner transcribed the id from
browser network traffic, because the page shows only its first 8 characters. Both the
browser (through the same-origin proxy) and the fetch script talk to the same api whose
database is `postgres:5432/pagentos_prod`; nothing deletes session rows; a closed
session was already fetchable. The defect was the human step the design demanded.

Decisions:

1. **The record is separated from the provider's ephemeral session.** The provider's
   client secret is minted per leg, handed to the client once, never stored; its expiry
   or revocation touches nothing on Cloud Core. The PersonalAgentOS session row plus the
   `voice_*` audit rows are the durable evidence, and they now name what spoke: `model`
   is recorded at create next to `voice` and `voice_profile`; the benchmark context
   carries `session_id, provider, model, transport, client_kind, voice, voice_profile,
   state, started_at, ended_at`, the five latency metrics, barge-in and the `noise`
   block (false starts / barge-ins / turns, gate opens, calibrations). The report is
   always recomputable from the audit rows and, at close, a snapshot (without the raw
   event list) is persisted on the row (`benchmark_at_close`) so the record survives
   later report-code changes and never depends on browser memory.
2. **Nobody types a UUID.** `GET /v1/voice/realtime/sessions` (owner-gated) lists the
   newest sessions with state, voice, model and timestamps; `fetch-benchmark.ps1 -Latest`
   fetches the newest, and on a 404 the script prints the recent sessions instead of
   leaving the owner to guess; the `/voice` page gains **Session ID kopyala** (copies the
   canonical full UUID, still available after disconnect) and shows the full id in
   diagnostics.
3. **The lifecycle is a regression test**: create with a voice → WebRTC timing events +
   mic metrics + calibration → close as a provider-secret revocation → a later fetch
   succeeds with metrics, noise counters, timestamps and the snapshot; the row is listed
   first; a mistyped id is a 404 and never an evidence loss.

Consequences: the qualification instructions use `-Latest` or the copy button; the
Cloud Core is released once more (api workload only) so the listing and the snapshot
exist in production; the qualified baseline, the provider and the K66 work are untouched.

## ADR-0047 — Realtime voice client: measured latency decomposition, reversible early mute, echo-aware and self-learning noise gate, non-sentinel calibration evidence (2026-09-03)

Status: accepted. Owners: voice-engineer. Scope: `apps/web` only; the server's
aggregation (`realtime_bench.py`, `service.py`) reads the payload contract below and
is not changed here. Cedar + the Arbor profile, the WebRTC architecture, the provider,
session handling, the sideband and the noise pipeline's speech-preservation rules are
untouched. No threshold is raised globally; the owner is asked to tune nothing.

Context — the owner's closed K66 session (voice cedar, profile arbor) measured:
mic→uplink p50 391 / p95 472 ms (target 120) with 6 unmatched mic-start samples;
EOT→first audio p50 674 / p95 1059 (target 700; outliers 925/1059); barge-in→stop p50
210 / p95 210 (target 150) — almost every sample 209–210 ms, one 0 ms; false starts 5,
false barge-ins 4, gate opens 15, gated out 6, click rejects 6; the persisted calibration
carried `env=0, sensitivity=0, peak_db=-100`. Root causes found in the client's data model:

- **Barge-in 210 ms is deterministic and self-inflicted.** `barge_in_start.t_ms` is the
  gate's onset BACKDATED by the mode table's `preRollMs` (120 ms in "normal") and the stop
  happens at the gate's open decision (`minOnsetMs` 70 ms + one 20 ms poll): 120 + 70 + 20
  = 210. The WebAudio gain reaches zero within a render quantum; nothing waits for the
  provider. The single 0 ms sample is a provider-sourced barge-in: `speech_started`
  arrival = onset = stop time.
- **Mic→uplink 391 ms measured nothing about the wire.** `uplink_first_packet` was the
  provider's `speech_started` (`basis: provider_speech_started`): pre-roll (120) + onset
  (70) + the provider's own VAD delay. The 6 unmatched samples are the 5 false starts
  (no `uplink_first_packet` is ever emitted for them) plus provider-first turns
  (`uplinkReported = true` at start, nothing emitted) — silent loss.
- **`peak_db = -100` means every sample of the 1.8 s window was exactly zero**: the
  analyser read digital silence (a suspended AudioContext / a track not yet flowing).
  The floor then clamps to −75 dBFS, every ambient frame sits 15+ dB above it, and the
  gate opens on room noise — a plausible driver of the K66 false starts, and a dead
  window could become "the calibration in force". `env=0` / `sensitivity=0` were also
  ambiguous between "first class" and "unmeasured".
- **False barge-ins (4 of 5 false starts) happened during playback**: the residual of
  the assistant's own voice through the loudspeaker is speech-shaped, and the playback
  margin was a constant (+6 dB) chosen without measuring that residual.

Decisions:

1. **Mic→uplink is measured in components, never inferred from a provider event.**
   The gate's `open` carries its onset accounting (`candidateAt`, `decidedAt`, the
   pre-roll applied); the detector stamps every analyser frame with its audio-thread
   time mapped onto the main clock (`AudioContext.getOutputTimestamp()`, output latency
   removed) so `capture_lag_ms` is measured for the onset frame; after the decision an
   `UplinkProbe` polls `RTCRtpSender.getStats()` outbound-rtp every 10 ms (bound 400 ms)
   and the first INCREASE of `packetsSent` is the real first uplink packet. Payloads,
   numbers only: `mic_speech_start {source, gate_ms, capture_lag_ms, pre_roll_ms}`;
   `uplink_first_packet {basis: 1 rtp_stats / 0 provider fallback, rtp_ms, provider_ms}`
   with `t_ms` = the RTP observation (basis 1) or the provider's confirmation (basis 0),
   both relative to the gate's decision. Every local start is settled by exactly one
   `uplink_first_packet` OR an explicit follow-up `state` event `{mic_metrics: 1,
   unmatched: 1, false_start | provider_first | network_lost | superseded | session_end}`.
   Honest consequence: with the metric defined as `mic_speech_start.t_ms →
   uplink_first_packet.t_ms`, the sample now equals pre-roll + gate decision + RTP
   cadence; the transport component alone is `capture_lag_ms + rtp_ms`. No transport
   latency is claimed beyond what those two numbers say.
2. **Barge-in: reversible early mute, measured split, flagged anomalies.** During
   playback the gate emits `evidence` once a candidate has persisted `EVIDENCE_ONSET_MS`
   (40 ms) with two consecutive speech-like frames (spectral score ≥ 0.5); the
   controller mutes local playback at once (`Playback.mute()`, gain 0 at the next
   quantum) WITHOUT cancelling anything; if the candidate collapses (`evidence_lost`)
   the gain is restored and nothing was relayed. The irreversible open (turn, provider
   cancel) needs `minOnsetMs + BARGE_ONSET_EXTRA_MS` (30 ms) and the same spectral run —
   stronger temporal consistency during playback only. The reported pre-roll becomes
   MEASURED (`capture_lag + frame + poll`, ≈ 50–60 ms) with the mode table as the cap,
   so the backdating no longer overstates the onset by ~60–90 ms. Expected in the
   browser: onset → silence ≈ 50 (pre-roll) + 40 (evidence) + ~5 (quantum) ≈ 95–110 ms,
   under the 150 ms target; the irreversible cancel follows at ~150 ms without the owner
   hearing it. `barge_in_start` payload: `{playback_stopped_ms, detect_ms (candidate →
   decision), pre_roll_ms, stop_command_ms (decision → response.cancel sent),
   gain_zero_ms (decision → gain at zero at the output), output_latency_ms, early_mute,
   audible, anomaly}`; `playback_stopped {anomaly, early_mute}`. `anomaly: 1` when
   nothing audible was stopped (response armed, no first audio) or the onset is unknown
   (provider-first with no gate candidate); a provider-first start WITH a gate candidate
   uses the candidate as the measured onset. A 0 ms sample can therefore no longer be
   silent. `stop_command_ms` is the name — `stop_cmd_ms` normalises to "stopcmdms",
   which contains "pcm" and the server refuses it; a test pins the rejected spelling.
3. **End-of-turn → first audio is decomposed and the client's share removed.**
   `first_audio.t_ms` is the first AUDIBLE sample (the local analyser, now polled every
   10 ms); the provider's `output_audio_buffer.started` is the generation mark. Payload
   `{basis: 1 local / 0 provider fallback, response_created_ms (end_of_turn →
   response.created: provider semantic-VAD decision + queue), first_delta_ms
   (response.created → audio started: generation), playback_ms (audio started → first
   audible sample: network/jitter/decode/WebAudio)}`; when nothing audible arrives
   within 300 ms of the provider's mark, the mark is used with `basis: 0` and
   `playback_ms` absent. `end_of_turn` gains `vad_lag_ms` (local gate close → provider
   `speech_stopped`) so the owner-perceived wait is visible. The output AudioContext is
   created and resumed inside the Connect click (`Playback.prepare()`), removing the
   only client-side contributor to a first-response outlier (a suspended context waiting
   for `resume()`). On the 925/1059 ms outliers: the hesitation guard cannot cause them
   (it delays the REPORT, not `t_ms`, and a resumed turn cancels the pair); in this data
   model they can only live in `response_created_ms` (the provider's semantic VAD waiting
   on an ambiguous Turkish ending) or `first_delta_ms` (generation) — which the rerun
   will now show. `tool_preamble` / `tool_done_to_speech` are excluded (n = 0).
4. **K66 ambient robustness without a global threshold.** (a) Echo-aware margin: the
   detector measures the residual of the assistant's playback on closed-gate playback
   frames (`EchoResidualTracker`: p80 per 1.5 s window, the first window applies at
   once, later windows raise immediately and lower ≤ 1 dB per window); the playback open
   threshold is placed 6 dB above that residual, the preset's constant is the MINIMUM,
   and the threshold is capped at −28 dBFS so barge-in stays physically possible. The
   residual persists in the profile (`measuredEchoResidualDb`) and seeds the next
   session. (b) Playback-only temporal/spectral consistency (decision 2). (c) The profile
   learns from its own session (`learnFromSession`): ≥ 2 false starts outside playback →
   +1 dB margin, +10 ms onset; ≥ 2 false barge-ins → +2 dB playback margin; a clean
   session with ≥ 5 confirmed turns decays them; bounds +4 dB / +30 ms / +6 dB
   (`ADAPTATION_LIMITS`), applied per device in `deriveGateParameters`, never a global
   constant, never an owner question. (d) The read-back is evidence: after every open the
   controller reports `state {mic_input: 1, aec, ns, agc, voice_isolation (omitted when
   unknown), sample_rate, channels, input_latency_ms, not_honoured, agc_bench,
   agc_bench_off_score, agc_bench_on_score, agc_bench_off_floor_db, agc_bench_on_floor_db,
   agc_bench_recommended_on}` and the profile stores `appliedSettings` as measured values.
   Synthetic proof (tests): on hum, clicks, loud broadband noise, a −38 dBFS and a −40 dBFS
   "speaker echo of assistant speech" signal, the ADR-0044 gate opens falsely ≥ 1 time and
   the new gate 0 times, with quiet speech at −35 dBFS still covered ≥ 90 % (also at the
   adaptation cap) and an owner at −22 dBFS still barging in through the measured margin.
5. **Calibration evidence can never be a sentinel.** Every measurement carries
   `measured: 1` and `samples: N` (live frames); classes are 1-based (`env_class` 1–4,
   `sensitivity_class` 1–3; the old `env` / `sensitivity` keys are gone); `peak_db` is
   absent when no peak was observed; an all-zero frame is dead input — counted, never
   measured; a window with only dead input for 3 s is a `mic_calibration_attempt: 1
   {measured: 0, samples, dead_frames, retry}` (also for a contaminated retry), keeps the
   parameters in force (the default −60 dBFS floor before a first measurement, never the
   clamped minimum) and retries at most 3 times; the detector resumes its context on
   start and treats a non-running context as dead. The server's "last calibration in
   force" can only ever be a measurement.

Consequences: `apps/web` tests 96 → 120 in 11 files (uplink probe with a streaming
track, provider-during-probe, DTX fallback with `basis: 0`, one-settlement-per-start
across false start / provider-first / network loss; early mute before the turn with the
split as numbers, collapse restores playback, the three anomaly shapes; first-audio
decomposition and the provider fallback; `vad_lag_ms`; read-back as numbers; uncalibrated
session reports no calibration numbers; attempt vs measurement; every ADR-0047 key
through `isForbiddenKey` with `stop_cmd_ms` pinned as refused; echo margin before/after,
cap, tracker dynamics, evidence ordering, click/collapse behaviour, the synthetic set,
measured pre-roll cap, learned adaptation bounds/decay/normalisation, dead windows). Two
existing tests moved from the provider's audio mark to local audibility. `pnpm --dir
apps/web test`, `lint`, `build` green. New payload keys, exact: `gate_ms`,
`capture_lag_ms`, `pre_roll_ms`, `basis`, `rtp_ms`, `provider_ms`, `unmatched`,
`provider_first`, `superseded`, `network_lost`, `detect_ms`, `stop_command_ms`,
`gain_zero_ms`, `output_latency_ms`, `early_mute`, `audible`, `anomaly`,
`response_created_ms`, `first_delta_ms`, `playback_ms`, `vad_lag_ms`, `measured`,
`samples`, `dead_frames`, `retry`, `env_class`, `sensitivity_class`, `echo_margin_db`,
`echo_residual_db`, `mic_calibration_attempt`, `mic_input`, `aec`, `ns`, `agc`,
`voice_isolation`, `sample_rate`, `channels`, `input_latency_ms`, `not_honoured`,
`agc_bench*`, `confirmed_turns`, `early_mutes`, `early_mute_reverts`, `evidence_events`,
`evidence_lost`.

What only the owner's real rerun can confirm: the measured `capture_lag_ms` and RTP
cadence on the K66 (whether the uplink's `rtp_ms` sits at one packetisation interval, and
whether the track streams continuously or DTX applies); the barge-in total under 150 ms
with the measured pre-roll on the real audio thread; `gain_zero_ms` and
`output_latency_ms` on the owner's output device; where the 925/1059 ms outliers land
(`response_created_ms` vs `first_delta_ms`); the K66's measured echo residual and whether
the false barge-in count falls to zero with the owner still able to interrupt at normal
speaking level; that no dead calibration window occurs after `prepare()`/`resume()`, and
the read-back of `echoCancellation` / `noiseSuppression` / `voiceIsolation` on the
owner's browser.

## ADR-0048 — Evidence is UTF-8 end to end, decomposed into measured sub-phases, and never a sentinel (2026-09-03)

Context: the owner's second real session (VOICE_OWNER_FEEDBACK.md, 2026-09-03) proved
persistence and exposed three instrumentation defects: Turkish text displayed as
mojibake, headline latencies with no component attribution (six unmatched mic-start
samples, a barge-in latency that was always 209–210 ms, one 0 ms sample), and
calibration values (`env=0, sensitivity=0, peak_db=-100`) that could not be told from
"not measured".

Decisions:

1. **UTF-8 is declared and decoded, not assumed.** Traced on the host: the database
   holds correct UTF-8 (`ş`/`ğ` at real positions, no `Ã` anywhere); the API sent
   `application/json` without a charset and Windows PowerShell 5.1 decoded the body as
   Latin-1. Every JSON response now declares `charset=utf-8`
   (`UTF8JSONResponse`), and the repository's own scripts read raw bytes through
   `HttpWebRequest` and decode UTF-8 regardless of the header (`scripts/lib/HttpJson.ps1`,
   used by `fetch-benchmark.ps1`), so a non-2xx body is intact too. Historical evidence
   was not mutated; the tests reproduce the defect on the exact bytes and prove the
   round trip (`utf8-json.tests.ps1`; a pytest posts Turkish text and checks the raw
   response bytes).
2. **Sub-phases ride existing timing kinds as payload numbers**, so no contract bump
   is needed: `mic_speech_start {gate_ms, capture_lag_ms}`, `uplink_first_packet
   {basis (1 = RTP stats, 0 = provider fallback), rtp_ms, provider_ms}`, `barge_in_start
   {detect_ms, stop_command_ms, gain_zero_ms, anomaly}`, `first_audio {response_created_ms,
   first_delta_ms, playback_ms}`. The benchmark context carries `breakdown`: per
   sub-phase n/p50/p95 with the same nearest-index percentile as the headline metrics,
   plus fallback and anomaly counts and how many events carried no breakdown. A latency
   is attributed to transport only when its components were measured. (`stop_cmd_ms` was
   rejected by the forbidden-key rule - it normalises to a string containing `pcm` -
   which is why the key is spelled out.)
3. **"Measured zero" is distinct from "not measured".** A calibration counts as measured
   only when the client reports `measured: 1` with `samples > 0`; the benchmark exposes
   `noise.calibration_measured`, and clients omit unmeasured quantities instead of
   sending a sentinel. Class-like values are not encoded as index 0.
4. **Scope of any conclusion.** `tool_preamble` and `tool_done_to_speech` stay out of the
   qualification conclusion while their sample count is zero.

Consequences: the next real K66 session yields attributable numbers; the client-side
optimisation pass (ADR-0047) is judged against them; the revised voice target remains
NOT PROVEN until the owner's rerun.

### ADR-0047 addendum — review fixes: numbers-only is enforced end to end; the early mute is always reversed (2026-09-03)

Two Low findings from the independent security review of the ADR-0047 branch, fixed on
the same branch with tests:

1. **Numbers-only was a convention, not an invariant.** `mic_speech_start` started from
   `{source: "local" | "provider"}` and `barge_in_start` spread the caller's `extra`
   (`hesitation_resume: true`) past `numbersOnly()`. Now every metric payload is built
   through `numbersOnly()`: `source` is a code (`1` local gate, `2` provider VAD —
   `SOURCE_CODE`), `end_of_turn.hesitation` is a code (`0` none, `1` filler, `2`
   elongated — `HESITATION_CODE`), `hesitation_resume` is `1`, and `tool_done.replayed`
   is `0/1`. `tests/voice/latency.test.ts` runs a full synthetic session (hesitation hold
   with a premature response, a barge-in through the early mute, the RTP probe, the
   provider fallback, a tool relay, a network drop) and asserts that every timing-kind
   payload value is a finite number — without exception on the seven metric kinds
   (`mic_speech_start`, `uplink_first_packet`, `end_of_turn`, `first_audio`,
   `barge_in_start`, `playback_stopped`, `response_done`), and on the tool/network kinds
   except the identifier keys the server relays (`call_id`, `name`, `status`, `reason`).
   A future string field on any of them fails the suite. The server's
   `timing_breakdown` reads only numeric fields and ignores `source`/`hesitation`, so
   the encoding changes nothing it aggregates.
2. **The reversible mute had one path that did not reverse it.** `onResponseDone` cleared
   `earlyMute` without restoring the gain; a `response.done` (or `response.cancelled`)
   arriving while a candidate was still pending left the output at zero until the next
   `arm()`. Both now go through the single `revertEarlyMute()` path `evidence_lost`
   uses; a test pins the unmute at the response's end, the counter, and that the
   microphone/uplink track is never touched. `apps/web` tests 120 → 122.

## ADR-0049 — Multi-device / roaming owner is an architectural invariant; the K66 work is per-device (2026-09-03)

Context: the K66 qualification is a per-device audio-quality qualification. The owner's
standing requirement is that PersonalAgentOS is usable and centrally manageable from
anywhere and from every owner-authorised computer or device, with the Hetzner Cloud Core
as the authoritative control plane and owner brain. Recording this now keeps the current
voice optimisation from quietly making the system single-machine.

Decisions:

1. **Invariant, not a feature**: PROJECT_CONSTITUTION §11a. Any enrolled, owner-authorised
   device attaches to the same owner identity with no per-machine source edit or manual
   configuration after enrollment; device configuration, capabilities, policies and updates
   are centrally managed; Cloud Core selects the device (explicit target, else presence +
   capability + policy); conversation, memory, tasks, research state and preferences roam;
   voice sessions move between clients under one identity; microphone/DSP settings are
   per-device profiles that never cross-contaminate; the Arbor target is owner-level; key
   material is per machine; central revocation without rotating the owner identity; no
   authority from tailnet reachability; automatic reconnect; nothing designed around a
   machine name, path, audio device id or SID.
2. **A guard runs in CI**: `services/api/tests/unit/test_multi_device_invariant.py` scans
   application code (API, browser agent, web client, Windows agent sources - not docs,
   tests or the owner's local dev configuration) for machine-specific literals (a
   domain SID, the K66 model name, a `DESKTOP-` machine name, a CGNAT tailnet address,
   the tailnet MagicDNS domain, a user-profile path) and asserts per-device state is
   keyed by device fingerprint while the target voice profile is a server-level setting.
3. **What already conforms**: the identity model (one owner, device-bound sessions,
   central revocation, ADR-0027), the device broker (outbound-only, per-machine keys,
   ADR-0028/0029), realtime session `attach` across clients (spec §7), per-device
   microphone profiles keyed by fingerprint (ADR-0044), the owner-level Arbor profile
   (ADR-0043). What does not yet exist and is the M19 milestone (`docs/ROADMAP.md`,
   `docs/ACCEPTANCE_TESTS.md`): device inventory and presence on Cloud Core, central
   device configuration/policy/capability advertisement, explicit and policy-based device
   targeting, roaming of task/research state, coordinated agent rollout with health check,
   rollback and version inventory across machines, and the real two-PC / two-microphone
   acceptance run.

Consequences: the K66 optimisation continues unchanged and independently; every new
device-side setting is added as a per-device profile field or a centrally managed policy,
never a constant; M19 is scheduled after the voice target is proven.

## ADR-0050 — M13 Real Browser + Research: fine-grained browser commands over the proven device path, Cloud Core as the research brain, device-aware from the start (2026-09-03)

Status: Accepted (design fixed before implementation; real acceptance pending the owner's
agent update and first real research run)

Context: M12's real K66 re-qualification is owner-blocked until the evening, so the product
roadmap moves to M13 without touching the voice implementation or its evidence. ADR-0035 left
an itemised list of what was missing between the offline M13 skeleton and a real browser
research run: the `browser.*` capability dispatch on the Windows agent, the async plumbing
on Cloud Core, a query → URL discovery mechanism, a durable workflow, the memory write. Two
facts found during discovery shaped the decisions: the production compose runs no Temporal
worker container (M3's durable workflow never actually executed in production), and the
companion's pipe executor caps one interactive command at 60 s.

Decisions:

1. **Fine-grained browser commands, orchestrated by Cloud Core.** The browser is driven with
   one device command per step (`browser.session_open`, `navigate`, `search`,
   `fetch_evidence`, `extract`, … — `packages/protocol/BROWSER_CAPABILITIES.md`), not one
   long-running "do the research" command. Every step is a Temporal activity with a
   deterministic idempotency key (`{task_id}:fetch:{sha256(url)[:16]}:{attempt}`), so the
   research job resumes from durable plan/state after a Cloud Core, DeviceService, Chrome,
   network or Tailscale interruption without duplicating side effects, and Cloud Core — the
   owner's brain — decides what the browser does next. The device is a safe, dumb executor:
   it never chooses sites beyond what a command names and never turns page text into an
   action. The pipe cap rises to 120 s for the browser family only.
2. **Browser Worker = the existing `services/browser` package as a companion child process.**
   The Session Companion (owner session, where a visible real Chrome must live) spawns
   `python -m browser_agent.worker` over stdio newline-JSON (§7 of the contract), owns its
   lifecycle (lazy start, restart with backoff, shutdown), enforces timeouts, the 48 KiB
   result cap and the forbidden-key rule, and audits each request without payload text. The
   DeviceService routes `browser.*` to the companion exactly like `desktop.*`. The worker runs
   the installed Google Chrome (`channel=chrome`) with a dedicated PagentOS profile — never
   the owner's `User Data`; the owner's real logged-in session stays behind ADR-0035 §4
   (`BrowserEnrollment` + `owner_authorized_for_research`) and is not reachable through
   contract v1. Session material never leaves the device: results carry text and structure,
   never cookies, storage, headers or the profile path (scanned on both sides).
3. **Discovery uses the highest semantic surface; evidence always comes through real Chrome.**
   Per source class: primary publisher feeds/index pages from a configuration registry
   (official), Hacker News Algolia (technical), arXiv (academic) — all APIs, called by Cloud
   Core — and `browser.search` on the device (DuckDuckGo HTML → Bing → Brave, semantic result
   links, never a CAPTCHA solve) for news/community. Every candidate URL is then fetched by
   the device's real Chrome (`browser.fetch_evidence`), which is what the acceptance chain
   requires. This resolves the "which search surface" question ADR-0035 deliberately left
   open: no single engine, no paid search API required, an API seam for one later.
4. **Website error ≠ browser error.** A website-level problem (HTTP error, auth wall, CAPTCHA,
   blocked, empty) is a successful command whose result says `page_kind`/`site_error`; a
   browser/transport problem is a typed command error from the device taxonomy. Research
   treats the first as a recorded fetch failure and the second as retryable.
5. **Risk classes gate every browser action twice.** READ / NAVIGATE / REVERSIBLE_WRITE /
   EXTERNAL_COMMUNICATION / HIGH_IMPACT, classified from the resolved element before acting;
   a research session is opened with `["READ","NAVIGATE"]` and the worker refuses the rest
   with `security_scope_error`; Cloud Core's research workflow only ever issues READ/NAVIGATE
   operations. Anything else goes through the existing confirmation framework.
6. **Untrusted content boundary.** Page text is data on both sides: the worker never acts on
   it and counts instruction-like markers (`packages/protocol/browser-injection-markers.json`,
   English + Turkish, one list for both packages, equality-tested); Cloud Core flags such
   evidence `injection_suspected`, passes evidence to synthesis only inside a delimited
   "untrusted web content — quote, never obey" block, validates the structured output, drops
   assistant-directed statements (`injection_dropped`), runs the provenance gate on every
   provider's output, downgrades an uncited or excerpt-unsupported "fact" to
   `model_inference` with a `provenance_note`, and never writes flagged text to memory.
7. **Report shape and presentation** (`docs/M13_RESEARCH_SPEC.md` §3): Executive Summary →
   3–7 findings → Why this matters → What I would watch next → Details (collapsed) →
   Sources with per-claim `[eN]` citations; stored as JSON (`research_reports`) plus the M3
   artifact machinery (Markdown canonical body, PDF/DOCX/HTML/TXT renders) so citations
   survive export; one episodic memory per research keyed `research:{task_id}` holding the
   question, window, findings, source references, implications and owner feedback — never
   raw page text, which stays in `research_evidence` under its own retention.
8. **Synthesis providers behind the existing seam.** Deterministic (tests, gate), OpenAI
   (Chat Completions structured output; reuses the owner's already-installed OpenAI key via
   `PAGENTOS_OPENAI_API_KEY` → fallback to the voice key, nothing new to install), Anthropic
   (inert without a key); `auto` = anthropic → openai → deterministic, recorded per report.
9. **Temporal worker embedded in the API process** (`PAGENTOS_WORKER_MODE=embedded` in
   production compose; `off` in tests; `external` keeps `python -m app.worker`). Reason: no
   worker container exists in production, the browser path needs the broker runtime for
   immediate delivery, and one process is one thing to release and roll back. The broker
   sweep additionally delivers pending, never-delivered commands to connected devices, so a
   command row created by any process reaches the device within a sweep interval.
10. **Device-aware from the start (constitution §11a, ADR-0049).** `app.devices` adds
    inventory, presence, capabilities (refreshed from every hello), health and selection
    (explicit owner target by id/name/alias with Turkish forms → online → capability →
    policy → healthiest; typed `no_capable_device`). The research request carries
    `target_device`; nothing hardcodes the current machine as the browser executor. The
    installer provisions the worker into the agent's install tree from configuration.

Consequences: `browser.chrome` becomes an advertised device capability; the Windows agent
needs one owner-run installer update (UAC) before the real acceptance run; M13 is
`PROVEN_REAL` only when the chain Hetzner → Tailscale → the owner's actual machine → actual
Chrome → live Internet → multiple current sources → evidence → synthesis → artifact → memory
runs on the first use case with a harmless public topic; fixtures remain gates. M14 (voice ↔
research ↔ browser) waits for the voice quality work to be qualified.

### ADR-0050 addendum (2026-09-03): what the build, the live runs and the reviews changed

Built as four parallel tracks and merged the same day (web `/research`, companion browser host +
installer provisioning, browser worker + operations, Cloud Core devices layer + pipeline +
embedded Temporal worker). Facts and decisions that were not in the design:

1. **Headful real Chrome with the dedicated persistent profile is the qualified posture.** On
   the live Internet the same public newsroom answered headless Chrome with 403 and headful
   Chrome with 200; every search engine served headful Chrome and challenged headless. The
   contract default `visible: true` stands; the live tests run headful. DuckDuckGo and Bing wrap
   organic links in click-tracking redirects (`uddg=`, `/ck/a?u=a1<base64url>`), unwrapped by
   `search_engines.resolve_result_url`; Mojeek and Startpage block automation and are not used.
   A bare HTTP 403 without login markers is `blocked` (bot filtering), not `auth_wall`.
2. **Destination policy on both sides** (§5a of the contract): public Internet hosts only,
   every resolved address checked (DNS rebinding), CGNAT/tailnet refused. Found by the security
   review: discovery output is third-party content, so a Hacker News submission or a search hit
   could have pointed the owner's browser at a LAN or metadata address.
3. **Forbidden-key scan is enforced three times** with one normalised rule (lowercase, strip
   non-alphanumerics, substring): worker, companion (`BrowserCapabilities.IsForbiddenKey`) and
   Cloud Core (`app.research.forbidden_keys`, parity-tested against the worker's list). The
   companion's first version matched raw substrings and missed `api-key`/`x-api-key`.
4. **The deterministic synthesis provider never writes page text into memory.** Its finding
   summaries are built from provenance fields only; `remember_activity` stores a summary only
   for a model-backed provider and only when no cited evidence is injection-suspected and the
   text carries no marker. Found by the security review as the one path that bypassed §6.
5. **Injection markers are grouped correctly and text is folded before matching** (NFKC,
   zero-width characters removed, whitespace collapsed) on both sides; the first list's
   alternation matched the bare word "reveal". The boundary is structural — page text has no
   path into any action — the detector is telemetry for Cloud Core's flag.
6. **Companion hardening**: only the 24 contract operations are forwarded (`capability_missing`
   before the worker's stdin), worker stderr is sanitised before logging (query strings, credential
   `key: value` lines, 2000-char cap), stdout lines are bounded (4 × 48 KiB, over → kill and
   restart with backoff), eager restarts stop after ten consecutive failures, the pipe cap of 120 s
   for `browser.*` was proven end to end (`timeout_ms_seen == 120000 − 500`).
7. **Provenance gate for every label**: dangling citations are stripped from all statements, not
   only `source_fact`, so the rendered Markdown never carries an `[eN]` without a Sources entry;
   sources carry `device_id` and `command_id` to the exact device command that fetched them;
   LLM output is bounded (3–7 findings, per-field caps); discovery bodies are capped at 2 MiB;
   device aliases must be unique (409) and an ambiguous alias is `no_capable_device`.
8. **Installer upgrade safety**: a re-run without `-BrokerRestUrl` preserves the installed
   broker endpoints (after RQ-2 the tailnet address), so the owner's agent update cannot silently
   repoint the qualified device at loopback.
9. **The e2e harness owns a database** (`pagentos_e2e_m13`): the shared dev database is
   truncated and re-migrated by the integration suites, which wiped a real run mid-fetch during
   the concurrent reviews; the API log is captured into the evidence directory.

10. **The installer deploys through the journaled engine, and proves the result** (2026-09-03,
    real owner run). The owner's M13 agent update built and staged the new binaries and then
    failed at the swap: `install-device-service.ps1` still used the pre-engine
    `Publish-StagedDirectory` rename, which NTFS refuses while the service and the companion
    execute from those directories — the exact incident `scripts/lib/Deployment.ps1` was
    written for on 2026-09-01 and which only `finalize-qualification.ps1` had adopted. The
    elevated window closed on the error, `.previous` stayed empty, `.staging` held the M13
    candidate, the old binaries kept running, and the verifier reported the agent as pre-M13.
    Now: `Invoke-AgentDeployment` with production handlers (`scripts/lib/AgentRuntime.ps1`:
    companion, service and any process executing from the install root — the Browser
    Worker's python.exe — stopped and awaited by PID), registration before the swap, health =
    installed binary answers the `capabilities` verb with the browser family when provisioned
    AND service + companion + pipe up AND both processes running from the new trees, a
    transcript under `ProgramData\PagentOS\install-logs`, an evidence block
    (`scripts/lib/InstallEvidence.ps1`: repo HEAD read from `.git`, source/staged/installed
    artifact hashes compared, SCM and logon-task executable paths read back, running image
    paths, worker path, manifest), and a loud failure when any of it disagrees.
    `installer-evidence.tests.ps1` pins the engine as the only deploy path.

11. **Google is the primary search provider; DuckDuckGo the fallback** (owner decision,
    2026-09-03 evening). `browser.search` runs through a provider abstraction: `auto` =
    `google → duckduckgo`, named engines alone (Bing/Brave stay selectable by name). Every
    outcome carries provider evidence — `requested_provider`, `provider`, `fallback`,
    `fallback_reason`, `query`, `result_count`, `attempts` — and Cloud Core writes it into the
    research run's events (`discovered_by=browser_search:<provider>`). Google organic results
    are read from the results region only (ads, "People also ask", knowledge panel, carousels
    and Google's own domains excluded; `/url?q=` unwrapped; rank/title/URL/snippet), the
    locale comes from the payload, the worker's `--locale`, or the machine's user locale
    (`hl`/`gl`), and Google's "unusual traffic" interstitial and consent page are recognised
    (URL and text) and never answered — a probe from the owner's address on the same day hit
    that block after a day of automated traffic, which is exactly the recorded fallback case.
    The M13 browser smoke (9.2) is unchanged and its `full` mode still passes.

12. **Capability response schemas are versioned and checked before use** (2026-09-03, real
    owner run). The first Google-primary qualification ran against an installed worker that
    predated the provider change (installed 19:10, change committed 19:52): `browser.search`
    succeeded with no provider fields and the smoke died on a missing property. Now the worker
    is `0.2.0`, its hello and `browser.worker_status` carry `contracts: {"browser.search": 2}`,
    every search result carries `schema_version`, Cloud Core's `SearchEvidence` records
    `schema_version`/`contract_ok` and writes a CONTRACT MISMATCH into the run's events instead
    of inventing a provider, and the smoke refuses to search on a lower contract with a
    message naming the installed version. `-UpdateAgentFirst` runs the journaled installer and
    waits for the device to reconnect so the update and the qualification are one action.

13. **Persistent, owner-visible, UI-driven research browser session** (owner decision,
    2026-09-03 night; contract v1.1 §3a, spec §5a). One Chrome session per research job,
    reused for every search/navigation/extraction and closed when the job ends; Google is
    driven through its real page (typed query, DOM/navigation readiness, no fixed sleeps),
    sources are fetched in new tabs so the results tab stays loaded, the dedicated PagentOS
    profile keeps legitimate cookies/locale/consent between runs. Interstitials are handled
    by owner handoff when the owner is present (`interactive=true`): Chrome is brought to
    the front, the run enters `waiting_for_owner_verification`, the workflow polls
    `browser.wait for=verification_cleared` and resumes the same session after the owner
    completes the page; unattended runs fall back to DuckDuckGo. The chosen path is recorded
    per query. Explicitly NOT stealth: no fingerprint spoofing, webdriver masking, stealth
    plugins or automatic CAPTCHA solving.

13a. **Persistent research session, Google-UI search and owner handoff — Cloud Core side**
    (2026-09-03, M13_RESEARCH_SPEC.md §5a / BROWSER_CAPABILITIES.md §3a). Implementation
    decisions made while wiring the contract that the spec left to the API side:
    - **Known-open session registry is process-wide, not per-gateway-instance.** Every
      activity (`discover_activity`, `fetch_activity`, `await_verification_activity`,
      `close_session_activity`) constructs its own `DeviceBrowserGateway` — Temporal
      activities are plain functions with no shared state across calls — so "one
      `browser.session_open` per job per process" has to live in a module-level registry
      keyed `(device_id, session_id)` (`app.research.browser_gateway._KNOWN_OPEN_SESSIONS`),
      reset per-test via an autouse fixture (`tests/conftest.py`) so fixture reuse across test
      cases (same device/task ids) does not leak "already open" state between tests. On an
      "unknown session" `validation_error` the registry entry is invalidated and reopened
      with a NEW idempotency key (`session_open:<attempt>`) — reusing the original key would
      just replay the broker's already-stored terminal ack instead of dispatching a real
      command, per DEVICE_PROTOCOL's idempotency-by-key contract.
    - **Owner-handoff budget is per waiting occurrence, not cumulative across the whole job.**
      The spec names `interactive_wait_s` "the interactive budget" without saying whether a
      job that hits a second interstitial after clearing the first gets a fresh allowance.
      Chosen: each `waiting_for_owner_verification` occurrence gets the full
      `interactive_wait_s`, since a second CAPTCHA minutes after the first is not the owner's
      fault and a job-wide budget would silently starve it. `POST /v1/research` bounds
      `interactive_wait_s` to 60–1800s (one `browser.wait` slice to 30 minutes).
    - **Dedup ("identical (query, provider) searches within a job are not re-issued")** is
      implemented as: before a "news"/"community" `discover_activity` call searches, it checks
      whether `research_candidates` already has a row for that `query_id`; if so it returns
      `{"status":"done","candidates":0,"path":"cached",...}` without dispatching
      `browser.search` at all. This is compatible with the handoff-cleared/timeout-fallback
      re-issue by construction: an interrupted (waiting) search never inserts candidates for
      its query, so the retry always finds none and proceeds to search for real.
    - **`await_verification_activity` never raises** on a device/transport error — it reports
      `{"satisfied": false, ...}` so the workflow's own budget loop treats a broker hiccup the
      same as "not cleared yet" rather than letting a Temporal retry re-consume the wait slice
      it already spent.
    - Adaptive device-command polling (`app.devices.commands.DeviceCommandClient`) starts at
      0.2s and backs off ×1.5 to a 1s ceiling (was a fixed 0.5s); `poll_interval_s` on the
      constructor is still the STARTING interval, so existing tests that pass a small fixed
      value keep working unchanged.
    - Migration `0014_research_owner_verification` widens `ck_research_runs_stage` to accept
      the new `waiting_for_owner_verification` value; it is deliberately NOT added to
      `TERMINAL_STAGES` so the web client's poller keeps running through it.

14. **Browser lifecycle invariant with hard guards** (2026-09-03 night, after a real
    regression: dozens of Chrome windows cascading on the owner's desktop). Proven
    mechanism: `launch_persistent_context` on the dedicated PagentOS profile while another
    Chrome holds it fails in 0.1 s and opens a new window in that Chrome; an orphaned
    PagentOS Chrome (its worker killed by the installer or companion) plus any retry source
    turns into one window per attempt. Decisions: the worker owns at most ONE research
    browser per profile (a different session id while one is alive is
    `browser_lifecycle_violation`, a new device-protocol error class, non-retryable); orphan
    PagentOS-profile Chromes are reaped before a launch (only processes carrying that
    profile dir - never the owner's Chrome); a locked-profile launch failure is never
    retried; every session-scoped result carries `lifecycle` identity (`session_uid`,
    `browser_pid`, `tab_count`, budgets: max 1 window, max 6 tabs unless the plan raises it
    to 12); `session_close` waits for the profile's Chrome to exit; the companion reaps
    profile-bound Chrome when it kills or loses a worker and now keeps a file log; the
    installer's runtime stop does the same. The owner's Chrome sessions are never touched.
    Fixing this does not change Google's CAPTCHA/handoff behaviour, which stays a separate
    provider concern.

15. **The persistent launcher was browser *detection*, and tests must never own a desktop
    window** (2026-09-03, late night, second real incident: "Chrome for Testing" windows kept
    appearing after the qualification command had exited). Proven under a desktop window
    monitor (scratch `desktop-window-monitor.ps1`: per-second `EnumWindows` + `Win32_Process`
    sampling, evidence in QUALIFICATION 9.13): `browser_agent.detect` read the browser
    version by running `<chrome.exe> --version`. On Windows Chrome has no print-and-exit
    `--version`; it starts the full browser with the default profile, the window outlives
    the caller (11 processes still alive after the probe exited), and with Google Chrome
    already running it hands off and opens a new window in the OWNER's Chrome. That ran on
    every worker start (hello), every installer/verify self-check, every companion worker
    restart and every test fixture - the same function behind both incidents. Decisions:
    (a) detection never starts any process that is a browser: Windows reads the PE
    VERSIONINFO resource through `version.dll`, POSIX keeps `--version` (there it does print
    and exit), and the headless-launch fallback is deleted (`tests/unit/test_detect_no_launch.py`
    forbids browser executables in any subprocess call during detection); (b) the browser
    test suite forces every `ManagedBackend` headless outside the `live` marker and reaps
    every Playwright Chromium the pytest process spawned at session end
    (`services/browser/tests/conftest.py`), and every future browser-suite run on the owner's
    desktop is executed under the window monitor with `max_visible_chrome_for_testing_windows
    == 0` as a hard expectation; (c) cross-process guards in `browser_agent.launch_guard`:
    an OS-level exclusive launch lock per profile (named mutex - released by the kernel when
    the holder dies, so it cannot go stale; a second worker process gets
    `browser_lifecycle_violation` without touching anything), a durable launch-rate circuit
    breaker (only *recovery* launches count - after an orphan reap or a failed launch; three
    within ten minutes write `browser-lifecycle-fault.json` in the worker data dir and every
    further research-profile launch is refused until it ages out, across worker restarts,
    surfaced in the hello as `lifecycle_fault`), and a Windows Job Object with
    kill-on-close holding the Chrome root so the kernel terminates the whole tree whenever
    the worker process ends (crash, `taskkill /F`, normal exit alike); (d) durable ownership
    (`browser-ownership.json`: research job id, browser session id, worker pid, Chrome root
    pid and start time, transport, profile path, lock name, tab ids, closed_at,
    browser_pid_exited) and the same identity on every result (`lifecycle.worker_pid`,
    `launch_kind`, `launch_lock`, `job_object_assigned`); (e) the stdio error envelope
    carries bounded `evidence` so the companion audit records which guard refused. Cross-
    process attach to an existing Chrome is deliberately NOT supported: it would need an
    open CDP debug port on the research browser; the "one controlled recovery attempt" is
    reap-then-single-launch, and the launch lock guarantees the reaped process was an orphan.

16. **Deployment truthfulness: INSTALL VERIFIED must prove the LIVE worker is the staged
    release** (2026-09-04, real owner result: `-UpdateAgentFirst` installer exit 0, "INSTALL
    VERIFIED", and the live worker still 0.1.0 with `browser.search` schema 0). Proven chain,
    from real evidence on the owner's machine and a local reproduction: repo HEAD, staged tree
    and installed source tree `<root>\browser\browser_agent` were byte-identical and new; the
    venv's NON-EDITABLE copy `<root>\browser\.venv\Lib\site-packages\browser_agent` was old
    (0.1.0, no `lifecycle.py`/`launch_guard.py`, dist-info `pagentos_browser-0.1.0` built from
    `.staging/browser`). Two causes: (a) uv rebuilt the project from its build cache - the
    staging path is the same on every release and uv's cache key for a local project is the
    mtime of `pyproject.toml`, which code-only releases never touch, so `uv sync --frozen
    --no-dev --no-editable` reused the wheel built on 2026-09-03 11:30 (reproduced: same path,
    source-only change, plain sync -> stale copy; `--reinstall-package pagentos-browser` ->
    fresh copy); (b) the installer's self-check ran `python -m browser_agent.worker` with the
    BROWSER TREE as working directory, so `sys.path[0]` shadowed site-packages with the fresh
    source and the check reported 0.2.0, while the companion starts the worker with its data
    directory as cwd and imported the stale copy. Exit code 0 was therefore truthful about the
    file swap and false about the running code. Decisions: the worker's hello and
    `browser.worker_status` carry a `module` block (executing `worker.py` path and sha256,
    whole-package digest, cwd; `browser_agent/release.py`); the source declares its release
    (`WORKER_VERSION` 0.3.0, package version 0.3.0, `[tool.uv] cache-keys` on
    `browser_agent/**/*.py`); the installer builds with `--reinstall-package
    pagentos-browser`, runs every self-check from a neutral cwd exactly like the companion,
    and asserts version, contracts, worker hash, package digest and module-inside-venv against
    the staged source (`scripts/lib/BrowserRelease.ps1`), plus a file-by-file comparison of
    site-packages with the source; the companion starts the worker eagerly and audits its
    module origin, and the engine's health check refuses to commit unless the audit row after
    the swap names a live pid running `<root>\browser\.venv\Scripts\python.exe` with the
    installed data dir, created after the deployment, reporting the staged release from the
    installed venv, with every pre-swap worker pid gone (the runtime stop now kills every
    `-m browser_agent.worker` process, including the base-interpreter child outside the
    install root); failure rolls back and prints INSTALL FAILED. The verifier gains 6b.3
    (executing release == installed source, site-packages byte-identical) and 6b.4 (the
    companion's live worker is that release). The owner smoke proves the live worker against
    the checkout's release before any Chrome operation and, after `-UpdateAgentFirst`,
    reports `deployment/version mismatch` with the install log instead of asking for a rerun.
    The dev-chain harness now runs a PACKAGED (non-editable, reinstalled) worker with the
    companion's cwd, so the runtime import path is exercised locally and in CI. Earlier 6b.1/6b.2
    PROVEN_REAL rows stand for what they measured (a worker starts as the owner; manifest
    agreement); they did not measure which copy executed, which 6b.3/6b.4 now do.

17. **Owner verification handoff: retry once, never loop; interactive vs unattended**
    (2026-09-04, after the real Google-primary run: Google attempted through the installed
    owner-session worker, `captcha interstitial detected; not answered`, deterministic
    DuckDuckGo fallback with `fallback_reason=google:captcha` - recorded as PROVEN_REAL
    for the provider path; Google success itself stays unproven). Decisions: `browser.search`
    gains the owner-facing `mode` (`interactive` = Google → owner handoff if needed → fallback
    only afterwards; `unattended` = Google → deterministic fallback if blocked), mirrored by
    `POST /v1/research` `mode`. A session hands an interstitial to the owner at most once;
    after a cleared verification the pending search is retried exactly once on the SAME
    `session_uid`/Chrome pid/research job; a further interstitial is recorded and the fallback
    applies (`handoff_repeat_fallback`); a fallback-mode search on a still-pending query never
    navigates to Google again (`handoff_timeout_fallback`). On a handoff the worker raises the
    Chrome tab and, on Windows, the Chrome window itself (its own root pid only). Google's
    verification/consent cookies persist in the dedicated profile as normal Chrome behaviour
    permits (no clearing, no spoofing). Timeout policy: the owner smoke asks in the terminal
    whether to fall back; the workflow offers `on_verification_timeout` `fallback` (default) or
    `fail` (stop with `owner_verification_timeout` so the owner decides); a synchronous in-run
    owner prompt (signal + web) is deferred. Nothing solves, bypasses, masks or retries a
    CAPTCHA; all lifecycle, audit, security and cleanup guarantees stay as proven.

18. **Verification evidence has one home; the owner's fallback decision is evidence-backed**
    (2026-09-04, real owner run: interactive handoff worked - interstitial detected, window
    brought forward, same session polled for 600 s - and then the smoke died with "Item has
    already been added. Key in dictionary: 'interstitial'" when the owner chose to fall back).
    Root cause, exactly: the smoke built the fallback payload as `$searchPayload + @{
    interstitial = "fallback" }`; PowerShell's hashtable `+` throws on a key both operands
    carry, and the original payload already had `interstitial = "handoff"`. The fallback
    attempt was never issued, so the run ended with a dictionary error instead of provider
    evidence. Nothing about Google, the browser lifecycle or the handoff itself was at fault.
    Decisions: (a) search schema 3 - the interstitial kind lives ONLY in the result's
    `verification` block (`handoffs`, `outcome` pending|cleared|timeout|repeat, `interstitial`,
    `verification_url`); a pending result's top-level `page_kind` is `waiting`, and Google's
    attempt outcome on the handoff paths is a verification code
    (`verification_pending`/`verification_timeout`/`interstitial_after_verification`), giving
    the canonical reasons `google:verification_timeout` and
    `google:interstitial_after_verification`; (b) the smoke builds every payload and every
    evidence object from fixed key lists in `scripts/lib/BrowserSmokeEvidence.ps1`
    (`New-BrowserSearchPayload`, `New-HandoffEvidence`, `ConvertTo-SearchEvidence`) and never
    merges hashtables; `Test-EvidenceKeysUnique` re-checks the JSON projection, and
    `scripts/tests/browser-smoke-evidence.tests.ps1` forbids the merge pattern in the smoke
    source; (c) after a verification timeout the owner's chosen fallback is a PASS with the
    honest record `provider=duckduckgo, fallback=true,
    fallback_reason=google:verification_timeout, verification.outcome=timeout`, exactly one
    fallback attempt, on the same session, which then closes normally; declining closes the
    session cleanly and reports `browser_pid_exited`; (d) the verification timeout is
    configurable - `-HandoffTimeoutSec` (15..3600, default 600) and `interactive_wait_s`
    (minimum lowered to 30 for qualification runs; production default unchanged at 600), so an
    owner qualification never costs ten minutes. Worker release 0.4.0 carries schema 3;
    Cloud Core still accepts schema 2 workers (their verification fields stay null).

19. **DuckDuckGo is the production research provider; deployment is separate from execution**
    (owner product decision, 2026-09-04, after the Google provider path was proven and Google
    kept answering automated Chrome with its verification page). Two decisions, both about
    not letting infrastructure block the product:

    (a) **Search policy.** DuckDuckGo is the default provider for Research: a default run
    records `requested_provider=duckduckgo, provider=duckduckgo, fallback=false`. Google stays
    fully implemented - the UI-driven search, interstitial detection, the owner-verification
    handoff with its one-retry policy, the timeout decision and every test - and is selected
    explicitly (`search_provider=google` on `POST /v1/research`, `engine=google` on
    `browser.search`, `-SearchProvider google` / `-ExpectProvider google` on the scripts).
    Nothing is deleted; Google is simply not attempted first for every job. The Cloud Core
    setting `PAGENTOS_RESEARCH_SEARCH_PROVIDER` (default `duckduckgo`) and the request field
    carry it, and `GET /v1/research/policy` reports the deployed policy so a client can tell a
    stale Cloud Core from a current one instead of silently running the old order.

    (b) **Deployment lifecycle separated from execution.** Qualification commands were running
    the transactional installer every time, even when the installed worker was already the
    right release. Normal execution now uses the installed worker with no installer, no
    staging/swap and no service restart. `Test-AgentReleaseCurrent`
    (`scripts/lib/BrowserRelease.ps1`) answers "is a deployment needed?" from files alone -
    checkout release/digest versus the installed source tree AND the non-editable copy inside
    the installed venv that actually executes - and separately answers whether the installed
    contract can still serve this checkout. The owner smoke gains `-AgentUpdate auto|never|force`
    (auto = deploy only on a difference; `-UpdateAgentFirst` is kept as the older spelling of
    force). `scripts/research/owner-research.ps1` never installs at all: it refuses with the one
    update command when the installed contract is incompatible, and releases the Cloud Core only
    when the deployed research policy predates the checkout's.

20. **Typed field contracts for research data; one bad candidate never kills a run**
    (2026-09-04, owner run `f6eb5021`: DuckDuckGo discovery found 243 candidates, 12 were
    fetched and ranked, and the job then died with
    `ValueError: invalid literal for int() with base 10: 'Bu model, yapay zeka …'`).
    Proven cause, reproduced exactly: `synthesis._parse_finding` did `int(data["importance"])`
    on a synthesis model's finding, and that model had answered the numeric `importance` field
    with a Turkish prose sentence. Nothing about the browser, DuckDuckGo, the device or the
    deployment was involved; the defect was a numeric field with no declared type, no range, no
    provenance and no validation, so free-form extracted text could reach it.

    Decisions: (a) `app/research/contracts.py` declares every numeric field the pipeline reads
    from data it did not compute - `discovered_result.rank`, `fetched_source.http_status`,
    `evidence_item.rank/score`, `ranked_candidate.rank/score`, `finding.importance` - with a
    name, a type, a valid range and a provenance sentence, and validates deterministically
    before use: real numbers and clean numeric strings only, never prose, dates, empty strings,
    booleans or containers. (b) The inverse guard is the same machinery: declared TEXT fields
    (url/title/snippet/excerpt/publisher/label) refuse numbers, so a rank or score can never be
    read as a title and a positional mix-up is caught at the field boundary. (c) A violation
    raises `ContractViolation` carrying entity, entity id, field, expected type/range, observed
    Python type and observed value CLASS (`prose_text`, `date_like_string`, …) plus the stage -
    and never the offending content, which must not leak out of the evidence store. (d) Fault
    isolation: a malformed finding or stored evidence row is quarantined
    (`invalid_evidence_contract`), its raw row is left exactly as it is, its reason is recorded
    in the run's event trail, and the run continues on the valid remainder; the run fails only
    when fewer than `MIN_VALID_EVIDENCE` (3) valid items remain, as
    `insufficient_valid_evidence` with counts and reasons. (e) A synthesis provider whose whole
    output violates the contract is replaced for that run by the deterministic provider, which
    builds findings only from validated evidence - recorded, never silent. Research policy
    version 2 carries the evidence contract, so exactly one Cloud Core release ships it.

21. **Named, versioned entity schemas; no research boundary reads a producer payload by raw key**
    (2026-09-04, the run after policy v2: DuckDuckGo discovery found 238 candidates, 12 were
    fetched and ranked with zero quarantined, and the job then died with `KeyError: 'label'`).
    Proven cause: `synthesis._parse_statement` did `data["label"]` and the synthesis model had
    returned a statement without one. `label` belongs to the synthesis output's labelled-statement
    taxonomy (spec §3), and it is REQUIRED - a statement without its provenance label cannot be
    attributed and is not publishable - but it is produced by a model, so it must be validated at
    the boundary rather than assumed.

    Decisions: (a) every research entity has a named, versioned schema in
    `app/research/contracts.py` (`discovered_result`, `evidence_item`, `statement`, `finding`,
    `detail_section`, `synthesis_response`), each naming its producer (search provider, device
    worker, synthesis provider, this Cloud Core); (b) every field is declared **required**
    (absent is a violation), **optional** (absent takes the schema's canonical default, and
    downstream code treats it as nullable) or **derived** (never read from a producer payload at
    all - rank, score and evidence ids are computed by the pipeline, and reading one from input
    is a programming error the schema refuses); (c) a violation reports `entity_type`,
    `entity_id`, `field`, `stage`, `producer` and `schema_version` instead of a bare KeyError;
    (d) a malformed statement, detail section or finding is quarantined individually and the
    rest of the report is kept - only the response's own required `executive_summary` is fatal,
    and even then the activity falls back to the deterministic provider; (e) the audit the
    incident demanded: a test scans every research module and fails if any producer-controlled
    payload is indexed by raw key, which moved the remaining boundaries (the model providers'
    HTTP envelopes, this Cloud Core's own stored reports, the device's evidence rows) into the
    contract layer instead of waiting for each missing key to fail one owner run at a time.
    Research policy version 3 carries the schema registry, so exactly one Cloud Core release
    ships it; the Windows worker is untouched (0.4.0 already matches).

22. **A page must be about the question, from the window and real before it can be cited; a
    report with fewer than three attributable findings fails instead of publishing**
    (2026-09-04, the run after policy v3: DuckDuckGo discovery, 12 pages fetched and ranked,
    zero quarantined, status `ready` - and zero findings). Nothing crashed. The report was
    published with a fluent executive summary and an empty findings list, and the owner's
    acceptance check, not the pipeline, caught it.

    Three separate defects met in that one run. (a) Nothing had ever asked whether a fetched
    page was on topic or inside the requested window: a ten-day-old IBM Granite model card and
    two unrelated arXiv abstracts (Catalan's constant, a dark-matter halo profile) were ranked
    as evidence, and every item's retrieval time was implicitly read as its publication time.
    (b) A Cloudflare interstitial whose extracted title was "Bir dakika lutfen..." was ranked
    as an article. (c) Zero findings still reached `ready`, because cardinality was a
    convention rather than a contract.

    Decisions: (i) `app/research/eligibility.py` judges every fetched page before ranking -
    page validity (`normal_content` / `consent` / `captcha` / `interstitial` /
    `login_required` / `access_denied` / `empty` / `malformed`, and only `normal_content` may
    become evidence), topic relevance against a Turkish/English agent lexicon, publication
    -date confidence (`high`/`medium`/`low`/`none`) and a recency verdict
    (`in_window`/`outside_recency_window`/`date_uncertain`), with retrieval time NEVER standing
    in for a publication date; (ii) each refusal is recorded with a named reason (`off_topic`,
    `outside_recency_window`, `date_uncertain`, `interstitial`, `duplicate_event`,
    `insufficient_content`) and the counts appear in the report itself, so a thin answer can be
    told apart from a thin web; (iii) the verdict is persisted onto the evidence row, because
    synthesis reloads evidence from the store on a later activity - refusing a page at ranking
    and letting synthesis read it back is how the interstitial became a source; (iv) a finding
    must cite evidence and a report must carry at least three of them (target five), enforced
    on every provider's result, with the ladder the owner asked for: reject the malformed
    synthesis, retry once against the same validated evidence, fall back to deterministic
    evidence-backed synthesis, then fail as `insufficient_valid_findings` - findings are never
    invented to reach a number; (v) the fetch budget prefers candidates whose URL and date hint
    suggest they can pass the gate, since twelve fetches spent on pages that will be refused is
    the same failure by another route. The run is preserved as regression fixtures
    (`tests/unit/test_research_regression_20260904.py` and the incident cases in
    `tests/unit/test_research_browser_activities.py`), each asserting that the specific real
    page can no longer be cited. Research policy version 4 carries the quality gate and the
    findings contract, so exactly one Cloud Core release ships it; the Windows worker is
    untouched (0.4.0 already matches, and none of this changes its contract).

23. **What the live dev-chain run found once the gate was on** (2026-09-04, three real runs
    against Chrome and the live web before asking the owner to rerun). The gate worked - and
    the first run exposed three things a unit test could not have.

    (a) **The gate refused pages that were genuinely on topic.** An official post announcing an
    agentic model family scored 0.325 against a floor of 0.35, because its headline was a
    product name and the whole signal sat in the body. Two causes: the domain lexicon was a
    flat phrase list, so one Turkish word ("ajanlariyla") matched four overlapping entries
    while an English announcement matched none, and every phrase counted the same, so a phone
    launch mentioning "yapay zeka asistani" once scored like real coverage. The lexicon is now
    a set of weighted CONCEPTS counted once each, with agent-specific ideas (agentic, tool
    calling, multi-agent, orchestration) weighted above generic AI mentions. The announcement
    now scores 0.65, the phone launch 0.33, the unrelated arXiv abstracts still 0.0.

    (b) **A fixed fetch budget spent once decided the size of the report by luck.** Nine of
    twelve fetched pages were refused, leaving exactly the minimum three findings. The
    workflow now runs up to three bounded top-up rounds when the gate leaves it short of the
    target: each round is sized to the shortfall, only ever fetches candidates never fetched
    before, and stops as soon as discovery has nothing left. It is not a loop that browses
    until it likes the answer - a run that cannot find enough usable coverage still fails.
    With top-ups the same target produced five evidence-backed findings from five sources.

    (c) **Ranking a second time gave two different sources the same citation id.**
    `assign_evidence_ids` kept existing ids (correct: a replay must not renumber a published
    report) but numbered new records by POSITION, so a newcomer at the front took an id an
    older record still held. The live report carried two sources both answering to "[e2]",
    which makes every citation of it unverifiable. New records now take the next id not in
    use, ranking clears the ids of rows a later round dropped, and synthesis cites only rows
    the latest ranking identified.

    Result: the full dev-chain harness passes end to end against the live web, including
    recovery after a Cloud Core restart mid-job, with five findings, five sources and
    nineteen refusals recorded by reason.

24. **Research Engine closed PROVEN_REAL; one quality backlog item kept open, non-blocking**
    (2026-09-04, owner run 17:28-17:33 UTC). `OWNER RESEARCH: PASS` with five findings from
    five distinct publishers, 28 refused pages recorded by reason, Chrome clean before and
    after, deployment skipped because the installed runtime matched, and the durable evidence
    in `research-1.json`. QUALIFICATION 9.16-9.19 are PROVEN_REAL and the slice is closed; the
    owner asked for no rerun.

    The owner's own reading of the report: some accepted findings were broader AI developments
    (a ministry's 2026-2030 AI plan, AI in the judiciary, data centres) rather than strongly
    agent-specific ones. The gate is doing what it was built to do - those pages are on topic
    by lexicon, dated, real and distinct - but "about AI agents" and "mentions agents while
    being about something else" are different questions, and only semantic intent scoring
    can tell them apart. Recorded as a backlog item for the eligibility layer: score how
    central the agent development is to the page, not only whether the concepts occur. It
    does not hold the infrastructure milestone open.

## ADR-0051 — M16 Activity Ledger + Self Explanation + stateful voice narration (2026-09-04)

Status: Accepted (reversible at the seams named; spec `docs/M16_ACTIVITY_LEDGER_SPEC.md`)

Context: with the Research Engine proven real, voice becomes the primary owner interface
to all of PersonalAgentOS. The owner should hear what the system did ("Son yaptıklarını
anlat") from durable evidence, drill into it ("Araştırmayı detaylandır", "Teknik anlat"),
interrupt it ("Dur") and resume it ("Devam et") at the same semantic point - without
PowerShell, dashboards or development output. M12 built the realtime control plane,
M4 the narration engine with a durable semantic cursor, M13/M15 the research evidence.
Nothing existed that could say what happened across subsystems, and nothing turned a
tool's answer into a narration a cursor could follow.

Decisions:

1. **One append-only ledger, backfilled from canonical rows, never seeded.**
   `activity_events` (`app/ledger`) is the single structured stream for every
   subsystem, with the spec's fields (event_type, subsystem, module, version, status,
   severity, production_state, command/trace/research/browser ids, `evidence_refs`,
   `factual_summary`, `detail_json`) and idempotency on `(source, source_ref)`. Live
   writers sit at existing transitions (research completed/failed and the quality gate,
   voice session lifecycle, explanation, narration pause/resume). Backfill derives
   events only from rows that exist (`research_runs`/`research_reports`, voice audit
   rows, `releases`, `incidents`) and every backfilled event references its row; a
   re-run records nothing new. The Evolution Engine's vocabulary
   (`evolution.idea_created` … `evolution.rolled_back`, `production_state`
   `shadow_ready`/`approval_required`/`deployed`) is reserved now so a future module can
   be described - and questioned - through the same stream.
2. **Knowledge and execution are separate permissions.** The ledger and the Self
   Explanation engine are read-only over evidence; nothing in them can start, deploy,
   promote or roll back anything. Production execution keeps its own path.
3. **Evidence first, then words; every statement is typed.** `app/explain` classifies
   the Turkish question (last activity, today, failures, problems now, subsystem status,
   why failed, evidence, research detail, technical, module problem), retrieves ledger
   events and the records they point at (the research report for findings, incidents
   for problems), and composes a briefing whose statements are `known_fact` (carries
   an evidence reference), `inference` (says so) or `uncertainty` (says so). No evidence
   → "Bu konuda kayıt bulamadım." The owner's qualification verdict is folded in only
   when the ledger holds it (`research.qualified`, recorded by the owner script from the
   real `research-1.json` with its SHA-256); the database alone never yields
   "doğrulanmış durumda" - that would be fabrication (unit test pins it).
4. **A briefing is an artifact, and its sections are the narration levels.** The
   briefing is persisted as `kind=activity_briefing` with a canonical Markdown body
   `# Özet` / `# Ayrıntı` / `# Teknik` / `# Kanıt`; a `narration_sessions` row attached
   to the realtime session carries the cursor. So `executive`/`detailed`/`technical`
   are cursor jumps into one document (`level_section_cursor`), Task → Artifact →
   Presentation holds, and the M4 machine's invariants ("dur" always wins, explain-then
   -return restores the exact cursor) apply unchanged. Terminal logs are never read
   verbatim.
5. **The provider speaks what the tool returns, verbatim.** Cloud Core never carries
   audio (ADR-0034). `activity.explain` returns `speech` for the requested level;
   `narration.control` now returns `speech` = the text from the new cursor to the end
   of its section. The persona instructs the provider to call these tools for the
   explanation questions and narration commands and to read `speech` as it is - nothing
   added, nothing invented. What is spoken is taken from the narration plan itself, so
   "devam" continues from exactly the words that were said.
6. **Where "dur" landed is computed, not guessed.** The client already receives the
   provider's output transcript; it now reports a `spoken` state event (top-level `text`,
   like `utterance`) when it cuts the assistant off (after playback stop, before
   `barge_in_start`) and when a response completes. Cloud Core aligns that transcript
   against the plan from the current cursor (`app/narration/align.py`: Turkish-folded,
   order-preserving token overlap ≥ 0.6, exact match for short sentences) and persists
   the cursor at the first sentence not fully spoken; the transcript itself is never
   stored (the audit scrubber refuses `text`). Without a `spoken` event the cursor stays
   at the start of the section handed out - still a correct semantic point. Barge-in
   ordering (playback stop first) is untouched. `CONTRACT_VERSION` stays 2: no request
   model changed, only an accepted event kind was added.
7. **"Madde" means a list entry when a list is being read.** M4/ADR-0036 counted items
   as content paragraphs document-wide. A briefing's findings are one numbered list per
   section, kept by the plan as one paragraph with one chunk per entry; while that
   section is read, "ikinci madde" / "önceki maddeyi açıkla" / "bunu atla" address ITS
   entries. Plain documents keep the old rule (existing intent tests unchanged).
8. **Proactive briefings are a policy with a durable queue.** `immediate`
   (critical), `completion` (an owner-requested long task finished: "Efendim, bilginize;
   araştırma tamamlandı. Beş önemli sonuç çıkardım. İsterseniz özetini anlatabilirim."),
   `once` (a future module reaches shadow_ready), `digest`, `ledger_only`. With a live
   session the sideband `say` speaks it; otherwise `pending_briefings` holds it for the
   next session's instructions, marked delivered when spoken.
9. **Acceptance is one owner action over the real stack.** `scripts/voice/owner-explain.ps1`
   preflights (release blockers, realtime provider, ledger policy → one transactional
   Cloud Core release if the deployed core has no ledger), records the real research
   verdict, backfills, starts the web voice shell, prints the phrases, and after the
   session asserts every step from `GET /v1/voice/realtime/sessions/{id}/activity` and
   the ledger: explain answered from evidence, detail and technical read, `spoken` then
   `barge_in_start` with an aligned cursor, resume from that cursor, clean close. No
   Windows component changes; the web client runs from the checkout as before.

Consequences: the Self Model and the Evolution Engine get their event contract and their
question surface ("Diagnostic Observer'da sorun ne?" already resolves to
`module_problem` and answers from the module's ledger events plus incidents, or says it
has no record). Open: semantic intent scoring for research relevance (ADR-0050 item 24),
and the proactive `say` path is exercised only through fakes until a live session
receives a completion briefing.

### ADR-0051 addendum — independent review and the real-database run (2026-09-04)

Security review (read-only, security-reviewer) verified as sound: owner-session gating of
`/v1/ledger/*`, bounded reads, `require_leg` on `spoken` events, the transcript never
reaching audit rows or the ledger, the alignment cursor bounded to the attached artifact,
no cross-session narration hijack, no announcer/inbox leak from briefing artifacts. It
found one real gap and two small ones, all fixed the same day: (1) free text posted
through `POST /v1/ledger/events` (and the owner script's evidence-file ingest) would be
spoken verbatim without the injection screen the research pipeline already applies - the
route now refuses instruction-shaped `factual_summary`/`detail_json` values (shared
markers plus ledger-local spoken-text patterns, Turkish and English; the shared marker file
in `packages/protocol` is frozen with the installed worker and was not touched), and the
engine flattens and bounds evidence-file strings before speaking them; (2) `severity=critical`
maps to the `immediate` briefing policy, so it may be posted only by `deployment`,
`security`, `cloud_core`, `device_service`; (3) `detail_json` is capped at 16 KiB.

The real dev database (`tests/integration/test_ledger_explain_over_real_runs.py`) found
two defects no unit fixture had: a release component with hyphens
(`browser-agent-demo-4326f3af`) made the whole backfill raise - components are now slugged
into the event type with the raw name kept on `module`, and one bad row or source never
ends the others (counted, named); and an ISO timestamp with a `T` separator inside a
briefing crashed the narration normaliser (`"T18"` handed to `int()`) - the normaliser now
parses the time tail (T, fraction, zone) and the engine renders times spoken-friendly.
Over the workflow tests' own research run the engine said "Efendim, son araştırma görevi
tamamlandı. Beş sonuç ve beş kaynak ürettim. Dört tekrar eden olayı eledim…" and did not
claim a qualification, because none was recorded - the behaviour the design requires.

### ADR-0051 addendum 2 — the first owner run: product worked, harness did not (2026-09-04)

Owner observation: `owner-explain.ps1` released the Cloud Core correctly, then started the
web voice shell with a fire-and-forget process, slept eight seconds and went on to wait for
a realtime session; the shell never answered on localhost:3000, so no session could exist,
and the harness failed with "no NEW realtime session since this script started". The owner
then started the shell by hand, connected, and the real voice UI answered evidence-backed
explanations from the ledger (task identity, discovered/fetched/evidence/rejected counts,
worker 0.4.0, research policy, deployment skipped). Recorded as a qualification-harness
defect, not a product one. Also observed: saying "Dur" after the assistant had finished
surfaced the provider's "Cancellation failed: no active response found" as an error.

Decisions: (1) `scripts/lib/VoiceShell.ps1` makes the two orchestration decisions pure and
testable - `Wait-WebShellReady` polls an HTTP probe of `/voice` with a bounded budget, and
`Select-QualificationSession` picks the session this run owns: not in the baseline taken
before the run (an older session never qualifies, however recent), from a web client,
started once the shell was ready, carrying a succeeded `activity.explain` call, newest
first; `Wait-QualificationSession` keeps asking within a budget so a late connection is
a wait, not a failure. (2) The harness checks whether the shell already answers before
starting one, starts it through the same `start-web-voice.ps1` the owner uses (output
captured to a log), and does not proceed until `/voice` answers; `-SkipWeb` means "it must
already be running". (3) A `critical`-free re-run performs no Cloud Core release: the
ledger policy version is current, and the Windows components are untouched. (4) "Dur" is
idempotent end to end: server side the narration machine's DUR always wins and never
errors (a second "dur" on a paused narration is paused again, with nothing to say);
client side a barge-in sends `response.cancel` only while a provider response is active,
stops local playback either way, and a provider "no active response" cancel error is
classified benign (counted, never a user-facing error). "Devam et" after a real
interruption resumes at the first unfinished sentence; after a completed section it goes
on to the next one. Regression coverage: `scripts/tests/owner-explain.tests.ps1` (shell
absent, already running, delayed connection, stale session, newest-wins, probe failure
isolation, harness wiring), `tests/unit/test_voice_explain_tools.py` (Dur idempotence,
Devam after completion, two interruptions), and the web client's `dur.test.ts`.

### ADR-0051 addendum 3 — the owner's UX verdict: good, usable, three control defects (2026-09-04)

Owner result on the real session: narration quality good; "Dur" stopped active speech and
persisted the cursor; "Devam et" resumed from the paused semantic position. Three defects:
other people talking in the room sometimes interrupted the assistant; the spoken answers
were too long by default; and "detaylandır" / "teknik anlat" were not recorded as the
expected narration-control intents even though the right explanations were produced (the
provider had routed them to `activity.explain`).

Decisions:

1. **Two-lane interruption policy, owned by the client.** The provider session now has
   `turn_detection.interrupt_response = false`: the provider no longer cancels its own
   response the moment its VAD hears any speech. In the web client, a *fast control lane*
   interrupts immediately on an explicit control phrase in the provisional transcript
   (dur, durdur, bekle, sus, kes, yeter, bir dakika; token match, so "durum" is not "dur"),
   through the unchanged stop-first barge-in path. A *conversational lane* treats a speech
   onset as a candidate: the existing reversible early mute applies at once, and the
   interruption is confirmed only with stable onset, near-field confidence from the
   calibrated gate and owner profile (K66), and a plausible provisional transcript within a
   short window - otherwise the mute reverts and the speech is counted as rejected
   background. No voice biometrics, no transport redesign, no single global threshold.
   Counters (`speech_detected`, `potential_barge_in`, `accepted_owner_interruption`,
   `rejected_background_speech`, `explicit_stop_command`, `false_interruption`) ride the
   existing `mic_metrics` state report into the benchmark's `noise` block and the session
   activity record.
2. **Narration budgets.** Narration is for listening. `executive` (default) is two to four
   sentences - the outcome, why it matters, whether the owner is needed; counts and
   identifiers belong to `detailed` and `technical`. `detailed` reads up to five findings
   and stops at a sentence boundary within ~900 characters; `technical` is a concise
   briefing of versions, evidence, failures and architecture (~700 characters, no recital
   of ids); only `hepsini oku` / `tamamını anlat` / `bütün detayları oku` (`Intent.FULL`)
   lift the budget. Budgets are enforced on the spoken text at the tool boundary
   (`speech_budget`), chunks end at sentence boundaries, and the cursor keeps the
   position, so "devam et" reads the next chunk. The owner's target sentence is now the
   default: "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. Beş
   farklı kaynaktan beş sonuç üretti ve yirmi sekiz uygun olmayan sayfayı eledi. Tarayıcı
   temiz kapandı; şu anda müdahalenizi gerektiren bir sorun yok."
3. **Owner relevance over chronology.** Every ledger event is classed as task completion,
   change, failure, security, evolution, telemetry or meta (`owner_relevance`). Meta -
   voice sessions, narration, explanations, backfills, briefings - and routine telemetry
   never lead an executive briefing unless the owner asks about that subsystem, so "Son
   yaptıklarını anlat" cannot answer with the previous narration. A failure or security
   event with nothing completed after it turns the closing sentence into a call for action.
4. **Normalised intents in the durable record.** `detay ver` / `detaylandır` / `ayrıntı
   ver` / `daha detaylı anlat` → `detail`; `teknik anlat` / `teknik detaya gir` / `kod
   seviyesinde anlat` → `technical` ("teknik" outranks "detay"); the full-read phrases →
   `full`; executive / stop / resume / skip / previous / next unchanged. When a briefing is
   attached and the provider routes a level word to `activity.explain`, the tool performs
   the same cursor jump as `narration.control` and records the normalised intent; the
   qualification accepts the intent from either tool.
5. **The owner test is under two minutes**: `Son yaptıklarını anlat` → concise briefing →
   another distant voice while it speaks (it keeps speaking) → `Dur` → `Devam et` →
   `Teknik anlat`. The harness verifies from the durable record: executive ≤ 420
   characters, a `spoken`/`barge_in_start` pair with an aligned cursor, resume from that
   cursor, `intent=technical` from either tool, and `false_interruption = 0` from the
   client's counters. The Cloud Core changed (ledger policy version 2 carries the budgets
   and ranking), so the command performs one transactional release; Windows is untouched.

### ADR-0051 addendum 4 — provenance is structural; wording is not evidence (2026-09-05)

The owner's real session worked: the voice UI answered from the ledger with the research
task identity, the run's counts, worker 0.4.0, the research policy and the skipped
deployment; background speech was rejected, "Dur" stopped it, the cursor persisted and
"Devam et" resumed. One acceptance check still failed - `briefing derived from the real
research run` - because it matched a Turkish sentence prefix (`speech_head -like "Efendim,
son ara*"`). The briefing had been made deliberately shorter and better worded the same
day, so a working system failed its own gate on phrasing.

Decision: **generated wording is never acceptance evidence.** A briefing now carries a
structural `provenance` block - the ledger event ids it cited, the research job those
events belong to, the evidence kinds (`activity_event`, `research_report`, `artifact`,
`file`), the structured facts the sentences were built from (findings, sources, rejected,
rejected_by_reason, verdict, installed release, deployed, policy version), the statement
labels used and an explicit `seeded: false`. It rides the tool result into the durable
session activity record. The owner command verifies: at least one cited ledger event and a
real research job; every cited event resolves in the ledger, is not seeded and belongs to
that job; and the narrated numbers equal the research run's own record. Turkish
paraphrasing is free; an unsupported claim is not.

`owner-explain.ps1 -VerifyOnly [-SessionId <id>]` re-verifies an already completed session
- no web shell, no waiting, no talking - so a checker fix never costs the owner a repeat of
a qualification the system already passed.

## ADR-0052 — The Holographic Core is an event contract, not a renderer (2026-09-05)

Status: Accepted (owner direction, 2026-09-05: reserve the architecture now, do not build
the 3D renderer tonight)

Context: the owner wants a future 3D "Agent Core" — a living visual centre that breathes
when idle, contracts while listening, expands while thinking, orbits sources during
research, converges during memory work, shows a construction layer during evolution and a
completed node at SHADOW_READY. The risk in building such a thing later is that it grows
tendrils into every subsystem, or worse, animates on timers that have nothing to do with
what the system is actually doing.

Decisions:

1. **The contract is the product; the renderer is a client.** `app/uistate` defines a
   fixed state vocabulary (`agent.idle|listening|thinking|speaking|researching|
   memory_retrieval|tool_running|waiting_owner|goal_completed|error`,
   `evolution.researching|designing|building|testing|shadow_ready`), a bounded event
   (intensity, progress, severity, status, task/goal/module/session identity, a short
   label, numeric metadata) and an owner-gated read surface `GET /v1/ui/state` with a
   replayable tail. A renderer learns states, never internals; subsystems may be rewritten
   freely as long as they keep publishing the vocabulary.
2. **Truthful by construction.** A state is published because a subsystem entered it. The
   voice control plane publishes from the client's OWN reported timing events
   (`mic_speech_start` → listening, `end_of_turn` → thinking, `first_audio` → speaking,
   `barge_in_start` → listening, `network_lost` → error); research publishes while it
   ranks. `progress` is `null` when the publisher does not know it, so a renderer cannot
   draw a bar for work of unknown length. There is no write endpoint: a client cannot
   claim a state it is not in.
3. **Content-free.** Metadata carries numbers, bools and short tokens under keys that pass
   the same forbidden-key rule the voice and ledger surfaces use; anything normalising to
   text/transcript/audio/secret/content is dropped at the boundary. Voice contributes a
   bounded 0..1 energy derived from levels the client already reports — never a sample,
   never a transcript. Nothing here is durable: the Activity Ledger records what happened;
   this is only what is happening now.
4. **Never in the way.** Publishing is non-blocking, bounded and swallows its own errors:
   a broken renderer, a hostile metadata value or a full tail can never slow or fail the
   work being described.

Consequences: the renderer can be built later as a pure consumer (poll now, subscribe
later) with no further subsystem changes; every new subsystem this milestone adds
(experience, goals, cognitive core, self model, evolution) publishes through the same bus
from the start. Deliberately deferred: the WebSocket/stream transport, any visual design,
and the audio-reactive detail of the speaking state.

## ADR-0053 — M17 Cognitive Foundations: six inspectable subsystems over durable evidence, and a lab that cannot reach production (2026-09-05)

Status: Accepted

Context: after M16 the system could answer *what happened?* from the Activity Ledger. The
owner's overnight direction asked for the next layer — memory of experience, compiled
lessons, goals with a cognitive loop, a world model, a self model, and the foundation of an
Evolution Engine — with two hard conditions: no claim of general intelligence, and an
Evolution Engine that can never reach production by itself. The obvious implementation (one
large model prompt holding "memory", "goals" and "self-knowledge" in its context) fails both:
it cannot be inspected, cannot cite its evidence, and has whatever authority the process has.

Decisions:

1. **Six packages, one rule: evidence first, then words.** `app/experience`,
   `app/goals`, `app/worldmodel`, `app/selfmodel`, `app/evolution` and the M16
   `app/explain` all read the same durable rows (`activity_events`, incidents, releases,
   research reports). Nothing may state as fact anything it cannot point at; a derived
   claim carries `inference` and an uncertain one carries `uncertainty`, all the way out
   to the spoken sentence. The alternative — letting a subsystem "know" things from its
   own prose — is what makes a self-model lie.
2. **The four truth kinds are the world model's entire point.** `source`, `installed`,
   `runtime` and `evidence` are separate and never averaged. A fact known only from the
   checkout may never be reported as what is running; a stale index answers "I don't
   know" rather than confidently answering from last week. Two defects of exactly this
   class were found and fixed the day this shipped (a junction walk that read files
   outside the checkout, and a component suffix match that attributed runtime truth to
   modules the evidence never named), which is the evidence that the distinction needs
   enforcing in code rather than in documentation.
3. **The cognitive loop is a set of named, testable roles.** Orchestrator, Planner, Actor,
   Critic, MemoryManager and CapabilityRouter are separate objects with separate inputs
   and separate tests, not personas in a prompt. A goal's success criteria are checked
   against evidence, not against the Actor's own report; the Critic can send a step back.
4. **Lessons are compiled from incidents, and stay attributable.** The Experience Compiler
   turns repeated real failures into a lesson with the incident refs that produced it.
   A lesson may inform planning and opportunity scoring; it may not silently become a
   fact about the world, and it carries its confidence with it.
5. **The Evolution boundary is a capability, not a rule in a prompt.** The lab holds a
   `LabAuthority` that has no production grant. `OWNER_APPROVED` is a different method
   (`approve()`) requiring an owner-session capability rather than a different string in a
   request body; `LIVE` additionally requires proof of an owner-approved release; the root
   policies are immutable from lab code. The lab may research, design, build, test,
   benchmark, review, package, reach `SHADOW_READY` and explain itself — and stop there.
6. **Every backlog transition is auditable.** Five lifecycle statuses originally mapped to
   no ledger event because the closed vocabulary had no honest name for them; that left a
   candidate able to be quarantined with no durable trace. The vocabulary now reserves
   `evolution.researching`, `evolution.qualifying`, `evolution.rejected`,
   `evolution.quarantined` and `evolution.superseded`, and a test asserts no status maps to
   nothing. `evolution.tests_failed` still wins where it applies, because it says why.
7. **One migration, one shape.** `0016_cognitive_foundations` creates `goals`,
   `goal_tasks`, `experience_lessons`, `code_modules`, `code_symbols`, `code_edges`,
   `module_provenance` and `evolution_opportunities`, verified by upgrade → downgrade →
   upgrade. The lifecycle enum and the database CHECK constraint assert against each other
   at import time so they cannot drift.
8. **Nothing runs on a timer yet.** No subsystem added here starts a background loop at
   application startup: indexing, compilation and lab work are invoked explicitly. A
   cognitive system that wakes up on its own is a separate decision with its own cost and
   safety questions, and the owner's rule against runaway background loops is easier to
   keep than to recover.

Consequences: the owner can ask "ne öğrendin?", "kendi üzerinde ne geliştiriyorsun?" and
"canlıya alınmayı bekleyen ne var?" and get evidence-backed Turkish answers at the existing
narration levels. What is deliberately NOT claimed: general intelligence, autonomous
deployment, or that any of this is proven beyond the tests and the real runs recorded in
`docs/QUALIFICATION.md`. Deferred: scheduled compilation, the shadow-run execution harness
(M18), and any production-side evolution writer.

### ADR-0053 addendum 1 — what the independent security review found, and what is still open (2026-09-05)

An independent review of the night's architecture ran against `537112a..HEAD` before the
overnight session ended. Three defects were found; each was **reproduced** before being
fixed, because a security finding that cannot be demonstrated is a guess.

**Fixed.**

1. *Production authority by ordinary subclassing (critical).* `Authority.__post_init__`
   was the only thing refusing a production grant. A two-line subclass overriding it with
   `pass` produced a fully privileged authority — DEPLOY, SIGN_RELEASE,
   WRITE_PRODUCTION_DB, MODIFY_POLICY_KERNEL — that `guard_production_action` accepted,
   with no forbidden import, no private name and no owner session, so
   `scan_evolution_package` could never have seen it. `Authority` and `OwnerCapability`
   are now runtime-final, and the choke point additionally requires the exact type and
   the module's own mint token, which also refuses an instance rebuilt around the
   constructor by pickle or `object.__new__`. Two locks, deliberately: removing either
   one alone does not reopen the door.
2. *The injection screen lived in one HTTP route.* Incidents, compiled lessons and every
   internal caller of `ledger.service.record` bypassed it, and their text reached the
   realtime voice provider verbatim. The screen moved to `app/ledger/screening.py` and is
   applied where every path meets — the point text becomes speech — and now redacts
   secrets as well, because the self model indexes docstrings verbatim.
3. *`stale` was documented but never computed.* It was written as `False` everywhere, so
   every answer claimed a freshness it had not checked. Both models now derive it from
   the observation's age, per truth kind.

**A correction worth recording.** The first attempt at (2) reused the browser marker set,
which matches the bare word "install". This system says "installed version" constantly, so
true sentences were replaced with a refusal notice — the system lying by omission about
its own work, which is worse than the risk being guarded against. The speech screen is
therefore deliberately narrower than the ingress screen: an ingress refusal is loud and
recoverable, a mid-sentence refusal is silent. A test asserts the ingress screen stays
strictly broader, and another asserts that ordinary operational Turkish is spoken
unchanged.

**Explicitly NOT claimed, and open.**

- *In-process containment.* Any code running inside the API process can reach
  module-private names by ordinary introspection. The authority checks make the boundary
  hold against ordinary code, careless refactors and the import scanner's blind spot —
  they are not a sandbox. The real boundary for untrusted candidate code is the
  process/sandbox boundary, and nothing tonight executed candidate code in-process.
- *67 medium acceptance-wording findings*, all in test files, found by the shadow
  candidate scanning this repository. Not triaged, not fixed; the candidate stays
  advisory rather than becoming a gate until they are.
- *The Experience Compiler has no pattern for this defect class.* It produced only its
  generic "recurring voice failure" lesson from the two real incidents, so the claim "the
  lesson is already compiled" is not yet true on the evidence — the specific lesson lives
  only in this document.
- *Two low-severity items not addressed*: the self-model index takes no cross-process
  lock, and a security gate accepts `status="info"` as a pass.

### ADR-0053 addendum 2 — the Experience Compiler learns the defect class that cost two owner runs (2026-09-05)

Phase 8 reported honestly that it could not close this: the compiler had no pattern for
"acceptance depends on generated wording", so both real incidents compiled as the generic
"Recurring voice failure (incident.opened)" lesson. The claim "the lesson is already
compiled" was therefore not true on the evidence - the lesson existed only in this
document, which is exactly the gap between knowing something and the system knowing it.

`PATTERN_ACCEPTANCE_WORDING` now exists on both compiler paths (incident and ledger
event), with the same generalizability prior as the other named patterns.

It is recognised from a **declared** class (`detail_json["defect_class"]`), not inferred
from prose. That is the honest way round: the writer of the event states the class, and
the compiler only decides whether the chain is complete enough to compile. A hostile or
mistaken declaration can mislabel a lesson, but it cannot inject text - every statement
the compiler emits is authored in the compiler, never taken from the row. A test asserts
that an ordinary voice failure without the declaration still compiles as generic, so the
pattern cannot quietly widen into "any voice failure".

Two related fixes in the same pass, both from the security review: the generic branches
embedded `repr(evidence_json)` / `repr(detail_json)` in the lesson's root cause, and that
text is read aloud. They now record the blob's KEYS only. An evidence blob can carry a
captured page, an exception message or a credential; its shape is informative, its
content is not ours to speak.
