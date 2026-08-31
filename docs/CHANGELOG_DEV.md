# Development Changelog

Autonomous engineering agents append concise accepted-change records here.

## 2026-08-31 — M4 voice core & narration complete (local-first)

- Deterministic tr-TR narration normalizer + Turkish number engine (date/clock/decimal/percentage/lira/currency/thousands/ordinal/phone/IP+CIDR/version/email/URL/path/abbreviation/mixed-TR-EN), 72-case machine-readable eval dataset (evals/voice); semantic table/code narration.
- Narration engine: stable section/paragraph/sentence cursor IDs, chunk planning + cancellation, TTS Synthesizer seam; oku/dur/devam/tekrar/jump/explain command state machine ("dur" top priority, explain returns to exact cursor); cross-device cursor persistence (narration_sessions); pronunciation dictionary CRUD.
- Provider-neutral TTSProvider/STTProvider/RealtimeProvider interfaces with deterministic fakes + HTTP-mocked real adapter skeletons (ElevenLabs/Azure/OpenAI, Faster-Whisper fallback), inert without keys; benchmark harness comparing >=2 providers (STT WER/CER + TTS) with reports to object store; capability-aware provider fallback router.
- OWNER/NOT_OWNER/UNCERTAIN speaker classifier (cosine sim + threshold band + required device-trust second factor; encrypted derived embeddings, never raw audio); voice preferences (explicit overrides inferred); barge-in realtime state machine.
- alembic 0004 (reversible). 340 unit + 40 integration API tests; full M0-M4 regression gate 11/11 PASS.
- Independent verification: all 9 M4 acceptance criteria PASS. Security review: no critical; 1 high (speaker-verification device-trust must be server-derived before it gates a privileged action — tracked hard-gate with the API auth layer) + 3 low all fixed same-day. docs/reviews/M4_SECURITY_REVIEW.md. ADR-0022.
- Owner actions batched: real voice-provider API keys and owner speech samples for the real-audio A/B quality benchmark and real speaker enrollment (plug into the same interfaces).

## 2026-08-31 — M3 research & artifact complete

