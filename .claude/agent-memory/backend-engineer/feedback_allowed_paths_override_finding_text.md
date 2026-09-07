---
name: feedback-allowed-paths-override-finding-text
description: When a delegated task's "Allowed paths" list conflicts with a specific finding's instruction text (e.g. the finding names a file to edit that the allowed-paths list excludes), the allowed-paths boundary wins.
metadata:
  type: feedback
---

Treat "Allowed paths" as a hard boundary that overrides literal instructions
inside individual findings/tasks, even when a finding explicitly names a file
outside that boundary as something to change.

**Why:** In a real M13 security-review task, finding HIGH-3 said to make
`app/research/forbidden_keys.py` "reuse" `app.voice.realtime_sessions.service
.is_forbidden_key` — "import it or move it to a shared module" — but the
task's stated allowed paths were `services/api/** (not app/voice/**)`. I
initially edited `app/voice/realtime_sessions/service.py` to delegate its
normalizer to the new shared module, which violated the explicit exclusion.
Caught it before finishing and reverted (`git checkout -- <file>`), keeping
the new shared module self-contained and leaving voice's file untouched, with
a docstring explaining why the two aren't actually unified.

**How to apply:** Before editing any file, check it against the task's
allowed-paths list. When a finding's prose names a file the allowed paths
exclude, read that mention as *context/precedent* (e.g. "the same technique
already exists over there") rather than as a mandate to touch it. If touching
it seems genuinely necessary to satisfy the finding, flag the conflict back
to the requester instead of silently expanding scope. Re-check the full
change set against the allowed-paths list before committing (`git status
--porcelain` and eyeball every path) — see also
[[project-worktree-git-stash-untracked-gotcha]] for a related git-diligence
habit in this same environment.
