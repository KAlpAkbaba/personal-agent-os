# ADR-0214 addendum (number by the lead) - the pool's tests decide the order of runs with a barrier, not a clock

Status: proposed by the worker of `cycle-pool-test-barriers` (cycle d20261003).

## Context

The second inspection of `cycle-seat-pool` (team/reports/d20261002/cycle-seat-pool-inspector-2.md,
findings 1 and 2) approved the pool and left two things:

1. Five cases of `scripts/tests/team-cycle.tests.ps1` were timing-shaped. "the pool: seats are per
   role ..." read a copy of the live status that the fake took one second after a run started; at
   the heaviest load it was taken about ten seconds late (`ins-c:inspector,wrk-d:inspector,wrk-f:worker`)
   and the case failed once in six runs on unmodified code. Four store cases pinned the cycle's poll
   at six seconds (`$onePoll = "-PollMilliseconds 6000"`) and hoped both runs of a pair ended inside
   one poll; at about forty processes three failed. The suite is a step of the full gate.
2. `-MaxRunsPerTask` under the pool was said to hold "by the tests that held it"; no test asserted
   the stop, and the inspector's probes P1 and P2 were not committed.

## Decision

The fake (`scripts/tests/lib/fake-claude.ps1`) gains a barrier, in the shape of its other switches:

- `PAGENTOS_FAKE_CLAUDE_MARKERS` (a folder): every run first writes `<role>-<task>.started`, holding
  the runs in flight as the markers say, and `<role>-<task>.ended` just before it answers, holding
  how its barrier ended (`file`, `guard`, `none`).
- `PAGENTOS_FAKE_CLAUDE_BARRIER`: `<role>:<task>=<file>[+<file>...]`, comma separated: that run waits
  after its `.started` until every file exists (a bare name is in the markers folder).
- `PAGENTOS_FAKE_CLAUDE_BARRIER_SECONDS` (default 60) is a hang guard only: a run let go by it says
  `guard`, and every rewritten case asserts `file`. A broken cycle makes a case RED, never a hang.

With nothing configured the fake answers byte for byte as before and writes no marker (its own
test checks both; the existing self-test of the fake is unedited).

The cycle is one thread: once a barrier's file exists, nothing the cycle does with another run can
come before the step that made the file is over. The barriers use that.

| case | before (pin) | after (marker / barrier) |
|---|---|---|
| each run's result is written when it is applied | `-PollMilliseconds 6000`; tail of five requests | task-one's worker waits for `inspector-task-two.started`; task-two's inspector waits for task-one's file on the integration branch; tail of six (the refill's read now sits between) |
| a merge whose write could only be tried at the batch's end ... NAMES the merge | `-PollMilliseconds 6000` | the same two barriers; assertions unchanged |
| a refused 'merged' is not forgotten ... | `-PollMilliseconds 6000` | the same two barriers; assertions unchanged |
| a refused 'merged' is TAKEN BACK ... | `-PollMilliseconds 6000`; fault `times = 2`; the exact list "task-two 503, task-one 409, task-two 503" | the same two barriers; the fault fails EVERY inspector write of task-two as 'inspecting' (the count of saves between them was the batch's); asserted: a task-two 503 before the refusal, and "look, task-one 409, task-two 503" adjacent - one save |
| the pool: seats are per role ... | status copies one second after wrk-f's and ins-c's starts (`SNAPSHOT_SECONDS 1`, run lengths 10/20 s) | in the store (every status kept): ins-a waits for the four other starts; ins-b and the workers wait for `inspector-ins-c.started`; asserted: a status names the five, no status names three inspectors, ins-c's `.started` names `ins-b, ins-c, wrk-d, wrk-e, wrk-f`, the status says the same |

Kept as a pin, for its own sake: "the pool: two approved inspections that end in the same poll
are merged one after the other" (`-PollMilliseconds 5000`) - the same poll IS its claim.

Two cases are added: "-MaxRunsPerTask holds under the pool" (P1: `-MaxRunsPerTask 1`, two tasks side
by side, two calls, both stopped with "bu döngüde 1 koşu yapıldı ve iş bitmedi") and "two RETURNs stop
a task while another task's worker is in flight" (P2: task-two's worker waits until the cycle has
collected task-one's second inspection - its report file - so the stop is applied while it runs).

## Evidence (worker, 2026-10-03)

- Under load (team-feed, team-area and script-syntax in a loop beside; 13-81 `powershell.exe`, 18-21
  `claude.exe`): BEFORE 1 failure in 150 (seats per role, "five runs ..."), AFTER 0 in 150.
- Mutations of a scratch copy (outside the repository): seat count ignored, one pool for every role,
  a full seat stops the queue -> seats per role RED; no re-read while runs are in flight and a result
  written only at the end -> "written when applied" RED; a late merge not named -> "NAMES the merge"
  RED; refusals of a failed save not kept / a waiting merge not remembered -> "not forgotten" RED;
  no undo -> "TAKEN BACK" RED; run count check removed -> P1 RED; RETURNs never stop -> P2 RED.
  "A candidate chosen in this refill does not hold its files" turns none of these five RED, nor the
  cycle case "two tasks with overlapping areas" (the move into work already keeps such a pair apart,
  Get-TeamAreaHolders); it is held by the function case "seats: a task whose area overlaps ...",
  which goes RED (`worker:task-one,worker:task-two,worker:task-three`).

## Consequences

A test of the pool now says which run waits for which event; a late snapshot can no longer fail it,
and a cycle that never produces the event fails it after the guard instead of hanging.
