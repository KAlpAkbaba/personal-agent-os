---
name: evolution-lifecycle-run-recipe
description: How to drive a real (not simulated) IDEA→SHADOW_READY run, and the two gaps that run exposed in app/ledger and app/experience
metadata:
  type: project
---

A real lifecycle run needs an ephemeral SQLite store built from ORM metadata
(the pattern in `tests/unit/test_experience_compiler.py` — ledger + selfhealing
+ memory + experience + evolution tables), then `EvolutionService(factory)` and
`service.advance(..., actor=ActorKind.LAB)` per step. Working example:
`services/api/lab/candidates/acceptance_wording_guard/run_lifecycle.py`.

**Why an ephemeral store, not the owner's PostgreSQL:** writing lab bookkeeping
into production needs `Grant.WRITE_PRODUCTION_DB`, which is a production grant
the lab does not hold. The *law* exercised is real; only the store is not, and
the run must say so rather than implying a production row exists.

Two gaps a real run exposes (both confirmed 2026-09-05, neither fixed):

- **Ledger:** `RESEARCHING` and `QUALIFYING` have no honest event type in the
  closed vocabulary, so a full lab run writes 7 `evolution.*` events for 7
  transitions minus those. Documented in `service.LEDGER_EVENT_FOR_STATUS`.
  `IDEA` publishes no UI state either.
- **Experience Compiler:** driven over real `status=failed` ledger events plus a
  later `completed` event in the same subsystem, it produces only its GENERIC
  pattern ("Recurring <subsystem> failure (<event_type>)"). Its two named
  patterns are the research-evidence leak and deployment-provenance ones. So
  "the lesson is already compiled by app/experience" is not yet true for any new
  lesson class — cite the compiled rows honestly as generic.

**How to apply:** cite `lesson` evidence only after actually compiling it —
`service._verify_lesson` returns `False` (a hard refusal) for a lesson id that
does not resolve, unlike an absent subsystem which returns `None`/unverified.

Related: [[evolution-candidate-tree-location]], [[evolution-production-boundary]]
