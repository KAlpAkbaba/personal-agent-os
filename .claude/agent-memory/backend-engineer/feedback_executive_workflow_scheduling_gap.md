---
name: feedback-executive-workflow-scheduling-gap
description: A Temporal workflow scheduler that gates a dependent step on "dependency VERIFIED" rather than "dependency SETTLED" silently deadlocks the whole downstream chain on any failure
metadata:
  type: feedback
---

In `app/executive/workflow.py` (M26, 2026-09-08), the step-scheduling loop originally
computed `ready` steps as those whose dependencies were in a `verified_or_skipped` set
(only VERIFIED/SKIPPED unlocked a dependent). This looked reasonable but was wrong: any
step that reached `failed` was added to `settled` but never to `verified_or_skipped`, so
EVERY step downstream of it — including the final `synthesis` step every graph in this
family ends with — stayed `pending` forever. The run could never reach `partial` (spec's
own "the synthesis still runs over what exists" promise), it just hung until the run's
wall-clock bound.

Found only by writing a deliberate unit test (`a step dependent on a failed step still
runs`) and reasoning through what SHOULD happen before running it — the bug would not have
surfaced from the "happy path" tests at all, since those never have a failing dependency.

**Why:** scheduling readiness and "does this step have usable evidence" are two different
questions. The FIRST is "has this dependency reached any TERMINAL state" (so the graph can
keep moving forward and reach an honest end); the SECOND is "did it actually produce
output" — answered separately, per-step, by resolving input references to `None` when a
dependency's evidence is missing, and letting each step's own handler (or precondition
check) decide what a missing input means for IT. Conflating the two by gating scheduling on
"verified" makes an honest partial outcome unreachable.

**How to apply:** in any DAG/topological scheduler over steps that can genuinely fail (not
just succeed), the readiness gate is "dependencies SETTLED" (any terminal state), never
"dependencies VERIFIED/succeeded" — the downstream step (or its precondition) is what
decides whether a failed/missing upstream result is fatal to IT, not the scheduler. This
generalizes beyond this codebase.
