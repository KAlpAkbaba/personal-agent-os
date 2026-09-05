---
name: project-m18-spec-gap
description: docs/M18_HOLOGRAPHIC_CORE_SPEC.md does not exist; ADR-0056 in docs/DECISIONS.md is the design record for the M18 Routine Engine until a real spec lands
metadata:
  type: project
---

`docs/M18_HOLOGRAPHIC_CORE_SPEC.md` was referenced by a task brief (2026-09-05, Routine
Engine build) as required reading, but it does not exist anywhere in the repo's git history
on any branch. `state/BUILD_STATE.json`'s `current_milestone` confirms M18
(`M18_HOLOGRAPHIC_CORE_ACTIVE_EYE_AMBIENT_PRESENCE`) is real; the only design material that
actually exists is `docs/DECISIONS.md` ADR-0052 (Holographic Core / UiState contract) and
ADR-0053 (M17 cognitive foundations). I wrote **ADR-0056** as the design record for the
Routine Engine specifically because no spec file existed to read.

**Why this matters:** if a future task references another `docs/M18_*_SPEC.md` file, check
it actually exists before treating its cited sections as ground truth — this one didn't, and
the task's inline description (trigger/condition/action shapes, event names, requirements)
was actually the authoritative spec, not the missing file.

**How to apply:** for M18 work going forward, read `docs/DECISIONS.md` ADR-0052/0053/0056
plus `state/BUILD_STATE.json`'s `m18_routine_engine` / status fields first. If a real
`docs/M18_HOLOGRAPHIC_CORE_SPEC.md` is later added, reconcile it against ADR-0056 rather
than assuming the ADR is stale — the ADR records what was actually built (schema, REST
surface, UiState contract v2, ledger vocabulary) which a later spec must not silently
contradict without a migration plan. See also [[project-app-presence-not-built]].
