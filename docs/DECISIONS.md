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
