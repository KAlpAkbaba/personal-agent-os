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

## ADR-0054 — Rotating the owner credential of a DEPLOYED Cloud Core (2026-09-05)

Status: Accepted

Context: the owner lost the Cloud Owner Credential. `app.identity.recover --rotate` is the
only supported reissue path — deliberately not an API operation, because a "forgot my
credential" endpoint is by construction an unauthenticated way to mint owner authority
(`/v1/identity/bootstrap` returns 409 "use the host recovery path" once a credential
exists). Its authorization is filesystem access to the identity root.

The existing `scripts\rotate-owner-credential.ps1` runs that tool LOCALLY, in
`services\api`, where the identity root resolves to `services/api/var/identity` — the DEV
root. When the live Cloud Core is the deployed host, that script rotates a root the API
never reads: verification against the remote core then fails, and its `finally` clears the
replacement. The rotation has committed and the new credential is gone.

Decisions:

1. **Deployed rotation runs inside the deployed container**, over Tailscale SSH:
   `docker exec pagentos-prod-api /srv/pagentos/.venv/bin/python -m app.identity.recover
   --rotate --json`, against the durable identity root (ADR-0027). The venv interpreter is
   named explicitly; the image's bare `python` has no site-packages and dies on `import
   structlog` before it can reach the root. `scripts/cloud/rotate-cloud-owner-credential.ps1`
   is that path.
2. **The replacement is captured before anything that can fail.** It is written to the local
   DPAPI store as the FIRST action after parsing, then verified. This is not theoretical: on
   2026-09-05 the first real run of this script rotated production successfully and then
   threw on the very next statement, because it called `icacls` by bare name and a spawned
   non-interactive PowerShell here does not inherit a usable PATH. The `finally` cleared the
   credential. A 256-bit secret that now guarded production had been generated, shown to
   nobody, and lost; the owner had to rotate again. Every line between "the secret exists"
   and "the secret is durably captured" is a line that can lose it, so there are none.
3. **Hardening the store is best effort, never fatal.** DPAPI already makes the bytes
   useless to another account; failing to also strip ACL inheritance warns and continues.
4. **The rotation path never prints the credential.** It goes to the DPAPI store and nowhere
   else, so an agent can perform the rotation without the plaintext entering a transcript,
   a log, or a captured tool result. Revealing it is a separate, explicitly requested mode
   (`-ShowStoredCredential`) that refuses to run under PowerShell transcription. That
   separation is what makes "at most one owner action" possible here.
5. **A read-only diagnostic must not require a healthy API.** `-StatusOnly` proves the whole
   remote path — SSH, `docker exec`, the interpreter, the single-JSON contract — and reports
   what a rotation would do, while the Cloud Core is down. Requiring health to *look* was a
   defect the tests found: a diagnostic that refuses to run precisely when things are broken
   is one you cannot use. Health remains REQUIRED to rotate.
6. **What is proven, and what is not.** The script asserts `rotations` advanced by exactly
   one, `created_at` is unchanged and the root path is unchanged — a rotation, not a second
   owner — and each is a hard stop, not a warning. It performs a real owner-auth exchange
   with the new credential, and a negative control proving a non-matching credential is
   refused 401, so a success means the hash matched rather than the endpoint accepting
   anything credential-shaped. When the old credential was LOST it cannot be presented, and
   the script says the check was skipped rather than reporting a pass it did not perform.

Consequences: `scripts/tests/cloud-owner-rotation.tests.ps1` (26 assertions, fake ssh, no
network) pins the capture ordering, the absolute `icacls` path, the identity invariants and
the "never printed outside the reveal mode" property. Deliberately not changed:
`scripts\rotate-owner-credential.ps1`, which remains correct for a local dev root; it now
has a sibling for the deployed case rather than a mode flag, because the two differ in
authorization, transport and post-steps.

## ADR-0055 — Owner-authorised deployment: the lab may never promote itself, the owner may authorise a release (2026-09-05)

Status: Accepted (owner authority policy update, 2026-09-05)

Context: M17 shipped answering "Bunu canlıya alabilir misin?" with "Hayır efendim, canlıya
kendim alamam." That is not the policy. The owner's rule has two halves, and stating only
the first is its own kind of untruth: it tells the owner they cannot ask for something they
can ask for, and it would justify never building the release path they want.

Decisions:

1. **Two claims, not one.** Autonomous promotion of the engine's own candidate is forbidden
   by construction — the lab holds no production grant and cannot mint one (ADR-0053 §5,
   and the runtime-final `Authority` of ADR-0053 addendum 1). An explicit, authenticated
   owner authorisation MAY permit a release, which then runs the ordinary transactional
   deployment path. The spoken answer states both, and says that asking is not authorising.
2. **The answer is composed from policy, never hardcoded.** `authority_policy()` reports
   `autonomous_promotion_permitted`, `owner_authorised_release_permitted`,
   `owner_authorisation_requires` and `asking_is_not_authorising`; the engine renders those.
   A test flips the flag and watches the refusal come back, so neither half is a fixed
   sentence.
3. **Asking is not authorising.** "Bunu canlıya alabilir misin?" is a question and must
   start nothing. Only an explicit imperative — "canlıya al", "production'a çıkar", "bu
   sürümü yayınla" — may CREATE an owner deployment authorisation request, and a
   risk-sensitive release requires a second explicit confirmation before any mutation.
4. **Voice identity is not root authorisation.** The authorisation binds to the
   authenticated owner session and the existing production-authority mechanism. It is a
   separate privileged capability and must not be mintable or bypassable by
   Evolution-generated code — the boundary of ADR-0053 §5 is unchanged by this ADR.
5. **The intended lifecycle**, for the workflow that executes it:
   `SHADOW_READY → OWNER_APPROVAL_REQUIRED → OWNER_AUTHORIZED → QUALIFYING → DEPLOYING →
   VERIFYING → LIVE`, with `DEPLOYING|VERIFYING → FAILED → ROLLING_BACK → previous LIVE
   restored`. Preconditions before mutation: candidate is SHADOW_READY, the version/diff is
   known, tests and evaluations passed, security review acceptable, the repository input is
   clean and committed, migration impact known, a rollback point exists, and the component
   genuinely requires deployment. After: health check, runtime/source provenance
   verification, the required qualification, an Activity Ledger event, and `LIVE` only after
   verification succeeds — otherwise automatic rollback to the last proven release and a
   report to the owner.

**What is implemented now, and what is not.** Decisions 1–4 are implemented and proven: the
answer states both halves from policy, and the M17 acceptance asserts both — that it will
not promote its own candidate autonomously, and that it knows an authenticated explicit
owner authorisation can permit a release. Decision 5's STATE MACHINE and executing workflow
are specified here and **not built**: the lifecycle still ends at `OWNER_APPROVED →
QUALIFYING → LIVE`, and no `DEPLOYING`/`VERIFYING`/`ROLLING_BACK` states exist yet. Building
them is M18 work, and half-building a production deployment state machine would be worse
than not starting it. The Acceptance Wording Guard stays `SHADOW_READY` and is not deployed
by the qualification.

## ADR-0056 — The Holographic Core renderer: silence is not calm (2026-09-05)

Status: Accepted

Context: ADR-0052 reserved the architecture and deliberately did not build the renderer.
This is the renderer, built in `apps/web` as the pure consumer ADR-0052 described, with no
further subsystem changes. Two things about the starting state are worth recording because
the brief assumed otherwise: `docs/M18_HOLOGRAPHIC_CORE_SPEC.md` did not exist (it does
now, written from ADR-0052 and the contract module), and the contract is **v1** with the
fifteen `agent.*`/`evolution.*` states — there are no `eye.*`, `owner.*`, `routine.*`,
`alarm.*` or `release.*` states in this repository. The renderer was built against what the
API actually serves, and treats anything else as an explicitly unknown state.

> That last paragraph was true of the branch this was written on and is no longer true of
> main, which published v2 the same day. Addendum 2 records the reconciliation; the sentence
> is left as written because it is the reason the renderer has an unknown-state path at all.

Decisions:

1. **Silence is four different facts, and they are drawn as four different things.**
   The hardest honesty problem was not "what does thinking look like" but "what does
   nothing look like". `agent.idle` (reported calm), `untold` (polls succeed, the bus has
   never published), `last_known` (a claim aged out), `unreachable` and `unauthorized` are
   separate visual kinds with separate Turkish wording. Collapsing any of them into a calm
   breathing core would be the most common lie the Core could tell, because silence is the
   most common thing it will ever have to render.
2. **A transient state expires; it does not become idle.** The bus publishes entries into
   states and never exits, so an old `agent.thinking` is evidence that it *was* thinking.
   After 12 s the client stops claiming it and shows the shape, still and faded, with its
   age. Falling back to idle would invent the one fact nobody published: that the work
   stopped. Steady states (`idle`, `waiting_owner`, `error`, `shadow_ready`) never expire;
   `goal_completed` is a moment with a 20 s headline.
3. **Every moving channel is zero unless an event set it.** `VisualIntent` has no default
   wobble anywhere: no reported intensity means no pulse, no reported count means no
   particles, no reported progress means no bar. There is exactly one use of elapsed time
   in the 3D scene, multiplied by an amplitude that is zero for every state that did not
   report motion. The exhaustive test walks every contract state and asserts both that it
   produces its own visual and that no other state does; it fails if the API grows a state
   and the visual table is not updated, so a new state must be a deliberate visual choice.
4. **Counts are capped for the GPU, never for the reader.** A tier bounds drawn
   satellites; the readout always states the true number and how many were drawn. A
   performance cap that quietly understated how much evidence exists would be the same
   class of defect as the animation problems this ADR exists to prevent.
5. **The 2D fallback is a fallback in fidelity, not in truth.** Same `VisualIntent`, same
   readout component, every fact preserved. It is pure SVG animated by CSS only, which is
   also why it — not the 3D scene — carries the rendering tests: it renders identically
   under `react-dom/server`, so the full assertion runs in Node.
6. **No browser in the test suite, on purpose.** No Playwright, no vitest browser mode.
   `services/browser/tests/test_test_isolation_guards.py` exists because browser tests once
   flooded the owner's desktop; the discipline is inherited here by not opening the door at
   all rather than by guarding it again.
7. **Read-only, with no write path.** The cockpit reads eleven GET surfaces and mutates
   nothing; approving a goal or a SHADOW_READY candidate stays an owner action on the
   surface that owns it. `app/lib/uistate/client.ts` has no publish helper, because the
   absence of a write endpoint is what makes ADR-0052 §2 enforceable.
8. **Panels distinguish empty from unknown.** Four outcomes — not asked, failed, genuinely
   empty, loaded — with four different sentences. A failed request never renders as an
   empty list, because "there are no goals" and "I could not find out whether there are
   goals" are different claims.

Dependencies added, pinned exact: `three` 0.185.1, `@react-three/fiber` 9.7.0,
`@types/three` 0.185.4. No `drei`; the scene uses plain three primitives.

Consequences: `/core` and `/core/cockpit` render from real state today for voice,
research, goals, evolution and the self-model indexer. Two publisher defects were found by
building the consumer and are recorded in the addendum below rather than fixed here, since
this work was scoped out of `services/api`.

### ADR-0056 addendum 1 — two publishers that never publish

Found by writing a client against the real call sites, not by reading the contract.

1. **`app/experience` cannot publish at all.** `engine.py:155` and `compiler.py:683` both
   call `publish(UiState.MEMORY_RETRIEVAL, subsystem="experience", phase=phase, **metadata)`,
   but `uistate.publish()` takes no `phase` and no `**metadata` — it takes a `metadata`
   dict. Every call raises `TypeError`, which the surrounding
   `except Exception: logger.debug(...)` swallows silently. The consequence is that
   `agent.memory_retrieval` is never published by experience ingest or lesson compilation,
   so the Core's memory-convergence visual has no live producer today. The soft-import
   comment in those functions says `app.uistate` did not exist when they were written; the
   import now succeeds and the *call* is what fails, which is why this survived the merge.
2. **Research publishes only at the ranking stage.** `browser_activities.py:952` is the one
   `publish_ui` call, with a constant `intensity=0.7`. Discovery and fetching therefore
   never light the Core, and the intensity is not a measurement. The renderer treats
   `intensity` as the publisher's declared figure and never describes it to the owner as
   an audio or effort measurement.

Neither is fixed here. Both are one-line-ish changes in `services/api`, which this work was
told not to touch, and both would need their own tests.

**Resolution of (1), 2026-09-05.** Fixed on main. The two private wrapper copies were
replaced by one `app/experience/signals.py`, which has no `except` of its own: the publisher
already guarantees "never raises" for runtime failure, so anything thrown at that call site
is a wrong call and must fail a test rather than silence a subsystem. Lists are reduced to
their length before publishing, because the publisher drops content-shaped values and
`IngestReport.errors` was therefore vanishing entirely. Tests read the stamped event back
off a real publisher rather than asserting a mock was called - a mock would have passed
against the broken code too.

(2) stands. `intensity=0.7` at the ranking stage is a declared figure, not a measurement,
and the renderer still treats it as one.

### ADR-0056 addendum 2 - the channel split, and contract v2

Status: Accepted (2026-09-05)

The renderer was built against contract v1 while main published v2, which added twenty-two
states: the camera (`eye.*`), the owner's presence (`owner.*`), routines and alarms, and the
owner-authorised release path (`release.*`). Reconciling them exposed a modelling error that
v1 had hidden: **the bus carries four different kinds of statement, and `current` is only
the newest of them.**

`agent.thinking` is what the assistant is doing. `owner.likely_asleep` is a fact about the
room. `release.deploying` is an operation being watched. Under v1 every state was the first
kind, so "the API's current event" and "what the core should draw" were the same question.
Under v2 they are not: publishing that the owner went to bed would have blanked a core that
was genuinely thinking, and the owner would have watched their assistant appear to stop work
because they yawned.

So states carry a **channel** (`agent`, `lab`, `ambient`, `release`). The core body draws
agent/lab via `coreClaim`; ambient and release are drawn beside it, each with its own age.
The eye and the owner are further separated by prefix, because a presence update says nothing
about the camera and must not be able to blank a privacy indicator.

Two more decisions worth recording:

1. **Two new state kinds, because twelve seconds is the wrong TTL twice over.** A presence
   observation is not stale after twelve seconds (`observation`, five minutes), and a
   deployment that takes four minutes is not lost (`operation`, fifteen). Both still expire:
   the M18 spec's rule is that an old observation degrades to UNKNOWN, never to "still
   present". When a publisher sends `ttl_s` it wins over every default here - the subsystem
   that made the observation knows how long it is good for, and the client guessing over the
   top of that would be the renderer inventing truth.

2. **The camera indicator may not say "off" without evidence.** `untold` and `disabled` are
   different sentences and only one is a privacy assurance, so they are separate statuses
   with separate wording, and an `eye.*` state this build cannot read is flagged as
   unreadable rather than falling to "off". Presence is rendered with the engine's own
   confidence or with "güven bildirilmedi" - never with a substituted number - and the band
   states on every render that perception is not authentication (M18 spec §2).
## M18 Presence Engine + Active Eye intake: implementation notes (2026-09-05)

Status: Accepted (reversible implementation choices, recorded per CLAUDE.md "Asking the
owner")

Context: building `app/presence` (M18_HOLOGRAPHIC_CORE_SPEC.md §1, §2) surfaced three
places where the spec's own integration requirements met existing M17/ADR-0052 code that
did not yet have the shape the spec assumed, or where a genuinely reversible design choice
had to be made without an obvious single right answer.

Decisions:

1. **The UI-state contract did not yet have `owner.*`/`eye.*` states.** ADR-0052's
   vocabulary (`app/uistate/contract.py`) was v1 and only knew `agent.*`/`evolution.*`.
   Rather than treat "these already exist in the contract" as true, `CONTRACT_VERSION` was
   bumped to 2, six `owner.*` states plus `eye.active`/`eye.disabled` were added, and a
   `"presence"` subsystem was added to `SUBSYSTEMS` — additive only, so a renderer that
   still speaks v1 is unaffected; `GET /v1/ui/state/contract` reports the true version.
2. **No new table, no new migration.** The Active Eye's enable/disabled state is durable
   via two ledger event types (`eye.enabled`/`eye.disabled`, `app.ledger.vocabulary`) read
   back by `app.presence.eye.is_eye_enabled` (always freshest-row-wins, never cached) rather
   than a dedicated boolean column — the ledger already is the durable record of what
   happened (ADR-0052 §3), and the current PRESENCE STATE itself stays in-process/ephemeral
   like `app.uistate.publisher`'s current event, with only MEANINGFUL transitions written to
   the ledger (`presence.state_changed`, spec §5's "do not overcollect"). `alembic heads`
   confirmed a single head (`0017_cognitive_ts_defaults`) before and after this change.
3. **`app/worldmodel/state.py` and `app/worldmodel/routes.py` were touched, narrowly.** The
   task instructions said not to touch "anything M17," and the World Model is an M17
   package — but the same M18 spec explicitly requires presence to feed
   `owner.presence`/`owner.awake_state`/`owner.activity_level`/`owner.last_seen`/
   `device.camera_state` into it. Read as in tension rather than contradictory: the
   restriction is against scope-creep into the OTHER M17 subsystems (goals, evolution,
   experience, self-model, explain), not against the one integration point this milestone's
   own spec names. The change is additive and minimal: one new `_collect_presence` section
   function following the existing optional-injected-runtime pattern
   (`_collect_devices`/`broker_runtime`), one new optional `presence_runtime` parameter on
   `assemble_snapshot`, one new `stale: bool = False` parameter on `_Collector.fact` (so a
   caller with its own, more specific TTL — `PresenceAssertion.is_stale` — can pass that
   floor through rather than being re-derived from the World Model's generic 15-minute
   `RUNTIME_TRUTH` default), and the corresponding `_presence_runtime` accessor in
   `worldmodel/routes.py`. Nothing else in either file changed.

Consequences: a future renderer or routine can read presence off `GET /v1/ui/state`,
`GET /v1/presence/state` or `GET /v1/world` and get the same underlying assertion, each at
the right level of detail. Deferred, and explicitly not built by this change (M18's own
dependency order): `app/routines`' actual trigger wiring (`owner.returned`,
`owner.likely_asleep`, `owner.awake` → conditions → actions) and the Active Eye's
device-side local-perception client — this change is the Cloud Core boundary they will call.
## ADR-0057 — M18 Routine Engine: trigger, conditions, actions (2026-09-05)

Status: Accepted

> Renumbered from 0056 on merge: two branches, unable to see each other, both
> claimed that number. The renderer's ADR-0057 kept it. The context below is also
> corrected by events - `docs/M18_HOLOGRAPHIC_CORE_SPEC.md` exists on main and did
> when this was written; it was simply not on this branch. The reasoning stands,
> and the spec agrees with it.

Context: the task brief for the Routine Engine (durable TRIGGER -> CONDITIONS -> ACTIONS)
instructed reading `docs/M18_HOLOGRAPHIC_CORE_SPEC.md` sections 3/4/7. That file does not
exist anywhere in this repository's history, on any branch. `state/BUILD_STATE.json` does
confirm the milestone is real (`M18_HOLOGRAPHIC_CORE_ACTIVE_EYE_AMBIENT_PRESENCE`, "routines/
alarms" named in its `status`), and `docs/DECISIONS.md` ADR-0052/ADR-0053 describe the
Holographic Core's event contract and the M17 foundations it builds on — but no document
carries the routine engine's own §3/§4/§7 design. Rather than invent product decisions
silently, this ADR records the ones made and why, per CLAUDE.md "Asking the owner": every
choice below is reversible and consistent with the constitution, so none of it was worth
interrupting the owner for; a genuine spec document, if one surfaces later, should reconcile
against this ADR rather than the other way around.

Decisions:

1. **The model is a plain durable row, not a running process.** `app.routines.models.Routine`
   holds a trigger, a list of condition descriptors and a list of action descriptors as
   JSON; `app.routines.service.evaluate_due` is the ONLY thing that ever decides whether it
   fires, and it is an explicit function call — there is no background timer anywhere in
   this package, matching the M17 rule that a cognitive system scheduling its own wakeups is
   a separate decision (ADR-0053 §8) this milestone does not make.
2. **Idempotent firing is a database constraint, not caller discipline.**
   `RoutineFiring` carries a unique `(routine_id, occurrence_key)` — an "at" trigger's key is
   the constant `"once"`, a "schedule" trigger's key is the local calendar date in the
   trigger's own timezone, and a "presence" trigger's key is the source `app.uistate` event's
   own `event_id`. A second `evaluate_due` call for an already-resolved occurrence is a
   cheap no-op at the database level even if two callers race.
3. **DST safety is structural, not a documented caveat.** A schedule trigger's due-check
   always converts the caller's `now` through `zoneinfo.ZoneInfo(trigger_timezone)` and
   compares wall-clock fields — never a cached UTC offset. The owner is in Europe/Istanbul,
   which currently observes no DST, so this only matters for other zones an integration
   might use; the mechanism is correct regardless, proven by
   `tests/unit/test_routines_triggers.py`'s Europe/Berlin spring-forward cases (a
   fixed-offset implementation would fire an hour off on one side of the transition without
   ever raising).
4. **Presence triggers depend on three event-name strings, not on `app/presence`.** That
   package is being written in parallel and does not exist in this checkout. Rather than
   block on it or guess its shape, `app.routines.triggers.PRESENCE_TRIGGER_EVENTS`
   (`owner.returned`, `owner.awake`, `owner.likely_asleep`) are plain string constants
   compared against whatever `state` value appears on `app.uistate.publisher`'s tail — not
   against `app.uistate.contract.UiState` enum membership, and not against anything
   `app/presence` exports. Whatever shape the presence track eventually publishes, as long
   as one event's `state` string-compares equal to one of these three names, a
   presence-triggered routine sees it. This is the narrowest seam that satisfies "depend
   only on the event names... NOT on app/presence internals" from the task brief.
5. **The Routine Engine's own outputs DO extend the UiState contract, because this package
   owns them.** `app.uistate.contract.CONTRACT_VERSION` moves 1 -> 2, adding
   `UiState.ROUTINE_ARMED`, `UiState.ROUTINE_TRIGGERED` and `UiState.ALARM_TRIGGERED` and a
   `"routine"` subsystem. This is additive (existing v1 states are unchanged) and is the
   opposite case from decision 4: presence is consumed from a track this package does not
   own, so its vocabulary is left alone; `routine.*`/`alarm.triggered` are published BY this
   package, so they belong in the shared contract like every other subsystem's states.
6. **Conditions fail closed on "unknown," always with a reason.** Every field of
   `app.routines.conditions.RoutineConditionContext` defaults to `None`/empty, meaning
   "no subsystem has told this evaluation what the real state is yet" — none of the five
   condition kinds (owner present, quiet hours, display state, active task, policy
   permission) treats unknown as a pass. A skipped firing always carries a non-empty reason
   in both `RoutineFiring.skip_reason` and the `routine.skipped` ledger event's
   `detail_json`, per the task brief's "never silently dropped."
7. **Execution is out of process by construction.** `app.routines.actions.RoutineDispatcher`
   is a `Protocol`; `NoopDispatcher` (used here and by every test) always reports
   `dispatched: False`. This package validates and snapshots WHAT should happen
   (`RoutineFiring.actions_snapshot`) and records the dispatch outcome the injected
   dispatcher reports — it never imports `app.voice`, `app.narration`, `app.devices` or a
   browser/media client to perform one.
8. **A media action's owner-chosen url/title is never rewritten.**
   `app.routines.actions.validate_media_playback` requires a non-empty `url` (refusing a
   routine that would choose content on the owner's behalf) and otherwise copies the detail
   dict verbatim — no trimming, casing, or normalization — proven end to end by
   `tests/unit/test_routines_service.py::test_media_action_preserves_the_owners_requested_item_end_to_end`.
9. **An alarm action must configure a ramp, not a level.** `validate_alarm` refuses a wake
   volume whose `start` is already at or above `MAX_WAKE_VOLUME_START` (0.5) or whose
   `start > end` — a routine cannot describe "jump straight to maximum" even by omission,
   because the default ramp (0.05 -> 0.8 over 60s) is what a caller gets without specifying
   one.

Consequences: `docs/DECISIONS.md` (this entry) is the closest thing to
`docs/M18_HOLOGRAPHIC_CORE_SPEC.md` §3/§4/§7 that exists right now. If that spec file is
written later, whoever writes it should diff it against this ADR rather than assume a clean
slate — the schema, vocabulary and contract version bump above are already live.

## ADR-0058 — M18 Active Eye: the device-side local-perception client (2026-09-06)

The Cloud Core boundary (`app/presence/observations.py`, `app/presence/eye.py`,
`M18_THREAT_MODEL.md` §1–§3) already existed and was already proven: a closed seven-field
schema, refuse-not-redact screening, and a disable path that is durable, immediate and
observable. What did not exist was the thing that calls it — a browser client that opens a
camera, and the guarantee that the frame it reads never becomes anything more than those
seven numbers. This ADR is that client, in `apps/web/app/lib/eye/` and `apps/web/app/core/`.

1. **The frame-never-escapes guarantee is a closure boundary, not a comment.**
   `BrowserFrameSource` (`perception.ts`) is the only class in the client that ever calls
   `getImageData`; its `capture()` return value is consumed exactly once, synchronously, by
   `computeGridLuminance` (`signal.ts`), which reduces it to a 12×9 (108-number) luminance
   grid before the pixel buffer goes out of scope. Every function downstream of that point —
   `motionEnergyBetween`, `derivePresence`, `deriveAwakeState`, `deriveObservation` — takes
   and returns plain numbers or the seven-field `EyeObservation`, never anything
   frame-shaped. There is no method anywhere in `FrameSource`, `PerceptionSession`, or
   `useActivePerception` that returns pixel data, a canvas, a video element or a
   `MediaStream`'s frames to a caller; a reviewer can grep the module for `ImageData`,
   `getImageData` or `Uint8ClampedArray` and every hit stays inside `BrowserFrameSource`.
   Even the one frame-shaped value that DOES persist across ticks — the previous luminance
   grid, needed for frame-differencing — is deliberately downsampled to 108 numbers
   specifically so it could not be reassembled into a recognisable image even if the closure
   boundary were somehow broken.

2. **Derivation is honest, weight by weight, and says so in code, not prose.**
   `activity_level` buckets a measured motion-energy value at three named, commented
   thresholds (`NOISE_FLOOR`, `LOW_ACTIVITY_MAX`, `MEDIUM_ACTIVITY_MAX`). `posture` is
   always `"unknown"` — a constant function, not a heuristic — because motion energy and
   luminance say nothing about body pose and this client does no face/body detection of any
   kind, exactly the spec's own worked example. `presence_confidence` is
   `evidenceQuality × sampleConfidence`: a multiplicative gate, not an additive one, because
   an early version of this formula (caught by its own test, see `signal.test.ts`'s "no
   evidence at all" case) let a single clean-looking reading round up to 0.75 confidence
   with zero samples collected — an additive weight small enough not to dominate the other
   terms cannot guarantee "confidence must be low when the evidence is weak"; multiplying by
   a sample-count gate can. Absence confidence is separately capped
   (`ABSENCE_CONFIDENCE_CAP = 0.75`) because a motion sensor has no positive evidence of an
   empty room — stillness looks identical to a present-but-motionless owner.

3. **Disable is proven, not asserted, against the tightest race the language allows.**
   `PerceptionSession#stop()` bumps a generation counter and calls `frameSource.stop()`
   synchronously (the camera light goes out before `stop()` returns), then relies on the
   fact that everything in `#tick()` between capturing a frame and the pre-post check is
   synchronous — there is no `await` in between, so JavaScript has no point at which
   `stop()` could interleave. `perception.test.ts` proves this by making `capture()` itself
   call `session.stop()` reentrantly (the tightest race constructible) and asserting the
   sample is never posted, plus separate tests for the one place a race genuinely exists —
   an ALREADY in-flight network POST — proving `stop()` aborts it via `AbortController` and
   schedules no further tick regardless of how that POST resolves.

4. **Durable-first ordering, both ways, each defended by a different mechanism.** Enabling
   locally opens the camera before calling `POST /v1/presence/eye/enable`, so the Cloud Core
   is never told perception is running when it is not. Disabling calls
   `POST /v1/presence/eye/disable` before `PerceptionSession#stop()`, so a sample already
   captured before the local stop takes effect is refused server-side
   (`is_eye_enabled` is read fresh from the database on every camera-sourced observation,
   per `app/presence/service.py`) the moment the flag flips — the client-side abort is a
   latency improvement, the server-side fresh-read is the actual backstop.

5. **The Turkish voice commands are ONE new `Intent`, not a second table.** `Gözünü kapat`,
   `Kamerayı kapat` and `Beni izleme` are matched in `services/api/app/voice/intents.py`'s
   existing `resolve_intent` — the same `normalize_transcript`/`_has`/`_has_exact` primitives
   every other intent in that file uses, checked before even `STOP` — rather than a new
   pattern-matching file. `record_client_events`
   (`app/voice/realtime_sessions/service.py`) calls `app.presence.eye.disable_eye`
   DETERMINISTICALLY the moment `Intent.EYE_DISABLE` resolves from a live utterance, rather
   than waiting for the realtime provider to decide to call a tool — a privacy-critical
   disable must not depend on a model's judgement call. `EyeControl.tsx` closes the loop on
   the device side: it watches the same `eye.*` UI-state claim `AmbientBand` already reads,
   and reacts to a `disabled` transition (however it was triggered — this device's own
   button, another device, or voice) by calling the local-only stop path, `stopLocalOnly()` —
   no redundant `disableEye()` call, since the Cloud Core is already told.

6. **The control is its own component, never merged into `AmbientBand`.** `AmbientBand`'s
   own tests assert it renders no `<button>`, no `<form>`, no `<input>` at all — that is
   contract v2's read-only half, and this feature must not compromise it. `EyeControl.tsx`
   (hook wiring) and `EyeControlView.tsx` (pure markup, tested with `react-dom/server`
   exactly like `AmbientBand`) are new, separate files, following the same
   owner-action-lives-on-its-own-surface rule the cockpit panels already use for goals and
   SHADOW_READY candidates.

