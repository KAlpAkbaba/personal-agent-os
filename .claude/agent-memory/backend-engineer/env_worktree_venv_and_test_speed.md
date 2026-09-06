---
name: env-worktree-venv-and-test-speed
description: services/api has no .venv inside a fresh git worktree; how to bootstrap it, real measured gate timings once warm, and the PYTHONIOENCODING=utf-8 needed to print Turkish.
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

**The first `uv run` in a fresh worktree is the slow one; after that the suite is fast.**
The initial `uv sync`/first `uv run` builds the venv and takes ~2 minutes (it will exceed a
120s foreground Bash timeout and fall through to background — that is normal, not a hang).
Once warm, measured 2026-09-06 in this worktree:

- `uv run ruff check .` — seconds.
- `pytest tests/unit/test_voice_*.py tests/unit/test_explain*.py tests/unit/test_research_routes.py tests/unit/test_health_endpoint.py -q` — 562 tests in **~20s**, `test_health_endpoint.py` included and NOT hanging.
- `pytest tests/unit -q` (whole suite, 3657 tests) — **~160s**.

So earlier notes of "8-10s per TestClient test" and "test_health_endpoint.py hangs for
minutes" describe a cold venv / an earlier environment, not the steady state: budget one
generous timeout for the first command in a new worktree, then treat the gates as cheap and
run them often. Still prefer targeted files while iterating, but the full suite is a
reasonable final check rather than a background-and-wait affair.

**Printing Turkish to stdout through the Bash tool needs `PYTHONIOENCODING=utf-8`.**
Without it Python's stdout is cp1252 here and any `ş`/`ğ`/`ı` in a `print` raises
`UnicodeEncodeError: 'charmap' codec` — which looks like a code bug and is not one. Prefix
every `uv run python` / `uv run pytest` invocation that may print Turkish with
`PYTHONIOENCODING=utf-8`.
