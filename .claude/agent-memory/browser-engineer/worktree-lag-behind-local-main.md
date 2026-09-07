---
name: worktree-lag-behind-local-main
description: A task brief describing files/modules that "already exist" may be wrong for YOUR worktree branch even though it is right for the project — the local main ref (not origin/main) is often ahead of the branch a given worktree was cut from.
metadata:
  type: feedback
---

In this project, many parallel agent worktrees (`worktree-agent-*` branches) are cut from
`main` at different points in time, and other worktrees' finished work gets merged back into
the LOCAL `main` ref (visible in the shared git object store) well before it reaches
`origin/main`. A task brief written against "the state of main" can therefore describe files
that exist on local `main` but not yet on the specific worktree branch a session is running
in.

**Symptom**: a task says "read `services/api/app/presence/observations.py`, it EXISTS" or
similar, but `ls`/`Read` on that path fails, and `git log --oneline --all -- <path>` shows the
introducing commit but it is not an ancestor of the current branch.

**Why**: git worktrees in this repo each check out their own branch; `git branch -a` shows
other worktrees' branches with a `+` prefix (checked out elsewhere) or plain (available).
`git rev-list --count main ^origin/main` frequently shows local `main` several commits ahead
of `origin/main` — those commits are merges from OTHER worktrees' finished sessions that
haven't been pushed yet.

**How to apply**: before concluding a task brief is wrong or hallucinated, check:
1. `git log --oneline --all | grep -i <keyword from the missing file/feature>` to find the
   introducing commit.
2. `git branch --all --contains <that commit>` to see which branch(es) have it — local `main`
   is a normal answer.
3. If local `main` has it and your current worktree branch does not, `git merge main` into
   your branch (this is a normal git operation on your own worktree's branch, not a
   cross-worktree write) to pull in the dependency before starting the actual task.

This is safe and expected — it is not the same as reaching into another worktree's working
directory (which the sandbox correctly refuses). Merging a local branch ref by name is a
read of shared git history, not a write to another worktree.

Confirmed working 2026-09-06 for M18: `worktree-agent-a945382534565e3fd` was several commits
behind local `main`'s Presence Engine / Holographic Core renderer / Routine Engine merges;
`git merge main` pulled them in cleanly with no conflicts before the actual task (the Active
Eye device-side client) could begin. See [[m18-active-eye-state]].
