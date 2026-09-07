---
name: reference-test-commands-and-gates
description: Exact tool paths and commands to run the API/web test suites, lint, and migrations in this repo (Windows, broken spawned-shell PATH).
metadata:
  type: reference
---

Spawned shells in this environment have a broken PATH — always use absolute tool paths, and
write files with Write/Edit, never bash heredocs (backslash/regex/path content gets mangled
through heredocs here — use Write/Edit or a scratch Python script instead).

- `uv` (from `services/api`):
  `C:\Users\alpak\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe`
  - Unit tests: `uv run pytest tests/unit -q`
  - One integration file (stack must be up): `uv run pytest tests/integration/<file>.py -q`
  - Lint: `uv run ruff check .`
  - Migrations: `uv run alembic current` / `uv run alembic upgrade head` — check `Settings().database_url`
    first (`uv run python -c "from app.config import Settings; print(Settings().database_url)"`)
    to confirm which DB you're about to touch; the dev DB is `pagentos` on port 15432, NEVER
    touch `pagentos_e2e_m13` (that's the isolated e2e harness DB).
- `pnpm` (from `apps/web`): `C:\Users\alpak\AppData\Roaming\npm\pnpm.cmd`
  - `pnpm test` (vitest), `pnpm lint` (oxlint), `pnpm build` (next build + tsc)
- `docker` (check stack status): `C:\Program Files\Docker\Docker\resources\bin\docker.exe ps`
  — dev stack containers are named `pagentos-postgres`, `pagentos-temporal`, `pagentos-minio`,
  `pagentos-redis`; `pagentos-temporal-ui` too.

**Gotcha:** a live dev API process is often running on `127.0.0.1:8001` (started outside the
task, e.g. `scripts/dev-broker.ps1`) — its artifact-ready announcer sweeps the same `tasks`
table integration tests use and causes unrelated, flaky-looking failures (documented in
`tests/integration/conftest.py`'s `no_live_api` fixture, which just warns rather than blocking).
If an integration test fails in a way that makes no sense and mentions notifications/announcer/
artifact-ready timing, check `Get-NetTCPConnection -LocalPort 8001` before assuming your change
broke it.

As of 2026-09-03 (M13 §5a work): API unit baseline 1965 tests, web baseline 163 tests, both green,
ruff/oxlint clean, `pnpm build` OK. [[project-pagentos-overview]]
