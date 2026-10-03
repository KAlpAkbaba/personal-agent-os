# ADR-0214 addendum (lead numbers it): the cycle's status is its lock's heartbeat

Status: accepted (task `team-lock-heartbeat`, cycle d20261003)

## Incident

2026-10-02: the cycle `d20261002` took the team lock at 11:05:28 UTC and was still working at
18:11 UTC (one worker run alive, its status written every two minutes). From 17:05 UTC - six
hours after `acquired_at` - the Ofis page showed "koşan ajan 0/6" and drew nobody working; the
owner asked "Ne oldu bu arkadaşlara". Cause: `LOCK_STALE_HOURS = 6` was counted from
`acquired_at` alone and nothing ever moved it. `office._is_live` asked `lock_is_running`, so the
page called a working cycle dead; and `lock_decision` answered `stale` -> `acquired: true` to
ANOTHER machine's acquire, so the owner's second machine would have taken the lock from a
running cycle and two cycles would have written one queue. The six hours were meant for a
holder that DIED. With the seat pool a cycle runs as long as there is work: every cycle will be
older than six hours.

## Decision

- `services/api/app/team/store.py::lock_alive_since(lock, status, at)` is the one rule: when the
  live status (`GET/PUT /v1/team/queue/status`) names the lock's own `cycle_id`, `machine`
  (case-insensitive, as the holder comparison already is) and `pid`, and its `updated_at` is
  newer than `acquired_at` and not further ahead of the clock than
  `STATUS_FUTURE_SKEW_MINUTES` (2, the Ofis page's bound - `office.py` now reads it from the
  store), the lock's age counts from that `updated_at`; otherwise from `acquired_at`.
- BOTH readers use it: `lock_is_running` (the Ofis page, `cycle_running` of the approvals
  route) and `lock_decision` (who may take the lock; FileStore and DbStore read the status
  beside the lock, the DbStore in the same session).
- Why the status: the cycle already writes it every pass and every 120 s while runs are in
  flight or the usage limit is waited out (`cycle.ps1` `$statusTickSeconds`), it names the
  holder exactly (cycle id, machine, pid), and using it needs no client change - a running
  cycle keeps the code it started with, so the fix is live from the release for it too.
- Holders with no heartbeat: the feeder (`feed-<date>`) and an integrate step write no status;
  for them, and for any status that is another cycle's / machine's / pid's, older than the
  lock, or dated beyond the skew bound, today's rule holds exactly: six hours from
  `acquired_at`.
- The silent bound stays `LOCK_STALE_HOURS = 6` (the test reading `$script:TeamLockStaleHours`
  passes unedited): a holder whose last status is over six hours old is stale and may be taken.
- `takeover_dead` (the holder's own machine saying its process is gone) is unchanged; another
  machine's `takeover_dead` is still not believed.
- No shape changes: the lock document, the request bodies and the answers' keys
  (`acquired, kind, holder, since, pid`; `since` is still `acquired_at`) are as before. No
  migration, setting or compose change.

## What another machine is told

`scripts/lib/TeamQueue.ps1 Get-TeamLockDecision` still reads "stale" six hours after
`acquired_at` and then asks the server to acquire. The server's answer counts: `acquired:
false, kind: held, holder: <machine>` while the holder's status is younger than six hours
(proved through the real lock route with the client's body - action acquire, machine,
cycle_id, pid, takeover_dead false - over the file store, SQLite and the dev stack's
PostgreSQL). The same machine with another pid is told `ours`, not acquired.

## Not fixed here

- File mode (`team/lock.json`, no server): the client decides alone from `acquired_at`, so a
  file-mode cycle older than six hours can still be taken over. Follow-up (after
  cycle-seat-pool): the cycle refreshes its own lock file (or the client applies the same
  status rule to `team/status.json`).
- Proposal, NOT applied: the pool writes its status every 120 s, also while waiting out the
  usage limit, and the page already treats a status older than ten minutes as "no cycle". For a
  holder that HAS written a status, a silent bound of about 30 minutes would hand a dead
  cycle's lock over hours sooner. Before shortening it, confirm that no path of `cycle.ps1`
  (pre-pool sequential mode, a single foreground command of up to an hour, the stop/drain path)
  goes longer than that without `Write-CycleStatus`, and keep the six hours for holders with
  no status.