- Durable Temporal ResearchWorkflow (provider-neutral ResearchProvider; offline DeterministicResearchProvider wired, WebResearchProvider seam) producing a Turkish-first canonical Markdown artifact with executive summary + detailed body + scored/deduped sources.
- Artifact service (Task->Artifact->Presentation): content-hash versioning, deterministic PDF/DOCX/HTML/TXT renderers stored in MinIO; PDF embeds bundled DejaVu Unicode font for full Turkish (no ASCII fold). REST for tasks/artifacts/canonical/renders; executive_summary column enforces READY-without-auto-read (body only via ?include=body or /canonical). alembic 0003.
- Windows desktop.open_artifact capability with defense-in-depth allowlist (path containment incl. ancestor-junction walk, executable denylist, extension allowlist); 91 xUnit tests.
- Web Artifact Inbox (apps/web /artifacts): topic input, readiness-only polling ("Rapor hazir"), executive-summary list, per-format download links; scoped CORS added (resolves deferred M0 finding #3).
- Verified live end-to-end in a real browser (topic -> READY -> exec summary -> PDF/DOCX). 132 unit + 28 integration Python tests; full M0-M3 regression gate 11/11 PASS.
- Independent verification PASS (all criteria). Security review: no high/critical; 2 medium findings fixed same-day (HTML injection neutralization, open_artifact ancestor-junction), docs/reviews/M3_SECURITY_REVIEW.md. ADR-0020/0021.

## 2026-08-31 — M2 browser agent complete

- services/browser: semantic-only browser automation (Playwright 1.62.0) with a BrowserBackend adapter seam — ManagedBackend (isolated + persistent dedicated profile, real-profile guard) and ExistingSessionBackend (enrollment-only, loopback-only CDP, reconnect); visual/coordinate automation reserved as a future separate adapter.
- Owner-browser attach modeled as explicit BrowserEnrollment (authorization record + capability grants), never raw debug-port exposure; extension_bridge transport reserved. Per-backend BrowserCapabilities (authenticated_session/downloads/uploads/extensions/existing_tabs/multiple_windows/visual_fallback) queryable by the orchestrator.
- Typed BrowserError taxonomy; retryable-only with_retry; BrowserCommandExecutor with cancellation and idempotency (op-fingerprint bound) mirroring the device protocol.
- Full deterministic scenario matrix tested: navigate, back/forward, tabs, text/click/select/checkbox/radio, form submit, SPA, iframe, popup, download+upload (hash-verified), typed browser error/timeout, stale-element recovery, ambiguous locator, browser crash + reconnect (managed relaunch and CDP reattach), cancellation, idempotency. 75 unit + 39 browser tests.
- Independent verification: all M2 acceptance criteria (architecture gates + scenario matrix) reproduced PASS, zero defects. Security review: 1 high + 1 med-high + 3 low findings all fixed same-day (loopback IP-literal gate, URL scheme allowlist, URL redaction, file_io_root confinement, op-fingerprint idempotency), 1 low tracked (docs/reviews/M2_SECURITY_REVIEW.md).
- Full M0+M1+M2 regression gate 11/11 PASS. ADR-0018/0019. CI browser-agent job added.

## 2026-08-31 — M1 cloud/device link complete (local-first)

- Device protocol v1 (packages/protocol + JSON schema): outbound-only WS, ECDSA P-256 challenge handshake, heartbeat presence, at-least-once delivery + agent idempotency, expiry/cancel semantics, audit requirements.
- Device broker as app/broker module in services/api: enrollment (single-use hashed tokens), WS handshake with indistinguishable rejection, presence, durable dispatch + redelivery, monotonic ack machine, server-side expiry sweeper, audit trail, stats; alembic 0002.
- Windows Device Agent (.NET 10 LTS, devices/windows-agent): Agent.Core + DeviceService (Windows-Service-capable, named-pipe server) + SessionCompanion (interactive execution, notepad/calc allowlist); persistent idempotency store; JSONL audit; full-jitter reconnect.
- Deterministic E2E (scripts/e2e-m1-device.ps1): command -> broker -> agent -> Notepad in interactive session -> ack chain -> broker+agent audit; automatic recovery verified after broker restart and agent restart. Wired into quality gate via -E2E.
- Tests: 103 -> 174 total green (85 unit + 21 integration Python, 65 xUnit agent, 3 new security-fix tests); full gate 10/10 PASS incl. M0 regression.
- Independent verification: all extended M1 acceptance criteria reproduced PASS. Security review: no high/critical; 3 low findings fixed same-day, 1 forward-looking medium gated on Windows-Service install (docs/reviews/M1_SECURITY_REVIEW.md).
- ADR-0014..0017. PS 5.1 stderr-tolerance hardening in all ops scripts.

## 2026-08-31 — M0 foundation complete

- FastAPI cloud-core (services/api): /v1/system/health with degraded semantics, structlog JSON logging with trace_id/task_id, reversible Alembic initial migration (owner + pgvector), ObjectStore provider interface with key validation, Temporal workflow + worker.
- Dev infra: pagentos compose stack (Postgres16+pgvector 15432, Redis 16379, MinIO 19000/19001, Temporal 17233 + UI 18233), loopback-only, all images pinned, Temporal healthcheck for deterministic CI readiness.
- Next.js 16.3.3 web shell (tr), health status panel, build + live-load verified.
- Quality gate: one command runs lint, 26 unit + 7 integration tests, web build; secret hygiene includes content-pattern scan. CI workflow for GitHub Actions ready.
- Independent verification: test-engineer agent reproduced all 10 M0 acceptance criteria PASS. Security review: no high/critical findings; outcomes in docs/reviews/M0_SECURITY_REVIEW.md.
- ADR-0011..0013 recorded.

## 2026-08-31 — M-1 environment & repository bootstrap

- Initialized git repository (`main`), first commit of the specification package, added `.gitattributes` (ADR-0007).
- Rewrote `scripts/preflight.ps1` with hard per-command timeouts and absolute-path tool resolution (ADR-0008); generated `docs/LOCAL_ENV_REPORT.md`.
- Verified Docker Desktop 28.3.2 + WSL2 engine; `docker run hello-world` passed.
- Installed user-scope (no UAC): uv 0.12.7, pnpm 11.24.0, GitHub CLI 2.98.0 (ADR-0009).
- No blocking owner action; deferred actions batched in `docs/LOCAL_ENV_REPORT.md`.

## 2026-08-31 — Specification package v1

- Created initial project constitution, architecture, voice, memory, recovery, evolution, security and cloud specifications.
- No application code implemented yet.
