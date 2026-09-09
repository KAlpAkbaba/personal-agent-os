---
name: feedback-test-suite-speed-and-tooling
description: services/api's TestClient-heavy unit tests are slow; test_health_endpoint.py can hang; use the worktree's own .venv/Scripts/python.exe -m pytest (PYTHONPATH=services/api, PYTHONIOENCODING=utf-8); full unit suite ~4-5min, full voice corpus (tests/unit/test_owner_utterance_corpus.py) ~13min; adding a new voice tool/intent means updating the exact-vocabulary tripwire tests too
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
running the file in isolation. *Update 2026-09-06:* in a fresh worktree venv it passed in
4.25 s (9 tests) when run alone, so the hang is environmental (whether a stale Postgres/
Redis/MinIO container is half-up), not a property of the file. Still run it alone and last.

**The FULL `tests/unit` suite (3656 tests) runs in ~2m20s in a warm worktree venv** when
invoked as one `pytest tests/unit -q` call. That is cheaper than several targeted multi-file
runs, so for a change that touches a shared contract (a report/payload schema, a vocabulary
set), one full-suite run at the end is the efficient way to find the frozen "the exact key
set is X" assertions in unrelated suites.

**How to apply:** when told to "run pytest," pick targeted files that exercise the actual
change plus any file with its own pinned/frozen assertions the change could affect (e.g. a
shared vocabulary or contract file's own "the exact set is X" test) rather than the full
suite — the full suite is safe but slow enough that background-and-wait is the practical
mode, and `test_health_endpoint.py` specifically should be run alone, last, and not treated
as a blocker if it hangs.

**Update 2026-09-09 (building the Owner Location Context / Live Weather / Morning Briefing
capability, [[project_pagentos_overview]]):**
- Use the worktree's own `.venv/Scripts/python.exe -m pytest`, not a global `uv`/`python`,
  with `PYTHONPATH=.` (from `services/api`) and `PYTHONIOENCODING=utf-8` set (Turkish text
  in test output/asserts needs it on Windows). `.venv` needs `uv sync` once per fresh
  worktree (~2 min).
- `tests/unit/test_owner_utterance_corpus.py` (the full voice corpus, ~1500 parametrized
  cases) took **776 s (~13 min)** to run to completion in a warm worktree — budget a
  background run, not a foreground timeout, whenever a change touches
  `app/voice/intents.py`, `tests/voice_corpus/corpus.py` or `harness.py`. A much faster
  sanity check for JUST the categories you touched: import
  `tests.voice_corpus.corpus.all_cases` and `tests.voice_corpus.harness.{build_harness,
  run_case}` directly in a `python -c` one-liner, filter `all_cases()` by `c.category`, and
  call `run_case(c, harness=None)` per case (each `run_case(..., harness=None)` builds its
  OWN fresh harness, so this is still one harness-build per case — cheap for one category's
  ~100-150 cases, too slow to do this for all ~1500 cases in the foreground).
- Adding a new voice tool changes the FULL registered-tool-name set, which at least one
  test asserts exactly (`test_voice_realtime_sessions.py`'s
  `test_create_selects_by_capability_and_returns_the_contract` lists every tool name
  literally) — this is the "exact-vocabulary tripwire" to update whenever
  `default_registry()` gains a `register_*_tools(reg)` line.
