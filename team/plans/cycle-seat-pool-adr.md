### ADR-0214 addendum (2026-10-02, cycle-seat-pool): the cycle is a pool, not batches - a seat is filled when it is free, the seats are per role, the store and the settings are read at every refill

Task `cycle-seat-pool`. Implements the owner's rule of 2026-10-01 (addendum 8: no agent idles
while there is work) inside one cycle; keeps addendum 11 whole and makes its re-read finer.

**What happened.** `scripts/team/cycle.ps1` started up to `-MaxParallel` runs and then waited for
ALL of them before it looked at the queue again. A seat whose run ended after five minutes stayed
empty until the slowest run of its batch ended (cycle adr0224-02: three workers of 3310 / 3295 /
3275 seconds - the two short ones waited for the long one), a finished worker's inspection waited
for the whole batch, and the researcher ran alone before any task. On 2026-10-01 22:15 the owner
saw three inspections hold the cycle's three slots while every WORKER seat was empty and eight
tasks were assigned. A setting changed at 16:45 took effect at 19:35: a cycle process is bound to
the arguments it started with, and a cycle that has work does not end.

**Decision.**
1. **A pool.** The runs in flight are polled (`-PollMilliseconds`, 250), never waited for one by
   one (`Test-TeamRunOver`; `Wait-TeamRun` only collects a run that is over). A run that ends is
   completed at once - its report, the task's state, the merge of an approved inspection, the
   write (`Complete-PoolRun`) - and then the free seats are filled from the queue as it is NOW,
   in its order (`Start-PoolRuns`). The pool replaces the batch loop; `Invoke-RoleRun` and the
   blocking `Wait-UsageLimit` are gone.
2. **Seats are per role.** `-MaxParallel` is the number of WORKER seats; beside them
   `-MaxInspectors` (2) inspections and `-MaxIntegrators` (1) integrators; the researcher and
   one lead split run beside those. A role never takes another role's seat
   (`Select-TeamSeatFill`, a function of the candidates, the runs in flight and the seat
   counts). The next role of a task follows its state exactly as before, so a finished worker's
   inspection starts while other workers are still running.
3. **Two runs never share an area.** The queue's own rules still hold (`Test-TeamQueue`,
   `Get-TeamAreaHolders`: an approved task is not moved into work beside the holder of its
   files). They judge the copy they look at; a run in flight is the cycle's OWN copy of its
   task. So the seat fill has the rule too: a worker or an inspector whose task's area overlaps
   the area of another task whose worker or inspector is in flight waits until that run ends
   (`Test-TeamAreasOverlap`, the one rule). An integrator's study holds no files (it writes a
   plan), and is neither held back nor a holder. Merges into the integration branch are made
   by the one thread that completes runs, one after the other.
4. **Every refill reads the store again** (addendum 11's `Sync-Queue`, now whenever a run ends
   and at least every `-RefillSeconds`, 120, while nothing ends). The task of a run in flight
   stays the cycle's copy, with the version that copy was read at: a re-read never replaces
   it. Its result is written when the run ends; if the store's copy changed meanwhile that
   write is the stale one and is dropped (`Test-TaskMovedInStore`, as before). A task the
   store took out stays in the cycle's copy until its run ends. Beside runs in flight the
   write before the read is the soft one (`Save-QueueNow`), and the queue is not read again
   over what could not be written; with nothing in flight it is the strict one, as before.
5. **The settings of a running cycle.** `team/cycle-settings.json`
   (`{ "max_parallel", "max_inspectors", "max_integrators" }`, each optional, 1..16) is read at
   every refill when it is there (`Read-TeamCycleSettings`); a file that is no setting changes
   nothing - the parameters stand, not half of the file - and is one line in the report. A
   count lowered below what is in flight starts nothing and stops nothing. After `-MaxHours`
   (4; 0 = never) the cycle starts nothing new and ends when its runs do: the scheduler's next
   start runs the current script.
6. **The usage limit does not block.** A run that comes back limited is started again at once
   one model down, as before. When no model is left, the wait is a time the pool starts
   nothing until (`Set-LimitWait`); the runs in flight go on and are completed as they end.
   Without `-WaitForUsageLimit`, or when nobody said when, the stop line - once, however many
   runs met the limit - and nothing new starts.
