# ADR draft (cycle-watchdog): the cycle watchdog, and a git call that cannot leave an orphan

Status: proposed (worker-2, cycle d20261006). The lead numbers it and moves it into docs/DECISIONS.md.

## Context

2026-10-06: the cycle (pid 4736, started 15:00) stopped iterating at 15:15:23. It used zero CPU
and never wrote its status again, though the heartbeat is every 120 s. The five runs it had
started kept working. The Ofis showed `running: false` with every seat idle, and the owner had
to ask "neyi bekliyorlar". The same day a `git worktree add` hung twice with an inner
`git reset --hard` at 0 CPU. `Invoke-NativeProcess` kills only the root on timeout, so the
children kept the pipes. An interim watchdog lives in the untracked
`%USERPROFILE%\.pagentos-team\team-feed-wrapper.ps1`. It uses `taskkill /T`, which also stops
Docker/WSL when a run started them.

## Decision

1. `scripts/team/watchdog.ps1` (rules in `scripts/lib/TeamWatchdog.ps1`) takes one look per run
   of the 30-minute "PagentOS Team Feeder" task, before feed.ps1:
   - It acts only on a lock held by THIS machine whose pid is alive AND runs
     `scripts\team\cycle.ps1`, with the last sign of life older than 15 minutes. The last
     sign of life is the newer of the holder's own status `updated_at` and the lock's
     `acquired_at`. A new cycle that never wrote its status is as stuck as one that stopped
     writing it.
   - In that case it stops the cycle's tree leaves first, then the cycle, then the tick and
     the tick wrapper above it. It never stops a process `Test-TeamTickKeep` keeps
     (Docker/WSL), nothing under one, and no conhost beside one. A child created before its
     parent is a recycled pid and is left alone.
   - Once the holder is gone, it releases the lock: API `release`, or `lock.json` set to
     `held:false` only if it still names that pid. Then it posts a `bilgi` to the board and a
     line under "Bekci" in `team/reports/<cycle>.md`, and runs `schtasks /Run` for the
     scheduled task.
   - If the holder will not die, the lock is NOT released, the task is not re-run, and the
     Danisman is asked.
   - If a restart is already recorded in `team/logs/watchdog-restarts.json` within the last
     2 hours, nothing is stopped and the Danisman gets a `soru`. A loop is not a fix.
   - A fresh status, another machine's lock, a free lock, or a dead holder is never touched.
     The next cycle takes a dead holder's lock over itself.
2. `scripts/team/new-worktree.ps1` makes the worktree through `New-TeamWorktreeGuarded`. Every
   git call runs under `Invoke-TeamTreeProcess`. On timeout it kills the whole process tree.
   If the tool exits but a child still holds its output, that child is killed too.
   - A `worktree add` that times out or fails is cleaned up: `worktree remove --force --force`
     (git locks a worktree while it is being made), `prune`, the folder removed, and
     `branch -D` when this call made the branch.
   - Then the script throws with the reason. `-GitTimeoutSeconds` defaults to 300.

## Consequences

- The cycle path itself (`cycle.ps1` -> `New-TeamWorktree` in `scripts/lib/TeamRun.ps1`) still
  uses the root-only timeout. Moving it to `New-TeamWorktreeGuarded` needs those two files,
  which are outside this card's area. Follow-up card: "cycle uses New-TeamWorktreeGuarded".
- The Danisman replaces the interim block in `team-feed-wrapper.ps1` with:
  `& "$repo\scripts\team\watchdog.ps1" -QueueUrl <url> -QueueToken $token`
  The new watchdog's threshold is 15 minutes (the interim one used 10) and its loop window is
  2 hours (the interim one used 1).
- The watchdog posts to the board as seat `lead`, task `cycle-watchdog`. The board's seat list
  has no `watchdog` seat.
- The watchdog's report line goes to its own file, `team/reports/<cycle>-bekci.md`, never the
  cycle's `<cycle>.md`: the restarted cycle runs with the same `-DailyId` and `Save-Report`
  rewrites that file whole, so a line appended there was gone within minutes (inspector,
  2026-10-06). In API mode the whole `-bekci.md` file is also sent with `Send-TeamReportApi`
  under that name, so the Onay Merkezi has it. A refused copy keeps the local line and logs
  `UYARI`.
- Only the lock holder's own status counts as its sign of life. A newer status written by
  another pid (an old cycle that lost the lock and still beats) does not make a stuck holder
  look fresh; the holder is judged by when it took the lock.
- Follow-up (inspector): `schtasks /Run` right after the wrapper is killed can be swallowed
  when the task (IgnoreNew) still shows "Running"; wait for Ready before /Run and verify a new
  instance afterwards. Not in this card.
