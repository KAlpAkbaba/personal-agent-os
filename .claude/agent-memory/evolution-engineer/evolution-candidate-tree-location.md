---
name: evolution-candidate-tree-location
description: Shadow candidate code goes in services/api/lab/, not app/evolution/lab/ and not skills/generated/ — both of those are refused by existing repo rules
metadata:
  type: project
---

Committed, reviewable SHADOW candidates live at
`services/api/lab/candidates/<name>/` (SPEC.md, THREAT_MODEL.md, NOTES.md,
`src/`, `run_lifecycle.py`, `evidence/`), with tests at
`services/api/tests/unit/test_lab_<name>.py`.

The two "obvious" locations are both wrong, and the repository already says so:

- `services/api/app/evolution/lab/…` — `app/evolution/sandbox.py` lists
  `services/api/app` in `PROTECTED_TREES`, so a sandbox root may never overlap
  it; `pyproject.toml` also ships `packages = ["app"]`, so anything there
  becomes part of the wheel and importable as `app.*`.
- `skills/generated/…` — that is the *generator's* runtime publication root
  (`PAGENTOS_EVOLUTION_SKILLS_ROOT`). It is git-ignored (`.gitignore` line 57)
  and its README states nothing there is hand-written.

**Why:** candidate code must be reviewable by the owner before approval, and
must be structurally incapable of being reached by the running product.
`services/api/lab/` satisfies both: it is tracked, has no `__init__.py` at any
level, and `SandboxPolicy(LAB_ROOT)` constructs successfully.

**How to apply:** put new candidates there and keep the no-production-import
test. Check the import graph with `ast`, not a substring grep — a grep for
`"from lab"` matches `from labels import …` in `app/voice/benchmark.py` and
fails the suite on innocent code.

Related: [[evolution-production-boundary]], [[evolution-lifecycle-run-recipe]]
