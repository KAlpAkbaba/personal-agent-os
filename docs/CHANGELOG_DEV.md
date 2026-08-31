# Development Changelog

Autonomous engineering agents append concise accepted-change records here.

## 2026-08-31 — M-1 environment & repository bootstrap

- Initialized git repository (`main`), first commit of the specification package, added `.gitattributes` (ADR-0007).
- Rewrote `scripts/preflight.ps1` with hard per-command timeouts and absolute-path tool resolution (ADR-0008); generated `docs/LOCAL_ENV_REPORT.md`.
- Verified Docker Desktop 28.3.2 + WSL2 engine; `docker run hello-world` passed.
- Installed user-scope (no UAC): uv 0.12.7, pnpm 11.24.0, GitHub CLI 2.98.0 (ADR-0009).
- No blocking owner action; deferred actions batched in `docs/LOCAL_ENV_REPORT.md`.

## 2026-08-31 — Specification package v1

- Created initial project constitution, architecture, voice, memory, recovery, evolution, security and cloud specifications.
- No application code implemented yet.
