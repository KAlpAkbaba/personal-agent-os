---
name: feedback-concurrent-agent-worktree
description: This repo's working tree can be modified by another concurrent agent session mid-verification; check git status before AND after, not just at start
metadata:
  type: feedback
---

During the ADR-0043/ADR-0044 verification (2026-09-02, see
[[project-pagentos-arbor-m12-noise]]), `git status` came back clean at the
start of the conversation (per the system-reminder snapshot), but by the time
I finished, the working tree had uncommitted changes I never made: edits to
services/api/app/voice/realtime_sessions/{routes,service}.py and its test
file (a fail-closed voice-validation check plus a `noise_summary` /
benchmark-report addition), a new `.claude/agent-memory/security-reviewer/`
file, and a deletion of apps/web/public/mic-check.html. This was a different,
concurrent agent (apparently security-reviewer, maybe also voice-engineer)
working in the SAME checkout at the same time — not something the user did,
not something I did.

**Why this matters:** the initial "git status: clean" system-reminder is a
snapshot taken once, at conversation start — it is not a live guarantee for
the rest of the session. In this multi-agent environment, another session can
land uncommitted edits into the same working directory while I am mid-review.
If I had trusted my own earlier file Reads without a final `git status`, I
would have reported clean-repo confidence about files that had since drifted.

**How to apply:** for any "verify X and leave the repo unmodified" task, run
`git status`/`git diff --stat` again right before writing the final report,
not just trust the start-of-conversation snapshot or my own memory of what I
read. If it's dirty and I didn't cause it, say so explicitly and scope the
verdict to the specific commits/files that were actually asked about, rather
than either ignoring the drift or reporting it as my own error.
