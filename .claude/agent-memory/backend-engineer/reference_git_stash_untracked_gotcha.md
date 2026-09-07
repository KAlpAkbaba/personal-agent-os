---
name: project-worktree-git-stash-untracked-gotcha
description: In this repo's worktree setup, `git stash` alone leaves new (untracked) files in place — use `git stash -u` to get a true pristine baseline before/after comparisons, and never `cd` outside the assigned worktree.
metadata:
  type: reference
---

Two git-worktree gotchas specific to how this project's agents work
(`.claude/worktrees/agent-*`):

1. **`git stash` without `-u` does not stash new files.** When establishing a
   "what was already broken before I touched anything" baseline (e.g. running
   the full test suite before vs. after a change), a plain `git stash` only
   hides *tracked* modifications — any newly created files stay in the
   working tree and get picked up by the test run, silently corrupting the
   comparison. Always use `git stash -u` (or `git stash --include-untracked`)
   for a true pristine baseline, then `git stash pop` to restore. Concretely:
   comparing test failures before/after a change without `-u` made a
   newly-added file's bug look "pre-existing" until re-checked correctly.

2. **The harness refuses `cd`/`-C` into a directory outside the current
   worktree**, even the shared checkout the worktree was branched from — any
   git command must run from inside `.claude/worktrees/agent-<id>` with no
   path redirection. Don't try `cd ../../..` or `git -C <shared-checkout>`;
   it errors out before running.

See also [[feedback-allowed-paths-override-finding-text]] for the related
discipline of checking the full changed-file list against a task's stated
scope before committing.
