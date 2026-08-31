# Autonomous Build Protocol for Claude Code

## 1. Milestone loop

For each milestone:

1. Read milestone requirements.
2. Inspect current code/state.
3. Create machine-readable task list.
4. Delegate discovery/implementation/review.
5. Implement smallest vertical slices.
6. Run targeted tests continuously.
7. Run milestone gate.
8. Fix until green.
9. Independent review.
10. Update docs/state.
11. Commit/release candidate.

## 2. No routine questions

When uncertainty is technical and reversible:

- research official docs;
- select a choice;
- record ADR-style entry in `docs/DECISIONS.md`;
- continue.

## 3. Worktree policy

Use subagent `isolation: worktree` for changes that can be parallelized cleanly.

Parent/lead integrates only after tests and review.

## 4. Completion gates

A task cannot close with:

- failing test;
- skipped acceptance test without documented reason;
- new static-analysis error;
- secret in diff;
- unresolved migration error;
- missing required docs/state update.

## 5. Regression policy

Every reproduced bug should generate a regression test unless technically impossible. If impossible, document why and create a synthetic monitoring check.

## 6. Dependency policy

- prefer maintained dependencies;
- pin versions/lockfiles;
- check license;
- avoid integrating large frameworks when a thin adapter is enough;
- isolate third-party code behind interfaces.

## 7. Documentation policy

Keep documentation aligned with real implementation. Do not preserve obsolete architecture merely because it was in the original package; instead update `docs/DECISIONS.md` with the evidence-driven change while respecting `PROJECT_CONSTITUTION.md`.

## 8. Owner communication

End-of-run summary should be short:

- what milestone is complete;
- tests;
- important decision;
- required owner action if any;
- next milestone.

Routine file lists and commit details are available on request, not dumped by default.
