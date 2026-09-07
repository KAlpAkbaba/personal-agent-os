---
name: feedback-parallel-track-prereqs
description: When a task's required-reading docs/protocol files don't exist yet in the worktree, check whether a parallel track already landed them on main as an isolated commit, then fast-forward merge rather than blocking or fabricating.
metadata:
  type: feedback
---

When assigned a milestone track whose instructions say "READ FIRST: <path>" and that path does
not exist in the current worktree, do not treat it as a blocker or write a stand-in — this repo's
milestones are frequently split into parallel tracks (e.g. M13 track A authors the protocol/spec
docs, track B implements against them) that race each other across worktrees.

**How to apply:** `git log --all --oneline -- <missing-path>` to see if another branch (usually
`main`, since a docs-only track tends to merge fastest) already added it. If a clean, narrowly-
scoped commit exists (touching only the doc/protocol files, nothing in your own scope), fast-
forward or merge that commit into your branch (`git merge <sha>`) before reading — this is safe
even under the "work only in your worktree" rule, since you are pulling read-only prerequisite
state into your own branch, not pushing or touching another track's implementation. Verify the
commit's diff stat first to confirm it doesn't also touch files outside your allowed scope.

**Why:** discovered on M13 track B (browser worker) — `packages/protocol/BROWSER_CAPABILITIES.md`
and `docs/M13_RESEARCH_SPEC.md` didn't exist in the worktree at task start, but `main` was already
one isolated commit ahead (`docs(m13): browser capability wire contract...`) containing exactly
those two files. A plain fast-forward merge resolved it in seconds with zero risk.
