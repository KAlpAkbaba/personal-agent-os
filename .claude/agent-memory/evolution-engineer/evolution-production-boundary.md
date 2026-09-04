---
name: evolution-production-boundary
description: The Evolution Engine's production boundary is enforced by an import guard test, so adding an "obvious" import to app/evolution can fail the suite
metadata:
  type: project
---

`app/evolution/authority.py` enforces the self-development rule structurally, and
one of its three mechanisms is an **import guard** (`scan_evolution_package`,
asserted by `tests/unit/test_evolution_authority.py`). Any new module under
`app/evolution` that imports a deployment, release-mutation or secret-root module
fails the unit suite.

Specifically refused: `app.selfhealing.pipeline` / `runtime` / `routes`,
`app.security.remediation` / `runtime` / `routes`, and everything under
`app.identity` except `app.identity.dependencies`. `app.selfhealing.service` and
`app.selfhealing.models` are symbol-restricted (evidence-only symbols such as
`compute_manifest_digest` and `Incident`; `Release` and the release mutators are
not importable).

**Why:** authority checks are worthless if engine code can import the deployer and
call it directly, so the import graph is treated as part of the boundary rather
than as a style rule. CLAUDE.md's "never model edits production source and
restarts" and constitution §6 are the source.

**How to apply:** when extending `app/evolution`, get release/deployment facts
through an injected seam (see `ReleaseEvidenceProvider` in `backlog.py`) instead
of importing the owning module. If a new legitimate symbol is needed, widen
`RESTRICTED_MODULE_SYMBOLS` deliberately and say why — do not delete the guard.

Related: [[evolution-concurrent-modules]]
