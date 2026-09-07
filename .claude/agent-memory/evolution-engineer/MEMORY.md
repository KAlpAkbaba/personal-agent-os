# Memory Index

- [Evolution production boundary](evolution-production-boundary.md) — an import guard test fails the suite if app/evolution reaches a deployment/release/secret module
- [Concurrent modules](evolution-concurrent-modules.md) — app/uistate and app/experience were absent during Phase 7; evolution imports them defensively against guessed APIs
- [Candidate tree location](evolution-candidate-tree-location.md) — shadow candidates go in services/api/lab/, never app/evolution/lab/ or skills/generated/
- [Real lifecycle run recipe](evolution-lifecycle-run-recipe.md) — how to drive IDEA→SHADOW_READY for real, and the ledger/experience gaps it exposes
- [Worktree Python environment](worktree-python-env.md) — no venv per worktree; use the main repo's interpreter with the worktree as cwd
