---
name: feedback-research-module-patterns
description: Conventions and gotchas specific to services/api/app/research (M13 pipeline) worth reusing on future research-module tasks
metadata:
  type: feedback
---

Working in `app/research/*` (M13 browser-research pipeline), several codebase-specific
patterns matter for future changes:

- **Pure decision logic lives in its own module, separate from the Temporal
  workflow/activities.** `app.research.eligibility`, `app.research.dates`,
  `app.research.evidence.dedup_and_rank` are all pure, I/O-free, directly unit-tested
  functions the workflow/activities call. There is no `tests/unit/test_research_browser_workflow.py`
  — the workflow orchestration itself (`browser_workflow.py`) is NOT directly unit
  tested (no `temporalio.testing.WorkflowEnvironment` harness in this repo). When adding
  workflow-level decision logic (e.g. an early-stop rule, a mode/policy resolution), put
  it in a pure function/module and unit-test THAT directly — this is how "a QUICK run
  respects its hard deadline (fake clock)" gets tested without needing a real or
  simulated Temporal clock: pass `elapsed_s` as a plain float argument.

- **Config/policy resolved once, persisted, never re-resolved on replay.** The pattern
  for anything a Temporal workflow needs consistently across activities and replays:
  resolve it ONCE in the first activity (e.g. `plan_activity`), store it in the
  already-existing JSON column (`plan_json`/`progress_json` on `ResearchRunRow` — no
  migration needed), and have every later activity read it back from DB rather than
  re-deriving it from an argument. This avoids new Alembic migrations for pipeline-
  internal telemetry/config and keeps replay-safety automatic.

- **`ResearchRunRow.progress_json` is the general-purpose bag for run-scoped counters**
  that don't need a dedicated column (rejected_by_reason, quarantined, and now
  challenge_counts/cooled_domains/challenged_pages). `runs_service.update_run(...,
  progress=partial_dict)` does a shallow merge (`{**old, **new}`), so a caller
  overwriting one key must pass the FULL merged value for that key, not a fragment.

- **CLAUDE.md's "existing project instructions override defaults" note extends to field
  naming collisions.** Before reusing a request-body field name for a new purpose
  (e.g. wanted to name a new "research speed" concept `mode`), always grep the existing
  route/tests for that field name first — `CreateResearchRequest.mode` already meant
  interactive/unattended (owner-handoff), tested in `test_research_routes.py`. Introduced
  a differently-named field (`research_mode`) instead of overloading/renaming the
  existing one, and recorded the naming-collision resolution as an autonomous, reversible
  decision in the relevant ADR rather than asking the user.

- **`uv run ruff format --check` on a whole directory will report pre-existing,
  unrelated formatting drift** (e.g. `app/research/compose.py`, `dates.py`,
  `destination.py` etc. were already not ruff-formatted before this task touched
  anything). Scope the format-check gate to the literal list of files touched in the
  task, not a directory glob, to avoid false-positive gate failures from pre-existing
  repo state.

- **`workflow.now()`** (from `temporalio.workflow`, not `datetime.now()`) is the
  deterministic wall-clock read inside a Temporal workflow body — needed for any
  elapsed-time/budget check inside `browser_workflow.py`. It lives in
  `temporalio/workflow/_context.py` in this repo's installed version; `workflow.time()`
  (a float) is the alternative. Never use raw `datetime.now(UTC)` inside
  `@workflow.run` — that breaks replay determinism.

- **Backward-compat trap when introducing a "mode"/"policy" ceiling that co-exists with
  an existing explicit numeric field** (here: `BrowserResearchRequest.max_sources` vs. a
  new `ResearchPolicy.max_sources`): always resolve the effective value as
  `min(caller_explicit, policy_ceiling)`, never let the newer policy silently override an
  existing, already-validated request field. Caught late via an integration test
  (`tests/integration/test_browser_research_workflow.py`) that still constructed
  `BrowserResearchRequest(max_sources=6, ...)` expecting that number to actually bound
  the run.
