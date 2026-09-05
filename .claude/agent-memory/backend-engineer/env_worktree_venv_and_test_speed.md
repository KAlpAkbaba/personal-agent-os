---
name: env-worktree-venv-and-test-speed
description: services/api has no .venv inside a fresh git worktree; how to bootstrap it, and why FastAPI TestClient-based test files are extremely slow here.
metadata:
  type: reference
---

`services/api/.venv` exists in the main checkout but is NOT present inside a fresh
`.claude/worktrees/<name>` copy (venvs aren't tracked by git and worktrees don't share
them). Before running `pytest`/`ruff` via `.venv/Scripts/python.exe` in a new worktree,
bootstrap it first:

```
cd services/api
"/c/Users/alpak/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe/uv" sync
```

That path is `uv`'s absolute location on this machine (plain `uv` is not on PATH in the
sandboxed shell). `uv sync` reads the existing `pyproject.toml`/`uv.lock` and creates a
matching `.venv` in seconds — no need to touch `pyproject.toml`. `tzdata` is already a
pinned dependency, so `zoneinfo.ZoneInfo("Europe/Istanbul")` (or any IANA zone) works out
of the box on Windows once synced.

**Test speed varies enormously by file.** Pure-logic unit tests (no FastAPI app, no
`TestClient`) run in well under a second even across 50+ tests. Any test file that calls
`app.main.create_app(...)` and builds a `fastapi.testclient.TestClient` is dramatically
slower in this environment — roughly 8-10 seconds PER TEST (a 15-test route file took
~133s; a mixed 6-file presence suite with ~15 such route tests took 394s). This appears to
be fixed overhead in `create_app`/import machinery, not something introduced by any one
package. Practical implications:

- Iterate on logic bugs using a fast, targeted `python -c` script or the non-route test
  files first; only run the full route-test file once you're fairly confident, and launch
  it with `run_in_background: true` — plan for multiple minutes, not seconds.
- Don't run `pytest tests/unit -q -k <keyword>` as a quick sanity check expecting fast
  results if the keyword happens to also match any route-test file — it will collect and
  run the WHOLE slow file too. Target specific non-route files by path when you just want a
  fast signal.
