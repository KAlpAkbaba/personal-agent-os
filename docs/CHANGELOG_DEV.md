# Development Changelog

Autonomous engineering agents append concise accepted-change records here.

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
