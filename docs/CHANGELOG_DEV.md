# Development Changelog

Autonomous engineering agents append concise accepted-change records here.

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
