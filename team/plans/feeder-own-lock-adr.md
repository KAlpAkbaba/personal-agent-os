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
   the idea row may be committed; another machine's fresh lock, and a live cycle's lock in
   FILE mode (one writer: the lock's holder), still stop it with exit 3.
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

`scripts/tests/team-feed.tests.ps1` (cases "lock-free 1..12", "lock-free writes",
"lock-free judge"; red first against the unchanged script), five mutation REDs,
`services/api/tests/integration/test_team_feed_lockfree_postgres.py` (real routes + DbStore on
the dev stack's PostgreSQL, real feed.ps1, fake lead). No server change, no migration, no
setting; `tick.ps1` is unchanged, so the scheduled task needs no re-registration. A running
cycle keeps the code it started with; the change is live from the first tick after the
release, for the feeder process only.