7. **What the batch loop guaranteed still holds**, by the tests that held it: `-MaxRunsPerTask`,
   two failed runs stop a task, the stop flag starts nothing new and lets the runs in flight
   finish, the dependency rule, the conditional writes (a stale write is that one task's), a
   refused fresh merge taken back, the heartbeat (now from the pool's loop), the report's run
   list. The cycle ends when nothing is in flight and nothing can be started.

**Decisions made on the way, each reversible.**
* *The periodic refill.* "Whenever a run ends" does not see a card that arrives beside ONE long
  run with every other seat free. The pool also refills every `-RefillSeconds`; 120 s is one
  GET of the queue every two minutes while a cycle lives (the queue is ~0.6 MB today).
* *A cap is checked at every refill that has something to start*, not once per batch: with
  `-MaxUsd`, fewer runs start after the money is spent, never more.
* *A cycle that dies with runs in flight kills them* (the `finally`): a run must not write to a
  worktree after the lock is released. The task is taken up again by the next cycle.
* *The seat counts are this PC's file*, also in API mode ("file store" in the card). A setting
  in the team store would need a route; not built.

* *A start that fails is a change, never the end* (added after the inspector's return,
  2026-10-02). The first pool read "nothing started, nothing in flight, no state moved" as
  "nothing can be started" - but a start that fails (a worktree that cannot be made) stops its
  task, and with one worker seat the cycle ended with two assigned tasks never run; the batch
  loop went on to them. A refill now counts its failed starts (`Failed`): the seat is still
  free, so the next refill is due at once - beside runs in flight too, not at the next run's
  end or `-RefillSeconds` later - and it is not an idle pass. It ends: each failed start stops
  its task. A researcher or a lead split that cannot be started (no role file, a model that is
  none) is caught as well: it used to throw out of the refill, which ended the cycle and let
  the `finally` kill every run in flight. Now it is that run's one try of the cycle and a line
  in the report (`araştırmacı: koşu başlatılamadı` under the stops, without the marker of a
  finished research run; `bölme koşusu: <id>: koşu başlatılamadı` under the risks, the
  proposal where it was), and the next cycle tries again.

**What changed in the tests that were there.** Three assert the old meaning of `-MaxParallel 1`
("one run of any role at a time") and now state the new one; four that replay a store outage in
the shape of a batch pin that shape with `-PollMilliseconds` (both runs end in one poll). Each
is named in the worker's report; no assertion about the store's rules was loosened.

**Evidence.** `scripts/tests/team-cycle.tests.ps1`, PROVEN_AUTOMATED (the fake in place of the
model now takes per-run durations, `PAGENTOS_FAKE_CLAUDE_SECONDS`; the fake listener is unchanged).
Nineteen new tests. RED on the batch loop, as behaviour: the third task's worker and the first
task's inspector start while the second task's worker still runs; both worker seats in use beside
an inspection; three workers and two inspectors in flight together and the third inspection
starting when one ends; a changed `cycle-settings.json` honoured at the next refill; a card
stored beside one long run started before that run ends; a task the store put into work on
the files of a run in flight held back while one on other files starts at once; the researcher
beside the worker. RED by the missing function or parameter only: the four unit tests of the
seat fill and the settings, `-MaxHours`, `-RefillSeconds`, `-PollMilliseconds` (two merges in
one poll). Green before and after, as "still holds": two approved tasks with overlapping areas
never in flight together on any status the cycle wrote; the stop flag with a run in flight.
Written after the pool: the limit waited out without blocking; one stop line for two limited
runs; a cycle that dies kills its runs. Seventeen mutations, each restored from a backup copy
with sha256 equal before and after, all RED: the area check removed from the seat fill; the
seat count ignored; one pool of seats for every role; no re-read while runs are in flight; no
refill unless a run ends; the settings file not read; `-MaxHours` not checked; a re-read
replacing the task of a run in flight; a finished run waiting for every other run; caps and
the stop flag not asked at a refill; runs started during the limit's wait; a second run for a
task in flight; the stop line once per limited run; runs left alive when the cycle dies; the
researcher alone again; a late merge refusal not named; no heartbeat from the pool.
After the return, five more tests, each RED on the first pool: three assigned tasks on one worker
seat with the first worktree blocked (the other two end merged); four failed starts in a row and
a fifth task run; a failed start beside a run in flight leaving its seat at once; a researcher
and a lead split that cannot be started (the cycle died with exit 1). Four more mutations, RED
and restored the same way: failed starts not counted against the idle pass; no refill after a
failed start; the researcher's failed start thrown again; the lead's thrown again.
PROVEN_REAL is the Ofis page showing a worker seat refilled while another worker of the same
cycle is still running: NOT_RUN here.

**Known and left.** (a) The Ofis page's capacity is still the server's own number; it does not
read `cycle-settings.json`. (b) `tick.ps1` / `register-nightly.ps1` pass `-MaxParallel` only
(outside this task's area): the new seats take their defaults until the lead passes them or
writes the settings file. (c) A lead split is judged against the queue as it is when the run
ENDS; a task that was in work when the split was written and merged meanwhile no longer
refuses an overlapping split - which is right, and different from the batch loop, where
nothing else ran during a split.
