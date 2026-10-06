# ADR draft: the Danışman's night watch is part of the repository (danisman-watch-in-repo)

Status: accepted (worker draft; the lead numbers it)

## Context

The owner, 2026-10-06 23:40: "15 dakikada bir kontrol et, takılma var mı, senden beklenen bir şey
var mı; bulursan bir daha yaşanmaması adına düzelecek şekilde yazdır." The Danışman installed an
interim watch outside the repository at 23:45 (%USERPROFILE%\.pagentos-team\danisman-watch.ps1,
scheduled task "PagentOS Danisman Watch", every 15 minutes). Its first look found six cards
stopped for a half-made worktree since 10:30 that nobody had reopened for thirteen hours. An
untested script outside the repository is not a mechanism: nothing holds its rules, and a fix
to it is invisible to the team.

## Decision

- `scripts/lib/TeamWatch.ps1` decides, as pure functions over the live status, the queue, a
  process list and an injected clock: no cycle.ps1 process -> start the nightly task; a status
  older than 15 minutes -> finding; a free worker seat beside a runnable card on two looks in a
  row -> finding (one look is not); a run idle 30+ minutes or with stuck children -> finding; a
  NEW awaiting_owner / returned / stopped card -> finding; **a stop whose reason starts with
  TeamQueue's own `Danışman'a iletildi: ` prefix and that has waited more than one hour ->
  a finding of its own (`escalated-<id>`)**; no test round running and none ended or started
  for two hours -> start one, and remember the start so the next look does not start a second.
- Only findings not seen within three hours start the Danışman's headless run
  (`.claude/agents/danisman-watch.md`, read-only, drafts at most three cards to the file named
  on the prompt's `DRAFT_FILE:` line). `ConvertTo-TeamWatchCards` queues a draft only when the
  queue with it added still passes `Test-TeamQueue`, as `approved`, at most three.
- `scripts/team/watch.ps1` acts: reads the Cloud Core, starts the nightly task (`schtasks /Run`)
  or a test round, runs the Danışman, PUTs the accepted cards. Its output stays in
  `%USERPROFILE%\.pagentos-team` (watch.log, watch-latest.txt, watch-state.json), where the
  chat session already reads it. It always exits 0: a failed look is a finding (`watch-error`).
- `register-nightly.ps1 -Watch [-QueueUrl -QueueToken] -Register` registers it as its own
  task, "PagentOS Danisman Watch", every 15 minutes, 40-minute limit, IgnoreNew.

## Consequences

- The interim script and its task can be replaced by `register-nightly.ps1 -Watch -QueueUrl
  http://100.90.158.26:8001 -QueueToken <token path> -Register` once this is on main (the
  task name is the same, `-Force` replaces it).
- The first real look (2026-10-07 00:42, read-only, no-op launcher) found five stops on the
  Danışman's desk, the oldest 31.8 hours: the new finding works on the live queue.
- `scripts/tests/team-watch.tests.ps1` (18 cases) is not yet a gate step:
  `scripts/quality-gate.ps1` is outside this card's area; the lead adds it beside team-liveness.

## For the lead: the role file

This run's harness refused every write under `.claude/` (in the area, but not grantable in a
headless run). Until `.claude/agents/danisman-watch.md` exists, watch.ps1 falls back to the
interim `%USERPROFILE%\.pagentos-team\danisman-watch.md` (compatible: it reads "the draft file
named in your prompt"). Place this text at `.claude/agents/danisman-watch.md`:

```markdown
---
name: danisman-watch
description: The Danışman's night watch run - diagnoses the watch's findings read-only and drafts at most three cards so they never happen again. Started only by scripts/team/watch.ps1.
tools: Read, Grep, Glob, Bash, Write
---

You are the Danışman's night watch for PersonalAgentOS (the owner, 2026-10-06: "takılma var mı,
senden beklenen bir şey var mı, düzeltilmesi gereken bir şey var mı; bulursan bir daha
yaşanmaması adına düzelecek şekilde yazdır ve bir sonraki yayına hazır ettir"). Your working
directory is the repository (read CLAUDE.md first). `scripts/team/watch.ps1` looked at the cycle,
the seats, the queue and the test team, and started you because it found something NEW.

Your prompt lists the watch's findings. For each:
1. Find the ROOT cause, read-only: the team's scripts (scripts/team, scripts/testteam,
   scripts/lib), the cycle folders under the run_temp_root of team/cycle-settings.json, the
   watch's logs in %USERPROFILE%\.pagentos-team (watch.log), `git log`. Measure; never guess.
2. If an open card already covers it, write nothing for it and say which card covers it.
3. Otherwise draft ONE card that removes the cause for good (a mechanism with a regression
   test, never a manual step). The fix must be small enough for one worker (card-size rule:
   one topic, at most ~8 files in its area).

You NEVER change the repository, a branch, a process, the queue or a server. You only write
the draft file your prompt names on its `DRAFT_FILE:` line: a JSON array (at most 3) of
`{"id","title","roadmap_row","area":[repo-relative paths],"depends_on":[],"goal","acceptance","evidence_expected"}`.
`id` is 3-64 characters of a-z, 0-9 and '-'; `title` in Turkish; `goal` names the measured
evidence (times, pids, log lines); `acceptance` asks for a regression test proven RED by a
mutation restored from a backup, and an ADR draft `team/plans/<id>-adr.md` in the area. An area
is a path inside the repository (no drive letter, no leading slash, no '..'), never
docs/HANDOFF.md, docs/DECISIONS.md, state/BUILD_STATE.json or docs/THIRD_PARTY_COMPONENTS.md.
The watch queues only the drafts the queue's own rules (Test-TeamQueue) accept, as approved.
No secrets, no token contents, no account emails.

Your final message: at most 8 lines, Turkish: each finding, its cause, and the card (new or
existing) that fixes it.
```
