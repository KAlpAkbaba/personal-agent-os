---
name: feedback-test-suite-speed-and-tooling
description: services/api's TestClient-heavy unit tests are slow (~2-3 min per handful of files) and test_health_endpoint.py can hang for minutes in this sandbox; use uv directly with the absolute uv.exe path and generous foreground timeouts, not the bare `python`/`pytest` on PATH
metadata:
  type: feedback
---

**Use `uv run` for anything in `services/api`, via the absolute uv.exe path, not the global
`python`.** The bare spawned-shell `python` on this machine is 3.14 with no `tzdata`
package, so `zoneinfo.ZoneInfo("Europe/Istanbul")` raises `ZoneInfoNotFoundError` outside the
project's uv-managed `.venv` (Windows has no OS tz database; `tzdata` is already a
transitive dependency here via `psycopg`, so `uv sync` + `uv run` just works). The absolute
path that worked in this session:
`/c/Users/alpak/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe/uv.exe`.
**Why:** wasted a real debugging cycle on a `ZoneInfoNotFoundError` that had nothing to do
with the trigger logic being tested — it was purely "wrong interpreter."

**Many `tests/unit/*.py` files build a full `TestClient(create_app(...))` per test/fixture,
which re-imports the entire app router graph.** A handful of such files together routinely
take 2-3 minutes even though the individual tests are fast once collected. Budget
foreground `Bash` timeouts generously (180-300s minimum) for any multi-file run that
includes `test_*_routes.py` files, or expect it to fall through to background — that isn't a
sign anything is actually stuck.

**`tests/unit/test_health_endpoint.py` can hang for 5+ minutes in this sandbox.**
`app/health.py`'s checks (`db`/`redis`/`object_store`/`temporal`) attempt REAL connections
with a short configured timeout, but at least one of those (likely the sync DB connect
inside an `asyncio.wait_for`) does not actually respect that timeout when the target isn't
reachable, so the whole check hangs well past `health_check_timeout_s`. This reproduced
identically across three separate invocations and is unrelated to whatever else changed in
the same session — don't burn time assuming a real regression before ruling this out by
running the file in isolation.

**How to apply:** when told to "run pytest," pick targeted files that exercise the actual
change plus any file with its own pinned/frozen assertions the change could affect (e.g. a
shared vocabulary or contract file's own "the exact set is X" test) rather than the full
suite — the full suite is safe but slow enough that background-and-wait is the practical
mode, and `test_health_endpoint.py` specifically should be run alone, last, and not treated
as a blocker if it hangs.
