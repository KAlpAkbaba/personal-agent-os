# ADR (unnumbered - the lead numbers it at merge): the release waits for the operation lock; the reconcile never does

Date: 2026-10-02 · Task: `release-lock-waits` · Status: proposed by the worker, PROVEN_AUTOMATED
Proposed as an addendum to ADR-0223 addendum 2 (the same lock, the same holder, the other caller).

#### Context

2026-10-01 23:48 UTC, the release of main `5f250e5b`: the preflight answered "another blue/green
release or recovery operation is running; retry later" and exit 82. Nothing ran but the recovery
timer's minute reconcile, which takes `/opt/pagentos/.bluegreen-operation.lock` for about two
seconds every minute (the host snapshot: held in 2 of 60 one-second samples).
`release-cloud-core-bluegreen.sh` asked once (`flock -n 9`) in every mode, so about one release
in thirty fell on its own housekeeping (QUALIFICATION 41.8). The maintenance window met the same
thing and waits (ADR-0223 addendum 2). The suite's fake `flock` answered "free" always, so no
case could see it - the third defect of this shape (38.12, 38.17).

#### Decision

1. **A release, a preflight and a rollback wait.** The lock is asked for once (`flock -n 9`);
   when it is held, the script says once on stderr `waiting for the release lock (held by
   another operation), up to N s` and waits (`flock -w N 9`). Only after that does it answer
   82, in words that name the wait: `... is still running after waiting N s; retry later`.
   `N` is `PAGENTOS_LOCK_WAIT_S`, default 45 (the maintenance script's number and variable).
2. **`PAGENTOS_LOCK_WAIT_S=0` is yesterday's behaviour**, message included: asked once, 82.
3. **A value that is not a whole number 0..600 is refused with exit 64 before the lock file is
   opened** - nothing is created, nothing is asked. A leading zero (`045`) is refused too: a
   number bash would read as octal is not a number an operator meant. 64 was free in this
   script's exit vocabulary; 82 keeps its one meaning ("the lock is held; retry later"), said
   at two moments, with the `exit` on its own line as `test_release_exit_codes.py` requires.
4. **The reconcile keeps `flock -n`, and is known by its mode alone**: `--reconcile`, as the
   timer's unit gives it (`reconcile.sh --reconcile`) or after a sha. In that mode the variable
   is not read at all - a value that would be refused for a release cannot fail the timer.
   Why it may not wait: queued behind a release it would get the lock the moment the release
   ends or dies, and before that it would hold a oneshot unit "activating" for the release's
   length; stepping aside (82, which the unit counts as success) and running a minute later is
   what it has always done. `PAGENTOS_RECOVERY_BUNDLE` was NOT used as the mark: restore
   (`restore-cloud-core.sh`) and an operator also run `--reconcile`, without the bundle, and
   they must step aside the same way.
5. **Stage-only does not reach this script** (`release-cloud-core.ps1 -StageOnly` extracts the
   tree and runs no host transaction), so it takes no lock and has nothing to wait for. The
   task card lists it among the waiting modes; there is no code path to change.
6. **The fake flock has the host's shape** (ADR-0235): the seconds the lock is held when a
   release asks come from `scripts/tests/fixtures/host-snapshot.json`, so every release,
   preflight and rollback case of the suite meets the minute reconcile. The reconcile cases
   default to a free lock - the snapshot's holder IS the reconcile, and the unit is a oneshot -
   and say so explicitly when they put a reconcile behind a held lock. 3 s, 600 s and a free
   lock stay as named cases whatever a later collection finds.

#### Consequences and open risks

- The fake does not sleep and holds no kernel lock: it answers by arithmetic (held `K` s:
  `-n` fails when `K > 0`, `-w T` fails when `K >= T`), as the maintenance suite's does. That
  `flock -n 9` followed by `flock -w N 9` on the same descriptor behaves so on util-linux is
  PROVEN_AUTOMATED only by reading; PROVEN_REAL is the next release whose preflight meets the
  reconcile and passes. `services/recovery-supervisor/tests/test_systemd_install.py` exercises
  the real kernel lock for the reconcile half on Linux and is unchanged.
- The message on a wait that ran out names the configured bound, not a stopwatch: `flock -w N`
  fails after N seconds by definition, and a second clock for the same fact is this repo's
  most recurrent defect.
- A release that waits 45 s behind ANOTHER release then answers 82 as before, 45 s later. The
  driver's ssh session stays open for that time; nothing on the host has been touched.
- Only the lock is taken from the snapshot in this suite. Its `Reset-Host` still starts from
  `blue` by default (the snapshot's serving colour today, and a constant there since before
  ADR-0235), with `green` as explicit cases; moving that default to the fixture rewrites the
  suite's eighty-odd colour assertions and was not part of this task.
- The suite takes about 25 minutes on the home PC under a running team cycle (measured twice,
  2026-10-02); the 27 new assertions add about a minute.
- The pinned recovery copy (`/opt/pagentos-recovery/reconcile.sh`) is this same file. The
  reconcile path through it is unchanged byte-for-byte in behaviour, but its sha256 changes,
  so the pin must be renewed at the release that carries this (the owner's step, as always).
