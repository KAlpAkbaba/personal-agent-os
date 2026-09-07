---
name: project-browser-worker-contract
description: services/browser implements packages/protocol/BROWSER_CAPABILITIES.md — versioned contract, worktree-per-task workflow, current baseline
metadata:
  type: project
---

`services/browser` (the Windows Browser Agent / "browser worker",
`python -m browser_agent.worker`) implements
`packages/protocol/BROWSER_CAPABILITIES.md` verbatim — that file is the
authoritative spec and must be changed there FIRST before code, per its own
header. `docs/M13_RESEARCH_SPEC.md` is the companion Cloud-Core-side spec
(REST shapes, Temporal workflow, synthesis) — read both before touching
search/fetch_evidence/session behavior.

**Contract versioning discipline already in place**: `CONTRACTS: dict[str, int]`
in `browser_agent/worker.py` (currently `{"browser.search": 2}`) and
`WORKER_VERSION` are surfaced on `hello` and `browser.worker_status` so a
consumer checks the contract BEFORE relying on new fields, rather than
hitting a missing-property failure. When a payload/result shape changes,
bump the relevant contract entry and add a `test_worker_advertises_the_...`
style pin test (see `tests/unit/test_search_google.py`).

**Task workflow observed for this repo**: work arrives as "branch from main
`<sha>`" pointing at a docs-only commit that just landed the NEXT contract
version's spec text (e.g. `fe605c4` added §3a to BROWSER_CAPABILITIES.md
with zero code) — the assigned worktree's own HEAD is typically one or two
commits BEHIND that `<sha>` on main, so `git checkout -b <branch> <sha>`
first, don't assume the worktree's current branch already contains it.

**Baseline as of contract v1.1 (2026-09-03)**: 275 unit / 64 browser tests
green (was 261/57 before v1.1: Google-through-the-UI, owner handoff,
`browser.wait for=verification_cleared`, `fetch_evidence tab=new`). Run from
`services/browser` with the absolute `uv.exe` path (see
[[machine-tool-paths]] in the main memory index) — spawned-shell PATH is
broken on this machine.

**Reusable pattern**: `search_engines.run_search()`'s `fetch(engine, url)`
seam accepts a 3-, 4-, or 5-tuple return (`(html, page_kind, http_status[,
final_url[, path_hint]])`) and now also accepts `fetch` raising
`GoogleHandoffPending` to short-circuit the whole provider loop with a
special SUCCESSFUL outcome instead of a failure. This kept `run_search()`'s
core fallback logic (and all its existing unit tests) completely untouched
while giving the worker's Google-specific UI-driving closure
(`Worker._google_ui_fetch`) a way to inject non-generic behavior (typing
into a search box, owner handoff, resuming a cleared session) through the
same seam. Worth reusing this shape (tuple-length side channel + a raised
signal exception) for any future provider that needs browser-driven
(non-URL-fetch) behavior inside `run_search`'s loop.
