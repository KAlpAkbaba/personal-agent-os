# ADR-0214 addendum (number by the lead) — the feeder cuts cards beside a running cycle, under its own lock

Status: accepted (worker, feeder-own-lock, cycle d20261003). Closes ADR-0237 "Known limit, not
closed here" and ADR-0214 addendum 11 "Not here".

## Context

`scripts/team/feed.ps1` took the cycle's lock as `feed-<date>` and stopped (exit 3) when a
cycle held it. A cycle holds it for most of the day, so the queue was fed only BETWEEN cycles
and could run dry beside idle seats. Addendum 11 removed the reason: the cycle reads the store
again before every pass and runs a card somebody else wrote. The server already makes every
task write conditional (`_check_put`, `DbStore.put_task`: a create of an existing id and an
update from a stale version are 409 `stale_write`). No server change is made.

## Decision

1. **Two paths.** When the team lock is held by a LIVE cycle of THIS machine (decision `ours`,
   the pid alive) AND the queue is the Cloud Core's (`-QueueUrl`), the feeder takes the
   **lock-free path**. Everything else is today's path, byte for byte: a free / stale / dead
   lock is taken as `feed-<date>` (so the tick's cycle cannot start under a feed in flight) and
   the idea row may be committed; another machine's fresh lock, a live cycle's lock in
   FILE mode (one writer: the lock's holder), and a live FEEDER of ours (holder cycle_id
   `feed-*`: it holds the team lock, not the feeder's own lock, so a second lead run beside it
   would cut the same rows again) still stop it with exit 3, no lead run, no report.
2. **The feeder's own lock.** A machine-local file outside the repository
   (`$env:LOCALAPPDATA\PagentOS\team-feeder.lock`, `-FeederLockPath` for the tests), created
   exclusively and kept open for the run (readable, not deletable), holding pid, machine and
   time. A second feeder that finds it held by a live pid says so and exits 3 with no report.
   A lock whose pid is gone, or older than 6 hours (`$script:TeamFeedOwnLockStaleHours`), is
   taken over and the report says so. Released in `finally`. The cycle's lock is never taken,
   released or written on this path.
3. **Isolation of the lead run.** The lead works in a throwaway detached worktree of the
   checkout's HEAD under `.claude/worktrees/feed/<feed>-<n>-<pid>`; the before/after snapshot
   (rule 7) is taken THERE, so what the running cycle writes into the main checkout meanwhile
   (its reports, split files, proposals) is neither refused nor blamed on the run, while the
   run's own stray write still refuses the feed whole. The accepted feed file is copied to
   `team/plans/` of the main checkout; the worktree is removed in `finally`
   (`git worktree remove --force`). A git command that meets another git's lock file is tried
   again (15 x 1 s), then fails loudly - never swallowed.
4. **No idea row on this path.** `docs/ROADMAP.md` is neither edited nor committed while a live
   cycle holds the lock (Edit stays excluded from the lead's tools; the report says
   "onaylanan fikir satırı bu koşuda yazılmadı (...): döngü çalışıyor"); the next
   between-cycles feed asks again. A commit in the checkout a running cycle works from is not
   made without its lock.
5. **The re-read before the write.** The lead run takes minutes. Before anything is written the
   queue is read AGAIN from the store; a card whose id somebody created meanwhile is dropped
   with every card that depends on it (named in the report), and the rest is judged whole
   against the FRESH queue (`Test-TeamFeed`, `Test-TeamQueue` on fresh + new): e.g. a title
   somebody queued meanwhile refuses the file whole.
6. **Create-only writes.** Only the new tasks are sent, each as its own `PUT` with
   `expected_updated_at: null`, in dependency order (`Save-TeamFeedCreates`,
   `scripts/lib/TeamFeed.ps1`). A task the store has is never sent, whatever its state. A 409
   means somebody made that id first: that card and its dependants are dropped and named, what
   was written stays, nothing is retried or overwritten. A store that does not answer at the
   re-read or a write: nothing further is written, the feed file stays on disk, the report says
   so, the exit code is 1.
7. **The report** of a lock-free run is written to `team/reports/feed-<date>.md` and NOT posted
   to the store: the Onay Merkezi shows the newest report, and that stays the cycle's. The cards
   are visible in the queue.
8. Unchanged: `-MinRunnable`, `-MaxNew`, the stop flag (nothing starts, the flag is left), the
   model chain and the limit rules of addendum 13 (`-MaxLimitWaitMinutes` still bounds a wait;
   on this path no cycle lock is held while waiting), `-DryRun` (it prints which path it would
   take and writes nothing: no lock file, no worktree, only GETs).

## Still not serialised

Two MACHINES' feeders: the local lock serialises the feeders of one machine only. Two machines
can each cut cards beside their own cycle; the store's conditional create keeps them from
overwriting each other (a duplicate id is 409 and dropped), but two different ids for the same
roadmap item are possible if both judges ran before either wrote. The title rule against the
fresh queue narrows this; it does not close it.

## Evidence

`scripts/tests/team-feed.tests.ps1` (cases "lock-free 1..13", "lock-free writes",
"lock-free judge"; red first against the unchanged script), five mutation REDs,
`services/api/tests/integration/test_team_feed_lockfree_postgres.py` (real routes + DbStore on
the dev stack's PostgreSQL, real feed.ps1, fake lead). No server change, no migration, no
setting; `tick.ps1` is unchanged, so the scheduled task needs no re-registration.

## Known gap: no production caller reaches this path yet

Nothing on the build PC starts `feed.ps1` while a cycle runs. The scheduled task
`\PagentOS Team Nightly Cycle` runs `tick.ps1` with `-MultipleInstances IgnoreNew`
(`register-nightly.ps1`), and `tick.ps1` waits for its cycle child (`Start-Process -Wait`), so
every tick that falls inside a running cycle is ignored; `cycle.ps1` never calls `feed.ps1`.
The lock-free path is therefore reachable only by hand today, and the queue can still run dry
beside idle seats. This addendum does NOT make the change "live from the first tick after the
release" - that claim is withdrawn. Follow-up card for the lead to open:
**`feeder-trigger-beside-cycle`** - start `feed.ps1 -QueueUrl ...` while a cycle runs (either
`cycle.ps1` calls it in a child process when fewer tasks are runnable than worker seats, with a
deadline, or a separate scheduled task that is not blocked by the tick's IgnoreNew); area
`scripts/team/cycle.ps1` or `scripts/team/register-nightly.ps1` + its tests. PROVEN_REAL for
this addendum waits for that card.
