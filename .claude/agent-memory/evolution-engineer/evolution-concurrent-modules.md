---
name: evolution-concurrent-modules
description: app/uistate and app/experience were being built by other agents in parallel with Phase 7; evolution imports them defensively
metadata:
  type: project
---

As of the Phase 7 Evolution Engine work (2026-09-05), `app/uistate` and
`app/experience` did not exist in `services/api/app` — other agents were building
them concurrently in their own worktrees.

**Why:** `app/evolution/service.py` therefore resolves both lazily (`try/except
ImportError`): UI-state publishing degrades to a no-op, and an `experience`
lesson cited as evidence is recorded with `verified: false` rather than accepted
or rejected outright.

**How to apply:** verify whether those packages exist now before assuming the
defensive paths are still dead code. If they do, check that
`_resolve_uistate_publisher()` matches their real publish API (it probes
`publish` / `publish_state` / `set_state`) and that `_verify_lesson` matches the
real lesson model name (it probes `Lesson` / `ExperienceLesson` / `LessonRow`) —
both were written against a guess, not a contract.

Related: [[evolution-production-boundary]]
