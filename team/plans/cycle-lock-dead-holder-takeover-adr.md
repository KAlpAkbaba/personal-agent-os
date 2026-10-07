# ADR draft: the cycle's lock - this machine's pid before the age; a refusal names the server

Task: cycle-lock-dead-holder-takeover (cycle d20261007). Number: the lead's.

## Context

2026-10-07 08:19-08:21 (account switch): the old cycle (pid 20832, d20261006, lock taken
2026-10-06T18:48:57Z on the Cloud Core) was stopped and the tick started twice; both cycles
exited 3. `Get-TeamLockDecision` judged the lock `stale` by its age (13 h > 6 h), so
`cycle.ps1` sent the acquire with `takeover_dead = false` (only `dead` sent true, and `dead`
was reached only from a FRESH `ours`). The server counts the six hours from the holder's last
status (`store.lock_is_running`), found the holder alive, said `ours` and refused. The report
said "bayat kilit devralındı" and then "kilit MAIL makinesinde ... hiçbir şey çalıştırmadı":
two clocks for one decision, and a stop that named neither.

## Decision

1. `Get-TeamLockDecision -ProcessAlive <scriptblock>`: for THIS machine's lock the holder's pid
   is looked at BEFORE the age. Gone -> `dead` (MayRun, the acquire says `takeover_dead = true`,
   the one word the server gives our lock up to); alive -> `ours` (never taken over, whatever
   the age). Another machine's lock is never probed (its pid means nothing here) and keeps
   the six-hour rule. Without `-ProcessAlive` the function is as before (feed, integrate,
   release, and the existing tests).
2. `Test-TeamLockHolderAlive`: the pid exists AND did not start after the lock was taken
   (+15 min); a pid reused after a restart is not the holder. An unreadable start time counts
   as alive (the safe side: never take over a running cycle).
   Why 15 minutes, not 1 (inspector, first review): in API mode `acquired_at` is the server's
   clock and `StartTime` this machine's. With the local clock more than 1 min ahead of the
   server, a LIVE holder looked "started after the lock", was judged dead, and the server -
   trusting `takeover_dead = true` - gave its lock to a second cycle of the same machine. The
   window now covers any plausible skew (measured 0 s on 2026-10-07; w32time keeps both within
   seconds). A pid reused within 15 min of the stamp counts as alive: the safe side, exit 3
   until released by hand; a reboot-reused pid on an hours-old lock is still caught.
3. `Enter-TeamLockApi` is the cycle's one acquire path: it derives `takeover_dead` from the
   decision and, when refused, returns the stop line naming the server's answer
   (`sunucu reddetti: kind=..., pid=...`), this machine's decision and the `takeover_dead`
   sent.

## Consequences

- An account switch that stops a cycle no longer needs the Danışman's hand release.
- A live but hung cycle of this machine is no longer taken over at six hours by a later cycle
  of the same machine (it was before, in file mode); it is stopped by hand or by its own
  `-MaxHours`. Accepted: a running cycle of ours must never be taken over.
- feed.ps1, integrate.ps1 and release.ps1 still take the lock the old way (age first, then
  `Set-TeamLockApi`); they were outside this task's area - follow-up card.

Evidence: scripts/tests/team-cycle-lock.tests.ps1 (10 cases, fake store with the server's rule;
the skew case stamps the lock 2/10/14 min before the holder's start -> holder, 30 min -> reused),
9 mutations RED (incl. tolerance 1 min and 60 min), each restored from a backup with an equal sha256.