Consequences: `services/api/app/presence/eye.py` and `observations.py` are unchanged — this
milestone's device-side half calls that boundary rather than altering it, per the task's own
scoping. `services/api/app/uistate/contract.py` and `app/ledger/vocabulary.py` are likewise
untouched; nothing in this change needed a new UI-state token or ledger event type. The one
open item from `M18_THREAT_MODEL.md` §9 ("the device-side local-perception client does not
exist yet... the client's own handling of frames has to be proven in that code when it is
written") is what this ADR closes — `perception.test.ts` and `signal.test.ts` are that proof.
## ADR-0059 — M18 release executor: risk tiers, preflight, and two boundary decisions (2026-09-06)

> Renumbered from 0058 on merge: the Active Eye client's ADR took that
> number first. Two branches, unable to see each other, chose the same next free
> integer - the third time this milestone.

Status: Accepted

Context: building the executing workflow ADR-0055 §5 specified but did not build — risk
tiers 1–5, preflight, post-deployment verification, automatic rollback — required two
decisions the spec's prose does not resolve on its own, plus surfaced one real gap in the
existing production-authority kernel.

Decisions:

1. **Risk tier is derived from touched paths, by a rule table, and stored once — never
   accepted as an argument.** `app/evolution/risk.py`'s `derive_risk_tier()` is a pure
   function of a candidate's changed-path list; the overall tier is the MAXIMUM tier any
   touched path matches. `EvolutionService.record_release_footprint()` is the only writer of
   `detail.risk_tier` on an opportunity, and it is lab-scoped (the engine may describe its
   own candidate) but refuses once the candidate has left the lab side of the wall.
   `EvolutionService.authorize()` — the new, separate owner action for
   `OWNER_APPROVAL_REQUIRED → OWNER_AUTHORIZED` — reads that stored tier back; it has no
   `risk_tier` parameter at all, so nothing can talk a release down to a lower tier by
   varying an argument. Tier 3+ requires a second, explicit `authorize(..., confirm_high_risk=True)`
   call; the first call refuses and reports the tier and reasons.
2. **A component's first-ever deployment cannot pass preflight.** The spec's
   `rollback_point_exists` precondition is read literally: with no previous `active` release
   recorded for a component, there is nothing to roll back to, so preflight refuses. This
   means the executor built here cannot bootstrap a brand-new component's first release —
   only re-releases of an already-live component. That is a real, known limitation, not a
   special case quietly designed away: reversed here only by recording an explicit ADR,
   consistent with "asking the owner" being reserved for irreversible conflicts, and this one
   is neither irreversible nor a genuine two-interpretation conflict. Bootstrapping a
   component's first release (e.g. by accepting a documented manual rollback plan in place of
   a `releases` row) is deliberately left for later, scoped work.
3. **`EvolutionService.advance()` now checks where a transition leaves FROM, not only where
   it goes TO.** Building the executor's failure path (a failed rollback should honestly land
   on `QUARANTINED`, not a false `ROLLED_BACK`) surfaced a real boundary gap: `QUARANTINED`
   and `REJECTED` are ordinary lab-reachable targets earlier in the lifecycle (a candidate can
   be quarantined mid-`BUILDING` with no owner involved), and neither is classified as
   `PRODUCTION_SIDE_STATUSES` — but both are ALSO legal targets from `QUALIFYING` and
   `ROLLING_BACK`, which ARE production-side. Because `advance()` only checked the target's
   classification, a **lab actor could call `advance(target=QUARANTINED, actor=LAB)` (or
   `REJECTED` from `QUALIFYING`) on an opportunity that was mid-deployment or mid-rollback,
   using nothing but its own default `propose_candidate` grant** — no production authority
   at all. Confirmed against the real service (not just the transition table) before fixing
   it. The fix: a transition now requires production authority whenever EITHER the target OR
   the CURRENT status is production-side, with the production action resolved from whichever
   of the two the action-table actually names. Regression tests pin both directions: the gap
   is closed, and the ordinary lab-side use of `quarantined`/`rejected` still works
   unchanged. See `EvolutionService.advance()` and
   `tests/unit/test_evolution_authorize.py::test_a_lab_actor_cannot_divert_a_live_rollback_to_quarantined`.

What is built: `app/evolution/risk.py` (risk tiers), `EvolutionService.record_release_footprint`
and `.authorize` (the tier-gated owner action), and a new `app/release` package —
`preflight.py` (ten checks, refuse rather than warn), `backend.py` (the
`DeploymentBackend` seam and `FakeDeploymentBackend`), `execution.py` (`ReleaseExecutor`:
`QUALIFYING → preflight → DEPLOYING → VERIFYING → LIVE`, with every failure path — including
a preflight refusal — ending in `FAILED → ROLLING_BACK → {ROLLED_BACK, QUARANTINED}`).
`app/release` is deliberately outside `app/evolution` (root policy `deployment_authority`:
deployment lives outside the engine); it mints no authority of its own and is exactly as
authorised as the `Authority` object its caller hands it. No real deployment backend exists
yet and none was run against the real Hetzner Cloud Core — everything above is proven against
fakes only.

## ADR-0060 — M18 routine dispatch: a routine that decides must also act, visibly (2026-09-06)

Status: Accepted

Context: after ADR-0057 a routine could be created, become due, pass its conditions, write a
`RoutineFiring` and a `routine.executed` ledger row — and then nothing happened. The only
`RoutineDispatcher` was `NoopDispatcher`, and there was no device capability an alarm could
have reached anyway. Worse, the wiring was such that a real dispatcher could have been written
and still never called: `evaluate_due` defaulted to `NoopDispatcher()` and the HTTP route never
passed one. That is the "built, tested, never wired" class this project has hit before, and it
was found by the agent building the dispatcher rather than by any test.

This ADR was written on merge. The branch that did the work cited an "ADR-0058" it never
wrote, and 0058 on main is the Active Eye client; its citations were re-pointed here.

Decisions:

1. **One real dispatcher, two ports.** `app/routines/dispatch.py::ActionDispatcher` routes each
   of the five action kinds to the subsystem that owns it through `BriefingPort` (narrate over
   the live realtime companion) and `DeviceActionPort` (one device command over the proven
   broker path — the same `select_device` → `DeviceCommandClient` path research uses). Every
   test fakes the two ports; nothing in the suite plays audio, opens a browser or touches a
   display. The dispatcher is registered from `app.main`'s lifespan, and `evaluate_due` reads
   the registry, falling back to `NoopDispatcher` only when nothing is registered.
2. **A failed or refused action is visible in three places.** The firing's own
   `dispatch_results`/`dispatch_status` columns (migration `0020_routine_dispatch`), a dedicated
   `routine.action_failed` / `routine.action_refused` ledger event per action — never only a
   field inside `routine.executed`'s JSON — and a `UiState.ERROR` publish, critical for an
   alarm and warning for everything else. A wake-up that did not fire is worse than a briefing
   that did not.
3. **The greeting cooldown starts after delivery, and only then.** `voice_briefing`'s success
   is the sideband push's own return value — not "a session existed", not "the text was
   queued". The cooldown (`record_greeting_delivered`, through the one seam
   `app/routines/presence_link.py`) starts only when a `greeting_allowed` condition passed AND
   the firing's briefing action actually succeeded. The whole `GreetingDecision` travels in the
   condition context, opaquely, so `app/routines` still never imports `app/presence` outside
   the seam.
4. **The alarm is a ramp on both sides of the wire.** Cloud Core refuses a malformed wake
   volume at creation and again at dispatch; the companion's `WakeRamp` refuses a start at or
   above 0.5, clamps an end above 0.85 and reports the clamp, and `WakeTone` clamps the
   generated samples to the ceiling independently — the two would have to fail together. The
   level scales the companion's own samples and never the Windows master volume. It always
   stops: `desktop.alarm_stop`, its own `max_duration_s` (10–1800 s), or companion shutdown.
5. **Display-off is built and unreachable, twice.** `desktop.display_off` exists on the
   companion (a `WM_SYSCOMMAND`/`SC_MONITORPOWER` broadcast via `SendMessageTimeout`, the only
   operation, and a test reads the source to assert no shutdown/suspend/hibernate/logoff API
   appears). It is advertised and routed only behind `DisplayPowerEnabled` (default false),
   refused by service and companion otherwise; and Cloud Core refuses a routine's
   `display_action` behind `DISPLAY_ACTION_QUALIFIED = False` until display-off has passed its
   own separate owner qualification. Neither gate knows about the other. Media actions play
   the exact URL the owner named through the browser family; no CAPTCHA or anti-bot handling
   exists anywhere in the dispatcher.

Consequences: `desktop.alarm_start`/`desktop.alarm_stop` are advertised unconditionally by
the Windows agent (a companion with no render endpoint answers `dependency_unavailable`,
retryable), so the installed agent must be updated before Stage 12.12 (a real alarm) can be
attempted — carried by the next needed install, not a forced redeploy. `voice_briefing`,
`media_playback` and `browser_action` execute end to end against what is already installed.

## ADR-0061 — The Core is the owner's voice surface: one session, drawn as itself (2026-09-06)

Status: Accepted

Context: the owner opened `/core`, saw a truthful Holographic Core, and could not speak to
PersonalAgentOS. The PROVEN_REAL realtime voice client (`VoiceSessionController`, M12–M17)
existed only as `/voice`, a 1057-line page that built its own audio rig and controller
inside a React effect and tore them down on unmount. Nothing on `/core` could reach it,
and the obvious wrong fix — a second controller for the Core — would have meant a second
`getUserMedia`, a second transport and a second realtime session in the same tab.

Decisions:

1. **One rig, one controller, one session per tab, and a counter that can disprove it.**
   The construction `/voice` did in its effect is extracted verbatim into
   `apps/web/app/lib/voice/rig.ts` (`createVoiceRig`, `browserRigParts`) and owned by a
   module-level singleton, `apps/web/app/lib/voice/store.ts` (`getVoiceStore`). The rig is
   built lazily on first use and never disposed by a view: leaving `/voice` no longer ends
   the session, and returning to it does not open a second microphone. `/core`,
   `/core/cockpit` and `/voice` are all `useSyncExternalStore` subscribers. The controller's
   behaviour is untouched — barge-in, the hesitation guard, `Dur`/`Devam et`, the narration
   cursor and tool routing are exactly the M12–M17 code. `voiceInstances` counts rigs,
   controllers, microphone opens (each is one `getUserMedia`), transports and
   `201 Created` sessions at the construction sites themselves, and
   `tests/voice/store.test.tsx` asserts `{1,1,1,1,1}` after two consumers, a remount, and
   two simultaneous `connect()` calls. The `BrowserMicrophone` in `lib/voice/audio.ts`
   remains the only audio `getUserMedia` in the codebase; the one exception outside the
   rig is `/voice`'s owner-triggered AGC A/B benchmark, which opens a temporary probe only
   while no session microphone is open and closes it before returning.
