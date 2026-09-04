---
name: evolution-concurrent-modules
description: app/evolution talks to app/uistate and app/experience through lazy imports because they landed on main after the Phase 7 branch was cut
metadata:
  type: project
---

Phase 7 (Evolution Engine, 2026-09-05) branched before `app/uistate` and
`app/experience` existed; both landed on main while the branch was open.
`app/evolution/service.py` therefore imports them lazily inside the call
(`try/except ImportError`) rather than at module load, and degrades instead of
failing: UI publishing becomes a no-op, and a cited experience lesson is
recorded with `verified: false`.

**Why:** a peer subsystem that is absent, or whose API moves, must never block a
lifecycle transition — presentation and task completion are separate concepts
(constitution §4). The lazy import also keeps `app/evolution` free of a
load-time dependency on packages it does not own.

**How to apply:** the adapters were verified against main's real APIs
(`app.uistate.publish(state, *, subsystem=..., progress=..., metadata=...)` with
the five `evolution.*` `UiState` members, and
`app.experience.models.ExperienceLessonRow`), so they are no longer guesses —
but they are still duck-typed. If either module changes shape, the evolution
side fails silently to a warning rather than loudly, so re-run the check by hand
after a change there.

Related: [[evolution-production-boundary]]
