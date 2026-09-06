---
name: worktree-pnpm-gates
description: A fresh .claude/worktrees checkout has no node_modules; run ONE `pnpm install --offline --frozen-lockfile` from the worktree root before any apps/web gate, never two `pnpm exec` in parallel
metadata:
  type: reference
---

A new agent worktree under `.claude/worktrees/` shares the git objects but not
`node_modules`. The first `pnpm exec <tool>` inside `apps/web` silently triggers a full
workspace install (89 packages hard-linked from `E:\.pnpm-store\v11`), and two such
`pnpm exec` calls started concurrently deadlock each other at "added 88/89" indefinitely
(cost ~20 minutes on 2026-09-06; had to Stop-Process the four node PIDs).

**How to apply:** from the worktree root run
`C:\Users\alpak\AppData\Roaming\npm\pnpm.cmd install --offline --frozen-lockfile`
once (it took ~10 min on this machine, in the background), confirm
`apps/web/node_modules/.bin` has `tsc vitest oxlint next`, then run the gates
sequentially. Once installed: full `vitest run` ~6 s, `tsc --noEmit` ~1 min,
`oxlint .` ~1 s, `next build` ~22 s. The `docs/M18_ACTION_CONTRACT.md` shared
checkout path is fine to READ, but git commands must target the worktree only.