2. **The voice session is drawn as a local overlay in the same `visualFor` pipeline, never
   as bus events.** `visualFor(truth, now, voice?)` takes a `VoiceOverlay` — the
   controller's state, two measured levels, a caption, a tool label, an error — and
   `applyVoiceOverlay` replaces the core body for the states the controller actually holds,
   keeping the release orbit exactly as the bus drew it. `idle`/`closed` return the bus
   intent untouched: a closed leg says nothing about what the agent is doing elsewhere.
   `VisualIntent` gained `source: "bus" | "voice"` and `voiceState`, and `StateReadout`
   names the source on every render ("Kaynak: bu cihazdaki ses oturumu" / "Kaynak: Cloud
   Core durum akışı"), because a listening core drawn from this device's own session and
   one drawn from the cloud's account of some other device are different claims. The Core
   still has no write path; the overlay is a report, and nothing here publishes.
3. **The speaking pulse is the assistant's real output envelope.** `Playback` gained an
   optional `outputLevel()`; `WebAudioPlayback` implements it as the RMS of the same
   analyser that already detects first audio, normalised by a fixed
   `OUTPUT_FULL_SCALE_RMS` (a linear measurement, not an auto-gained one), 0 whenever the
   path is not playing or is silenced, `null` when no output context exists. The page
   samples it with `requestAnimationFrame` only while the controller is `speaking`, and
   the local gate's level only while `listening`. There is no timer that advances state and
   no synthesised rhythm: `interrupted` pulses at exactly 0 because the controller already
   silenced the path (stop-first, ADR-0040), and an unmeasurable envelope draws no pulse and
   is worded "Çıkış seviyesi ölçülemedi" rather than drawn as silence. The Core itself has
   no Web Audio code — it reads one number from the store.
   *Addendum (2026-09-07, ADR-0066):* the envelope is a measurement of amplitude and
   nothing more. It does not decide when `speaking` ends — the controller's speech
   lifecycle does (first audible playback → the final audio of that response actually
   completed, or an interruption), and `response_done` alone does not end it. A pause
   inside an answer is `speaking` with a pulse of 0, never `listening`.
4. **The Core's voice control states facts and offers one action.** `VoiceControl` /
   `VoiceControlView` is an `ambient-cell`: connected / connecting / disconnected, the
   controller state, the selected microphone and speaker by name, the noise mode from the
   device profile (or that no profile exists yet), the provider, a running tool, the last
   error, and one control — Bağlan / Bağlantıyı kes / Yeniden bağlan — plus a link to
   `/voice`. No `<select>`, `<input>` or `<form>`: choosing devices, voices and noise modes
   stays on `/voice`, which is now the diagnostics/developer view over the same session.
   After the first browser permission and owner sign-in, reconnecting is done from the Core.
5. **The speech caption is one semantic line, never the transcript.** While speaking the
   Core's label is `speechCaption()`: the running tool's Turkish phrase
   (`research.start` → "Araştırma sonuçlarını anlatıyorum…"; an unlisted tool falls back to
   its own name rather than an invented phrase), else the narration cursor's position, else
   nothing. `assistantText` is not part of the overlay type at all. The transcript stays on
   `/voice`.
6. **Eye ↔ voice needed no new mechanism, only a pure seam and a test.** `Gözünü kapat`
   already resolves server-side, the bus already publishes `eye.disabled`, and
   `EyeControl` already called `stopLocalOnly()`. The one-line decision is now
   `lib/eye/reconcile.ts::shouldStopLocalPerception` (stop-only: a bus `eye.active` never
   starts this device's camera, because starting is a `getUserMedia` grant on this device),
   and `tests/eye/reconcile.test.tsx` runs it against a real `PerceptionSession` on a fake
   frame source: the loop stops, the camera is released synchronously, no further tick is
   scheduled, and the band and control both read disabled; `eye.active` is reflected on the
   same render. React effects cannot run under `react-dom/server`, so the effect itself is
   one line joining three tested parts rather than a fourth thing to test.

What was found wrong along the way, recorded rather than silently fixed:

- `/voice` disposed the controller on unmount, so any navigation ended the session and the
  next mount opened a fresh microphone — the defect this ADR exists for, and the reason
  the store never disposes the rig.
- `/voice`'s `connect` re-listed devices after the grant but its `changeMic` resolved the
  profile from the *pre-grant* device list; the store resolves on both paths.
- `docs/M18_CORE_RENDERER.md` §3 said the Core has "no Web Audio code" and never reads
  owner audio; the first is still true and the second needed qualifying (the local gate's
  bounded level is read while listening). Rewritten, with the overlay table.
- The controller exposes no cognition `query_kind`/subsystem, so the caption cannot yet
  say "World Model"; it says the tool or the cursor, and nothing when neither is known.

Consequences: `pnpm --filter @pagentos/web test` grows from 420 to 488 tests, all Node +
`react-dom/server` + the existing fakes; no browser, no Playwright. The owner's next real
run on `/core` should show: Bağlan → `listening` with inward flow tracking the microphone,
a tool → `tool_running` with its Turkish name, speech → a pulse that is the audio, "Dur" →
`interrupted` with the pulse at zero on the same frame, and "Gözünü kapat" → the eye cell
reading disabled while the local camera light goes out.


## ADR-0062 — Presence from a camera: the unit is the cell, and presence is a memory (2026-09-06)

Status: Accepted

Context: the first real M18 owner run. The owner enabled the camera and sat in front of it
for eleven minutes; the production Presence Engine held `away` at confidence 0.75 the whole
time, on 1,102 camera observations, and published exactly one transition. Every layer below
the camera behaved correctly — the intake screened, the engine fused, the ledger recorded,
the alarm fired — and the answer was still wrong, because the observation the device client
sent was wrong. Its `person_present` was "the whole-grid mean luminance difference between
two samples exceeds 2%". A seated person typing or turning their head changes a handful of
the 108 grid cells strongly; averaged over all of them that is under 1%. The client was a
motion sensor whose unit was the whole frame, and at that unit a still person and an empty
room are the same picture.

A second defect compounded it: the UI-state bus publishes `owner.*` on change, the camera
observation's TTL is 90 s, and a held state was never republished — so the Core showed
"Sahip durumu bilinmiyor" for ten of those eleven minutes while the engine held a live claim.

Decisions:

1. **The unit is the cell.** `measureMotion` reports the FRACTION OF CELLS whose luminance
   changed by more than `CELL_CHANGE_THRESHOLD` (0.05, which a cell's thousands of averaged
   pixels do not reach by noise), plus the strongest single-cell change. Activity buckets on
   that fraction: two cells is the least a real movement produces, a tenth of the grid is a
   shift or gesture, a third is someone walking through. The whole-grid mean is kept only
   for the record.
2. **Presence is a memory, not a moment.** A person in a room moves within a minute or two,
   always. `person_present` is "a meaningful movement within `PRESENCE_MEMORY_MS` (90 s)",
   and confidence is a stated function of how recent that movement was and whether the
   owner is moving now — never of a single frame.
3. **An exit is distinguishable from stillness.** Leaving a room is a large burst of change
   followed by nothing; sitting still after a fidget is not. The level of the last movement
   before the stillness began is kept: an exit-sized last movement leans "absent" as the
   silence grows; a small one leans "present, very still" through a `LINGER_MS` (10 min)
   window at a confidence that says how weak that evidence is (a 0.35 floor, fading), and
   only then "absent". Absence confidence never passes 0.75, because a motion sensor has no
   positive evidence of an empty room.
4. **The numbers are on the Core.** The eye cell shows the age and level of the last
   movement and the changed-cell fraction, so "why does it think that?" is readable off the
   screen during qualification without any frame existing anywhere.
5. **A held state heartbeats.** The presence service republishes a held, non-UNKNOWN state
   once half its TTL has elapsed since the last publish — a bus event, not a ledger row.

Consequences: the thresholds are named constants calibrated against synthetic frames and
one real room's failure; the next real owner run is what calibrates them further, and the
diagnostics on the Core exist so that run can say what it saw. `resting` is now reachable
(a present owner still for five minutes after a small movement) and so, in time, is
`likely_asleep`; both remain inferences with stated confidence. Not built: any person or
face model. The client still cannot tell one person from another, and must not.

## ADR-0063 — Actions are receipts: WRITE -> READ-BACK -> SPEAK (2026-09-06)

Status: Accepted

Context: the owner's real M18 run, two defects in one session. The owner said "Gözünü
kapat." The deterministic safety net in `record_client_events` really disabled the eye,
but the realtime model had no tool for the eye and answered "öyle olmuş gibi düşün" - a
spoken claim about a physical mutation, grounded in nothing but the intent. "Gözünü aç"
then did nothing at all: no enable path existed on either side. Separately, "Kendi
sisteminde şu anda ne görüyorsun?" was routed to `activity.explain`, whose `world_state`
branch narrated how many facts of each truth kind the World Model held. The owner asked
what the system sees now and heard bookkeeping. `docs/M18_ACTION_CONTRACT.md` is the
binding contract for both halves; this ADR records the Cloud Core decisions.

Decisions:

1. **A mutation is narrated only from its receipt.** `OWNER COMMAND -> normalise ->
   authorise -> execute -> wait for the terminal ACK -> read back the resulting runtime
   state -> only then speak.` Every mutating capability the owner commands by voice ends
   in an `app.actions.receipt.ActionReceipt` built from the read-back, the tool result IS
   the receipt, and the model reads its `speech` verbatim. Speech claims the mutation only
   when `terminal_status` is `verified` or `already`; below that it says the thing could
   not be done. The fake-completion phrases (`gibi düşün`, `sayabiliriz`, `varsayalım`,
   `oldu varsay` ...) live in one tuple, a test sweeps every speech template for them, and
   the persona forbids them by name.
2. **One router, three classes.** `app.voice.intents.resolve_intent` is the only Turkish
   command interpreter. Every `ResolvedIntent` now carries `klass` (`query` | `action` |
   `control`) and, for an action, the canonical `capability`. `EYE_ENABLE` (gözünü aç,
   kamerayı aç, beni izle, beni tekrar izle, Active Eye'ı aç) is built on the same word
   forms as `EYE_DISABLE` and checked after it, so "beni izleme" stays a disable and "beni
   izle" is an enable. `DEPLOY` (canlıya al, yayına al) is an imperative and therefore an
   action; "alabilir misin" stays the `can_deploy` question. The classifier gained one
   query kind, `eye_state` (kamera açık mı, göz açık mı), and no third table exists.
3. **Current state comes from the live runtime, never the ledger.** `state.now` composes
   `cloud_core.health`, `voice.session`, `eye.enabled`, `eye.last_observation_age_s`,
   `owner.presence`, `devices.online`, `release.shadow_ready` and `tasks.running` by
   calling the World Model's own collectors and shaping the result: each fact carries
   source, observed_at, age, confidence and stale; what cannot be established is an
   uncertainty with a reason. `activity.explain` delegates `world_state` / `eye_state` to
   the same composer, so the answer is identical whichever tool the model picked, and
   attaches no briefing artifact or narration session for it. Speech is result-first,
   one to three sentences, stale said as stale.
4. **Durable eye writes are idempotent and a real disable invalidates camera evidence.**
   `_set_eye_state` writes, publishes and returns True only when the flag actually
   changes. On a real disable the presence service drops camera observations from the
   fusion window, sets the assertion to UNKNOWN with reason `eye_disabled`, publishes the
   degraded state once and resets the heartbeat; the World Model reports the presence
   uncertainty as `eye_disabled`. An UNKNOWN assertion is an uncertainty, not a fact.
   Enable sets the durable flag only when the client reports its camera `ACTIVE`, and
   never asserts presence.
5. **Authority by voice is a refused receipt.** `release.promote` always returns
   `execution_status: refused`, `error_class: owner_authorization_required`, the fixed
   sentence, and an `action.receipt` row in the deployment subsystem - the refusal is
   evidence that the owner asked and the system declined.
6. **The ledger vocabulary grew, not the rules.** `action.receipt` and
   `voice.state_answered` are event types; `verified`, `already` and `unverified` are
   statuses, because a receipt row's `status` IS its terminal status and "did the camera
   really close?" must be answerable from the status column. ~~The safety net in
   `record_client_events` stays and records `eye_safety` in the session context so a
   following `eye.disable` call for the same command is `verified`, not `already`.~~
   Reversed the same day; see the amendment below.

Amendment (2026-09-06, later the same day; owner session `3eb6fee7`, 13:57Z):

7. **Exactly one mutation path per capability: the tool. The utterance safety net is
   removed.** The production record showed every eye receipt as `capability_missing` /
   `failed` (the client relayed without `observed_after` - a client bug fixed there) while
   the camera physically closed at 13:57:33, written by the utterance net in
   `record_client_events` 7 ms after the tool call with reason `voice:gözünü kapat`. Two
   writers, one receipt, and the receipt was the wrong one: the record could not connect
   the failed action to the closed camera. An utterance resolved to `EYE_DISABLE` /
   `EYE_ENABLE` is now resolved and audited (intent, klass, capability) and mutates
   nothing; `eye_safety` and the handler's same-turn window are gone; the contract's §5.3
   says why. Privacy rests on the persona making the model call the tool always, on the
   disable handler writing the flag regardless of the client's report, and on the owner's
   button - never on a write nobody narrates.
8. **The receipt says which session and when it looked.** `ActionReceipt` carries
   `session_id`, `observed_at` (the read-back moment) and the client's bounded
   `action_trace`; the ledger `detail_json` carries them too, and `session_activity`
   exposes `session_id`, the receipt's `observed_after` and `action_trace` per tool call.
   `GET /v1/state/now?scope=` (owner-gated) returns the `state.now` composer's answer
   over HTTP, so the harness can hold the runtime's view, the browser's report and the
   receipt side by side.
9. **Truthful in both directions.** The client names its failure (`device_not_found`,
   `device_busy`, `get_user_media_failed`, `stream_created_but_track_ended`,
   `perception_start_failed`, `state_transition_failed`, plus the four already known) and
   each has its own sentence. When the browser reports the camera physically in the
   requested state but the Cloud Core's record is not confirmed (write raised, read-back
   mismatch), the receipt is `unverified` and the sentence is "Kamera kapandı/açıldı ancak
   işlem kaydını doğrulayamadım." - never "kapatamadım/açamadım", which on 2026-09-06 was
   said of a camera that was off. An enable relayed as `ACTIVE` with the media track
   `ended` is `failed` and never sets the flag.

Consequences: every future mutating capability (display-off, mail, files, media, a
deployment the owner authorises) inherits the receipt, the four terminal statuses, the
ledger row and the "speak only from the read-back" rule; none is retrofitted now. The
web half owns the local execution (`EyeStore`, `observed_after`), and a client without an
eye capability is reported as `capability_missing` - the server never claims a camera it
cannot see. The engine's `invalidate_source` is general: any source the owner forbids can
be withdrawn from the fusion window the same way. Not built: a server-side enable safety
net (a camera cannot be opened from the cloud) and a health probe per tool call (the
composer probes its own database and accepts a caller's health results).


## ADR-0064 — A milestone closes from the durable record, and a harness FAIL is attributed before it is believed (2026-09-06)

Status: Accepted

Context: M18 was qualified by the owner six times on one day. Every product capability the
milestone claims was eventually exercised for real and left its mark in the Cloud Core's own
record — sessions, tool calls, action receipts, ledger rows, routine firings — while the
monolithic qualification harness printed `FAIL` after `FAIL`. Each of those failures had a
cause that was the harness's own: a one-element array unrolled by a parenthesised call; a
callback bound with `GetNewClosure` that could not see a dot-sourced function; a readiness
filter that excluded a session created nine seconds after the harness started, while `/core`
was still compiling; a presence watcher that began after the transition it was waiting for
had already been written; a hidden-mutation check that read the receipt's timestamps off the
wrong level and built no windows. In the final run the owner ended a 600-second wait by hand
while `voice.single_session` — one line later, from the baseline alone — was already saying
the session existed. The product had worked. The proof was in the record. The harness was in
the way.

Decisions:

1. **The record is the acceptance.** `scripts/core/reconcile-m18.ps1` evaluates every Stage 12
   row from the durable record of a window — web sessions created inside it, their tool calls
   (query kind and subsystem, terminal receipts, what the browser observed), ledger rows (eye
   rows with the action id that wrote them, presence transitions with confidence and sources,
   routine dispatch results, action receipts), and the privacy shape of every presence row —
   and writes its verdicts beside the evidence (`docs/evidence/`). M18 closed on that, with the
   two owner runs of 2026-09-06 as its evidence. No monolithic run was repeated for ceremony.
2. **A FAIL is attributed before anyone is asked to repeat anything.** A harness verdict that
   contradicts the record is a harness defect until shown otherwise; the record is read first
   (a scratch probe with the DPAPI credential, never an assumption about the code), and the
   defect is named as a qualification-selection defect where the capability is independently
   proven. The owner's time is the scarcest resource this project has.
3. **Correlation is by identity, never by an instant or a tool name.** The Core's session is the
   one the Core itself names on the UI-state bus, or a session created since the run's
   baseline; it is recognised by having gone through the router at all, not by having called
   `activity.explain`. Every receipt and every eye row carries the action id and the session
   id, so evidence is joined by identity and time windows are the fallback.
4. **Qualification is progressive and bounded.** Per-step windows sized by the thing being
   waited for (a connection: seconds; a router call: a minute; a presence transition: the
   engine's own sustain and memory) replace one global wait. A step that is not reached is
   named with what was seen, without invalidating capabilities observed by other steps.
   `Core Voice connected: <id>` is printed the moment it is true.
5. **What the record cannot hold is asked for alone.** The only capability the final run's
   record did not contain — the owner leaving the room and returning — was asked for in a
   presence-only run of six minutes, and passed.
6. **Two rows stay open by name, not by omission.** 12.13 (an owner-selected media action plays
   the named item) was never exercised for real and gets its own short owner run when the
   owner wants it; 12.19 (display-off) is deliberately separate and gated twice. Neither is
   claimed by the closure.

Consequences: M18 is `PROVEN_REAL` on 18 of 20 rows with the two exceptions recorded; the
harnesses that remain (`owner-m18-eye.ps1`, `owner-m18-presence.ps1`, `owner-m18.ps1`)
share the library's identity correlation and bounded waits, and `harness-symbols.tests.ps1`
guards every harness against the closure and undeclared-symbol classes structurally. The
next milestone's qualification starts from the record, not from a script that must run to
completion in one sitting. The same-day amendment to ADR-0063 stands: action contract v3
(action id and session id on the durable eye row) is what production runs.


## ADR-0065 — The Core's visual language: layered, bounded, measured (2026-09-07)

Status: Accepted

Context: M18 closed PROVEN_REAL with a Core that was truthful and plain — an icosahedron, a
wireframe, a lattice, a few rings. The owner asked for the thing the milestone had deferred:
a visual language of its own, spatial and holographic, in which listening visibly pulls
energy inward, thinking expands the topology, research grows a constellation, memory
converges, the assistant's speech drives the light, and the Minimal mode gives the Core the
viewport. The temptation in that brief is the one ADR-0052 and ADR-0056 exist to refuse: to
make it *look alive*. A structure that breathes, turns and sparkles on its own would teach
the owner, within a day, that the Core's motion means nothing. So the design problem was
stated the other way round — what is the richest structure that can move **only** on
evidence — and that constraint is what makes the result original rather than a copy of any
film interface. Nothing here relaxes the rule that the Core draws only what was published
or measured; the frozen Core/Voice/Eye architecture, the voice store and the eye reconcile
seam are untouched.

Decisions:

1. **The structure is layered; the layers are the vocabulary.** A translucent nucleus;
   three concentric internal rings on tilted planes (the topology layers); the connection
   paths across the interior; two translucent structural shells that stand off the nucleus;
   and, beyond them, only things a subsystem said exist — the evidence constellation, the
   parked capability nodes, the eye's aperture, the lab's construction layers, the release
   orbit. Two bounded particle populations move through it: one pulled inward while the
   system listens or recalls, one travelling the paths while it thinks or works. Depth is
   the layering itself plus a slight camera lean towards the pointer, which is a way of
   seeing the layers and not a claim about the system. `docs/M18_1_CORE_VISUAL_LANGUAGE.md`
   holds the shape table and the per-state table.
2. **Every new visual channel is a number on `VisualIntent`, derived in `visual.ts` from a
   published field or a real measurement, and nowhere else.** `energy` is the publisher's
   `intensity`, or the gate's microphone level, or the playback RMS, and is 0 when nothing
   was declared or measured. `glow` is a per-state base plus half of `energy`. `shellSpread`,
   `ringSpin` and `flowRate` are per-state constants that `energy` may raise. `ownerVoice` is
   exactly the local gate's `micLevel` while this tab's own session is listening — the
   OWNER_SPEAKING the owner asked for is "listening with that number above zero", and a bus
   `agent.listening` never claims it, because its intensity is declared elsewhere, not
   measured here. `eyeActive` is read from the eye's own claim through `eyeView`, so the
   aperture and the ambient band cannot disagree. `visual.test.ts` now asserts, for every
   core state, that it has a channel profile no other state shares, and that with
   `intensity: null` and no measurement the rhythm channels (`pulse`, `ownerVoice`,
   `energy`) are all zero; and that every silence kind and every room state leaves every
   motion channel at zero.
3. **Research without a count draws a fixed motif and says so.** The owner's brief asked
   for a constellation whenever research runs. The publisher sends counts at some stages and
   none at others, and drawing a plausible number at the latter would be the estimate rule 2
   of `visual.ts` forbids. So `constellationNodes` is the published count when there is one
   and the constant `CONSTELLATION_MOTIF` (5) when there is none; the 2D markup marks the
   motif `data-constellation="motif"` with hollow nodes and no `core-sources` group, the
   readout appends "Çizilen takımyıldız sabit bir temsildir, sayım değildir." to the
   existing "Kaynak sayısı bildirilmedi.", and the motif is byte-for-byte the same on every
   render. Its motion, `constellationDrift`, is published progress or the constant
   `CONSTELLATION_REST`; never a random or clock-driven figure. When both `candidates` and
   `kept` were published, the difference is drawn as a faint outer field — the set the
   evidence was kept from — and only then.
4. **SHADOW_READY parks capability nodes; the count is the lab's or it is one.**
   `app/evolution/service.py` publishes one event per candidate with no count, so the
   honest figure is one — the candidate the event is about — with `capabilityNodesCounted`
   false and the readout saying the lab did not count. A publisher that sends `ready` or
   `candidates` is read verbatim, capped at `MAX_CAPABILITY_NODES` (8) for the GPU while the
   readout keeps the true number. The nodes are parked and never orbit; the first sits under
   the completed satellite's halo, which is the same object as before.
5. **The frame is a pure reducer, and allocation-free by construction.** `stepScene(state,
   intent, dt, pointer)` in `app/lib/uistate/scene.ts` approaches every smoothed value
   towards its channel (exponential in `dt`) and accumulates rotations and particle phases
   (linear in `dt`); `CoreScene` only copies its numbers onto three.js objects. The truth is
   the intent; the frame draws it. `tests/uistate/scene.test.ts` runs the reducer in Node
   with no three.js: a zero intent stays still through thirty seconds of frames; sixty small
   steps and twenty large ones reach the same picture; the state object and its phase buffer
   are the same objects after five hundred steps; and a structural test reads the source of
   both the reducer and the scene's frame body and refuses `new`, literals and array
   methods inside them. That last test is what "no per-frame allocations" means here — a
   property of the text, not a profiler's opinion.
6. **A hidden tab does no work; reduced motion draws one settled frame.** `CoreCanvas` now
   takes `hidden` and `still` apart. Hidden puts the canvas in `demand` mode, the frame loop
   returns before any arithmetic, and no effect invalidates. Reduced motion settles the
   structure on the new intent in one long reducer step and draws that frame once per
   intent change — the shape, the counts and the glow are shown; nothing moves.
7. **Tiers budget the structure and a pure function states the ceiling.** `TIER_BUDGETS`
   gains `rings`, `shells`, `maxParticles` and `parallax` (high 3/2/160/yes, balanced
   2/1/80/yes, low 1/0/0/no). `sceneBudgetFor(tier)` derives the most the scene can mount —
   high 26 drawables, 296 instances; balanced 24, 152; low 18, 32 — and `quality.test.ts`
   pins those figures, so a change to the scene's composition is a change to a table. `low`
   keeps one ring so the structure keeps its identity and drops shells, particles, paths,
   parallax and the glow shell whole. Particles are instanced, two meshes, culling off.
8. **The 2D fallback carries the same identity.** `CoreFallback2D` draws the rings, shells,
   aperture, constellation, field and capability nodes in static SVG at the same proportions,
   from the same constants in `scene.ts`; the ring rotation is a CSS animation whose duration
   is `7 / ringSpin` seconds and which is absent at zero spin, and it stops under
   `prefers-reduced-motion` like the breath. It remains the view the render tests assert
   on, under `react-dom/server`, with no browser.
9. **Minimal mode gives the Core the viewport; the Cockpit prints the numbers.** On `/core`
   the stage is sized by the window's shorter side, the readout sits over its lower edge, and
   the voice cell, the room and the eye control recede into one row that comes forward on
   hover or focus — except the camera cell, which never fades, because a faded privacy
   assurance is not one. `/core/cockpit` keeps its panels and adds `ChannelReadout`: every
   channel printed as it is, with the counted/uncounted state of the constellation, the
   field and the capability nodes in words.

Consequences: `pnpm --filter @pagentos/web test` grows from 588 to 660 tests, all Node +
`react-dom/server`; no browser, no Playwright; `next build` compiles in about 12 s and the
whole gate in under 20 s. What is deliberately not drawn: any idle activity beyond the
reported-idle breath and a minute-scale ring drift; any synthesised speech rhythm (the pulse
and the glow while speaking are the playback RMS); any estimated source count; any random
position, phase or tilt; any motion for `untold`, `connecting`, `unauthorized`,
`last_known`, `unreachable` or for the room's states on the core body; any parallax on
`low` or as a claim anywhere; any strobe. The first thing the owner should notice on a real
run is that the Core is still when the system is quiet and that this reads as calm rather
than as broken — the whole reason the structure was allowed to become this rich.

## ADR-0066 — Speaking is a lifecycle; energy is a measurement (2026-09-07)

Status: Accepted

Context: M18.2 DEFECT 1, owner-observed on a real realtime session over WebRTC: the Core
left SPEAKING in the middle of a sentence and drew `listening` while the assistant was
still audibly talking. The mechanism was in the client, not in the analyser. The
controller moved `speaking` to `listening` on the provider's `response.done`, and over
WebRTC that event marks the end of GENERATION — the media track still holds whatever was
generated but not yet played, often a sentence or more, and the provider only sends
`output_audio_buffer.stopped` when playback actually ends. The wire dialect already turned
that into `audio_stopped`; the controller only used it to clear a flag. The playback RMS,
which some suspected, never decided the state — it drove the pulse and nothing else, which
is exactly what ADR-0061 §3 intended and what this ADR keeps.

Decisions:

1. **`speaking` is the semantic speech lifecycle of one response, tracked per response.**
   `VoiceSessionController` keeps a `SpeechLifecycle` — `responseId` (from the events),
   `phase` (`idle` → `generating` at `response_started` → `audible` at the first observed
   audio → `draining` when generation ends while audio is still playing → `done`),
   `firstAudioAt`, `generationDoneAt`, `playbackDoneAt` and the `basis` on which the end was
   judged — and exposes it on the snapshot as `speech`. The `state` vocabulary is
   unchanged: `speaking` stays `speaking` through `draining`; the voice cell and `/voice`
   word the phase ("üretim bitti, kalan ses çalıyor") so the owner can see the difference
   between a generation that is over and speech that is over.
2. **`response_done` ends generation, never speech by itself.** If the provider's buffer is
   still playing (`responseAudible`), the state stays `speaking` in phase `draining` and
   two bounds are armed; it leaves `speaking` only on (a) the provider's `audio_stopped`
   for THAT response — an `audio_stopped` carrying another response's id is stale and is
   ignored; (b) analyser silence for `PLAYBACK_RELEASE_MS` (400 ms) counted from the later
   of generation end and the last measured output energy; (c) `PLAYBACK_DRAIN_MAX_MS`
   (8 s) after generation end, whatever the analyser says; or (d) an interruption. The
   analyser is polled every `PLAYBACK_POLL_MS` (50 ms) while draining, energy above
   `PLAYBACK_ENERGY_LEVEL` (0.02 of full scale) counts as audio, and a path this client
   silenced itself (an early mute, a potential barge-in) is not silence — the lane's own
   verdict or the cap decides. A `tool_running` continuation keeps its behaviour: the
   state goes to `tool_running` at `response_done` and the drain still closes truthfully
   underneath it. A provider `audio_stopped` that precedes `response_done` is remembered
   and the response ends at `response_done`, at the stop's time.
3. **Interruption ends it now.** A barge-in (either lane), an explicit "dur", a
   `response_cancelled`, a new `response_started` over a draining one, a lost leg and a
   disconnect all close the lifecycle immediately with `basis` `interrupted` (or
   `superseded` for the takeover, whose real end is unknown from here); no timer and no
   later provider event may end a response twice.
4. **Nothing invents speech after playback finished, and no end is lost.** A response that
   never became audible ends at `response_done` with no audio event at all. Every response
   that had audio ends in exactly one new client timing event, `audio_done` (`t_ms`, `turn`,
   payload `response_id`, `basis: provider | silence | cap | interrupted | superseded`,
   plus `drain_ms` and `audible_ms` as numbers), reported through the same fire-and-forget
   reporter as every other timing kind. The server is gaining the kind in parallel; until
   then `service.py` skips unknown kinds inside an accepted batch, and the client's
   contract list carries `audio_done` so the reporter cannot throw on it.
5. **The overlay is unchanged in kind and pinned in test.** `applyVoiceOverlay` draws
   `speaking` with `outputLevel` 0 as a calm speaking Core — breath and scale kept, pulse
   0 — and `voice-overlay.test.ts` now asserts that a pause differs from `interrupted` and
   that resuming changes only the amplitude channels.

Consequences: `pnpm --filter @pagentos/web test` grows from 660 to 678 tests
(`tests/voice/speech-lifecycle.test.ts` covers (a)–(h) of the defect brief with
`FakeTransport` + `FakePlayback`, no browser); four existing tests that asserted
`listening` at `response_done` over draining audio now assert `speaking`/`draining` and end
the response through the provider's stop. `docs/M18_CORE_RENDERER.md` §3 and ADR-0061 §3
carry the rule. Owner re-run pending: the expected observation is that the Core stays in
`speaking` — calmer through pauses — until the last word, and drops on "dur" as before.

## ADR-0067 — Research speaks its findings; the pipeline speaks only when asked (2026-09-07)

Status: Accepted

Context: M18.2 DEFECT 2. After a research run, the assistant's spoken answer narrated
pipeline diagnostics ("242 aday keşfedildi, 28 sayfa elendi, 11 interstitial, 6 tekrar")
instead of what was found. Tracing the actual completion path (`research.start` ->
discovery -> fetch -> evidence -> rank -> synthesis -> durable report row -> what reaches
the Voice session -> narration) rather than guessing at it found THREE separate facts, not
one:

1. **`research.start` (`app.voice.realtime_sessions.tools.research_start`) never
   triggers the real M13 pipeline.** It fabricates a local `plan` dict (a random
   `plan_id`, the topic/scope echoed back) inside the session's own `context_json` and
   returns `{"status": "running", ...}`; the Temporal `BrowserResearchWorkflow` that
   actually discovers, fetches, ranks and synthesizes a report
   (`app.research.browser_workflow`/`browser_activities`) is started only through the
   separate REST route `POST /v1/research` (`app.research.routes.create_research`),
   which nothing in the voice tool calls. A voice-initiated "araştır" and the durable
   report a later "sonuçları anlat" finds are, today, two unrelated things linked only by
   the owner's own memory of having asked.
2. **Nothing ever completes the `research.start` tool call.** `service.complete_tool_call`
   exists and is reachable only through the owner-authenticated HTTP route
   `POST .../tool-calls/{id}/complete` — by design, per its own docstring: a worker/
   pipeline "must arrive through its own, non-owner credential — never through this
   path." No caller anywhere (Temporal activity, sweeper, announcer) exists that reaches
   that path for a research run, so a `research.start` call sits `TOOL_STATUS_RUNNING`
   in `realtime_tool_calls` forever; the provider is never told the tool call so much as
   finished, so the model can never be truthfully "answering the research.start call" —
   there is no sideband `tool_completed`, ever, for this tool, in production today.
3. **The actual narrated defect: `app.explain.engine._research_executive` built its
   outcome sentence from the ledger event's own counts.** When the owner later asks
   anything that resolves to "last thing that happened" (`son yaptıklarını anlat`, and
   also `araştırma detaylandır` / `teknik anlat`, all of which route through the SAME
   `research.completed`-latest branch of `explain()`), the EXECUTIVE level always read
   `ev.detail` — `{**ReportStats, findings, sources}`, written verbatim by
   `app.ledger.service.build_research_completed_event` — and built: `"{sources} farklı
   kaynaktan {findings} sonuç üretti ve {rejected} uygun olmayan sayfayı eledi."` This is
   the sentence the owner actually heard. It is not the model improvising over the
   ledger's raw rows (`activity.explain`'s persona instruction to read `speech` verbatim
   was honoured exactly) — it is the ENGINE handing the model diagnostics as if they were
   the answer. Item 3 is the one this milestone item fixes; items 1 and 2 are real gaps
   this ADR records and partially closes (the completion mechanism, see decision 3) but
   does not fully wire end to end — see Consequences.

Decisions:

1. **`app.research.result` is the one place a research OUTCOME and a research
   DIAGNOSTIC are different types.** `ResearchResult` (`topic`, `executive_summary`,
   `findings: [{finding, why_it_matters, sources}]`, `why_it_matters`, `sources:
   [{title, url_host, ref}]`) is built by `from_report_json` ONLY from the validated,
   provenance-gated report (`ResearchReportRow.report_json`) — never from a count, never
   fabricated. `ResearchDiagnostics` (`discovered_count`, `fetched_count`,
   `rejected_pages`, `rejected_by_reason`, `refused_pages`, `quarantined_pages`,
   `dedup_stats`, `fetch_failed_count`, `synthesis_provider`, `provider_errors`,
   `timings`) is built by `from_report_json` from the SAME report's `stats` block —
   `ReportStats` gained one field, `quarantined` (every contract-violation-quarantined
   item at either the evidence or the synthesis stage), so a diagnostic question can say
   how much of the pipeline's own output was self-rejected. `spoken_result(result)`
   deterministically renders the owner-facing Turkish narration — a conclusion, up to
   three findings ("Birincisi, İDDİA. Bu önemli çünkü AÇIKLAMA."), then an offer to say
   more — and is asserted, directly, to never contain "elendi", "eledi", "interstitial",
   "tekrar", "dedup", "aday" or a digit immediately followed by "sayfa". A run that failed
   its quality gate (`InsufficientValidFindings` / `InsufficientValidEvidence`) renders
   through `ResearchResult.insufficient_evidence(reason=...)` instead: honest and
   concise, never a recital of the failure log.
2. **The completion mechanism this milestone item required is built, even though nothing
   in production triggers it yet (decision above, gap 1).** `service.
   complete_tool_call_system` is the non-owner sibling `complete_tool_call`'s own
   docstring calls for: same durable outcome, same `tool_completed` sideband shape, no
   owner-session identity, and it tolerates a session that has since closed (a research
   run can easily outlive it). `app.voice.realtime_sessions.research_announcer.
   ResearchToolCallAnnouncer` is the trigger, in the SAME cross-process shape as
   `app.mobile.announcer.ArtifactReadyAnnouncer` and for the identical reason stated in
   that module's own docstring: the Temporal worker holds no sideband/device-WebSocket
   registrations (it is a different process from the API's `BrokerRuntime`), so it
   cannot deliver anything itself — it can only make `ResearchRunRow`/`ResearchReportRow`
   terminal. This sweeper, running in the API process where the live connections
   actually are, watches for a RUNNING `research.start` call whose own `result_json`
   names a `task_id`, and once that run is terminal, calls `complete_tool_call_system`
   with `app.research.result.build_tool_terminal_payload` (or
   `build_insufficient_terminal_payload`) — the owner-facing schema `{spoken_result,
   executive_summary, findings, source_summary, diagnostics}`. It is wired into
   `app.main.create_app`'s lifespan exactly like `mobile.announcer`. What is NOT done:
   nothing in `research_start()` sets that `task_id` yet, because doing so honestly
   requires closing gap 1 above (starting the real Temporal workflow from a synchronous
   tool handler, including device selection) — a separate, larger change flagged for a
   follow-up session rather than attempted half-integrated here. The announcer and
   `complete_tool_call_system` are fully exercised by their own tests against a
   fabricated linkage, independent of whether anything sets it yet.
3. **The EXECUTIVE level consumes `ResearchResult`; counts move to TECHNICAL and to
   explicit questions.** `_research_executive` now builds `ResearchResult.
   from_report_json(report)` and speaks `spoken_result(...)`; when a qualification
   sentence already addressed the owner, the "Efendim, " address is trimmed from the
   result sentence rather than repeated. `_research_detailed`'s "Elenen sayfalar" bullet
   moved to `_research_technical`, which now also reports `quarantined_pages` /
   `refused_pages` when either is nonzero — this is what "Ayrıntı" already got closer to
   right; it should never also have carried a rejection tally. Two query kinds are new,
   `research_problems` ("araştırma sırasında ne sorun oldu?") and `rejected_pages`
   ("hangi sayfalar elendi?"), both forced to `LEVEL_TECHNICAL` and routed to
   `subsystem="research"` — ahead of the generic "sorun"/"hangi" patterns in
   `app.explain.classify`, so a research diagnostic question is never misread as a named
   module's problem. A `research.failed` event, reached as "the last thing that
   happened", now answers through `ResearchResult.insufficient_evidence` instead of the
   generic activity fallback.
4. **UI state during research was verified, not changed.** `app.research.
   browser_activities` already publishes `UiState.RESEARCHING` through discovery, fetch
   and rank with real counts (`test_research_uistate.py`), and synthesis deliberately
   stays `RESEARCHING` rather than switching to `THINKING` — the code's own comment: "it
   is one job, and the evidence the Core is drawing is the evidence being synthesised."
   No stage publishes nothing and no stage publishes an invented number; nothing needed
   fixing here.
5. **`audio_done` joins `TIMING_EVENT_KINDS`.** The web client's playback-completion mark
   (`response_id`, `basis`) is now stored exactly like every other client timing event
   (`app.voice.realtime_bench`); no metric pair is defined for it yet.

Consequences: `services/api` gains `app/research/result.py` and
`app/voice/realtime_sessions/research_announcer.py`; `ReportStats` gains `quarantined`;
`_research_executive`/`_research_detailed`/`_research_technical` and `classify()` change
shape; `session_activity`'s `speech_head` now falls back to a result's `spoken_result`
when it carries no `speech` key. `docs/DECISIONS.md` (this entry) records gaps 1 and 2 from
the Context above as open: `research.start` still does not start a real research run, and
the announcer this ADR builds has nothing to announce until it does. Closing that gap —
a synchronous tool handler starting `BrowserResearchWorkflow` and recording its `task_id`
on the call's own `result_json` — is the natural next M18.2 item, not attempted here to
keep this change bounded and fully tested end to end on the half that could be.

### Amendment (2026-09-07): `research.start` starts the real run; gaps 1 and 2 close

Gap 1 ("a voice-initiated 'araştır' and the durable report a later 'sonuçları anlat'
finds are, today, two unrelated things") and gap 2 ("nothing ever completes the
`research.start` tool call") are closed together, in `services/api` only.

1. **One start path, two callers.** `app.research.routes.create_research`'s body is
   extracted into `app.research.service`: `start_browser_research(db, broker, *, input,
   target_device, recency_days, max_sources, trace_id, source, session_id,
   tool_call_id) -> StartedResearch` is the synchronous half (create the task, select a
   `browser.chrome`-capable device, record the PLANNED/FAILED run row) — the same code
   both the REST route and the voice tool run, from a plain `Session` with no
   `asyncio` involved, so a synchronous tool handler running inside its own DB
   transaction can call it directly. `source` ("rest" | "voice") and, for voice,
   `session_id`/`tool_call_id` are recorded on the run row's own PLANNED/FAILED event
   (`app.research.runs_service.update_run`'s `event=`) — provenance alongside, not
   instead of, the `task_id` linkage `find_running_tool_call_by_task_id` already used.
   `start_browser_research_workflow(client, artifacts, *, task_id, workflow_id, ...)`
   is the asynchronous half (`Client.start_workflow` + persisting the workflow id);
   the REST route awaits it inline exactly as before (its own tests, including the
   `Client.connect` patch target, are unchanged) — the extraction changed nothing
   about `POST /v1/research`'s behaviour.
2. **`research_start` (the voice tool handler) runs the synchronous half for real,
   inside `handle_tool_call`'s own transaction, then hands the asynchronous half to a
   follow-up.** A sync handler cannot `await`, so `ToolContext` gains `followups: list[
   Callable[[], Awaitable[None]]]` (`ctx.add_followup(...)`); `handle_tool_call` takes
   an optional `followups` list from its caller and seeds the `ToolContext` with it —
   the SAME list object, so the realtime-session ROUTE (`POST .../tool-calls`) sees
   whatever the handler appended once `asyncio.to_thread` returns, and awaits each one
   AFTER the tool-call transaction has committed and the HTTP response body is built.
   `research_start` derives `recency_days` from the topic through
   `app.research.dates.parse_recency_window` (falling back to
   `app.research.plan.DEFAULT_RECENCY_DAYS`) exactly as the REST plan stage would —
   "son üç gündeki ..." records `recency_days: 3` on the tool's own local `plan` dict,
   which now also carries the REAL `task_id`, `workflow_id` and selected `device`
   (`plan_id` is `str(task_id)`, so `row.plan_id` — already set from `plan["plan_id"]`
   by `handle_tool_call` for any long-running tool — is the task id, not a throwaway
   UUID). No capable device is `start_browser_research` returning `StartedResearch(
   error=...)`, which the handler turns into an immediate `VoiceError(
   CAPABILITY_MISSING, "Araştırma için tarayıcı yeteneği olan bir cihaz yok.")` — a
   FAILED tool call from the first round trip, never a fabricated "running". The
   follow-up connects to Temporal and calls `start_browser_research_workflow`; on any
   exception it completes the call itself, via `complete_tool_call_system`, as FAILED
   with `error_class="research_workflow_start_failed"` and the Turkish sentence
   "Araştırmayı başlatamadım; arka plan servisine ulaşamadım." — never leaves a call
   RUNNING that will never receive a `task_id` a durable run can complete.
3. **Failure speech rides the same `result_json` shape `session_activity` already
   reads.** A raised `VoiceError` may now carry `details={"speech": ...}`; both
   `handle_tool_call`'s `except VoiceError` branch and `complete_tool_call_system`'s
   `error=` branch copy that into the call's own `result_json["speech"]` alongside
   `message` — `exc.message` itself is not read as speech (most handlers' messages are
   internal/English), so this is opt-in per raise, not a blanket promotion. Because
   `session_activity` already reads `_result_field(result, "speech")` regardless of the
   call's status, a FAILED research.start's `speech_head` shows the truthful sentence
   from durable rows alone, the same way a SUCCEEDED one's does.
4. **`ToolContext.live` gains `artifacts_runtime` and `voice_runtime`.**
   `RealtimeVoiceRuntime` takes an optional `artifacts` (the same `ArtifactRuntime`
   `app.state.artifacts` already is) and exposes it, plus itself (for `.session()` and
   `.sideband` after the follow-up's own transaction), through `live_sources()`. Both
   default to `None`/unset, so every existing `RealtimeVoiceRuntime(...)` construction
   in a test that does not touch `research.start` is unaffected; a session with no
   `artifacts_runtime` simply cannot serve `research.start`
   (`DEPENDENCY_UNAVAILABLE`, not a crash).
5. **`plan.redirect` on a research plan is now an honest, unconditional refusal.**
   `app.research.browser_workflow.BrowserResearchWorkflow` defines no `@workflow.signal`
   or `@workflow.query` — checked, not assumed — so there is no mechanism to change a
   running run's topic or scope. Claiming a redirect that never reached the workflow
   would be exactly the false-completion class this action contract exists to refuse
   (`app.actions.receipt.FAKE_COMPLETION_PHRASES`); `plan_redirect` now returns the
   plan UNCHANGED with `{"status": "refused", "speech": "Bu araştırma çalışırken
   kapsamı değiştiremiyorum; bitince yeni bir araştırma başlatabilirim."}` and pushes
   no `plan_changed` frame, for any plan whose `kind` is `"research"` (today, every
   plan `research.start` sets). The old cancel-and-replan behaviour is kept, dead code
   for now, for a future plan kind whose backing pipeline actually supports it.
6. **`ACTION_CONTRACT_VERSION` becomes 4.** `research.start`'s terminal schema is new
   (`spoken_result`/`executive_summary`/`findings`/`source_summary`/`diagnostics` on
   success, `message`/`details`/`speech` on failure) and its failure/redirect honesty
   rules are new; the health manifest's `action_contract_version` is how an owner
   qualification tells a deployment that actually starts research by voice from one
   that still only announces a linkage nothing sets.

Consequences: `services/api` gains `app/research/service.py`
(`start_browser_research`, `start_browser_research_workflow`, `connect_temporal`,
`StartedResearch`); `app.research.routes.create_research` is behaviourally identical,
now composed from that module; `ToolContext` gains `followups`; `research_start` and
`plan_redirect` in `app.voice.realtime_sessions.tools` are rewritten; `handle_tool_call`
takes an optional `followups` list and preserves a raised `VoiceError`'s
`details["speech"]`; `complete_tool_call_system` preserves an `error["speech"]` the
same way; `RealtimeVoiceRuntime` takes an optional `artifacts`. Every existing
`test_voice_realtime_sessions.py` / `test_voice_research_completion.py` fixture that
exercises `research.start` now wires an `ArtifactRuntime` + a `browser.chrome`-capable
device (mirroring `test_research_routes.py`'s own fixture), and the REST route's own
tests (`test_research_routes.py`) are unchanged. What this amendment does NOT build: a
UI or voice affordance to CHANGE the recency/device/max_sources of a research already
running (the honest refusal above is the whole answer for now), and no attempt to make
the M13 workflow itself signal-aware — that stays a real gap, named rather than
half-closed.

## ADR-0068 — Research fast path: modes, waves, and challenges left alone (2026-09-07)

Status: Accepted

Context: the owner's first end-to-end spoken research (2026-09-06 18:30Z, session
a4455670, "Son üç gündeki yapay zekâ ajan gelişmelerini araştır") worked — findings were
discovered, fetched, ranked, synthesized and spoken — but it discovered 254 candidates
and took several minutes, much of it spent on CAPTCHA/challenge pages. Nothing in the
M13 pipeline (`app.research.browser_workflow`/`browser_activities`) had ever declared how
much research a *conversational* request should cost: `BrowserResearchWorkflow` fetched
everything `fetch_targets_activity` handed it up to `max_sources`, ranked once, and only
topped up (`MAX_TOPUP_ROUNDS`) when short of `TARGET_REPORT_FINDINGS` — a single, fixed
policy regardless of whether the owner asked a quick conversational question or an
explicit "araştır kapsamlı". Discovery itself issued every expanded query
(`app.research.plan.expand_queries`, typically 8-12 phrases) against every source class,
which is where 254 candidates came from. And a CAPTCHA/interstitial page was only ever
recognised once, at ranking (`app.research.eligibility.classify_page_validity`), long
after the budget to fetch it — and every other page from the same blocked site — had
already been spent.

Decisions:

1. **Three named modes, as data** (`app.research.policy.ResearchPolicy`,
   `POLICIES = {quick, standard, deep}`). Every number in the owner's rule 2 is a field on
   one dataclass, not a scattered constant:

   | field | QUICK (default) | STANDARD | DEEP (explicit only) |
   |---|---|---|---|
   | discovery_queries_max (per source class) | 2 | 4 | 8 |
   | candidate_urls_max (considered before any fetch) | 25 | 40 | 60 |
   | max_sources (pages actually fetched) | 10 | 16 | 24 |
   | concurrent_fetches | 4 | 4 | 4 |
   | per_page_timeout_s | 10 | 12 | 15 |
   | per_domain_max_pages | 2 | 3 | 4 |
   | final_findings_max | 5 | 7 | 7 |
   | target_findings (early-stop threshold) | 4 | 5 | 6 |
   | min_distinct_publishers (soft goal, diagnostics only) | 3 | 3 | 4 |
   | wave_size | 4 | 4 | 6 |
   | max_waves | 3 | 4 | 4 |
   | target_budget_s | 90 | 150 | 360 |
   | hard_budget_s | 120 | 210 | 600 |

   `BrowserResearchRequest.mode` (default `"quick"`) is resolved to a `ResearchPolicy`
   ONCE, by `plan_activity`, and stored in the plan row's own JSON
   (`plan_json["policy"]`) — every later activity and the workflow's own wave loop
   re-read that SAME stored policy rather than re-resolving `mode` independently, so a
   replayed/resumed run never picks up a changed default mid-flight. `research_start`
   (the voice tool) derives the mode from the owner's own words via
   `app.research.policy.derive_mode_from_utterance` — "kapsamlı"/"derinlemesine"/"detaylı
   araştır" → DEEP, "geniş"/"karşılaştırmalı" → STANDARD, else QUICK — applied to
   `topic + " " + scope` since the tool's schema has no separate free-text "utterance"
   argument today (a real, if minor, gap: giving `research.start` its own utterance field
   would make this detection more reliable than reading it back out of whatever the model
   folded into `topic`/`scope`; left for the model-side/tool-schema work this pass does
   not touch). The REST route gets a NEW field, `research_mode` (default `"quick"`,
   pattern `quick|standard|deep`) — deliberately NOT named `mode`, because
   `CreateResearchRequest.mode` already means interactive/unattended owner-handoff
   (ADR-0050 §5a/contract §3a, tested in `test_research_routes.py`); reusing the name
   would have silently broken that existing, tested contract. `GET /v1/research/policy`
   (bumped to policy version 5) publishes `research_modes`, `research_mode_default` and
   the full `research_policies` table so a client can read a deployment's actual budgets
   rather than assuming the owner's numbers above.

2. **Wave-based fetching with early stop, replacing the old fixed-budget-then-top-up
   loop** (owner rule 6). `BrowserResearchWorkflow.run` fetches `policy.wave_size`
   candidates, ranks once (guaranteeing the existing `InsufficientValidEvidence` gate
   still runs at least once, unchanged), then loops: after every rank,
   `app.research.policy.decide_next_wave` (pure, unit-tested independent of Temporal) is
   asked whether to fetch another wave, checking in order — enough evidence
   (`evidence_count >= policy.target_findings`) → stop; `waves_used >= policy.max_waves`
   → stop; `elapsed_s >= policy.hard_budget_s` (measured via `workflow.now()`, a
   deterministic workflow clock, never wall-clock `datetime.now()`) → stop; the run's own
   `max_sources` ceiling reached → stop; otherwise fetch
   `min(policy.wave_size, remaining_budget)` more. This is never a loop that keeps
   fetching until it likes the answer — the exact discipline the old top-up loop already
   had, generalised to apply from wave 1 rather than only after the whole budget was
   already spent once. A QUICK run that hits its 120s hard budget still calls
   `synthesize_activity` with whatever evidence it has; that activity's own
   `InsufficientValidEvidence`/`InsufficientValidFindings` gates (unchanged) decide
   whether that is enough for a defensible answer or an honest
   `ResearchResult.insufficient_evidence` (ADR-0067, unchanged either way). Fetches within
   one wave run up to `policy.concurrent_fetches` at a time
   (`BrowserResearchWorkflow._fetch_all`, chunked `asyncio.gather` over
   `workflow.execute_activity` coroutines — a standard, deterministic Temporal pattern;
   one bad/slow fetch in a chunk never cancels its siblings). `fetch_targets_activity`
   trims the FULL pending-candidate pool to `policy.candidate_urls_max` on cheap,
   pre-fetch signals (`_prefetch_preference`: URL topicality + a recency hint from the
   provider) before any per-class ordering or navigation — "quick ranking before any
   expensive navigation" (owner rule 4) — and enforces `policy.per_domain_max_pages`
   across the whole run (using already-fetched evidence's own domains, not just the
   current wave), so one site can never consume a disproportionate share of the budget
   even when it was never challenged. `synthesize_activity` truncates the synthesis
   provider's own findings list to `policy.final_findings_max` AFTER the
   `MIN_REPORT_FINDINGS` floor is already satisfied, so a mode's ceiling can never be the
   reason a run fails its findings floor.

3. **Challenge policy: detect fast, never retry, cool the domain, rank it last**
   (`app.research.challenge`, owner rule 3). `fetch_activity` classifies every fetched
   page THE MOMENT it returns, with the SAME text-based classifier the quality gate
   already used at ranking (`eligibility.classify_page_validity`) plus the device's own
   `page_kind` — never a second, competing detector, and never a solve/bypass attempt.
   The page is still a *successful* fetch (ADR-0050's "website error != browser error"):
   the challenge is recorded as `challenge_reason` on the evidence row and the domain's
   own challenge count (`run.progress_json["challenge_counts"]`) is incremented right
   there. A CONFIRMED challenge is a zero-retry event by construction — it is data, never
   an exception, so Temporal's retry policy never sees it at all; `_FETCH_RETRY` itself
   drops from 4 attempts to 2 (one retry) so even a genuinely transient dispatch failure
   (timeout, dependency_unavailable) never quietly re-spends a QUICK run's tight budget.
   The SECOND challenge on one domain in a run cools it
   (`DOMAIN_COOLDOWN_THRESHOLD = 2`, `cooled_domains`): `fetch_targets_activity` skips
   every remaining candidate from a cooled domain WITHOUT dispatching any device
   command — never even attempted, so five URLs from the same blocked site cost nothing
   after the second one. A domain with exactly one challenge (not yet cooled) is not
   excluded, but `select_fetch_order`/`_prefetch_preference` sort it last within its
   source-class bucket ("a domain already challenged ranks last", owner rule 5) — still
   tried, just after everything that has not raised a flag yet. `ResearchDiagnostics`
   (never `spoken_result`) gains `challenged_pages` and `cooled_domains` counts, read from
   the run's own accumulated progress at synthesis time.

4. **Coarse, fixed Turkish labels on the UI-state bus — never the raw topic, never a
   count** (owner rule 7). Exactly four labels, one per stage that is actually slow:
   "Kaynaklar aranıyor" (discovery), "N güvenilir kaynak incelendi" (published by
   `fetch_activity` itself, incrementing per completed source — `fetch_targets_activity`
   publishes no label at all at its own point in the stage, since nothing has been
   fetched yet when it runs), "Bulgular doğrulanıyor" (ranking), "Sonuç hazırlanıyor"
   (synthesis). The previous behaviour — the raw topic text as the ranking/fetching-stage
   `label` — is replaced; `test_research_uistate.py`'s
   `test_fetch_targets_publishes_the_counts_it_actually_has` (previously asserting
   `event.label == "ai agents"`) is updated along with it, the one existing-test change
   this ADR's rule 7 required. Bus `metadata` (candidate/target/kept counts) is untouched
   — those are bounded numbers on a technical channel already, not the spoken-language
   label this rule governs.

5. **Search snippets already never become evidence — verified, not changed.**
   `app.research.discovery.DiscoveredCandidate` and `app.research.evidence.EvidenceRecord`
   have no `snippet` field at all; `discover_activity`'s "news"/"community" branch builds
   candidates from `SearchHit.url`/`.title`/`.published_hint` only, and every fetched
   page's `excerpt` comes from the device's own extraction
   (`browser.fetch_evidence`), never from a search result's snippet text. This was already
   true by construction; `test_search_snippets_never_become_persisted_candidate_data`
   pins it as a structural regression guard.

6. **Ranking already prefers primary over secondary and drops duplicate stories —
   verified, not changed.** `app.research.evidence.dedup_and_rank`'s source-class weight
   (official > academic > news > technical > community) and near-duplicate-title
   syndication were already exercised by
   `test_dedup_and_rank_orders_by_source_class_weight` and
   `test_near_duplicate_title_syndication_prefers_higher_priority_source_class`; nothing
   in this pass changes that ranking formula, only what reaches it (the candidate-pool
   trim and domain quota in decision 2).

7. **No lightweight (plain-HTTP, no-browser) fetch path added — kept as a named gap,
   not attempted.** ADR-0050 addendum item 1 recorded a specific, hard-won finding: on
   the live internet, the SAME public newsroom answered headless Chrome with 403 and
   headful Chrome with 200, and every search engine challenged headless automation but
   served headful Chrome. That is direct evidence that "ordinary article pages" are
   exactly where bot detection is most aggressive against a non-browser fetch — the
   opposite of the assumption that would make a lightweight path safe. Adding one now
   would mean a second fetch/evidence path with its own provenance, destination-policy
   and injection-boundary review (ADR-0050 §6), decided under conversational-latency
   pressure rather than on its own merits. The device/real-Chrome path stays the only
   fetch path this pass builds; a lightweight path — IF ever justified by data from
   further live runs — is separate, future work, not attempted here.

8. **`ACTION_CONTRACT_VERSION` becomes 5.** `research.start`'s terminal `diagnostics`
   schema gains `mode`/`budget_s`/`elapsed_s`/`waves`/`challenged_pages`/`cooled_domains`,
   and every run now carries an explicit, resolved speed mode instead of always running
   what v5 calls QUICK's unbounded predecessor. `test_health_endpoint.py` is updated
   alongside it (v4 → v5), matching the precedent `ACTION_CONTRACT_VERSION` v3→v4 set
   for the SAME kind of change (ADR-0067 amendment).

Consequences: `services/api` gains `app/research/policy.py`
(`ResearchPolicy`/`POLICIES`/`resolve_policy`/`derive_mode_from_utterance`/
`decide_next_wave`) and `app/research/challenge.py`
(`domain_of`/`is_challenge`/`record_challenge`/`is_domain_cooled`); `ReportStats` and
`ResearchDiagnostics` gain the six fast-path fields; `BrowserResearchRequest` gains
`mode`; `plan_activity` gains a `mode` argument and stores the resolved policy;
`fetch_targets_activity`/`fetch_activity`/`synthesize_activity` read that stored policy
and the run's own progress for challenge/cooldown state, never re-resolving or
re-deriving it; `BrowserResearchWorkflow.run`'s fetch/rank/top-up section is rewritten as
the wave loop; `CreateResearchRequest` gains `research_mode`; `research_start` derives a
mode from topic+scope. What this ADR does NOT build: a dedicated `utterance` argument on
the `research.start` tool schema (item 1's open note), enforcement (as opposed to
diagnostics-only reporting) of `min_distinct_publishers`, and the lightweight fetch path
(item 7) — each a real, named gap for a future pass rather than half-closed here.

## ADR-0069 — M18.3: a Living Core the owner can wake up to, and a display that prefers to stay on (2026-09-07)

**Context.** The owner's directive after M18.2: the small wireframe Core is no longer
acceptable as the primary experience; the owner wants to say "Yarın 07:30'da YouTube'dan
… ile beni uyandır" and be woken by real music, a rising volume, the displays coming on
and "Günaydın efendim" — with no voice session open, no tab focused, and no dependence on
the network being up at that minute; and the displays should turn off when the owner is
away or asleep, yet ANY key or mouse movement must wake them at once even if the camera is
off or wrong. Everything M18 proved (receipts, one router, the eye lifecycle, presence
from the camera, the alarm ramp, browser isolation, the authority boundary) is the floor.
The full architecture is `docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md`; this ADR records
the decisions and what was refused.

**Decisions.**

1. **A wake alarm is a routine with a clock and a device fallback, run as a receipted
   sequence.** The `WakeAlarm` aggregate holds the owner-facing policy and lifecycle;
   its trigger is an ordinary `at`/`schedule` routine with one `wake_alarm` action, so
   `RoutineFiring`'s uniqueness stays the first line against double firing. A named,
   health-visible `RoutineClock` (asyncio, 10 s, single-flight) calls `evaluate_due`;
   this amends M18 row 12.15 — the routines package still owns no timer, the clock is
   the thing that asks. The firing runs `disarm → display.wake → media (or tone) → ramp
   → greeting with duck/restore → completion`, each physical step an `ActionReceipt`
   with a ledger row; both audio paths failing is `FAILED` + `UiState.ERROR`.
2. **The device arms its own fallback.** `desktop.alarm_arm` persists the alarm on the
   companion; the companion rings the tone at `fire_at + grace` unless a cloud start or
   disarm for that `alarm_id` came first, and a cloud start consumes the arm. A network
   interruption cannot erase an accepted alarm; a reconnect cannot ring it twice.
3. **Alarm media is the browser worker's own `alarm` profile, verified by the media
   element, never helped.** Four worker operations (`media_play/volume/status/stop`);
   the ramp and the ducking are the `<video>` element's volume inside our dedicated
   window; Chrome's autoplay policy is a preference of that window, not an anti-bot
   measure; a CAPTCHA, bot check, sign-in or consent wall is a recorded failure and the
   tone. The owner's own Chrome and tabs are never touched.
4. **The greeting is background narration, not a voice session.** OpenAI TTS in the
   persona's nearest voice → a single-use, five-minute audio token → `desktop.play_audio`
   on the companion (fetch from the broker origin only, sha256-checked, played through
   the tone's own render path at its own level). No microphone, no realtime session.
5. **The display policy lives in Cloud Core and prefers ON; the last line of defence is
   on the device.** `app/ambient.decide()` is pure: `UNKNOWN`, stale, eye disabled,
   low confidence, any holdoff, an alarm context or a display already off → `none`;
   only sustained AWAY or sustained, confident LIKELY_ASLEEP with `auto_off_enabled`
   → `display.off`. Independently, the companion refuses `desktop.display_off` inside
   its own recent-input window (`{display_off:false, refused:"recent_input"}`, a
   successful command the cloud reads as a refused receipt). Keyboard/mouse wake the
   display at the OS level; we never intercept input, we observe an idle tick count.
6. **The device reports on the heartbeat; no new frame type.** An optional `status`
   object (input idle, display state, alarm ringing, armed alarms, local fires) rides the
   existing heartbeat every ~10 s; the cloud derives `input` presence observations,
   `owner.input_active`, holdoffs, `display.on|off` and World Model facts from it.
7. **The Living Core is full-viewport, gold/amber and still drawn only from what is
   true.** UI state contract v3 adds `alarm.*` (a wake-surge channel that never displaces
   a thinking/speaking Core) and `display.*` (ambient strip only). Fullscreen only on
   the owner's gesture with a visible exit; a PWA manifest; tiers, hidden-tab stillness
   and the 2D fallback preserved.
8. **Display power is the only machine-state capability.** Nothing in M18.3 locks,
   sleeps, hibernates, logs off, reboots or shuts down; the source-reading guard extends
   to every display file. `ACTION_CONTRACT_VERSION` becomes 6.

**Refused, and why.** A per-alarm Temporal workflow (durable, but a second scheduler
beside the routine engine the owner already proved; the DB row plus a clock plus the
device arm is simpler and survives the same failures). A self-hosted YouTube IFrame page
(needs an origin and adds a second player surface; the real page plus the media element
is the highest semantic control we own). CoreAudio per-app ducking (touches the
machine's mixer, which M18 forbade). Browser `setTimeout` alarms (the owner's explicit
prohibition). A new device→cloud frame type (the heartbeat already flows; additive
optional fields need no protocol version). WebView2 inside the companion (a second
browser runtime on the device when the qualified worker already exists).

**Consequences.** The Windows agent gains capabilities and must be updated on the owner's
machine (an owner-authorised install with `-DisplayPower`); the Cloud Core release that
carries M18.3 also carries M18.2's research fast path (one release, contract v6); the
routine clock runs in production from that release with `auto_off_enabled=false` until the
owner turns it on. Four tracks implement the spec in parallel (ADR-0070 web, ADR-0071
cloud, ADR-0072 Windows, ADR-0073 browser); the integrator owns the harnesses, Stage 14 of
`docs/QUALIFICATION.md` and the owner queue.

## ADR-0070 — The Living Core: a full-viewport gold/amber cognitive machine, still drawn only from what is true (2026-09-07)

Status: Accepted

Context: M18.1 (ADR-0065) gave the Core a layered visual language, and the owner's verdict
on the running result was that it is still not the thing: *"the existing small wireframe
sphere is no longer acceptable as the primary owner experience. /core must become a
full-viewport living visual presence."* The brief that followed is the most concrete
visual direction this project has had — luminous gold/amber, a warm nucleus, concentric
rotating structures, independent orbital rings, translucent volumetric shells, internal
connection paths, procedural circuitry, bounded particle transport, floating structural
fragments, depth and parallax, restrained bloom, dense internal structure; "a living
artificial cognitive machine", not "a simple animated sphere" — and it arrives with the
same trap ADR-0052, ADR-0056 and ADR-0065 exist to refuse. Every one of those words can be
satisfied by a shader that runs on a clock, and a Core that moves on a clock teaches the
owner, within a day, that its motion means nothing. In parallel, M18.3's contract v3
(`docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md` §7) adds eight `alarm.*` states and two
`display.*` states that must be drawn without the alarm becoming a second, louder way for
the Core to look busy.

So the design problem was stated the same way it was in ADR-0065, one level up: what is the
richest, densest machine that can move **only** on evidence, and what does it look like
when it fills a screen rather than a card. `docs/M18_3_LIVING_CORE_VISUAL_IDENTITY.md`
holds the palette, the nine layers, the channel table, the budgets and the overlay rules.

Decisions:

1. **Nine layers, gold, and every one of them still at zero unless a channel says
   otherwise.** Outermost first: outer field (2.18), containment shell (1.52), topology
   shell (1.30), three independent orbital layers (1.30/1.62/1.98), a procedural circuit
   layer (1.20), the two bounded particle populations, floating processor fragments (1.42),
   the energy chamber (0.92) and a nucleus (0.62) that is warm white at its body and gold
   at its skin. Structure is always mounted — a machine does not assemble itself when work
   arrives — but its **brightness** is `glow` and its **motion** is the reported channels,
   so an untold Core is a complete, dark, motionless machine rather than an empty stage.
   The orbitals turn at three incommensurate rates and two directions (`ORBITAL_RATES`
   +0.52, −0.31, +0.19), all multiplied by the same `ringSpin`: they never beat together,
   and they stop together.
2. **Depth is geometry, and the bloom is two shells.** Nine radii, five planes,
   counter-rotation and the existing pointer lean are what make the structure read as a
   volume. The "restrained glow" is one additive back-face shell around the nucleus plus
   the existing pulse shell (and one blurred disc in the 2D path). **No post-processing
   package, and no dependency was added for this milestone at all** — a bloom pass would
   have been the easy way to buy the look and would have put a second renderer between the
   owner and the evidence.
3. **The wake surge is its own channel, and it does not borrow the thinking channel.**
   `alarm.*` sets `wakeStage`/`wakeSurge` and nothing else: it never sets `kind`, never
   touches a core motion channel, and a thinking Core with an alarm playing draws both. The
   surge exists only while the alarm is actually sounding (firing 0.65, playing 0.45,
   greeting 0.80, raised by a published ramp level); `armed` and every terminal stage are
   zero, so a failure is loud in words and silent in geometry. The spec's own sketch said
   "rings accelerate"; they do not, deliberately — ring speed is the thinking channel, and
   spending it on an alarm would teach the owner that it means nothing. `display.*` has no
   field on `VisualIntent` at all: it is a cell on the ambient strip and a test asserts the
   absence.
4. **The claims are read by vocabulary, not by channel.** v3 put the alarm lifecycle on the
   release band, so `releaseClaim` now reads the release path's own state tokens rather
   than "the newest event on the release channel" — otherwise a ringing alarm would blank a
   deployment genuinely in flight. v2's `alarm.triggered` keeps its old meaning ("a routine
   fired") rather than being retconned into the new lifecycle, and an `alarm.*` token this
   build cannot read is not drawn as a ringing alarm.
5. **A v2 server is read, not refused.** `KNOWN_CONTRACT_VERSION` is 3 and
   `MIN_SUPPORTED_CONTRACT_VERSION` is 2: v3 only added states, so an older Cloud Core
   serves a subset we can read and is drawn normally, with one line under the connection
   dot saying which states it will never publish. Refusing to draw anything would have been
   a worse lie than the lag, and an empty alarm cell without that line would read as "no
   alarm is set". A server NEWER than this build stays a hard mismatch: we do not know its
   vocabulary.
6. **The stage is the viewport, and the coverage is arithmetic.** `/core` is fixed to the
   viewport (`position: fixed; inset: 0`) and near-black (`#06050a`), so there is no page
   scroll at all rather than a scroll with nowhere to go. `stageSizeFor(w, h)` sizes
   the stage so the Core covers 60–80 % of the usable viewport at every aspect ratio
   (portrait 0.74, square 0.78, landscape 0.72, ultrawide 0.66 of the short side, clamped
   into the band), and the `CORE_FILL` it works from is **derived from the camera** rather
   than typed beside it, so a change to the framing moves the layout instead of quietly
   falsifying the claim. `tests/uistate/stage.test.ts` checks the band across sixteen real
   viewports from a 360-wide phone to 32:9.
7. **The cluster fades; the strip does not.** The six controls (voice, eye, tier, 2D,
   fullscreen, cockpit) recede to 25 % after four idle seconds and return on any pointer,
   key or focus event, via a pure reducer (`controlFadeReducer`) rather than a timer buried
   in a component. Fading is not hiding: the buttons keep their labels, their ARIA state and
   their place in the tab order at every opacity. The ambient strip is deliberately excluded
   from the fade, because a parent's opacity cannot be undone by a child and a faded privacy
   assurance is not one.
8. **Fullscreen is a gesture, and the PWA does not cache.** `requestFullscreen` is called
   from exactly one callback bound to one button and from no effect anywhere (a structural
   test reads the source); the same button offers the labelled exit and names Esc. The
   manifest is `standalone` with `start_url: "/core"` and dark colours, and has **no**
   `display_override: ["fullscreen"]` and **no service worker** — a cached shell that
   rendered yesterday's state would be the most expensive lie in the product.
9. **A route that does not exist yet is a fourth outcome.** The cockpit's new Alarmlar and
   Ekran/Ortam panels read `/v1/alarms` and `/v1/ambient/policy`, which Track C is still
   building, so `Loaded<T>` gains `absent` and a 404 renders "Henüz yok." with the path.
   "There are no alarms", "I could not find out" and "this server has no alarms yet" are
   three different sentences, and the panel says which one it is. A device that sent no
   heartbeat `status` is drawn as unreported, never as a screen presumed on.

Consequences: `pnpm --filter @pagentos/web test` grows from 678 to 775 tests, all Node +
`react-dom/server`; no browser, no Playwright; `tsc --noEmit` and `oxlint` clean. The scene
budget is re-derived and re-pinned (high 36 drawables / 398 instances, balanced 33 / 206,
low 22 / 32), and `low` drops the outer field, the circuitry and the fragments whole while
keeping one ring and one orbital layer so the machine stays the same machine. Minimal mode
no longer renders `EyeControl`, so the eye's reconcile seam (`stopLocalIfStale`) moved onto
the page itself and is pinned by a test — losing it would have left a camera running that
the Cloud Core believed was off, which is the one regression this rewrite could have made
that the owner would care about most. Because no browser is launched in this environment
the WebGL scene has still never been seen here; the 2D path is rendered to
`apps/web/preview/*.svg` and a single page so the integrator can hand the owner a picture
before qualification A, and scale, depth and light remain something only a real browser can
judge. What was refused: a post-processing bloom pass, any new dependency, a service
worker, an automatic or manifest-driven fullscreen, ring acceleration from an alarm, any
`display.*` on the Core, a fading privacy cell, an empty list for a route that does not
exist, and every form of motion that a published event or a real measurement did not
produce.

## ADR-0071 — Wake alarms are routines with a clock, a device fallback and a receipted sequence; display-off is a policy that prefers ON (2026-09-07)

Context: M18.3 (`docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md`) asks Cloud Core for two
things the owner will judge with their eyes closed. An alarm has to wake them — with the
camera off, no browser tab focused, no voice session live, and the cloud possibly
unreachable — and the screens in front of them have to go dark only when the system is
genuinely sure, and come back the instant a hand touches the keyboard. Both are physical,
both happen while nobody is watching the logs, and both fail in ways a person cannot
correct afterwards: a wake alarm that did not ring is not recoverable at 07:31, and a
screen that went dark because the camera broke is not recoverable by explaining it later.

Everything below follows from that asymmetry.

Decision:

1. **A wake alarm is a routine, not a second scheduler.** `WakeAlarm` (table `wake_alarms`,
   migration `0021_wake_alarms`) is the aggregate; the TRIGGER is an ordinary
   `app.routines` routine carrying a single new action kind, `wake_alarm`, whose detail is
   nothing but the alarm's uuid. One-shot alarms get an `at` routine, recurring ones a
   `schedule` routine, every snooze a fresh `at` routine. The routine row therefore cannot
   hold a stale copy of the media, the ramp or the greeting the owner changed afterwards,
   and `RoutineFiring`'s uniqueness on `(routine_id, occurrence_key)` stays the first line
   against a double ring — inherited, not reimplemented.

2. **There is now a clock, and it is named.** M18 row 12.15 said "no routine fires without
   something asking: there is no background timer". That property was never "no loop
   exists" — it was "no HIDDEN loop". `app/routines/clock.py::RoutineClock` is a single
   asyncio loop started by the API lifespan (`ROUTINE_CLOCK_INTERVAL_S` default 10 s,
   `ROUTINE_CLOCK_ENABLED` default true), single-flight, each tick in a worker thread with
   its own session, running `evaluate_due` → the alarm tick → the ambient tick. It is
   owner-visible on the health manifest (`checks.routine_clock`: running, enabled,
   interval, ticks, last tick, last error) precisely because a configured clock that is
   not running is the state in which no alarm would ever fire. The routines PACKAGE still
   owns no timer and `evaluate_due` is still its one explicit entry point;
   `tests/unit/test_routines_clock.py` asserts both halves structurally, and that is the
   amended form of row 12.15. The alternative — leaving evaluation to whoever thinks of it
   — is not a scheduler a person can rely on to wake them, which is the whole point of
   M18.3.

3. **The clock is a cadence; the schedule is in the rows.** Nothing in the tick decides
   anything from "this is the Nth tick": arming, the greeting, and completion are all
   derived from stored timestamps compared to the supplied `now`. A process that was down
   for ten minutes therefore catches up on its first tick instead of losing what it missed,
   which is what makes "an ARMED row fires after a fresh process's first tick" true rather
   than hopeful.

4. **Idempotency lives on the ALARM, not only on the occurrence.** `WakeAlarm.last_firing_id`
   plus the state machine (`app/alarms/state.py`) mean a second tick, a second process, or
   a broker redelivery all find the alarm already past ARMED and do nothing. An occurrence
   is a row another process may not have committed yet; an alarm is the thing that is
   physically making noise. A duplicate-suppressed dispatch reports SUCCEEDED with
   `deduplicated`, not FAILED — turning correct suppression into a critical UiState.ERROR
   every time a tick overlapped a dispatch would have been a self-inflicted alarm.

5. **The wake sequence is a list of promises, each with a receipt.** Disarm the device's own
   fallback FIRST (so a cloud ring and a local fallback ring can never both happen, and a
   FAILED disarm never stops the sequence — an unreachable device that rings its own
   fallback is exactly what the owner wants). Wake the display best-effort; a dark screen is
   never a reason for a silent alarm. Then the owner's media through the browser worker's
   dedicated `alarm` profile, and if that is not possible for ANY reason — no capability, no
   url, a challenge, autoplay blocked, an unverified play — the device's own ramping tone,
   with the media failure recorded as its own receipt carrying the real reason. Only if BOTH
   audio paths fail is the alarm FAILED, and then it says so (ledger `alarm.failed` plus a
   critical `UiState.ERROR`). `app.alarms.sequence.RECEIPT_BY_DEVICE_CALL` enumerates every
   device capability this package dispatches against the receipt capability it is recorded
   under, and a structural test asserts the two sets are equal in both directions: a step
   added later without a receipt fails the suite rather than quietly becoming a hidden
   action.

6. **`verified` means the browser watched `currentTime` advance.** An unverified
   `browser.media_play` is treated as a FAILED play and the tone takes over. A silent tab is
   indistinguishable from a broken alarm to a sleeping owner, so "play() was called" is not
   allowed to count as ringing.

7. **The greeting needs no voice session, no microphone and no camera.** It is a WAV
   synthesised through `OpenAITTSProvider` (the owner's existing key) with the voice nearest
   the realtime persona — `cedar` first, an explicit `alloy` fallback, and which voice
   actually spoke rides the receipt — delivered as a one-time 256-bit token
   (`app/alarms/audio_store.py`, 5 minute TTL, single use, sha256 in the signed command).
   `GET /v1/alarms/audio/{token}` is the ONE endpoint in this milestone with no owner
   session, on its own router so the exemption is a visible line rather than a per-route
   flag: the fetcher is a Windows service holding no session, and the token is the
   authority — the same shape as the broker's enrollment exception. The alarms package is
   structurally forbidden from importing `RealtimeSessionRow` or anything under
   `app.voice.realtime_sessions`, and a greeting that cannot be synthesised is recorded and
   the music keeps playing; it never becomes a claim that it was spoken.

8. **Uncertain means ON.** `app/ambient/policy.py::decide` is pure and is the only place in
   this system that may conclude "turn the owner's screens off". Its table is ordered by
   certainty, so the reason it reports is the first TRUE one — which is the one an owner
   asking "why did/didn't my screens go off?" actually wants. UNKNOWN presence, a stale
   assertion, low confidence, a disabled eye, an unreadable display state and any active
   holdoff each return `none` with their own reason, and the two paths that DO act require a
   sustained AWAY, or a sustained AND confident LIKELY_ASLEEP, with automatic display-off
   off by default until the owner turns it on. An unwanted screen left on is the accepted
   failure mode; a screen that goes dark because a camera failed is not.

9. **Physical owner input outranks passive inference, on both sides independently.** The
   heartbeat's optional `status` object (protocol version unchanged; validated leniently
   here because the authoritative schema is Track D's and a device sending a shape this
   Cloud Core has never seen must stay CONNECTED — a disconnected device cannot ring an
   alarm) feeds `app/devices/status.py::DeviceStatusRegistry`. An input-idle RESET writes
   `owner.input_active` and starts the cloud's `input` holdoff; a `recent_input` REFUSAL
   from the device — a successful command carrying `refused`, mapped to
   `execution_status=refused` with its own sentence, never a failure — starts the same
   holdoff. Neither side depends on the other: the cloud can be unreachable and the device
   still protects the owner, and the device can be old and the cloud still does. A long idle
   produces NOTHING: absence of input is not evidence of absence.

10. **Display power is the only machine-state capability, and that is enforced by reading
    the source.** `tests/unit/test_alarms_structure.py` screens every executable line of
    `app/alarms` and `app/ambient` for shutdown/suspend/hibernate/logoff/lock API names, the
    cloud half of the guard the companion already has over its own display files. `display.on`
    / `display.off` on the bus come from the device's OBSERVED power state on the next
    heartbeat, never from having asked — a refused command must not leave the strip claiming
    the screens are dark.

11. **`ACTION_CONTRACT_VERSION` becomes 6** (M18.2 released 5) and the UI-state contract
    becomes 3. A whole family of mutating capabilities now reaches the owner by voice
    (`alarm.create/cancel/snooze/stop/status`, `display.off/wake/status`,
    `ambient.set_policy/test_display` — ten tools registered from `default_registry()` by one
    added line), with two receipt shapes the contract had not needed: a DEVICE REFUSAL that
    is a successful command, and an `observed_after.local` that is a device's read-back
    rather than a browser's. The intents live in the ONE router (`resolve_intent`), checked
    before the generic stop words — "Alarmı durdur" is built from words that are also
    STOP_TOKENS, and a ringing alarm that answered by stopping the NARRATION would leave the
    owner listening to it — and after the eye's privacy stop, which still wins over
    everything. There is no second Turkish table anywhere.

12. **Two refusals are the system declining to invent something.** An unparseable "when" is
    refused rather than rounded to a guess (`app/alarms/tr_time.py` raises `UnparsedWhen`; a
    bare number is not a clock, so "Beni bir ara uyandır" cannot become 01:00). And a
    RECURRING alarm whose music the owner only named — a title with no url — is refused
    outright with `needs_media_confirmation` and nothing is created, because a repeating
    alarm that silently rings a tone every weekday instead of the named song is a lie that
    repeats. A ONE-SHOT alarm in the same position IS created with the tone and the speech
    asks for the link: the owner still wakes up tomorrow.

13. **Two clock readings, deliberately.** `spoken_clock_tr` is how a person tells the time
    ("yedi buçuk") and belongs in the greeting; `clock_words_tr` reads the digits back
    ("yedi otuza kurdum") and belongs in a confirmation, so a set time cannot be misheard as
    a rounding. Spec §3.7 fixes the first and §6 the second; a test pins that they differ.

14. **Test mode is the production path.** A test alarm is a real alarm with `is_test=true`
    and `max_play_seconds=120`; the owner's display test arms a moment and the CLOCK issues
    the real, receipted `display.off` when it arrives — which is also what gives the owner
    the ten seconds to take their hand off the keyboard. Every terminal state — stopped,
    cancelled, completed, failed — runs the same release: stop playback, disarm the device,
    close the media session, resolve or re-schedule the routine, write `alarm.cleaned_up`.
    Not a special case for test alarms: a real alarm that left a device armed would ring
    again tomorrow for no reason.

Consequences: `services/api` gains `app/alarms/` (models, state, tr_time, speech,
audio_store, greeting_audio, sequence, service, routine_port, routes) and `app/ambient/`
(policy, holdoff, service, ingest, routes), plus `app/devices/status.py`,
`app/devices/routes.py` and `app/routines/clock.py`. `app.routines.actions` gains the
`wake_alarm` kind; `app.routines.dispatch` gains a third port (`WakeAlarmPort`) and the four
`browser.media_*` names on the cloud allowlist alongside the new `desktop.*` capability
constants; `app.broker.frames.HeartbeatFrame` gains an optional `status`; the ledger
vocabulary gains the `alarm.*` lifecycle events, `owner.input_active`,
`ambient.policy_changed` and an `ambient` subsystem; `app.uistate.contract` gains the
`alarm.*` and `display.*` states at version 3. What this does NOT build: the browser
worker's four media operations (Track B), the Windows agent's display/arm/activity/audio
capabilities (Track D), and the Living Core renderer (Track W). Until those land, an alarm on
a device without them fails honestly — `no_capable_device` or `capability_missing` — and
falls back to the tone, which is the behaviour the fallback exists for rather than a gap
being papered over.

## ADR-0072 — The companion refuses to darken a screen the owner just touched, arms its own fallback alarm, and reports what it sees on the heartbeat (2026-09-07)

M18 gave the device a wake alarm and one machine-state action (`desktop.display_off`), both
behind gates. M18.3 asks the device to be part of a living core: to bring a screen back, to say
what it can see, to be ready for a wake-up the cloud may not be able to deliver, and to speak a
greeting the cloud rendered. That is six new capabilities, and the interesting question for each
of them was not how to build it but what it is allowed to do when nobody is looking. This ADR is
the answer to that question, in `devices/windows-agent` and `packages/protocol`.

1. **Six new names are advertised UNCONDITIONALLY, and the seventh still is not.**
   `desktop.display_wake`, `desktop.display_status`, `desktop.activity_status`,
   `desktop.alarm_arm`, `desktop.alarm_disarm` and `desktop.play_audio` join
   `AgentCapabilities.Compose` with no flag, in one `Ambient` group appended after the alarm
   pair so a manifest diff reads as an addition rather than a reshuffle. The rule that decided
   this is not "how risky does it feel" but **does it take anything away from the owner**. Waking
   a screen is the exact inverse of the one operation that does. Reporting a state removes
   nothing. Arming a local fallback can only make an alarm the owner already asked for more
   likely to ring. Playing a bounded, digest-verified sound the owner's own broker rendered is
   the audio equivalent of `desktop.open_artifact`. `desktop.display_off` is the only one that
   subtracts, and it keeps both gates of ADR-0028's successor arrangement: the service will not
   route it and the companion will not execute it unless `DisplayPowerEnabled` is set in BOTH
   configurations, which `scripts/install-device-service.ps1 -DisplayPower` now writes to both
   in one place — writing one alone produces a device that either advertises a name it will
   refuse or refuses a name it advertised.

2. **Display-off gains a third gate that is not a flag, and refusing is a SUCCESS.**
   Even fully enabled, `desktop.display_off` now refuses when the owner touched the machine
   inside `holdoff_s` (default 120 s) or while an alarm is ringing, returning
   `{display_off: false, refused: "recent_input"|"alarm_active", input_idle_s, holdoff_s,
   observed_state, observed_at}` as a **successful** `command_ack`. That choice is the load-bearing
   one. An error class here would make a correct refusal indistinguishable from a broken device,
   and a caller — Cloud Core's routine dispatcher, which retries retryable classes — would retry
   it until the holdoff happened to expire, which is exactly the "screen goes dark two seconds
   after a keystroke" failure the gate exists to prevent. The alarm check runs first and
   overrides idle time entirely: three hours of quiet is precisely the state a wake alarm fires
   into, and darkening the screen at that moment is the machine working against the thing it
   just did. The holdoff comparison is strictly-less-than, so 120 s of quiet is enough and 119.9
   is not; and an **unknown** idle time (a companion with no real input source) satisfies the
   holdoff on its own no more than it blocks the off — the device does not fabricate a zero,
   which would refuse forever, nor a large value, which would claim quiet it never observed.

3. **The display state is observed, never inferred, and "unknown" is a real answer.**
   `Win32DisplayStateObserver` is a message-only window registered for
   `GUID_CONSOLE_DISPLAY_STATE` and `GUID_MONITOR_POWER_ON`; the reported state is the last value
   Windows handed it, with the moment it arrived, and before the first notification it is
   `unknown` with a null timestamp — permanently, if nothing ever arrives. The temptation here
   was to infer "on" from a recent keystroke, and it was declined because that would report the
   idle timer twice under two different names, and because the display idling out and the owner
   idling out are different events with different consequences. Windows offers no reliable
   synchronous "is the display on?" call, and the ones that resemble it are the same APIs that
   turn a display off; so the observer holds no way of driving a display at all — no broadcast
   target, no execution state, no synthetic input — and a test asserts that from outside. A
   successful display-off reports the state read BACK after the broadcast, so a broadcast nothing
   honoured cannot be reported as a dark screen.

4. **`display_wake` is a pointer move and an execution-state reset, and there is no keyboard
   member in its input structure to fill in.** Two steps in a fixed order: a MOMENTARY
   `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` — never `ES_CONTINUOUS`, because a standing
   claim nobody clears is indistinguishable from a broken power plan on the owner's side — then
   `SendInput` with `MOUSEEVENTF_MOVE` and `dx = dy = 0`. The synthetic-input struct declared in
   `DisplayWake.cs` has a pointer member and no key member, which makes "this file cannot type
   into the owner's foreground window" a fact about the code rather than a promise about its
   behaviour.

5. **The source-reading guard now covers the whole family, by glob, with a bigger list.**
   `AmbientCapabilityTests.The_display_capability_can_only_turn_a_display_off_never_suspend_the_machine`
   reads every `Display*.cs` and `Monitor*.cs` in the companion — a glob, so a display file added
   next month is guarded the day it appears rather than the day someone remembers this test —
   and fails on any name that could end a session or change the machine's power state
   (`ExitWindowsEx`, `SetSuspendState`, `InitiateSystemShutdown`, `hibernate`, `logoff`, and
   `LockWorkStation` plus a capital-`Lock` catch-all), any CONTINUOUS execution-state constant,
   any keyboard API, and any display-CONFIGURATION API (M18.3 reports monitor topology from
   `EnumDisplayMonitors`; it never sets resolution, orientation or the primary monitor). One
   deliberate imprecision, recorded so it is not "fixed" later: the forbidden token is capital
   `Lock`, not bare `lock`. Banning the lower-case keyword would fail on ordinary mutual
   exclusion, and the predictable response to that failure would be to weaken the list — a guard
   that cries wolf is a guard that gets deleted. DISPLAY OFF IS NOT SYSTEM SLEEP is the whole
   design (M18_THREAT_MODEL.md §5), and it now has a test that grows with the code.

6. **The alarm arm is a fallback, and the hard part is that it rings exactly once.**
   `desktop.alarm_arm` writes `{alarm_id, fire_at, grace_s, label, wake_volume, max_duration_s}`
   to `%LOCALAPPDATA%\PagentOS\companion\armed-alarms.json` (write-then-move, the same shape as
   the idempotency store), and a `TimeProvider`-driven ticker rings the ordinary
   `AlarmController.Start` path at `fire_at + grace_s`. The failure it exists for is the one
   where the network is down at 06:30 — and an in-memory arm would also be gone if the companion
   had restarted overnight, so the file is the only version of this that survives both. The grace
   (default 60 s) is not padding: firing at exactly `fire_at` would race the cloud's own command
   over a link with any latency at all, and the owner would sometimes hear two alarms. Three
   things consume an arm, each removing it from the store first — a `desktop.alarm_start` naming
   that id (the ordinary case: the cloud got there in time), a `desktop.alarm_stop` naming it
   (the owner has already dealt with it), and `desktop.alarm_disarm` — and a local firing removes
   it too, **persisted before the first sample is generated**, so a companion that dies mid-ring
   gives the owner a missed alarm, which they notice, rather than a second one on the next start.
   Reload is deliberately conservative: an overdue arm rings once if it is less than two hours
   late and is expired with an audit row otherwise, because waking someone twenty minutes late is
   a late alarm while waking them at 14:00 for a 06:30 alarm is a machine behaving badly.

7. **The heartbeat carries a status, and the Device Service closes its key set.**
   `desktop.activity_status` returns seven fields — `input_idle_s`, `display_state`,
   `display_observed_at`, `alarm_ringing`, `ringing_alarm_id`, `armed_alarms`, `next_alarm_at` —
   and the Device Service asks the companion for that same object before each heartbeat and
   attaches it as an OPTIONAL `status` (schema `deviceStatus`; `protocol_version` stays 1, the
   field is purely additive, and a heartbeat without one is byte-for-byte the frame v1 always
   sent). Two decisions inside that are worth stating. First, the wait is hard-bounded at 1.5 s
   and every failure — no companion, slow companion, throwing companion — collapses to "no
   status": presence is computed from heartbeats, so a status path that could stall one would let
   a busy companion make the device look offline, which is strictly worse than a heartbeat with
   nothing on it. Second, `status` is `additionalProperties: false`, which means one unknown key
   from a newer companion would fail the broker's validation for the **whole heartbeat** and take
   the connection down; so `HeartbeatStatus.Project` runs on the SERVICE side and keeps only the
   seven known keys. A component that changes independently must not be able to break the frame
   that proves the device is alive. What the status deliberately does not carry: any key,
   character, pointer position, window title or application name — `input_idle_s` comes from one
   `GetLastInputInfo` tick count, and a structural test fails if a hook, key-state, raw-input or
   foreground-window API name appears in `InputActivity.cs`. And there is no "the owner is asleep"
   field: the inference from idle time and display state to a person's state belongs where it can
   be explained, argued with and turned off, in Cloud Core against the presence model
   (M18_THREAT_MODEL.md §4). A device that shipped its own conclusion would make that argument
   unreachable.

8. **`desktop.play_audio` is bounded four ways, and the origin check lives on the side that has
   not been asked to fetch yet.** The Device Service refuses the command with
   `security_scope_error` before it reaches the pipe unless the URL's scheme, host AND port equal
   the broker REST origin this device is enrolled against; a device with no configured broker
   origin refuses every `play_audio`, because "we could not tell where audio may come from" and
   "this audio may be played" must not be the same answer. The companion then enforces the other
   three: at most 2 MiB, checked while reading rather than from a `Content-Length` header that is
   only a claim; a SHA-256 that must match the bytes that arrived, whose mismatch is
   `security_scope_error` and **never retryable** because fetching the same URL again would not
   change what is at it; and at most 20 s at a level that defaults to 0.75 and **scales the
   samples**, with audio over the cap trimmed rather than refused (the owner asked to be greeted,
   and a slightly short greeting serves that better than silence plus an error). The alarm's
   promise about the machine's mixer now covers the greeting too, and is asserted the same way: a
   test reads every companion source and fails if an endpoint-volume API name appears in any of
   them. The container parser is written here rather than pulled in, and accepts exactly one
   shape — RIFF/WAVE PCM16, mono or stereo, 8–192 kHz — because every refusal is a refusal to
   hand unfamiliar bytes to an audio stack running in the owner's session. The render endpoint is
   opened at the WAV's own rate and the shared-mode path converts; there is no resampler in this
   agent, and there should not be one to be wrong about at 06:30.

Consequences: `PagentOS.Agent.Core` gains `AgentCapabilities.Ambient`,
`Protocol/HeartbeatStatus.cs`, `Connection/IHeartbeatStatusProvider.cs` and an optional
`HeartbeatMessage.Status`; `AgentConnection` takes an optional status provider and asks it under
a 1.5 s bound. `PagentOS.SessionCompanion` gains `InputActivity.cs`, `DisplayStateObserver.cs`,
`DisplayWake.cs`, `MonitorInventory.cs`, `ArmedAlarmStore.cs`, `AlarmArmController.cs`,
`ActivityStatusReporter.cs`, `WavAudio.cs` and `GreetingPlayer.cs`; `DisplayPowerController`
gains `Wake`, `Status` and the holdoff refusal; `CompanionRuntime` routes the six new names and
runs `desktop.play_audio` on the concurrent path the browser family already used, so a status
request arriving during a 20 s greeting is still answered. `PagentOS.DeviceService` gains
`CompanionHeartbeatStatusProvider` and the audio-origin check in `InteractiveCapabilityExecutor`.
`packages/schemas/device-protocol.schema.json` gains the optional `deviceStatus`;
`DEVICE_PROTOCOL.md` gains §6e–§6h and an updated §9. What this ADR does NOT build: any
device-side inference about the owner's state, any way to change monitor topology, any resampler,
any authenticated fetch (the greeting URL is expected to be capability-bearing or loopback-scoped
on the broker's side — the device authenticates the CONTENT with the digest, not the request),
and no way for the companion to reach the machine's volume, now or later without a test going red.

## ADR-0073 — Alarm media plays in the worker's own alarm profile, verified by the media element, never helped past a challenge (2026-09-07)

Status: Accepted

Context: M18.3's owner outcome B ends with real music: "90 saniye sonra seçtiğim YouTube
müziğiyle test alarmı kur." → the display wakes → *the named YouTube item really plays* →
the volume ramps → the greeting speaks over ducked music → "Alarmı kapat." stops it. Before
this milestone the browser worker (M13, ADR-0050) could navigate, read and search, but it
had no notion of a media element, no way to say whether audio was actually coming out, and
exactly one persistent profile — the research one, which at 07:30 may well be holding the
tab the owner fell asleep reading. Three facts shaped the decisions:

1. **"It played" is not observable from a navigation result.** A `page_kind: "ok"` on a
   YouTube URL says the page loaded. It says nothing about whether the video element
   exists, whether `play()` was honoured, or whether a consent gate is standing in front of
   it. An alarm that reports success on the strength of a 200 is an alarm that silently
   does not ring.
2. **The autoplay policy is a real obstacle and the honest way past it is a preference for
   our own window, not a trick.** Chrome refuses `play()` without a user gesture; the
   documented switch `--autoplay-policy=no-user-gesture-required` changes that for the
   profile it is given to. It is not a bot-detection bypass, and this project has a
   standing rule (ADR-0050 decision 3, spec §1 item 6) that nothing is ever spoofed,
   masked or solved — so the switch had to be scoped so narrowly that it could not be
   mistaken for one.
3. **The 2026-09-03 owner-machine incident was about one profile held twice.** The M13
   lifecycle guards (launch lock, breaker, kill-on-close job, one owner session id) were
   written for "the research profile" as a proper noun. A second persistent profile that
   inherited none of that would have reintroduced the exact window cascade — this time at
   an hour the owner is asleep.

Decisions:

1. **A second dedicated persistent profile, `alarm`, derived rather than configured.** The
   alarm profile directory is the research profile's sibling (`<profile-dir>-alarm`); the
   worker refuses at startup an `--alarm-profile-dir` that is, or contains, the research
   profile, and `ManagedBackend`'s existing real-profile guard refuses either of them
   anywhere inside a real Chrome/Edge/Brave tree. Deriving it means a configuration mistake
   cannot collapse the two onto one directory — the shape of the incident. `profile:
   "alarm"` and `session_kind: "media"` are one thing, checked from both directions, and a
   media session may never use the research profile. The four media operations REFUSE to
   run on a session that is not `session_kind: "media"`: alarm audio staying out of the
   owner's research browser is a structural refusal, not a convention.
2. **The one-owned-browser guard is generalised from "research" to "each persistent
   profile".** The single-owner session id, the launch lock, the launch-rate breaker and
   the kill-on-close job now apply per profile, so the research and alarm browsers coexist
   and neither can be opened twice; a second session id on `alarm` is
   `browser_lifecycle_violation` exactly as it is on `research`. The worker's last-resort
   `atexit` reap sweeps BOTH profiles — an alarm Chrome outliving the worker would keep
   playing music at the owner, the loudest possible way to leak a browser. The durable
   `browser-ownership.json` stays what it is, the RESEARCH job's ownership record; an alarm
   session is not a research job and does not claim it.
3. **The autoplay switch is scoped to a media launch and documented as a preference.** It is
   passed only for `session_kind: "media"`, to that session's own dedicated window, and both
   the contract (§2) and the module docstring say in as many words that it is not an
   anti-bot measure: it changes how our profile treats our page's `play()` call and defeats
   no site protection, bot detection, DRM, advertising or consent handling. A unit test
   reads the source for that sentence, so the claim cannot quietly rot away from the code.
4. **Playback is proven by the media element, never assumed.** `media_play` sets `volume`
   FIRST (so the owner is never hit by the page's own level for the instant before the
   ramp), calls `play()`, and then reads the element's own `currentTime` again after
   `verify_seconds`: `verified` is true only when it advanced ≥ 0.5 s with `paused === false`.
   A `play()` that resolves and then does not move the element is `reason: "error"` — a
   truthful failure, not a "verified" that would leave the owner asleep.
5. **A wall is named and reported; it is never opened.** `challenge` (CAPTCHA, "confirm
   you're not a bot", sign-in wall — reusing M13's `page_kind` classification and the
   existing Google interstitial detector), `consent_wall` (a `consent.youtube.*` /
   `consent.google.*` landing, or consent wording on a page that produced no media element),
   `autoplay_blocked` (Chrome's `NotAllowedError`), `no_media_element`, `navigation_failed`,
   `error`. No retry, no bypass, no click on anything — not a consent button, not an ad, not
   a challenge; no DRM or ad circumvention; no download; nothing that reads cookies or
   storage. Each is a SUCCESSFUL command carrying the reason, so Cloud Core writes a
   truthful `media.play` receipt and rings the device's own tone instead. Consent WORDING
   alone is deliberately not enough: a cookie banner floating over a video that plays anyway
   is a successful play, and calling it a wall would cost the owner real music for nothing.
6. **The ramp runs inside the page, and the command comes back.** `media_volume` generates a
   script that steps the media element's own `volume` on a 250 ms `setInterval`, cancelling
   any ramp already running (the greeting's duck and restore issue three ramps within
   seconds), clamping every write and landing exactly on the level asked for. The operation
   returns as soon as the interval is armed — a 20 s wake ramp must not hold a device
   command open for 20 s — while a ramp of ≤ 2 s is awaited so the greeting can follow it
   immediately. The generator is a pure function, tested by reading its output; this is the
   media element's own volume and never the Windows master volume (M18.3 §1 item 8).
7. **Two deviations from the spec's result table, written into the contract rather than
   discovered later.** A transport-level navigation failure inside `media_play` is a
   successful command with `reason: "navigation_failed"` rather than a retryable typed
   error, because a firing alarm's caller needs a receipt it can record and fall back from,
   not a `dependency_unavailable` mid-sequence; payload and destination-policy errors stay
   hard errors. And `media_volume.level_from` / `media_status.volume` / `duration_s` answer
   `null` when there is no element (or no known duration) instead of a fabricated `0.0` — a
   missing element must not raise in the middle of a greeting, and must not lie either.
8. **Worker release 0.5.0 and `contracts["browser.media"] = 1`.** The media family is new
   capability surface, so the release moves and the worker advertises a media contract
   version alongside the search one. Cloud Core checks it BEFORE planning a media wake: an
   agent installed before M18.3 then produces a named contract mismatch and the tone
   fallback, rather than a missing-key failure inside an alarm that is already ringing —
   the lesson of the 2026-09-03 owner run, applied before it could cost a second one.
9. **No test may launch a browser, and the guard now says so by name.** The media suite is
   fake-`Page` only; the two `session_open` tests replace `ManagedBackend` with a recording
   double. `tests/test_test_isolation_guards.py` reads the media suite's source and fails if
   it ever grows a real launch. This is the one surface that opens a VISIBLE window on the
   owner's desktop at an hour the owner is asleep, so the standing rule from the
   2026-09-03 desktop flood is enforced here specifically, not only in general.

Consequences: `browser.media_play`, `browser.media_volume`, `browser.media_status` and
`browser.media_stop` become advertised device capabilities whenever a browser worker is
configured (a device without one advertises none of them, so an alarm on it plans the local
tone from the start), and the installed Windows agent must be updated before a media wake can
be attempted — the release-currency check now says so by version rather than failing at
07:30. What this ADR does NOT settle: how YouTube's consent page actually behaves on a
freshly created `alarm` profile has never been observed on the owner's machine, so the first
real media wake may well answer `consent_wall` and fall back to the tone; the honest fix, if
it does, is for the owner to accept the consent once by hand in that profile's own window
(the profile is persistent and keeps it), never for the worker to click it. Cloud Core's
dispatch allowlist and the `media.play` receipt are Track C's.

## ADR-0074 — A short research is allowed to be thin, never empty by accident (2026-09-07)

Status: Accepted

Context: ADR-0068 gave conversational research a budget it had never had, and it worked —
the runs got fast. On 2026-09-06 the owner asked the same kind of short question three
times on the same device inside six minutes, and the production record says what the
budget cost:

- **afee23c9** (19:49:51Z): 103 candidates discovered, shortlist capped at 25, openai.com
  challenged twice (`page_validity` access_denied, then interstitial) and COOLED, then
  "19 candidate(s) skipped: domain cooled" and "11 candidate(s) skipped: per-domain
  quota"; `fetch_done` 10 over 3 waves; 2 pieces of evidence survived the gate
  (rejections: date_uncertain, off_topic); synthesis: "openai produced 0 defensible
  finding(s); 3 required" → FAILED `insufficient_valid_findings` at **81 s of a 120 s**
  hard budget.
- **d914e44f** (19:54:09Z): 109 discovered, the same shape, 10 fetched, **1** piece of
  evidence (interstitial 2, date_uncertain, off_topic 2, outside_recency_window 2,
  insufficient_content) → FAILED at **58 s**.
- **deabbd44** (19:51:55Z): 46 discovered, 3 pieces of evidence after 8 fetches,
  "synthesized via deterministic", 3 findings, READY at 43 s. The owner heard this one.

Two runs in three failed for lack of evidence while a third to a half of their own budget
sat unspent, and the owner heard "yeterli doğrulanmış kaynak bulamadım" about runs that
had found real, defensible things. Three separate defects, none of them the challenge
policy (which did exactly what ADR-0068 said and cost the run nothing after the second
challenge):

1. **The shortlist was a truncation, not a plan.** `fetch_targets_activity` sorted the
   whole pending pool by one preference (`_prefetch_preference`) and kept the top
   `candidate_urls_max`. openai.com answered the query best and carried recent date hints,
   so it filled most of those 25 slots — and then those slots were thrown away twice over,
   by the cooldown and by the per-domain quota. Nothing refilled them: the ~78 candidates
   from other publishers that discovery had ALREADY found were never looked at again. The
   cap had landed on what was DISCOVERED rather than on what could be FETCHED.
2. **A wave count, not the budget, ended the run.** `decide_next_wave` stopped at
   `waves_used >= max_waves` (3) and at `sources_fetched >= max_sources` (10), both of
   which a QUICK run reaches in under a minute. The 39-62 s that remained were simply not
   spent. `elapsed_s` was also measured from the first fetch, not from the start of the
   run, so the "120 s budget" was 120 s of fetching on top of however long discovery took,
   and the number in diagnostics was not the time the run took.
3. **The findings gate was binary.** `MIN_REPORT_FINDINGS = 3`, so two real findings and
   zero findings were the same outcome to the owner: a failure sentence. The pipeline had
   no way to say "here is what I found, and it is thin".

Decisions:

1. **The shortlist is built for domain diversity, from the whole remaining pool, every
   wave** (`app.research.browser_activities.build_fetch_shortlist`, pure and unit-tested
   independently of the activity). Candidates are grouped by domain, ordered within a
   domain by the existing `_prefetch_preference`, and taken round-robin: domains take
   turns, best-first. A domain contributes at most `per_domain_max_pages` MINUS what it
   has already spent this run (read from the run's real fetched-evidence tally), and at
   most `SHORTLIST_DOMAIN_SHARE` (0.4) of the shortlist before other domains have had a
   turn. A domain with no allowance left therefore occupies **no slot at all**, and a
   cooled domain is filtered out before the shortlist is built — it is not "less
   preferred", it is not fetchable. `candidate_urls_max` now means *the shortlist of
   FETCHABLE candidates*, which is the reading that makes the number mean something: the
   cap is on what can be fetched, not on what was discovered. The refill is structural —
   slot *k* is the *k*-th best candidate of a domain that has not had its turn — rather
   than a special case that has to fire.

2. **`max_waves` and `max_sources` are floors of attempts; the budget is the ceiling.**
   We took the first of the two options the brief offered and did NOT raise QUICK's
   `max_waves`: raising a number would have moved the same arbitrary wall a little
   further out, and it was `max_sources` (10, reached at wave 3) that actually bound
   these runs, not the wave count. `decide_next_wave` now continues while (evidence <
   `target_findings`) AND (something is fetchable) AND (`elapsed_s` + one wave's own
   worst case still fits the budget), stopping on `ResearchPolicy.fetch_ceiling`
   (= `max(max_sources, candidate_urls_max)`) pages as an absolute bound and on a
   structural safety net (never more waves than pages) so the loop terminates even
   against a frozen clock. **The policy table of ADR-0068 is unchanged**; two derived
   properties (`wave_expected_s` = `per_page_timeout_s × ceil(wave_size /
   concurrent_fetches)`, `fetch_ceiling`) are added instead.

   Three things make this stricter rather than looser, which matters because "spend more
   of the budget" is exactly the kind of change that quietly becomes "spend more than the
   budget":

   - the budget check is now `elapsed + wave_expected_s >= budget`, not `elapsed >=
     budget`: a run never STARTS a wave it cannot be sure of finishing inside the budget,
     where before it could start one at 119.9 s;
   - the run clock starts when the run does (`workflow.now()` at the top of
     `BrowserResearchWorkflow.run`), not after discovery, so `hard_budget_s` bounds the
     whole run and the `elapsed_s` in diagnostics is the time the run actually took;
   - a run that could ALREADY publish a full report (`evidence >= MIN_REPORT_FINDINGS`)
     is bounded by the SOFT `target_budget_s` instead, so the extra waves belong to a run
     that would otherwise have no answer — deabbd44's 43 s success cannot become a 110 s
     one in pursuit of a fourth finding.

   A new stop reason, `no_fetchable_candidates`, distinguishes "ran out of web" from
   "ran out of time": a wave that returns fewer targets than it asked for has drained the
   shortlist, and the workflow reports that as `fetchable_remaining=0`.

3. **A run that verified between 1 and `MIN_REPORT_FINDINGS - 1` sources answers THINLY
   and says so** (`app.research.synthesis.synthesize_thin`), instead of failing as though
   it had found nothing. The report is READY, carries the findings that exist, and its
   `executive_summary` states the thinness in the owner's own language — "Kısa araştırma
   bütçesinde yalnızca N kaynak doğrulanabildi; bulgular sınırlı." — with `uncertainty`
   naming each reason (`evidence_thin`, `cooled_domains`, `undated_pages`, as diagnostics
   codes on `ReportStats.thin_reasons`; the owner hears them as plain Turkish sentences,
   never as codes). `ResearchReport.thin` and the tool terminal payload's `thin` are the
   one bit the explain engine and the harness branch on. `spoken_result` says the
   thinness without a single count or crawler word and closes with a broader run —
   "İstersen daha geniş araştırayım.", deliberately phrased in the words
   `derive_mode_from_utterance` already resolves to STANDARD, so an owner who says yes
   gets the mode the sentence promised.

   Three boundaries hold: **zero evidence is still a truthful failure**, unchanged;
   **`MIN_REPORT_FINDINGS` is untouched** as the threshold for a FULL report (thinness is
   a differently-shaped, explicitly-labelled answer, never a lowered bar); and **the
   provenance gate is not weakened** — every thin finding rests on real evidence and
   passes `run_provenance_gate` exactly as a full report's does. A thin run uses the
   deterministic provider ONLY: asking a model for three findings from one source is
   inviting it to invent the other two, and the honest fix is not to ask.

4. **QUICK's two discovery queries are the two most DIFFERENT ones, not the first two**
   (`app.research.plan.diversify_queries`). `expand_queries`' first three entries are
   Turkish variants of the same phrase ("X", "X haberleri", "X son gelişmeler"), so
   `queries[:2]` bought one language's view of one engine's coverage — part of why ~100
   candidates concentrated in a handful of domains. The pick is greedy and deterministic:
   the owner's own phrasing always leads, then the remaining query with the lowest word
   overlap against everything already picked, ties broken by the expansion's own order.
   For a Turkish topic naming an English entity this yields the Turkish query and the
   English core query without any language list — no provider change, no new dependency.

What this ADR REFUSED to do, and why:

- **Lower `MIN_REPORT_FINDINGS` globally.** That would make every report — including
  STANDARD and DEEP ones with a full budget behind them — publishable on two sources, to
  fix a problem that only ever appeared under QUICK's tight budget. The floor is a
  statement about what a full answer is; the fix is to have a second, honest shape of
  answer, not to move the floor.
- **Retry a challenged page.** ADR-0068 decision 3's zero-retry rule is what made the
  budget affordable at all, and the record shows it working: openai.com cost this run two
  fetches, not nineteen. A run short of evidence has many other domains to try; it does
  not have another attempt at a site that already refused.
- **Add a headless / plain-HTTP fetch path.** ADR-0068 decision 7 already recorded the
  live evidence against it (the same newsroom answered headless Chrome 403 and headful
  Chrome 200). Nothing about being short of evidence makes that finding less true; it
  makes the temptation stronger, which is exactly when a recorded refusal earns its keep.
- **Bypass, solve or wait out any CAPTCHA, consent wall or interstitial.** Unchanged and
  not negotiable, at any evidence count.
- **Raise QUICK's `max_sources`.** See decision 2: the honest change was to stop treating
  a page count as the run's limit, not to pick a bigger page count.

Consequences: `app/research/policy.py` gains `wave_expected_s`, `fetch_ceiling`,
`REASON_NO_CANDIDATES`, `SHORTLIST_DOMAIN_SHARE` and two new `decide_next_wave`
arguments (`fetchable_remaining`, `publishable`); `app/research/browser_activities.py`
gains `build_fetch_shortlist` and the thin branch of `synthesize_activity`;
`app/research/synthesis.py` gains `synthesize_thin` and the `THIN_REASON_*` codes;
`ReportStats` gains `thin`/`thin_reasons` and `ResearchReport`/`ResearchResult`/
`ResearchDiagnostics` and the tool terminal payload gain `thin`;
`app/research/plan.py` gains `diversify_queries`; `BrowserResearchWorkflow.run` starts
its clock at the top and threads the two new wave arguments. A QUICK run may now fetch
more than 10 pages and take longer than it used to when it is short of evidence — up to,
never past, the 120 s the owner set — and a run that already has a publishable answer
stops at the 90 s soft budget instead. `tests/unit/test_research_regression_20260906.py`
pins all three runs by their real counts. What this ADR does NOT build: any use of the
new `thin` flag inside `app/explain` (the flag and its reasons are published; which
explain level says what about them is that module's own decision), enforcement of
`min_distinct_publishers` (still diagnostics-only, ADR-0068's open item), and a
`research.start` utterance field (ADR-0068 item 1's open note, still open).

## ADR-0075 — A research explanation never becomes a second crawl (2026-09-07)

Status: Accepted

Context: the owner's real run, 2026-09-06/07 (production record). A research had
completed — "Son üç gündeki OpenAI ile ilgili gelişmeler", task `deabbd44`, artifact
`5eacab10`, a report with three findings. The owner then opened a NEW `/core` voice
session and said one thing: "Teknik anlat." The router resolved the technical intent,
the model called `activity.explain` at the technical level and the diagnostics were
spoken correctly, concisely, only now — that half worked. AND the model also called
`research.start`, which (since the ADR-0067 amendment wired it to the real M13
pipeline) created a second research task and began a second crawl. Nothing in the
server connected "teknik anlat" to the run it was obviously about, and nothing stopped
a question about a finished research from becoming a new one: the only thing linking
the two was the owner's own memory of having asked. The owner's directive is explicit
and is the shape of this ADR: "Teknik anlat after a completed research run must NEVER
start a new crawl unless the owner explicitly requests a fresh/re-run research. Fix the
architecture, not just the exact Turkish phrase. Do not rely only on the LLM behaving
correctly."

Decisions:

1. **Four research interaction classes, decided in the ONE router.**
   `app.voice.intents.classify_research_interaction` is a pure function over the
   utterance's tokens, the query kind `app.explain.classify` already decided, and one
   durable bit of context — whether a COMPLETED research exists. It answers
   `NEW_RESEARCH` ("Son üç gündeki AI agent gelişmelerini araştır."), `RESEARCH_RETRY`
   ("Araştırmayı yeniden yap.", "Tekrar araştır.", "Yeniden araştır."),
   `RESEARCH_TECHNICAL_EXPLANATION` ("Teknik anlat.", "Hangi sayfalar elendi?",
   "Araştırma sırasında ne sorun oldu?") or `RESEARCH_FOLLOWUP` ("Kaynakları söyle.",
   "Birinci bulguyu detaylandır.", "Neden önemli?") — or None, which means "not about
   research", never "safe to crawl". Only the first two may start a crawl
   (`RESEARCH_CLASSES_MAY_CRAWL`); the last two are ABOUT a run that already finished
   (`RESEARCH_CLASSES_BOUND_TO_A_RUN`) and exist only when there is one to bind to.
   The class rides on `ResolvedIntent` (`research_class`, in `to_dict()`), so the
   durable `voice_intent_resolved` audit row and `session_activity`'s `intents` say
   which class was decided — and `resolve_intent` keeps its ordinary answer alongside
   it: "Teknik anlat." is still `Intent.TECHNICAL`, klass `control`, exactly as before.
   Ordering is where the owner's rule lives: an explicit re-run outranks everything, a
   research imperative outranks the follow-up classes, and "tekrar" beside the word
   research is NOT a re-run — "araştırmayı tekrar anlat" asks for the finished run to
   be narrated again, so a retry additionally requires a RUN verb ("yap", "başlat") or
   the imperative "araştır" itself.
2. **A follow-up binds to a completed run by identity, never by re-running the query.**
   `app.explain.research_context.bind_completed_research` reads, in order: the research
   THIS session started and saw complete (`context_json['last_research']`, written by
   `service._record_research_linkage` from the `task_id` the RUNNING `research.start`
   result already carried — both completion paths, owner and system); then the session's
   open plan (`plan['research_job_id']` / `plan['task_id']`); then the most recently
   completed research of the owner — a `ResearchRunRow` at stage `ready` whose
   `ResearchReportRow` carries a report, inside a 14-day context window. "Completed"
   means what the pipeline means by it, so a still-crawling run, a failed run and a
   ready run whose report never landed are all "nothing to bind". The bound pair
   travels everywhere a checker can read it: `explain()` takes `research_job_id` and
   selects THAT `research.completed` event instead of whatever is latest; `Briefing`
   gained `research_artifact_id` beside `research_job_id`, both in `provenance()` and
   `cognition()`; `BriefingRecord.as_dict()` carries `research_job_id` /
   `research_artifact_id` at the top level, so the `activity.explain` tool-call row
   itself names the run; `session_activity` and `GET .../sessions/{id}` expose
   `last_research` and `last_utterance`. `activity.explain` on a fresh session with no
   plan still finds the completed research through the ledger event and its report —
   that worked in the owner's run and is unchanged.
3. **The refusal lives in the server's tool relay, not in the model's judgment.**
   `service.handle_tool_call` calls `tools.research_followup_refusal` BEFORE any
   handler runs, for any tool in `CRAWL_STARTING_TOOLS` (today `research.start`), keyed
   on the LATEST utterance the router resolved for that session
   (`context_json['last_utterance']`, the same record the audit row is written from,
   overwritten every utterance so an intervening "yeniden araştır" is never shadowed by
   an older follow-up, and bounded by `RESEARCH_TURN_TTL_S = 600`). On a
   `research_technical_explanation` / `research_followup` turn with a completed run
   bound, the call is recorded SUCCEEDED with
   `{"status": "refused", "reason": "research_followup_turn", "research_class": ...,
   "research_job_id": ..., "research_artifact_id": ..., "binding_basis": ...,
   "ambiguous": false, "speech": "Yeni bir araştırma başlatmadım efendim; son
   araştırmanın sonuçlarını anlatıyorum."}` — the same honest shape `plan.redirect`'s
   refusal has (ADR-0067 amendment): the tool CALL succeeded, the crawl did not, and
   the result says so in the owner's language. It is audited under its own action,
   `voice_research_start_refused`, with the bound ids. Because the guard sits in the
   relay every tool call passes through, the model's CHOICE of tool cannot route around
   it; because it is keyed on the resolved intent rather than on the tool's arguments,
   a differently-worded topic cannot either.
4. **Ambiguity is a question, not a guess — and still not a crawl.** When two completed
   researches finished within six hours of each other and neither is linked to the
   conversation, `activity.explain` returns
   `{"status": "needs_clarification", "reason": "ambiguous_research_context", "speech":
   "Efendim, iki tamamlanmış araştırmam var: «...» ve «...». Hangisini anlatayım?"}`
   with the candidates, and the guard refuses `research.start` on that same turn with
   the clarifying question as its speech. Picking the newer of two runs by a few
   minutes would be a guess presented as a fact; starting a third research because the
   context was unclear would be the original defect wearing a different hat.
5. **The persona is told, in two sentences, what the server will do anyway.**
   `RESEARCH_FOLLOWUP_TR`: after a research completes, every question about it is
   answered from the completed report through `activity.explain`; `research.start` is
   only for a new topic or an explicit "yeniden"; otherwise the server will refuse, so
   do not try; and when the tool returns one short question about which research is
   meant, ask it verbatim rather than choosing.

What was refused:

- **Trusting the model.** The persona instruction (decision 5) is the cheap half and it
  is worth having; it is not the fix. The owner's directive says so directly, and the
  2026-09-06 eye run said it before that: privacy and correctness must not rest on the
  model calling the right tool. The guard is deterministic, server-side, and audited.
- **A second Turkish table.** Every temptation here was to add one — a list of
  follow-up phrases in `tools.py`, a "is this a research question?" check in the
  explain service, a client-side hint. `app.voice.intents` is the one command
  interpreter and `app.explain.classify` the one question table (ADR-0063 §2); the new
  classes are computed in the former FROM the latter's answer, so the two cannot
  disagree, and no third table exists. Two tables that must agree will not — the
  `_explain_kind` docstring records what that already cost once.
- **Inferring the job by re-running the query.** The obvious shortcut for "which
  research does the owner mean?" is to match the topic text, or to re-run the search
  and see. Identity comes from a durable row's own primary key or from nothing: the
  session's recorded linkage, the plan's task id, or the most recent ready run — and
  when that is ambiguous, from the owner.
- **Refusing a crawl whenever a research exists.** The guard is keyed on the TURN, not
  on the existence of a report. A new topic after a completed research still crawls; so
  does an explicit re-run. Only a question about the finished run is refused.

Consequences: `services/api` gains `app/explain/research_context.py`;
`app.voice.intents` gains the four classes, `classify_research_interaction`,
`research_class_for` and `ResolvedIntent.research_class`; `explain()` /
`explain_to_briefing()` take `research_job_id` and `Briefing` carries
`research_artifact_id`; `handle_tool_call` refuses a crawl on a follow-up turn and
`complete_tool_call` / `complete_tool_call_system` record the session's research
linkage; `record_client_events` establishes "is there a completed research?" once per
request and stores the resolved turn on the session. What this ADR does NOT build: a
way for the owner to name WHICH older research to explain ("geçen haftaki araştırmayı
anlat" binds to the latest, not to a searched-for one) — the clarifying question covers
the two-run case and a topic-addressed history is a larger change; and nothing here
teaches the M13 pipeline to resume or extend a finished run, so "biraz daha araştır"
on a completed topic is still a NEW crawl, honestly, rather than a continuation.

## ADR-0076 — A research is a thing the owner points at: a durable focus, a deterministic reference resolver, and no crawl for an unresolved reference (2026-09-07)

Status: Accepted

Context: the owner's real evening, 2026-09-06 (production record). Three research runs
finished in seventy-five minutes, and two of them had the SAME title: "Son üç gündeki
OpenAI ile ilgili gelişmeler" at 19:53Z and again at 20:18Z, then "Bu haftadaki OpenAI
ile ilgili geliştirmeler" at 21:08Z. Two things then happened, one on each side of the
ADR-0075 deployment, and they are the same defect seen from opposite ends.

On contract v6, "Teknik anlat." on a fresh voice session started a NEW research and bound
the technical explanation to THAT run — `before_job_id != explained_job_id` in the
harness's own output. That is the defect ADR-0075 was written for, and its guard closed
it. On contract v7, with the guard deployed, the same phrase on a fresh session produced
the clarification "Efendim, iki tamamlanmış araştırmam var: «…» ve «…». Hangisini
anlatayım?" SIX TIMES IN A ROW, 21:05:00 through 21:07:35. Nothing was wrong with the
question. What was wrong was that nothing in the server could accept the answer: the
owner said which one, and there was no state anywhere that a spoken "the second one" or
"the 20:19 one" could land on. And when the owner reloaded the page, the new WebRTC
session had no context at all — ADR-0075's binding lived in the voice session's own
`context_json`, and a reload is a new session.

The owner's directive, in its own terms: research identity is ID-based, never title-based
(two jobs may share a title); there must be a canonical, durable, owner-level research
FOCUS with a bounded stack that survives voice reconnects and page reloads; deictic and
anaphoric references must resolve deterministically ("bu", "bunu", "bunun", "bu
araştırma", "az önceki", "son araştırma", "bir önceki", "bundan önceki", "onu", "bunun
kaynakları", "bunu teknik anlat", "ikinci araştırma"); a selection in the UI sets the
focus; ambiguity causes ONE concise clarification the owner can answer BY VOICE, never a
guess and never a crawl; follow-up tools execute against an explicit resolved job id; and
`research.start` is never a fallback for a reference the server could not resolve.

Decisions:

1. **The focus is a durable, owner-level row, and it is append-only.**
   `research_focus` (migration `0022_research_focus`) records, each time the focus moves,
   which research is now the one being talked about and WHY: `research_just_completed`,
   `result_just_spoken`, `owner_selected_in_ui`, `owner_selected_by_voice`,
   `followup_reference` — a closed vocabulary, in a CHECK constraint, so a source outside
   it cannot reach the database from a path that skips the Python guard. The current
   focus is the most recent row; `previous_focus` is the most recent row naming a
   DIFFERENT job; an ordinal counts distinct jobs down the same list, bounded at eight
   (`app.research.focus.FOCUS_STACK_LIMIT`) because "üçüncü araştırma" is a thing an owner
   says and "sekizinci" is not. Nothing is ever updated in place: recency IS the ordering,
   and an UPDATE would erase exactly the history "bir öncekini anlat" reads. It is
   owner-level rather than session-level for the single reason the record demands — a page
   reload must not lose it. `research_job_id` is a task id with a foreign key onto
   `tasks.id`: the schema saying, structurally, that identity is the id.
   The focus is set when a run's row goes READY (in `runs_service.update_run`, the ONE
   choke point the REST-started and the voice-started run both pass through), when the
   announcer's terminal payload is handed to a session and spoken, by the UI route, by a
   spoken selection, and by a resolved reference — so after "bir öncekini anlat" the
   previous research IS the current one, which is what the next "bunu" has to mean.

2. **One resolver, one decision table, no title matching as identity.**
   `app.research.reference.resolve_reference` is pure over durable state and answers
   `resolved` / `ambiguous` / `missing` in a fixed order: an explicit job id; an answer to
   a live clarification; a PREVIOUS reference; an ORDINAL; a CURRENT/deictic reference —
   or no reference at all, which means the same thing; and finally a TOPIC phrase, which
   is a FILTER over completed reports and never an identity. A topic matching exactly one
   report resolves; matching several with the focus among them resolves to the focus;
   matching several without one asks. Title equality never creates ambiguity when a
   contextual identity exists: two runs called "OpenAI son gelişmeler" are not ambiguous
   to someone who was just told about one of them, and the unit fixtures are deliberately
   that pair — identical topic, different ids, different artifacts.
   The Turkish lives where ADR-0075 put it, in the one router:
   `app.voice.intents.classify_research_reference` reads the tokens the same pass that
   decides the intent and the research class, and returns a bounded `ResearchReference`
   (kind, ordinal, clock time, day, at most six content words). That projection — never a
   transcript — is what the session row keeps, because a tool call carries no utterance of
   its own; that is the point.

3. **A clarification is a question the server remembers, so the owner can answer it.**
   `research_owner_state` holds one pending clarification — the question and the bounded
   candidates — with a ten-minute TTL applied on read. The answer may be an ordinal
   ("ikincisi", "ilki"), a relative reference ("bir önceki"), a superlative ("en son",
   "sonuncusu"), a day ("bugünkü") or, most usefully, the time the question itself
   offered: "Aynı konuda iki araştırmanız var: bugün 20:19'daki mı, yoksa 19:53'teki mi?"
   The times are local (Europe/Istanbul) with "bugün"/"dün", and the locative suffix is
   derived from how the minutes are read aloud — 19 is "on dokuz", so `-daki`; 53 is "elli
   üç", so `-teki`. The question is asked with TIMES rather than titles precisely because
   the two candidates in the owner's record answer to the same title, and a question built
   from titles is the same question twice.

4. **Three follow-up tools that take no title and no job id from the model.**
   `research.explain {level}`, `research.sources {}` and `research.finding_detail
   {index}`: the server resolves which research from the turn's own reference, binds
   `research_job_id`/`research_artifact_id`, and reads ONLY that job's durable report row
   (`app.research.answers` composes the Turkish from `ResearchResult` /
   `ResearchDiagnostics`, keeping ADR-0067's outcome/telemetry split). Every result names
   the job, the artifact, `resolution_reason` and `focus_source`. An ambiguous or
   unresolvable reference returns `{"status": "needs_clarification", "speech": <one short
   question>}` and never a crawl. `activity.explain` was rewired onto the same resolver
   for research-classified turns, so the existing path binds identically and the two
   cannot drift. The answer is spoken IMMEDIATELY, prefixed naturally ("Bu araştırmada
   …"); "kayıtlarımı kontrol edeceğim" and "hangi kayda bakmam gerektiğini bulmaya
   çalışıyorum" are banned by name (`app.actions.receipt.BOOKKEEPING_PHRASES`,
   `contains_bookkeeping`) because they are what the owner heard INSTEAD of an answer.

5. **The guard refuses a crawl for any turn that POINTS at a run — including when there is
   nothing to point at.** ADR-0075's guard refused only when a completed research could be
   bound; with none bound it stood aside, and that is the hole the v6 half of the record
   fell through. Two changes close it. `classify_research_shape` answers what class an
   utterance IS without the "does a completed research exist?" precondition, so "Teknik
   anlat." on an empty history is still a technical-explanation turn. And the guard now
   also refuses on the reference itself (`ResearchReference.points_at_a_run`) and on the
   turn after an open clarification. With nothing to resolve, the answer is "Hangi
   araştırmayı kastediyorsunuz efendim?" — a question, not a research. Only explicit
   new-research semantics still crawl: "araştır" on a topic, "yeniden/tekrar araştır",
   "araştırma yap/başlat". The refusal happens in the relay before any handler runs, so no
   `ResearchRunRow` and no task row is created and then refused; the tests assert the row
   count and that no task exists with a `created_at` after the utterance.

6. **The record says which research, and why that one.** `session_activity` exposes
   `research_job_id`, `research_artifact_id`, `resolution_reason` and `focus_source` at
   the TOP level of every tool-call entry, and `research_class` plus `research_reference`
   (current | previous | ordinal | topic | selection | none) on every intent — the
   assertions an owner qualification makes instead of comparing timestamps.
   `GET /v1/research/focus` returns `{current, previous, stack, pending_clarification}`,
   `POST /v1/research/{task_id}/focus` sets it (409 `not_completed` for a run with no
   READY report), and `GET /v1/research` rows gained `mode`, `source_count`,
   `completed_at` and `is_focus` so the web track can select a research by what it was and
   when it finished rather than by a task id. `ACTION_CONTRACT_VERSION` is 8.

What was refused:

- **Title matching as identity.** The obvious fix for "which research does the owner
  mean?" is to compare the topic text. The owner's own record is the counter-example: two
  completed runs, one title, twenty-five minutes apart. A topic phrase is allowed to
  FILTER candidates and is never allowed to BE the identity; when the filter leaves more
  than one and nothing points at any of them, the server asks.
- **An arbitrary latest row.** ADR-0075's third binding rule was "the most recently
  completed research", and it is a guess wearing a timestamp: on the owner's evening the
  most recent run was frequently not the one being discussed. Recency is now recorded as
  an EVENT the owner caused — a completion they were told about, a result they heard, a
  click, a spoken choice — rather than inferred from a `updated_at` column.
- **A crawl as a fallback.** No unresolved reference may become a research. This is the
  whole ADR in one line, and it now holds in the case ADR-0075 did not cover: with no
  completed research at all, "Teknik anlat." gets a question. Starting a crawl because the
  server could not work out what was meant is the original defect with better manners.
- **LLM-only resolution.** The persona is told what the server does anyway
  (`RESEARCH_FOCUS_TR`: the tools take no title and no id, ask nothing yourself,
  `research.start` only for a new topic or an explicit re-run), and that block is the
  cheap half. The resolver is deterministic, server-side and audited, for the reason this
  repository has now recorded three times: correctness must not rest on the model choosing
  the right tool with the right arguments.
- **A session-scoped memory, again.** ADR-0075's `context_json['last_research']` is kept
  (it costs nothing and is honest about what a session saw), but it is no longer what
  answers "which research?". A page reload is a normal thing for an owner to do.

Consequences: `services/api` gains `app/research/focus.py`, `app/research/reference.py`,
`app/research/answers.py`, two tables and migration `0022_research_focus`, two research
routes and four fields on the research list; `app.voice.intents` gains the reference
vocabulary, `ResearchReference`, `classify_research_shape` and
`ResolvedIntent.research_reference`; `tools.py` gains three tools and a guard keyed on the
turn's SHAPE and REFERENCE rather than on whether anything could be bound;
`record_client_events` stores the bounded reference on the session row and
`session_activity` projects the identity fields. What this ADR does NOT build: a way to
point at a research older than the eight-deep stack other than by naming its topic (an
owner-facing history search is the web track's, not this one's); resuming or extending a
finished run, so "biraz daha araştır" is still honestly a NEW crawl; and any
disambiguation by ARTIFACT — the focus names an artifact but the owner cannot yet say
"the report you saved", because nothing in the voice vocabulary distinguishes a research
from its report.

## ADR-0077 — A tool result is a contract: no empty success, and one authoritative answer per research turn (2026-09-07)

Status: Accepted

Context: the owner's fourth M18.2 record, 2026-09-06 22:11Z–22:20Z (production, contract
v8; sessions c3d88970 and 96f06af4). Crawling, titles and the focus are PROVEN by that
record and were not the defect. What the durable rows show, call by call:

1. `research.explain` at 22:12:09 was recorded **`succeeded`** with no `research_job_id`
   and, for a result, the clarification "Aynı konuda iki araştırmanız var: bugün 00:10'daki
   mı, yoksa dün 23:19'daki mı?" — a question stored as a success. A harness that counts a
   bound succeeded call as an answer cannot tell that row from one; neither could the
   owner, who heard the question and then nothing that was about a research.
2. "Bunu teknik anlat." (the ONE router: `research_technical_explanation`, reference
   `current`) went to **`activity.explain`**, which (a) resolved the MODEL's paraphrase of
   the question instead of the turn — `previous_focus` for "bunu", at 22:19:28 —
   (b) answered with the LEDGER's technical narration ("Sürümler. Research policy v5
   çalıştı. Kanıt. …", `subsystem: ledger`) rather than the report's own diagnostics, and
   (c) attached a narration session to the voice session.
3. "Bir önceki araştırmayı anlat." twenty-eight seconds later was routed to `narration` as
   a `jump_level` inside that narration — the SAME job again, no reference resolved — so
   the previous research was never reached. Two tools, two different answers about "the
   research", and the second one about nothing in particular.

The owner's directive, in its own terms: a successful terminal result must contain an
explicit structured result; never emit `succeeded` with no identifiable target and no
owner-facing result; the target id is part of the event contract; the tool result must be
delivered to voice; `activity.explain` and `research.explain` must not both answer — there
is ONE authoritative owner-facing answer for a turn; test the exact contracts under the
canonical router; fix the harness only after the product contract is explicit.

Decisions:

1. **The relay decides the tool status from the result, by contract.**
   `handle_tool_call` no longer records every returned dict as `succeeded`.
   `tools.terminal_status_for(name, result)` reads a RESEARCH-BOUND result — the three
   follow-up tools by name, and any result whose `routed` is `research_report` or
   `research_reference` — and returns the status it earns: `ok` with a `research_job_id`
   AND a non-empty `speech` → `succeeded`; `needs_clarification` with a question and no
   target → `needs_clarification`; `no_report` → `failed` / `empty_result`; anything else
   → `failed` / `internal_bug`. A failed row keeps the identity the handler did resolve and
   always carries a truthful sentence (`failed_result_payload`): the handler's own for an
   empty result, otherwise "Bu araştırma için anlatabileceğim bir sonuç bulamadım efendim."
   — never the words of an answer that had no target. Results that are not research-bound
   are untouched: an action's refused or failed RECEIPT is a successful report of what
   happened (docs/M18_ACTION_CONTRACT.md §5.5), and `research.start`'s guard refusal stays
   the succeeded call ADR-0075 made it.
2. **`needs_clarification` is a tool status of its own.** `realtime_tool_calls.status`
   gains the word (migration `0023_tool_call_clarification`: the column widened from 16 to
   32 characters, the CHECK constraint re-stated; downgrade relabels such rows `failed`).
   The row payload carries `result` for it exactly as for a success — the one question, as
   `speech` — and `session_activity` projects the status verbatim, so a qualification
   harness that filters `succeeded` never again mistakes a question for an answer.
3. **A research-bound `succeeded` always names its target and speaks.** This is the
   contract's own assertion, in the relay rather than in the handler's good manners: an
   `ok` with no `research_job_id` or with empty `speech` is recorded `failed`
   (`internal_bug`) and spoken as an honest empty answer. A test pins it by patching the
   sentence builder to return nothing.
4. **`activity.explain` on a research turn IS the research answer.** Whether a turn is
   about a finished research is decided from the ONE router's record of the owner's own
   words (`last_utterance`: the research SHAPE first; a ledger question kind is the
   ledger's; otherwise only a pointer an owner uses for one run — "bunu", "bir önceki",
   "ikinci" — and never the topic kind, which any sentence with content words gets, nor
   "son", which "son yaptıklarını anlat" carries too), never from the model's `question`,
   which is a paraphrase and is consulted for identity only when no turn record exists at
   all. On such a turn the tool hands over to the research answer path: the same resolver,
   the same report row, the same sentences (`app.research.answers`), `routed:
   research_report`, `answered_by: research.explain`, `provenance` with the job, the
   artifact and the report's own numbers; the level from the turn's shape ("teknik anlat"
   is technical whatever the model passed), then the model's explicit `level`, then the
   intent. No briefing artifact and no narration session are created, so the NEXT research
   turn is resolved afresh instead of becoming a cursor move. The ledger paths — the last
   activity, failures, evidence, goals, the eye and world state, authority questions — are
   unchanged, and a technical question with NO completed research is still the last
   activity's technical account (ADR-0075).
5. **The client hands a clarification to the model as the result it is.** `apps/web`
   submits the `result` of a `needs_clarification` response as the function output (the
   question, with its status inside), never wrapped as a failure — which would have the
   model apologise instead of asking. `ToolCallStatus` names the fourth word.
6. **The persona names one tool for research follow-ups.** `RESEARCH_RESULT_TR` and
   `RESEARCH_FOLLOWUP_TR` no longer send "teknik anlat" to `activity.explain`; they name the
   `research.*` family and say that `activity.explain` gives the same answer on a research
   question. Cheap, and no longer load-bearing: correctness does not rest on the model's
   choice of tool, which is the point of decision 4.
7. **Contract v9.** `ACTION_CONTRACT_VERSION` 8 → 9; the owner harness releases the Cloud
   Core once, before the check, when the deployed contract is older.

Rejected:

- **Mapping every `status: failed` / `refused` dict to a failed tool call.** An eye action
  that could not verify its read-back is a `succeeded` call carrying a receipt that says
  so (§5.5); a refused crawl is a succeeded call that says nothing was started. The
  contract here is scoped to research-bound results on purpose.
- **Resolving the model's `question` when a turn record exists.** The paraphrase is what
  bound "bunu" to the previous research. It is used only when the client reported no
  utterance at all (a CLI relay), and then it is all there is.
- **Keeping the narration route for research turns.** "Teknik anlat" as a cursor jump
  inside an earlier briefing is exactly what swallowed "bir önceki araştırmayı anlat".
  A briefing about the ledger still gets its cursor moves; a research turn does not.
- **Fixing the harness first.** The harness is extended only after the contract exists:
  the clarification head it looks for now includes ADR-0076's wording, and one new row
  (`followup.no_empty_success`) asserts that no research-bound call is recorded succeeded
  without a job and words and that no clarification is recorded as succeeded.

Consequences: `services/api` gains `terminal_status_for` / `failed_result_payload` /
`result_is_research_bound` and the research-turn predicate in
`app/voice/realtime_sessions/tools.py`, `TOOL_STATUS_NEEDS_CLARIFICATION`, migration
`0023_tool_call_clarification` (the single head), `VoiceErrorClass.EMPTY_RESULT`, the
`answered_by` field on the session record and a `provenance` block on every research
answer; `apps/web` accepts the fourth status; the persona is harmonised; nine contract
tests in `test_research_focus.py` run the owner's exact sequence under the canonical
router (the clarification status, the bound success, the empty-success refusal, the
unreadable report, the paraphrase that says "bir önceki" against a turn that says "bunu",
one answer from either tool, the previous research as an answer rather than a cursor
move, a ledger question left to the ledger, the record's projection). What this ADR does
NOT change: which research the resolver picks (ADR-0076), the guard against a second crawl
(ADR-0075), the research result itself (ADR-0074), or the statuses of any tool outside
the research family.

## ADR-0078 — The alarm path is proven through the application object, and two wiring defects it surfaced (2026-09-07)

Status: Accepted

Context: the owner's day plan of 2026-09-07 asked for the durable wake alarm to be proven
end to end without the owner's ears. The M18.3 suites prove every component against
injected fakes — the voice tools with a `ToolContext.live` the test built, the clock with
three counters, the sequence with a scripted device, the routine engine with a fake
dispatcher. Writing the one test that drives the owner's evening through the surfaces
production uses (the realtime relay for the words and the model's tool call, the
`RoutineClock`'s own `tick_once`, the routine engine, the `WakeAlarmRunner`, the wake
sequence, the ledger, with only the device answering from a script) failed twice before
it passed, and both failures were production defects:

1. **The wake sequence was never on the route.** `RealtimeVoiceRuntime.live_sources()`
   handed a tool the broker, the artifacts and the voice runtime — and nothing else.
   `tools_ambient` reads `ctx.live["wake_sequence"]` and `ctx.live["device_statuses"]`;
   the unit tests set them by hand. On contract v9 in production, "Alarmı kapat." marked
   the alarm STOPPED while the music went on (`stop_alarm(sequence=None)` skips the
   physical stop and the disarm), "Ekranları kapat." / "Ekranları aç." answered "no device
   runtime" every time, and "Ekranlar açık mı?" answered from an empty registry.
2. **The model could not even create an alarm.** `alarm.create`'s schema named its
   argument `when_text`, and the relay refuses any tool argument whose key contains
   `text` (`service.FORBIDDEN_KEY_PARTS`: transcripts and credentials never ride a tool
   call, under any spelling). A real model call would have been a 422 before the handler
   ran; the handler's tests passed it the key directly.

Both are the recurring defect class this repository has now named three times: a component
built, tested and never wired. Integration-point tests must assert the wiring, not the
component.

Decisions:

1. **Live runtimes are registered where they are built.** `RealtimeVoiceRuntime`
   gains `register_live(**sources)`; `create_app` registers `wake_sequence` and
   `device_statuses` (the same objects `app.state` exposes) the moment it has built them.
   `live_sources()` merges them in, so a tool call and a route can never see two.
2. **The voice argument is `when_spoken`.** The REST body (`POST /v1/alarms`) keeps
   `when_text`; it never passes the relay. The tool schema, the handler, the persona and
   the spec table say `when_spoken`.
3. **Two structural guards.** `test_the_app_hands_the_wake_sequence_and_the_status_registry_to_the_voice_tools`
   reads the live sources off the real `create_app` product; `test_no_tool_schema_names_an_argument_the_relay_refuses`
   walks every schema in the registry, every nesting level, against the one blocklist.
4. **One end-to-end run, kept.** `tests/unit/test_alarms_wiring.py` is the owner's
   evening as a test: created by voice (the canonical router classifies the words, the
   receipt speaks, the rows are durable and in Europe/Istanbul), armed on the device by
   the clock, fired at the instant through the routine engine (once — a second process's
   tick at the same instant fires nothing), the display woken before the media, the alarm
   profile opened, the media verified, the ramp, one receipt per physical step under
   ADR-0071's vocabulary, the greeting on a later tick with duck and restore and a
   single-use audio token redeemable exactly once through the open route, "Alarmı kapat."
   by voice stopping the medium that is actually playing, the test alarm cleaning itself
   up, and the session record saying so. The negative is kept beside it: with no wake
   sequence on the live sources the row changes and no device is told.
5. **Contract v10.** `ACTION_CONTRACT_VERSION` 9 → 10, so the owner harnesses release the
   Cloud Core once before any owner run of B or C.

Consequences: `runtime.py` (`register_live`), `main.py` (the registration), `tools_ambient.py`
and `persona.py` (`when_spoken`), the spec table, `receipt.py` v10, the health pin, and
`test_alarms_wiring.py` (seven tests). What this ADR does NOT change: the sequence, the
policy, the device protocol, or any receipt shape. What it does not prove: audibility —
that remains the owner's evening test B.

## ADR-0079 — Ambient display autonomy, hardened: explicit temporal policy, the owner's own words, holdoffs that survive a restart, and an explanation from the record (2026-09-07)

Status: Accepted

Context: the owner's directive of 2026-09-07 (afternoon): turn the displays off when they
have been away for a while or appear to have fallen asleep, wake them at once on keyboard,
mouse, return or alarm — and make the policy conservative, predictable and
production-ready. ADR-0071 built the policy (`app/ambient`: a pure `decide`, four holdoffs,
"uncertain means ON", wake on return, alarm context). Reading it against the directive
found five gaps and one defect: `quiet_hours` was stored and never read; there was no
explicit "keep the screens on" preference; the holdoff registry was in-memory only, so a
Cloud Core restarted a minute after the owner touched the keyboard met a stale AWAY with
one fewer reason to say no; an explicit "Ekranı aç." started no holdoff; nothing answered
"why did you turn the screens off"; and `note_alarm_wake` — the alarm-wake holdoff — had
no caller anywhere, so it had never once started in production.

Decisions:

1. **Explicit temporal policy, every value owner-configurable and persisted.** The row
   gains `keep_on` (false), `asleep_after_outside_quiet_s` (1800) and
   `camera_unknown_grace_s` (120) beside `away_after_s` (900), `asleep_after_s` (600),
   `asleep_min_confidence` (0.7) and the four holdoffs (`input` 600, `command` 900,
   `alarm_wake` 1800, `return` 600). Migration `0024_ambient_hardening`, server defaults.
2. **Absence and sleep stay distinct, and neither comes from one frame.** AWAY needs
   `away_after_s` of sustained absence; LIKELY_ASLEEP is the fusion engine's own escalation
   of sustained RESTING (twenty minutes by default) and then needs `asleep_after_s` more,
   at `asleep_min_confidence`. Long stillness is RESTING until the engine says otherwise.
3. **A camera that stopped delivering is a degraded perception, never an absent owner.**
   `collect_inputs` reads when the fusion engine last held a camera observation; `decide`
   refuses an off when that is older than `camera_unknown_grace_s` (`perception_stale`).
   Permission lost, process dead, no usable frame, the eye disabled — all of them stay ON.
4. **Local input outranks passive perception.** Unchanged in mechanism (the heartbeat's
   input reset writes `owner.input_active`, feeds an `input`-sourced PRESENT observation
   into the fusion engine, and starts the input holdoff; the device refuses `display_off`
   inside its own window; the OS wakes the display before any of us), now proven by the
   scenario matrix with the eye both on and off.
5. **Every wake starts a holdoff, and holdoffs survive a restart.** Input, an explicit
   "Ekranı aç." (`note_owner_display_command`, new), the owner's return, and an alarm
   (`note_alarm_wake`, now called from `fire_alarm` and from the local-fallback reconcile).
   `restore_holdoffs` rebuilds them on the first tick of a process from the ledger's
   witnesses (`owner.input_active`, `ambient.policy_changed`, a `display.wake` receipt,
   `alarm.firing`, a `presence.state_changed` to `returned`) for the time each has left.
6. **The alarm outranks the ambient off.** A ringing or imminent alarm is `alarm_context`;
   after it, the alarm-wake holdoff holds the screens for `alarm_holdoff_s`. A failed
   display wake never stops the audio (ADR-0071, unchanged).
7. **The owner's words set the policy; the model's booleans do not.** The one router
   derives `policy_changes` from the utterance (`ambient_policy_changes`): "Ben yokken
   ekranları kapat / kapatma", "Uyuduğumda ekranları kapat / kapatma", "Otomatik ekran
   yönetimini aç / kapat", "Ekranı açık tut / tutma", "Ben geri geldiğimde ekranı aç /
   açma". The turn record carries them; `ambient.set_policy` applies THEM and uses the
   model's arguments only when no turn record exists. `keep_on` outranks every inference,
   every holdoff and the policy's own switches until the owner lifts it.
8. **Quiet hours are schedule-aware, never a schedule.** `{"start", "end", "timezone"?}`
   in the owner's timezone, crossing midnight; inside the window LIKELY_ASLEEP needs
   `asleep_after_s`, outside it `asleep_after_outside_quiet_s`, with none configured the
   normal threshold everywhere. A window that cannot be read is treated as unset.
9. **Display off is not system sleep.** The device path stays structurally guarded
   (ADR-0072); the cloud's own device vocabulary (`RECEIPT_BY_DEVICE_CALL`) is asserted to
   contain no sleep, hibernate, shutdown, lock, log-off or reboot call.
10. **Multi-monitor stays a set.** The cloud reads one observed display state per device
    and never a topology; power is the only thing this policy touches.
11. **Persistence.** The policy row survives everything; the holdoffs are rebuilt from the
    ledger (5); the presence assertion is deliberately not persisted — a restarted process
    is UNKNOWN, which is ON.
12. **An explanation from the record.** `ambient.explain` (a query; `GET /v1/ambient/explain`)
    answers "Ekranları neden kapattın?", "Neden açık bıraktın?", "Şu an ekran politikası
    ne?" from the live `decide`, the presence assertion, the active holdoffs, the latest
    `owner.input_active` and the latest display receipt — which now carries its `reason`
    and no longer names a placeholder alarm as evidence. A missing fact is spoken as
    missing ("Kayıtlarda otomatik bir ekran kapatma yok efendim."), never guessed.
13. **The scenario matrix runs through the real policy layer.** `test_ambient_scenarios.py`
    drives a real `PresenceFusionEngine` with structured camera observations, the real
    status registry through `ingest_status`, the real holdoff registry and the real alarm
    service, with the device as the only fake, for all fifteen owner-listed scenarios.
14. **Contract v11.**

Consequences: `app/ambient/policy.py` (keep-on, quiet hours, the camera grace),
`service.py` (validation, `restore_holdoffs`, `note_owner_display_command`, `explain`),
`app/alarms/service.py` (the wired alarm-wake holdoff), `sequence.py` (receipt reason,
display-only receipts), `speech.py` (the preference sentences and the explanation),
`intents.py` (`AMBIENT_EXPLAIN`, `ambient_policy_changes`, `ResolvedIntent.policy_changes`),
`tools_ambient.py` (`ambient.explain`, turn-derived changes, `keep_on`), `routes.py`,
migration 0024, and two new suites (`test_ambient_scenarios.py`, `test_ambient_hardening.py`).
What is NOT here: a durable presence assertion (by design), an owner-facing editor for
the thresholds beyond the REST and voice surfaces, and the physical run — row 14.12/14.14
real evidence is the owner's item 26. AMBIENT DISPLAY ENGINEERING COMPLETE;
READY_FOR_OWNER_PHYSICAL_TEST.


## ADR-0080 — The Owner Utterance Corpus: synthetic voice qualification through the real path, and the routing defects it found on its first run (2026-09-07)

Status: Accepted

Context: the owner's directive of 2026-09-07 (evening): stop making the owner the test
harness for voice routing. Every pending M18 owner test was really two tests — "does the
audio pipeline carry my voice?" (only the owner can answer) and "do my words route to the
right tool with the right target and no forbidden side effect?" (deterministic, and the part
that had actually been failing: ADR-0075/0076/0077 were all routing defects found by ear).
The directive asks for a textual utterance injected as synthetic owner speech at the ONE
canonical boundary — right after transcription — through the SAME router, tool dispatch,
response and speech-output path, with structured evidence and assertions; paraphrase sets
per command; Turkish variation (punctuation, colloquial, polite, short/long, deictics, ASR
misspellings, number/time formats, missing diacritics); negative routing assertions;
multi-turn focus; side-effect sandboxing; a versioned corpus that every fix expands; a
nightly bounded suite with a report; a Living Core / Cockpit state; and the marks VOICE
ROUTING = PROVEN_AUTOMATED, AUDIO PIPELINE = PROVEN_PROXY, PHYSICAL OWNER AUDIO =
READY_FOR_OWNER_TEST.

Decision:

1. **One corpus, one harness, no second router.** `services/api/tests/voice_corpus/corpus.py`
   is the versioned `OwnerUtteranceCorpus` (`CORPUS_VERSION = 1`, 345 cases): each
   `UtteranceCase` carries `case_id`, `utterance`, `expected_intent`, `expected_tool`,
   `expected_response` (ok / refused / needs_clarification / running / control / none),
   `expected_target` (current / previous), deterministic `expected` extras (research class,
   level, local_time, weekdays, is_test, alarm state, snooze count and minutes, policy
   changes, query kind, eye flag after), `forbidden_tools`, `side_effects` (the device
   capabilities the case may touch), `context` (none / research_focus_b / alarm_ringing /
   alarm_scheduled / eye_disabled), `category`, `source` (canonical / paraphrase /
   asr_noise / regression / generated), `locale`, `regression_issue_id`. Variants are
   generated deterministically (no final punctuation, lower case, diacritics stripped);
   the alarm family is a bounded product of prefixes × days × times × verbs. No LLM
   paraphrases: every label is deterministic.
   `harness.py` builds a fresh application per case (`create_app`, the real relay, the real
   router, real services, SQLite, the `FakeDeviceAction` as the only fake), seeds the
   context through the product's own paths (two same-title researches completed through
   `research.start` + the announcer; a ringing alarm through `fire_alarm`; the eye through
   `disable_eye`), posts the utterance as a client `utterance` event, reads the ONE router's
   resolved intent / research class / reference / policy changes off the response, dispatches
   the contract-expected tool through `POST /tool-calls` with the arguments the persona tells
   the model to pass (derived from the contract, never from a second parser), dispatches
   `research.start` on every research follow-up turn to PROVE the relay refuses it, checks
   the fake device's calls against the case's side-effect policy and the research/alarm
   tables for rows a wrong route would have created, checks the speech (non-empty, no
   banned completion phrase) and, through the client's own lifecycle events, that the
   session record shows an audible turn. `build_report` produces the counts by category and
   source, the confusion rows, and HEALTHY / REGRESSION_FOUND from the numbers.
   `tests/unit/test_owner_utterance_corpus.py` is one parametrised test per case plus the
   aggregate "zero forbidden side effects" and the report writer
   (`PAGENTOS_VOICE_CORPUS_REPORT`); `scripts/core/voice-routing-qualification.ps1` is the
   nightly run (report to `state/reports/`, `-Post` records it on the Cloud Core with the
   DPAPI owner credential, never printed).

2. **The first run found nine routing defects; all fixed, all with regression tests, per the
   closed-loop policy.** 261 of 345 passed before the fixes; 0 forbidden side effects at any
   point. Fixed in the ONE router (`app/voice/intents.py`), the time parser
   (`app/alarms/tr_time.py`), the question table (`app/explain/classify.py`) and the snooze
   tool (`tools_ambient.py`):
   - a bare "Sustur." / "Tamam, kapat." / "Kes şunu." / "Durdur." while an alarm is RINGING
     is `ALARM_STOP` — `resolve_intent(..., alarm_ringing=)` is the router's second and last
     piece of live context (the service reads `alarms_ringing(db)` once per request); with no
     alarm ringing the words keep every meaning they had (`Kes.` is still STOP);
   - "On dakika sonra tekrar çal." is a snooze (again + ring verb + a "later"); "Şarkıyı
     tekrar çal." is still a repeat; the router derives the minutes SAID
     (`spoken_minutes`, `ResolvedIntent.alarm_minutes`, on the turn record) and
     `alarm.snooze` prefers them over the model's `minutes` argument — the same rule ADR-0079
     §7 made for the ambient policy fields; the corpus had shown "10 dakika ertele" snoozing
     for the default five;
   - "Ekranı uyandır." is `DISPLAY_WAKE` (it went to `alarm.create`: a screen noun with the
     wake verb and no alarm noun is never an alarm); "Görüntüyü kapat." is `DISPLAY_OFF`;
     "Ekran durumu ne?" / "Monitörler kapalı mı?" are `DISPLAY_QUERY` ("durum" + ne/nedir/nasıl
     is a question shape; "monitörler" was missing);
   - the eye's privacy-critical exact forms and the screen's open verbs gained their
     diacritic-free spellings ("gozunu kapat", "kamerayi ac", "ekranlari ac");
   - "Bunun arka planda nasıl çalıştığını anlat." is a technical explanation
     (`_technical_match`: one helper for the research class AND the TECHNICAL intent, so the
     two cannot disagree); the diacritic-free "arastirma ... detay" reaches the same
     research_detail kind as its spelled sibling;
   - "Beni görüyor musun?" is an eye_state question and "Şu an burada mıyım?" a world_state
     question (both had resolved to nothing);
   - the ASR's "7 30 da" (two numerals, a case suffix) and the spoken "yedi otuzda" /
     "sekiz kırk beşte" are clocks; "on beşte" is 15:00 (a compound hour read before the
     minutes — it had been 10:00), "yirmi bir otuzda" is 21:30; two bare numbers with no
     suffix and no clock word are still refused ("never guess").
   Regression tests: `test_voice_corpus_regressions.py` (37), `test_alarms_tr_time.py`
   (+6); the corpus keeps the utterances. Three corpus expectations were wrong and were
   corrected rather than the product: "detayını açıkla" / "hangi sayfalar elendi" / "ne
   sorun oldu" resolve as EXPLAIN questions whose research class is technical (the class
   and level are the contract); a snooze re-arms at once (ARMED with `snooze_count` 1 is the
   state it rests in); `state.now` answers with `query_kind`, not a `routed` field.

3. **The state is a fact, not a claim.** `app/voice/qualification`: `POST
   /v1/voice/qualification` (owner-gated) records a run as ONE `voice.qualification` ledger
   row (subsystem voice; counts + at most 20 confusion rows; idempotent on source + suite +
   corpus version + generated_at; the summary is recomputed from the counts — a client
   cannot post a healthy word over failing numbers); a failing run opens one
   `EvolutionOpportunity` per wrong route (source `voice_corpus`, source_ref the case id,
   citing the ledger row; idempotent across nights); `GET /v1/voice/qualification` derives
   NOT_YET_RUN / HEALTHY / REGRESSION_FOUND (failing, nothing tracks it) / SELF_HEALING
   (failing, an opportunity is open) / OWNER_AUDIO_TEST_REQUIRED (routing healthy, no
   `owner_audio` run recorded) from the rows alone. The Cockpit's "Ses yönlendirme sınaması"
   panel shows exactly that (`VoiceQualificationPanel`; empty ≠ failed ≠ absent).

4. **What the marks mean.** VOICE ROUTING = PROVEN_AUTOMATED: 345/345 through the real
   path, 0 wrong routes, 0 clarifications where an answer was expected, 0 forbidden side
   effects (a PROVEN_PROXY-class mark whose stand-ins are the synthetic utterance and the
   fake device, named as the owner asked). AUDIO PIPELINE = PROVEN_PROXY: the speech text,
   its record and the client's lifecycle events are asserted; no sample is synthesised or
   heard. PHYSICAL OWNER AUDIO = READY_FOR_OWNER_TEST: items 23b and 25 are now a thin
   final layer — the routing under them is already proven.

Consequences: a routing regression is found by the nightly suite, recorded, and turned into
an opportunity before the owner hears it; every routing fix must add its utterance to the
corpus (the policy, and the only way the corpus stays ahead of the owner); the router has
exactly two pieces of live context (a completed research exists; an alarm is ringing) and
both are read once per request in the service. Named gaps: no property-based fuzzing beyond
the deterministic variants yet; the corpus has no "owner-real" source rows until the owner's
next run is reconciled into it; the owner-audio qualification is recorded only when a
harness posts it with `source=owner_audio` (none does yet); the Living Core (WebGL) has no
state for this — the Cockpit panel is the surface.

Evidence: `tests/voice_corpus/`, `tests/unit/test_owner_utterance_corpus.py` (345 cases +
2 aggregates), `test_voice_corpus_regressions.py` (37), `test_alarms_tr_time.py` (27),
`test_voice_qualification.py` (10), the neighbouring intent suites (469 passed after the
fixes), `apps/web/tests/cockpit/voice-qualification.test.tsx`;
`scripts/core/voice-routing-qualification.ps1`.


## ADR-0081 — M18.4 foundation: the Evolution Supervisor, the owner's voice over self-evolution, the version model, expand-only migrations and the blue/green Cloud Core release (2026-09-07)

Status: Accepted (foundation; the real handoff and the agent/web halves are named as not yet proven)

Context: the owner's directive "M18.4 — CONTINUOUS SELF-EVOLUTION + SELF-HEALING +
ZERO-DOWNTIME UPDATE FOUNDATION" (34 sections). The inventory found most of the machinery
already built and proven in isolation — the authority kernel (ADR-0055), the opportunity
backlog and lifecycle, the M7 sandboxed pipeline with independent review, shadow/canary
runners, risk tiers (ADR-0059), the release executor, the self-healing pipeline and the
recovery supervisor with its real-process end-to-end proof — and four things missing: an
observer that turns the OTHER durable signals into opportunities on a clock; a version
model the owner can ask about; a Cloud Core release that does not recreate the one api
container; and the owner's voice over all of it. `docs/M18_4_SELF_EVOLUTION_SPEC.md` is
the design; this record is what landed and how it is proven.

Decision:

1. **The Evolution Supervisor** (`app/evolution/supervisor.py`): a pure, clock-driven
   observer on the `RoutineClock` (`evolution_tick`, last, every
   `evolution_supervisor_interval_s` = 300 s) that reads self-healing incidents
   (open/recovered → **P0**), recurring failed action receipts (same capability + error
   class ≥ 2 in 7 days → **P1**), recurring research failures (same error class ≥ 2 → **P2**)
   and open generation-resolved capability gaps (**P3**), and opens ONE
   `EvolutionOpportunity` per signal through `create_from_evidence` — evidence-verified,
   scored, audited, deduplicated by the backlog's `(source, source_ref)`. The promotion
   class (`AUTO_SAFE` / `AUTO_CANARY` / `OWNER_APPROVAL_REQUIRED` / `NEVER_AUTO_PROMOTE`) is
   derived from the risk tier of the paths the signal's component maps to — the SAME
   table the release path reads (`app/evolution/risk.py`): any Cloud Core app module is
   tier 3 → owner approval; the authority kernel, the recovery supervisor, schema and
   deployment mechanics are tier 4–5 → never; tests, corpus rows and generated skills are
   tier 2 → canary; docs and web tier 1 → safe. A signal cannot declare its own class.
   The supervisor holds the engine's LAB authority only and never calls `advance`
   (structurally asserted). The owner's pause switch is the latest of two ledger rows
   (`evolution.paused` / `evolution.resumed`, strictly ordered in time); a paused scan
   records nothing and says so; a scan that opened something writes one
   `evolution.supervisor_scanned` row. `GET /v1/evolution/supervisor` (+ `/scan`,
   `/pause`, `/resume`) and `checks.evolution.supervisor` on health expose it;
   `opportunity_dict` carries `priority` and `promotion_class`.

2. **The owner's voice** (`tools_evolution.py`, corpus category `evolution`, 68 cases):
   `Kendi kendini geliştirmeyi duraklat/aç/kapat`, `Kendini geliştirmeye devam et` →
   `evolution.control` (the action is the router's `evolution_action` on the turn record,
   never the model's argument); `Bu geliştirmeyi iptal et` / `Geliştirmeden vazgeç` →
   the ONE lab-phase candidate is rejected `owner_cancelled`, two → a refusal naming them,
   none → a truthful refusal; `Bunu canlıya/yayına alma` → the candidate awaiting
   approval is rejected `owner_held`; `Önceki sürüme dön` / `Eski sürüme geri al` →
   `release.rollback`, always refused with the authority sentence, naming the last-known-good
   when the host exported one; the four questions (`Şu an ne geliştiriyorsun?`, `Son hangi
   hatayı düzelttin?`, `Hangi sürüm çalışıyor?`, `Bekleyen aday sürüm var mı?`) are query
   kinds in the one question table, answered by `evolution.status` from the supervisor's
   rows whichever tool the model picked (`activity.explain` hands over, as it does for
   live state). Every control is a receipt. Action contract v12. The evolution matcher
   runs before the alarm's ringing-aware bare stop, so "kendi kendini geliştirmeyi kapat"
   while an alarm rings is still the switch.

3. **The version model** (`app/release/version.py`): `/v1/system/health.release` and
   `GET /v1/release/current` name the component, the sha the release script exported
   (`PAGENTOS_RELEASE`; `unknown` when unset, never guessed from the tree), the app
   version, every contract version this process serves (action, ui_state, ambient,
   voice_qualification), the exported last-known-good, the start instant and the uptime.
   `GET /v1/release/components` lists the Cloud Core (live), each enrolled device's reported
   software version and capabilities, and the web as `unknown_from_server` (its marker is
   known to the browser). `GET /v1/release/slo` counts release windows and incidents from
   the ledger and reports `availability: null, measurement: "none"` until a prober records
   samples — a fraction is not claimed.

4. **Expand-only migrations** (`tests/unit/test_migration_compatibility.py`): every
   `upgrade()` may add, index and backfill; `drop_table` / `drop_column` / renames /
   NOT NULL need a `contract-phase: ADR-XXXX` declaration; a type change or a constraint
   drop needs a `compat: widening` note beside it. The four historical widenings
   (0013, 0014, 0018, 0023) are so marked. The blue/green drain depends on this.

5. **The blue/green Cloud Core release** (`scripts/cloud/release-cloud-core-bluegreen.sh`,
   `infra/docker/edge/nginx.conf`, compose services `api-blue` / `api-green` / `edge`
   under the `bluegreen` profile): build the image for THIS sha (the old image untouched),
   migrate (expand-only), bring the IDLE colour up while the active one keeps serving,
   verify health / contract / served release ON the idle colour (in-container; the colours
   publish no port), switch the edge's one-line upstream and reload nginx (in-flight
   requests finish on the old upstream), verify through the edge, drain the old colour for
   `PAGENTOS_DRAIN_S` (60 s: in-flight requests, device reconnects, the voice session's
   next tool call), stop it, record `RELEASE` / `LAST_KNOWN_GOOD` / the active colour.
   Rollback is the switch in reverse (the old colour still up during the drain; started
   again after it). A wrong served release (76), an older contract (73) or a colour that
   never answers (75) are refused before the switch. The first cutover stops the legacy
   `api` and starts the edge — one last gap, said so. The Windows driver gains an opt-in
   `-BlueGreen` switch; the default stays the proven single-container path until the owner's
   next authorised release exercises the cutover. Session state lives in Postgres and the
   realtime media path is client ↔ provider, so a voice session survives the colour switch
   by construction; the drain window is what guarantees the tool call in flight.

6. **What is designed and NOT built**, named as such in the spec (§7–9, §13): the Windows
   agent's staged update with supervisor rollback, the browser worker drain, the web live
   update, and probe-based availability.

Consequences: a durable failure becomes an opportunity within one scan, with a priority
and a class the owner can read and a switch the owner can flip by voice; "which version is
running" is a fact on the health surface; the next Cloud Core release can be zero-downtime
by passing one switch, and every migration from here on is expand-only unless it says
otherwise. Nothing here widens the engine's authority: the supervisor proposes, the
lifecycle and the owner decide, and both rollback and promotion by voice are refused with
the reason recorded.

Evidence: `test_evolution_supervisor.py` (10), `test_voice_evolution_tools.py` (26),
`test_release_version.py` (5), `test_release_routes.py` (4), `test_migration_compatibility.py`
(27), the corpus at 413/413 HEALTHY (`evolution` category 68), `cloud-release-bluegreen.tests.ps1`
(18/18 under fakes), web `evolution-supervisor.test.tsx`; the unchanged
`test_selfhealing_e2e.py` remains the real-process self-healing proof (CI integration job).

### ADR-0081 addendum — the closed loop and the production qualification harness (2026-09-07, later the same evening)

The owner's follow-up directive ("M18.4 PRODUCTION QUALIFICATION — PROVE SELF-EVOLUTION
FOUNDATION FOR REAL") asked for the real pipeline behind an opportunity, not a scripted
result. What landed:

- **`app/selfhealing/closed_loop.py`** + `POST /v1/selfhealing/opportunities/{id}/heal`: an
  incident-born opportunity is driven through the M6 self-healing pipeline's REAL phases,
  and its lifecycle follows each gate as it lands — `load_incident` → researching,
  `analyze_issue` → design_ready, `patch` → building (an isolated work directory),
  `regression_on_candidate` → testing, `review_change` → evaluating (the independent
  reviewer), `staging_deploy` → shadow_ready (green under the staging health policy); the
  component's release is promoted by the supervisor deployer and the opportunity ends at
  `owner_approval_required`, because LIVE is the owner's whatever the promotion class says
  (ADR-0055). A failed gate parks the candidate `quarantined` (from build/test/evaluate) or
  `rejected` (from research/design) with the failing step in the transition reason; the
  incident stays recovered and the component's active release is untouched. The seam is
  `SelfHealingPipeline(on_step=...)` (`_ObservedSteps`): the steps arrive as they happen,
  nothing is replayed after the fact, and the lifecycle table decides what is legal.
  `build_pipeline` in `app/selfhealing/routes.py` is now the ONE construction path for
  both routes.
  The loop lives on the self-healing side of the authority wall: the lab's import guard
  (`test_no_evolution_module_imports_a_deployment_release_or_secret_module`) refused the
  first placement under `app/evolution` — correctly, since the pipeline deploys — so the
  side that deploys hosts the loop and reads the lab's service, never the reverse.
- **`tests/unit/test_evolution_closed_loop.py`**: real processes — the recovery
  supervisor (subprocess) promotes 1.0.0, detects broken 1.1.0 and rolls back; the report
  is ingested; the supervisor scan opens the P0 opportunity; `/heal` runs the real
  pipeline; the lifecycle reads idea → researching → design_ready → building → testing →
  evaluating → shadow_ready → owner_approval_required with dated reasons; releases show
  1.1.0 rolled_back / 1.1.1 active; the incident is fixed; `evolution.shadow_ready` and
  `evolution.owner_approval_required` ledger rows exist; the Approval Center and the
  supervisor status list it. Then a deliberately broken candidate (self-test raises)
  passes regression and review and FAILS on staging: quarantined with
  "gate 'staging_deploy' failed", its release rejected, the component still on 1.1.1.
  22 s, SQLite, loopback.
- **`scripts/core/qualify-m18-4.ps1`**: the production qualification, unattended once the
  host is reachable over Tailscale SSH (`-WaitForHost` polls): a health prober at 250 ms
  through the canonical edge measures every phase (gap = first failed probe to the next
  success; dropped = failed probes); phases: snapshot → the FIRST blue/green cutover via
  `release-cloud-core.ps1 -BlueGreen` (a gap is expected and recorded) → a
  controlled-failure release whose post-switch verification is pointed at an unreachable
  URL (the script switches back; expect zero dropped probes) → an explicit `--rollback` to
  the other colour and the roll-forward (expect zero dropped probes) → the supervisor's
  first scan of REAL production signals, pause → skipped_paused → resume → scanned, with
  production health checked while paused → reconciliation (release/contracts/edge
  colour/RELEASE and LAST_KNOWN_GOOD on the host/devices/SLO). Evidence:
  `docs/evidence/m18-4-qualification-<stamp>.json` plus the probe logs.
- `release-cloud-core-bluegreen.sh --rollback` now records what actually runs: RELEASE
  becomes the sha the colour returned to was released with, LAST_KNOWN_GOOD the sha it
  left (the file it used to copy could name a build no colour was running). The edge
  directory defaults to the persistent volume path compose mounts and is pinned into the
  env file.

Why the production loop is still `PROVEN_PROXY`: the production image carries neither the
recovery supervisor script nor the synthetic target service (`checks.selfhealing` reports
both absent), so the synthetic self-healing story runs with real processes on the
development machine and in CI, not inside the Hetzner container. Shipping those two files
in the image is a deployment-mechanics change (tier 4) deliberately not made in the same
evening as the first cutover.

### ADR-0081 addendum 2 — the production qualification: what was measured, what broke, what is proven (2026-09-07, night)

Three runs of `scripts/core/qualify-m18-4.ps1` against the Hetzner host, once the owner had
completed the Tailscale SSH check (a browser login; the one human action of the evening).
Every number below is from `docs/evidence/m18-4-qualification-2026-09-07-*.json` and the
probe logs beside them (a probe every 250 ms through the canonical edge; "dropped" = probes
that did not get a 2xx; "gap" = first failure to next success).

**Run 1 (14:58Z, sha 396deae) — the FIRST cutover.** Production moved from the single
`api` container (contract v8) to the edge + `api-blue` (contract v12); migrations 0023 and
0024 applied; alarms 1 → 1, routines 1 → 1. The first cutover's gap, from the legacy
container's exit to the edge's start, was **4.1 s** by the containers' own timestamps
(15:03:05.26 → 15:03:09.39); bounded above by 14 s if uvicorn held the socket through its
stop grace. The prober's numbers from this run are void: it constructed
`System.Net.Http.HttpClient`, which Windows PowerShell 5.1 does not load, so every probe
failed before the network. The device rows were empty for a second harness defect (below).

**Run 2 (15:11Z, sha ea92674).** Release: 234 probes, 1 dropped, gap 3.28 s — the drop is a
timeout at 15:11:37, fourteen seconds in, during the image build on the host, long before
the switch. Controlled-failure rollback 63/0, explicit rollback 25/0, roll-forward 113/0.
This run found the real bug: the rollback path removed `/opt/pagentos/app` from inside it
(the script had `cd`'d there for the build), so every `docker compose` call after the `mv`
failed with `getwd: no such file or directory`, the edge was NOT switched back, and the
marker (green) disagreed with nginx (blue) until the next explicit rollback corrected it.
No request failed — the colour nginx kept serving was healthy — but the rollback had not
happened. Fixed: both rollback paths `cd "$base"` first; the fake docker now refuses a
deleted working directory too.

**Run 3 (15:22Z, sha c109302) — 23/23 PASS.** Release 247/0/0 s; controlled-failure
rollback 92/0/0 s with the edge switched back and the idle colour stopped as designed;
explicit rollback 37/0/0 s; roll-forward 99/0/0 s. The Windows agent was online on the
answering colour at the first check after every switch (it reconnects within seconds of
the old colour stopping). Supervisor: 2 real signals, 0 opened, 2 already tracked (the
clock's own first scan after the first cutover had opened them: a P1 for recurring
`eye.enable` `capability_missing` receipts and a P2 for recurring
`insufficient_valid_findings` research failures — both from the owner's real runs of
2026-09-06). Pause → `skipped_paused` with production health `ok` → resume → `scanned`.
Final: `health.release.version` = c109302, contracts action 12 / ui_state 3 / ambient 1 /
voice_qualification 1, `RELEASE` and `LAST_KNOWN_GOOD` on the host and in the env file,
`/edge/active` = blue, images for c109302, ea92674 and the v8 `:local` retained.

**Harness defects found and fixed, with the rule each taught:** (1) PS 5.1 has no
`HttpClient` — probe with `HttpWebRequest`; (2) `@(Get-ArrayProperty ...)` wraps the
returned array as ONE element (measured on the live listing: count 1, `Object[]`), the bare
assignment receives the elements — never wrap a call that returns `, @(...)` in `@()`;
(3) never `cd` into a tree a rollback may remove.

**A finding recorded as an opportunity, then corrected.** Device presence is the
answering process's in-memory WebSocket set, so during the drain window a read or a
command routed to the NEW colour sees the agent offline while its socket still ends on
the OLD colour; the agent moves within seconds of the old colour stopping. The first
opportunity for this (`m18-4:device-presence-during-drain`) stated a 1–3 minute lag from
the empty device rows and was rejected with that reason; the corrected one
(`...:v2`, P2, owner approval required) names the drain window as the bound and the fix
direction (close device sockets with a reconnect close code at the start of the drain).

**Marks (spec §19 / directive §9), from this evidence and nothing else:**

| Capability | Mark | Evidence |
|---|---|---|
| EvolutionOpportunity detection | PROVEN_REAL | two opportunities opened by the clock-driven supervisor from production ledger rows; deduplicated across three later scans |
| Isolated candidate creation, autonomous bug fix, regression retest, shadow | PROVEN_PROXY | real processes (`test_evolution_closed_loop.py`, `test_selfhealing_e2e.py` in CI); the production image carries neither the supervisor script nor the synthetic service |
| Canary | PROVEN_PROXY | `CanaryRunner` over eval cases; not exercised on production |
| Blue/green | PROVEN_REAL | first cutover 4.1 s gap (by design, once); two later releases 0 dropped of 247 and 234 (one build-time timeout) |
| Rollback | PROVEN_REAL | controlled failure: automatic switch-back 92/0; explicit `--rollback` 37/0 and 25/0 |
| Last-known-good | PROVEN_REAL | host markers, env file and `health.release.last_known_good` agree; the rollback used the colour's recorded sha |
| Reboot / interrupted-promotion recovery | NOT_YET_PROVEN | not simulated on production |
| Windows agent staged update, browser worker staged update | NOT_YET_PROVEN | designed (spec §7–8), not built |
| Owner pause/resume | PROVEN_REAL (REST on production) + PROVEN_AUTOMATED (voice path, corpus) | run 3 phase E; corpus category `evolution` |

**M18.4 SELF-EVOLUTION FOUNDATION CLOSED** on this evidence: every non-human engineering item
is built, gated and on production; the four NOT_YET_PROVEN rows are named designs, not
claims. Deployed-version reconciliation: Cloud Core c109302 (contract v12) on `api-blue`
behind the edge; Windows agent 0.1.0 on device MAIL (29 capabilities, connected; the M18.3
capabilities wait on the owner's elevated update, item 26); browser worker present through
the agent's `browser.*` capabilities, version not reported separately; web/Living Core
served locally, marker unknown to the server. Named gaps carried forward: a device-bound
command during the drain window; the image build on the production host (one 3 s timeout);
the recovery supervisor and synthetic service absent from the image; SLO availability still
unmeasured (counts only: 4 releases, 4 rollbacks in 24 h, all from this qualification).

### ADR-0081 addendum 3 — M18.4 final gap closure: the device handoff, the interrupted promotion, the agent and worker halves, one real lifecycle (2026-09-07, night)

Status: Accepted (owner directive "M18.4 FINAL GAP CLOSURE — BEFORE M19", 2026-09-07 night). Supersedes the four NOT_YET_PROVEN rows of addendum 2 where this addendum's evidence says so, and nowhere else.

**Gap 1 — device presence during the drain (opportunity `m18-4:device-presence-during-drain:v2`).**
Addendum 2 measured it: the old release moved HTTP and the device WebSocket together and left the device sessions on the old colour until it stopped, so for the drain window the new colour was authoritative for device commands while `is_online` on it said offline. The fix is in the canonical architecture, not a health override: the edge now has two upstreams (`pagentos_api` for everything, `pagentos_devices` for `/v1/devices/connect`), and the release moves DEVICE authority first — the device upstream to the idle colour, then the active colour drains (`POST /v1/devices/drain`, loopback-only like the identity bootstrap: every device socket closed with 1012, presence dropped at once, new device connections refused with 1012; `/undrain` is the exact reverse for rollback and reconcile), then the script waits until the idle colour's own health shows the sessions (`checks.broker.active_sessions`, exit 79 → rollback when they never arrive), and only then moves HTTP. The agent's reconnect does the rest: its first retry after a server close is inside one second (`BackoffPolicy`, attempt reset on authentication). Rollback and `--rollback` reverse the same handoff, undraining first.

A second real defect surfaced on the way: the edge's `nginx.conf` was a bind mount of the tree's file, and after the tree swap the running container kept the OLD inode — no nginx.conf change could ever reach a running edge. The edge now starts on a copy in the persistent edge dir (`-c /etc/nginx/edge/nginx.conf`) which the script refreshes on every release / rollback / reconcile and reloads; compose recreates the container only when its definition changed (once, in run 4, said out loud).

**Gap 2 — interrupted promotion recovery.** `release-cloud-core-bluegreen.sh --reconcile` rebuilds the canonical state from the marker, `RELEASE` (the sha of the last COMPLETED promotion), the env file's per-colour shas, the containers and their health. The rule: the last completed promotion is canonical even when the edge already names the candidate; a colour running a sha `RELEASE` does not name is a half-promoted candidate — drained, stopped, its tree kept aside as `app.interrupted` — and never made live by this path; a canonical colour that cannot start falls back to the other recorded colour LOUDLY (exit 81, marker and RELEASE made to say so). `PAGENTOS_INTERRUPT_AT` is the controlled SIGKILL (`kill -9 $$`, no trap runs) for the proof; `infra/systemd/pagentos-bluegreen-reconcile.service` runs the reconcile once per boot after docker, from `app` or `app.prev`.

**Gap 3 — Windows agent staged update.** The installer already staged, swapped through the journaled engine and proved the live worker. Added (`scripts/lib/AgentUpdate.ps1`): a candidate manifest written at staging — the staged service binary's own `capabilities` verb (which now names `software_version`; the agent is 0.2.0), every staged file hashed, the browser worker's release identity — and re-verified file by file right before the engine moves anything; and `Test-AgentHeartbeatOnCore`, run inside the engine's health handler: Cloud Core must list the device online with the candidate's version and every promised capability, or the engine rolls back to the previous trees. The owner session for that read comes from the DPAPI credential; without it the read is reported skipped, never faked.

**Gap 4 — browser worker staged update.** `BrowserWorkerHost.SwapWorkerAsync`: a candidate worker starts BESIDE the current one and must say hello, keep every capability the current worker serves (and the browser), and match the expected release when named; then the current worker drains — in-flight requests finish on it, a request arriving during the drain waits on the start lock and lands on the candidate, it must hold no open browser session (`browser.worker_status`; owned media stays untouched: the swap answers `busy` and is retried later) — shuts down gracefully and retires; new work routes to the candidate. The candidate is never handed a request while the old worker lives (one profile, one Chrome); the owner's own browser is never touched. The trigger is a request file in the companion's data directory (`BrowserCandidateWatcher`, polled every 5 s) whose candidate must live under the admin-only install root; the outcome is written beside it.

**Gap 5 — one real lifecycle.** Opportunity 57674b6a (`m18-4:device-presence-during-drain:v2`, P2, OWNER_APPROVAL_REQUIRED, opened by the harness from production ledger rows in addendum 2) walked idea → researching → design_ready → building → testing → evaluating (twice back to building, on the run 5 and run 6 findings) → shadow_ready → owner_approval_required, every transition a REST call with the commit, the CI run and the evidence file as its refs (`scripts/core/evolution-advance.ps1`). There it stopped — and found a real product gap: `authorize` (owner_approval_required → owner_authorized, ADR-0055 §5, the second explicit confirmation for a tier-3 candidate) and `record_release_footprint` (the derived risk tier) existed in the service alone, with no REST route, so no client could carry a tier-3 candidate past the owner's line. Both routes were added (`POST /v1/evolution/opportunities/{id}/footprint`, `POST …/authorize`), tested (`test_evolution_routes.py`: tier derived from the paths, the first authorisation refused with the tier named, the deliberate second call recorded against the owner's session, the footprint frozen past the line), released through the same blue/green path, and the lifecycle then completed on production: footprint (tier 3) → owner_authorized (the owner's session, second confirmation, under the owner's directive to verify on production) → qualifying → deploying → verifying → live — see the closing note below. The lifecycle rows are real; the autonomy is not: the patch was authored by the engineer, there is no coding backend for Cloud Core code — PROVEN_PROXY for isolated candidate / autonomous fix / regression / shadow / canary, unchanged from addendum 2.

**What run 4 found (docs/evidence/m18-4-qualification-2026-09-07-163343.json).** Phase G at after_idle_up passed on the host (both colours up, the reconcile made c109302 canonical, the candidate drained and stopped, the tree restored, 0 dropped probes, device gap 0 s). Every later switch failed inside `reload_edge`: with the edge started on `-c /etc/nginx/edge/nginx.conf` (pid /tmp/nginx.pid), `nginx -s reload` without -c looked for /run/nginx.pid — exit 77, api-blue kept serving, not one device-offline poll (fixed a5bd0e8: -c on the signal too; the fake docker cannot see this, recorded as its limit). The OLD tree's script, run by the harness for the later phases, left the marker and upstream file naming a stopped colour; the host was repaired by hand.

**Runs 5–7; the closing run 7 (docs/evidence/m18-4-qualification-2026-09-07-173803.json).** Run 5 (170850) proved the interrupted promotion at after_idle_up and found that the handoff cannot START from the live colour c109302, which predates the drain route (POST /v1/devices/drain answers 405): the script now reads the drain's HTTP status and falls back to the legacy switch out loud (e003e41). Run 6 (172512) proved after_switch too and found a race the earlier runs had not hit: `nginx -s reload` only sends the signal, and the probe through the edge one second later still reached the old workers, so a good release was rolled back (76); the post-switch probe is a bounded settle wait (ae88edd). Run 7 (173803), 50 checks, 0 failed: G after_idle_up and after_switch reconciled with 0 dropped probes (device gaps 0 s and 5.68 s); the release of ae88edd 246 HTTP probes / 0 dropped, the one-time LEGACY switch (presence gap 61.49 s = the 60 s drain window + the agent's reconnect, named by the script and the harness); the controlled-failure rollback 141/0 with the devices taken back FIRST (1/1 after 1 s; presence gap 2.23 s); the explicit rollback 49/0 (handoff 1/1 after 1 s; gap 2.23 s); the roll-forward 112/0 (handoff 1/1 after 0 s; gap 1.12 s); the agent online through the edge after every switch; supervisor 2 signals / pause / resume; the reconcile unit installed, enabled, RECONCILE OK from its journal; health.release == ae88edd, contract v12, last-known-good exported. Each failed run left the edge files naming a stopped colour (the OLD tree's script, whose pre-switch rollback restores neither); repaired by hand three times, and the harness now runs HEAD's script for every phase.

**Marks (only the four words the directive allows):**

| Gap | Mark | Evidence |
|---|---|---|
| 1 Device presence during drain | PROVEN_REAL | run 7: the controlled-failure rollback took the devices back first (1/1 after 1 s, presence gap 2.23 s, 141/0 HTTP); the explicit rollback handoff 1/1 after 1 s (gap 2.23 s, 49/0); the roll-forward handoff 1/1 after 0 s (gap 1.12 s, 112/0) — a 1–2 s gap that is the agent's own reconnect, not the drain window; the one-time legacy release from c109302 named its 61.49 s gap |
| 2 Interrupted promotion recovery | PROVEN_REAL | runs 5, 6, 7: SIGKILL after_idle_up (three times) and after_switch (twice — the edge already on the candidate, both colours up, RELEASE unwritten) → `--reconcile` made the last completed promotion canonical, drained and stopped the candidate, restored the tree; 0 dropped probes; device gaps 0 s / 5.68 s |
| 2 Reboot recovery (the unit at boot) | PROVEN_PROXY | the unit installed, enabled and run once on the consistent host from its own journal; no autonomous reboot of production |
| 3 Windows agent staged update | PROVEN_PROXY | `agent-update.tests.ps1` 16/16 (manifest round-trip, tamper/missing/extra file, browser digest, heartbeat wait / version / capability / offline / unreadable); the real run is the owner's elevated update (item 26) |
| 4 Browser worker staged update | PROVEN_PROXY | `BrowserWorkerSwapTests` 12/12 over the real fake worker (drain of an in-flight request, late request lands on the candidate, failed/rejected candidate keeps the old, busy on an open session, request-file guard); agent suite 397/397 |
| 5 Autonomous candidate / bug fix / regression / shadow / canary | PROVEN_PROXY | unchanged from addendum 2: the patch was authored by the engineer under the owner's directive; the lifecycle rows are real, the coding backend for Cloud Core code is not |

## ADR-0082 — M19 Digital Operator: one interactive path, a focus guard, and a task that verifies its own postconditions (2026-09-07, late night)

Status: Accepted (owner master directive "CLOSE M18.4 AND COMPLETE M19 -> M28", 2026-09-07). Spec: `docs/M19_DIGITAL_OPERATOR_SPEC.md`.

**Context.** The owner's environment must become operable — apps, windows, keyboard, pointer, UI Automation, screen, files, a governed terminal — without a second desktop-control path and without the model assuming a click worked. The Session Companion already is the only process in the owner's session that executes interactive capabilities (DEVICE_PROTOCOL §9); the action contract already makes every mutation a receipt with an observed_after (ADR-0063); object focus is already ID-based and durable (ADR-0076).

**Decisions.**

1. **Every operator capability is an interactive-family name executed by the companion** (`AgentCapabilities.Operator`, gated by `OperatorEnabled`), reached only through a device command. No REST route, script or worker touches the desktop; the Device Service routes the family over the pipe like the desktop family and refuses everything else.
2. **The focus guard is a refusal, never a retry into whatever is in front.** Before keyboard or pointer input the companion compares the expected window (handle, pid, image, title prefix from the plan's OBSERVE) with the actual foreground window; a mismatch answers `focus_mismatch` carrying both. `keyboard.type` refuses `secret: true` payloads outright and every payload passes the forbidden-key scan: secrets are the password manager's job.
3. **A task is OBSERVE → PLAN → ACT → OBSERVE AGAIN → VERIFY.** `OperatorTask` (Cloud Core) executes deterministic plans step by step; each step names its postcondition over the RE-OBSERVED result (a window state, a read-back value, an exit code), retries at most twice, times out, can be cancelled, and reports `postcondition_failed` when the world did not change as planned. A step's success is never inferred from the command having been accepted.
4. **Interaction levels are ordered and recorded.** Official API → browser DOM → UI Automation → keyboard navigation → visual targeting → raw coordinates, last; the receipt says which level acted.
5. **The terminal is an allowlist.** `terminal.execute` runs only read-only allowlisted commands, headless, with a timeout; anything else is `permission_denied` before a process exists.
6. **Object focus generalises.** One durable `object_focus` stack (kind, object id, label, source, selected_at) carries windows and apps in M19 and files, documents, mail, artifacts, projects, builds in M20–M28; `research_focus` is untouched. "Bunu kapat" resolves the current window by id, never by title.
7. **The lab is real and harmless.** The M19 test lab runs real Notepad / Explorer / PowerShell / the browser worker on this machine in the owner's session, touches only windows it created, and reads every effect back (UI Automation values, window rects, exit codes). PROVEN_REAL for the mechanisms; the deployed-agent path stays PROVEN_PROXY until the owner's elevated update ships 0.2.0 with `-Operator`.

**Consequences.** Contract v4 of the UI state adds `operator.running/verifying/failed`; the corpus gains the `operator` category; the installer gains `-Operator`; the companion gains an `Operator/` module with Win32 + UI Automation code and a per-command cap of 30 s. Nothing here grants the system more authority than the owner's session already has.

**Closing note (runs 8 and 9; the lifecycle LIVE).** Run 8 (`m18-4-qualification-2026-09-07-175914.json`, 49/50) was the first with a drain-capable colour live: the interrupted promotions at after_idle_up and after_switch reconciled with the real handoff (presence gap 1.11 s during the after_switch reconcile); the release of 1072d0d handed the device sessions over BEFORE HTTP (1/1 after 0 s, presence gap 1.12 s, 245 HTTP probes); the controlled-failure rollback 139/0 (devices back first, gap 2.23 s); the explicit rollback 47/0 (handoff 1/1 after 1 s, gap 2.23 s); the roll-forward 114/0 (handoff 1/1 after 1 s, gap 2.25 s). The one failed check was one probe of 245 that took longer than 3 s, 24 s into the release, during the image build — the same shape as run 2; the prober now measures latency per probe and calls a drop only an answer that never came within 10 s. Run 9 (`m18-4-qualification-2026-09-07-182258.json`), 50 checks, 0 failed, with the latency-aware prober: the release of 338ffec 239 HTTP probes / 0 dropped / max latency 2189 ms / 0 slower than 3 s, the device sessions on the new colour BEFORE HTTP (1/1 after 1 s, presence gap 2.26 s); the interrupted promotions reconciled with 0 dropped (presence gaps 0 s / 1.2 s); the controlled-failure rollback 140/0 (devices back first, gap 2.23 s); the explicit rollback 49/0 (handoff 1/1 after 1 s, gap 2.25 s); the roll-forward 117/0 (handoff 1/1 after 1 s, gap 2.24 s); the reconcile unit RECONCILE OK from its journal; release == HEAD, last-known-good exported.

The lifecycle then found two more product gaps past the owner's line, both fixed and released as part of this closure: (1) `deploying` requires an owner-approved release and the production build carried the null evidence provider — the ledger is now the provider (`app/evolution/release_evidence.py`: an opportunity is release-ready exactly when a `deployment.cloud_core.released` row names its candidate's sha, rows the harness records under the owner's session after the switch was verified through the edge; LIVE points at that row); (2) the CLI passed a comma-joined footprint as ONE "path" that matched no risk rule and derived tier 2 for a tier-3 change, so the first authorisation was accepted without the second confirmation — the route refuses a path with commas or whitespace (422) and the CLI splits its list; the production row keeps the tier it recorded (a footprint is frozen past the line), which is why its `owner_authorized` transition carries no `second_confirmation_given`. With the ledger provider live (run 9), opportunity 57674b6a walked qualifying → deploying (release ref = the ledger row of the 1072d0d release) → verifying → live over REST with the run 8/9 evidence as its reasons: the first production-originated opportunity to complete the whole lifecycle on the real Cloud Core. The lifecycle rows are PROVEN_REAL; the autonomy of the candidate and the patch stays PROVEN_PROXY (the engineer authored the fix under the owner's directive; no coding backend for Cloud Core code).

### ADR-0082 addendum 1 — the device half as built (2026-09-07, track A)

Status: Accepted. Implementation choices made autonomously on the agent side, each reversible and consistent with the decisions above; recorded so the Cloud Core half and the integrator meet the device as it is.

1. **The family runs on the companion's long-running path** (the one the browser family and `desktop.play_audio` use), not the synchronous read loop: a launch waits up to 10 s and a terminal command up to 30 s, and the heartbeat's status request must never queue behind them. The operator serialises its own actions with one gate, so "concurrent" means only that the pipe loop stays responsive. The pipe deadline becomes the action's budget (the browser's 500 ms headroom rule, unchanged); budget expiry is `timeout`, a caller's cancel is `cancelled`, and a `cancelled` a helper raised on the budget's token is re-classified as `timeout` before it leaves.
2. **The three error classes live in three places at once** — `ErrorClasses` (the agent's validator), `packages/schemas/device-protocol.schema.json` (the contract) and `services/api/app/broker/frames.py` (`ERROR_CLASSES`, the broker's validator) — because either validator would otherwise refuse a device ack carrying `focus_mismatch`, `permission_denied` or `postcondition_failed`. A test reads the schema and asserts the C# set equals it.
3. **UI Automation comes from the WPF profile of the Windows Desktop framework** (`<FrameworkReference Include="Microsoft.WindowsDesktop.App.WPF" />`), the smallest reference that carries `UIAutomationClient`/`UIAutomationTypes`; `UseWPF` is not set and the companion stays a console process. The installed machine needs the Windows Desktop runtime (present on the owner's machine, 10.0.11; the installer should verify it before 0.2.0 ships with `-Operator`).
4. **`ui.set_value` has a semantic fallback.** Classic Notepad's multi-line edit is a UIA `Document` with a Text pattern and no Value pattern (found by the lab). Rather than fall back to synthesised keystrokes, the companion sends `WM_SETTEXT` to the element's native text window — a message to the control, no focus needed, no input event — and reads the value back through the Text pattern.
5. **The terminal child's PATH starts with System32.** A spawned shell on the owner's machine has been seen with no System32 on PATH, and `hostname` was "not recognised". The runner prepends the system directories so an allowlisted name resolves to the system binary regardless of the owner's PATH — which is also the right posture against a same-named executable earlier on a tampered PATH. Output is forced to UTF-8 (the headless code page is 437/857 and garbles Turkish file names).
6. **`file.open` accepts an optional `application`** (an allowlisted name) so a plan can open a document in a chosen app deterministically; without it, ShellExecute as the spec says. `ui.inspect` accepts an element query (`automation_id` / `name` / `name_prefix` + `control_type`) to start the bounded walk at that element — the same query `ui.invoke`, `ui.set_value` and `ui.select` use.
7. **The lab skips, never lies.** `[LabFact]` computes the skip at discovery: `Environment.UserInteractive`, a visible window station (`GetUserObjectInformation` `WSF_VISIBLE`), a foreground window, `notepad.exe` present, and `PAGENTOS_OPERATOR_LAB=0` as an explicit off switch; the reason names the condition. On the owner's machine nothing is skipped (47/47, real Notepad and Explorer, 7 s).

Marks: the mechanisms (every family member through the real dispatch with a re-observed result; the focus guard refusing a wrong-window type with the front window's document read back empty; secrets refused; modal detection and dismissal through `ui.invoke`; cancellation and timeout typed) are PROVEN_REAL in the lab on this machine; the service→pipe→companion→operator chain PROVEN_REAL over the real pipe in-process; the deployed-agent path stays PROVEN_PROXY until the owner's elevated update ships 0.2.0 with `-Operator` (item 26/27), and CI on `windows-latest` is NOT_YET_PROVEN from this worktree (nothing was pushed).

**Addendum — the web side of contract v4 (track C, 2026-09-07).** Recorded here rather than as a new ADR because every choice is a reading of decision 3 and of ADR-0052's rule that the Core shows only what was published.

1. **`operator.*` is a core channel of its own.** `stateChannel` returns `"operator"` for the family and `isCoreChannel` includes it, so the Core body draws the newest of agent / lab / operator by sequence and nothing else: an `operator.failed` is replaced by the next `agent.idle` exactly as `agent.error` is, and the room (`owner.*`, `display.*`) never displaces a step in flight. It is named separately so the cockpit can ask "what is the operator doing" by membership (`isOperatorState`), never by prefix — a newer server's `operator.cancelled` reaches the Core as `unknown_state` and the panel ignores it.
2. **Kinds and horizons.** `running` / `verifying` are transient; `failed` is steady (held until replaced). The step horizon is `OPERATOR_STEP_TTL_MS = 45 s`, not the 12 s transient default: the publisher speaks once per task transition and the companion's per-command cap is 30 s (§3), so the default would report a healthy twenty-second `app.launch` as lost. The publisher's `ttl_s` still wins.
3. **Three visual kinds, not one.** `operator_running` is the tool-execution posture (M18.1 §4) a shade more open, `operator_verifying` the same work turned inward (the second OBSERVE), `operator_failed` the error posture with restraint. The caption is `metadata.step` verbatim (else the publisher's label, else nothing); the capability, the observed window title and the error class are carried as words on `VisualIntent` and printed by the readout and the panel as sent or as "bildirilmedi". No progress is drawn: none is published.
4. **The voice overlay yields to a live operator body.** A local `tool_running` and a bus `operator.running` describe the same moment, and the bus knows the step and the window; `applyVoiceOverlay` keeps the bus body only while it is live (`isOperatorActing`), so a last-known operator shape, a failure and every other bus state keep ADR-0061's precedence.
5. **The panel reads the bus, not REST.** `DigitalOperatorPanel` takes `CoreTruth` like `RunningToolsPanel`; there is no `/v1/operator` status route and inventing one client-side would be a contract nobody serves. `useCockpitData` says so in place.
6. **Version lag is named per family.** `KNOWN_CONTRACT_VERSION = 4`, `MIN_SUPPORTED` stays 2; a v3 server is `older_supported` and `contractLagNote` names exactly the families between the versions ("dijital operatör durumları" for v3, alarm/display too for v2) — an absent operator row must not read as "the operator never ran".
7. **`metadata.step` is read in both shapes, and the label is printed bare.** The core track's `OperatorService` publishes the step as its zero-based INDEX (`step: <n>` from `enumerate(task.steps)`, `step_count` beside it, the step's name as the event `label`; the task-level start and failure events carry the plan's name as the label and, on failure, `error_class` alone). The client reads a string `step` as the step's name and a number as its index (`OperatorFacts.stepIndex` / `stepCount`); the Core's caption is the name, else the publisher's label, else "adım 1/3" from the index — and the panel's line writes the index the owner's way, `adım 2/3` for index 1 of 3. The label is printed without a "görev:" prefix because whether it is the goal or the step's name belongs to the publisher. The two tracks were written against the spec, not each other; if the core side moves to a string `step`, nothing here changes.
