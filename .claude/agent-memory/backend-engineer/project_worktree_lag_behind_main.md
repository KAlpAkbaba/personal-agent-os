---
name: project-worktree-lag-behind-main
description: A freshly-assigned worktree branch can be many commits behind main in this repo, because sibling agent worktrees merge into main continuously; check before assuming a referenced ADR/file doesn't exist yet.
metadata:
  type: project
---

This repo runs many parallel `.claude/worktrees/agent-*` sessions against the same
`main`, each closing a different gap/ADR follow-up. A task can hand you a worktree
branch (e.g. `worktree-agent-a69b4706f8bf1dfa8`) whose tip is many commits behind
`main` — in one session, `main` was already 5 commits ahead (two other agents' merged
work, including the ADR the task itself referenced as "already recorded").

**Why this matters:** a task prompt that says "read ADR-00NN in docs/DECISIONS.md" can
be wrong about the ADR existing on YOUR branch even though it is 100% accurate about
the repo's current state — the ADR landed on `main` after your worktree was cut. Do not
conclude the task prompt is mistaken or that you must write the ADR from scratch. Also,
building on the stale base risks a large merge conflict later, and re-implements work
(e.g. a shared module the missed commits already added) that already exists on main.

**How to apply:** before starting real work in an assigned worktree, run
`git merge-base --is-ancestor <your-branch-tip> main` (or `origin/main`). If your tip is
an ancestor of `main` and your branch has NO commits of its own beyond that point (check
`git log --oneline -3` on your branch vs. `main`), it is safe to fast-forward:
`git merge main --ff-only`. This is non-destructive (no rebase, no rewritten history) and
picks up sibling work — including referenced ADRs/modules — before you start. If your
branch DOES have its own unique commits already, fast-forward is not possible; escalate
or merge normally instead of guessing.
