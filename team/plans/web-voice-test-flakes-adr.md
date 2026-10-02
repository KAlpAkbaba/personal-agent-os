# ADR (unnumbered): two voice tests waited on the wall clock (web-voice-test-flakes)

Status: proposed by worker `web-voice-test-flakes`; the lead numbers it at merge.

## Context

The web suite is a gate step since ADR-0237. The inspector of `office-worker-seats` saw 3
red runs in 17, one failure each: `tests/voice/latency.test.ts` (5 s timeout) and
`tests/voice/store.test.tsx` ('closed' where 'listening' was expected). The card asks which
it is for each: (a) the test waits on wall-clock time for something that has an event, or
(b) the product has an ordering race.

## Finding: both are (a). Neither is a product race.

**One shared cause.** Both files had `tick = await new Promise(r => setTimeout(r, 0))`. A
`setTimeout(0)` is not "the next turn": it waits a timer period, 15.6 ms on Windows. What
the tests wait for is a promise chain that is already resolving (the fake Cloud Core's
answer, the probe's next poll after `FakeScheduler.advance()` fired its sleep) - an event,
not a time.

**latency.test.ts.** `pump()` is `advance(10)` + `tick(4)` per step. The fallback test pumps
250 ms: 104 real timers, 1.6 s idle, 3.47 s measured under three suites at once; the
runner's limit is 5 s. The controller itself is on the injected `FakeScheduler` and was
never the one waiting. I did NOT reproduce the timeout itself (0 failures in 108 runs of
this file before the change); the cause is the measured duration, 69 % of the limit, and
the count of real timers, not an observed failure.

**store.test.tsx.** Two things had to line up:
1. A rig has no scheduler port, so in this file the controller runs on the REAL scheduler
   and its `EventReporter` flushes on a real 250 ms timer.
2. `FakeCloudCore` (`app/lib/voice/fake.ts`) has one session id and one `closed` marker for
   its whole life. After disconnect + connect, the "new" session is the closed one, and its
   first report is answered 410; `onReportFailure` then sets `state: "closed"`.
The test asserted 'listening' after `tick()` (six timers, ~95 ms idle). It held only while
the assertion ran before the 250 ms flush. Reproduced: 1 of 30 full-suite runs under load
(`reconnect closes the open leg...`, 646 ms), and deterministically, with no load, by
flushing explicitly before the assertion (2 tests RED: `expected { state: 'closed' } to
match { state: 'listening' }`).
The product is not wrong here: the Cloud Core mints an id per create
(`RealtimeSessionRow.id default=uuid.uuid4`), so a new session's report is not answered
410, and a controller that says "closed" on a 410 is the designed behaviour. The fake was
unlike the server, and the timer decided whether the test noticed.

## Decisions

1. `tick` in both files is one event-loop turn (`setImmediate`), no timer. No timeout was
   added or lengthened anywhere; the runner's 5 s stays as the hang guard it is.
2. `latency.test.ts` gains a test that pins the cause: over a pumped session with a probe
   that runs its whole bound, `setTimeout` and `setInterval` are called 0 times - by the
   controller (the injected scheduler is the only clock) and by the file's own helpers.
   RED before (104 calls).
3. `store.test.tsx`: the rig's fetcher starts a NEW session on `POST /sessions` (clears the
   fake's `closed`), as the server does; and the two reconnect tests flush the new
   session's first report and assert it was ACCEPTED (2 `LISTENING` reports on the server)
   and that the controller is still listening with no error. The assertion no longer
   depends on which timer fires first, and it is stronger than before: mutation S2 below is
   caught only by the new lines.

## Evidence (PROVEN_AUTOMATED; this machine, other workers running on it)

| measurement | BEFORE | AFTER |
| --- | --- | --- |
| latency file, 30 runs, 3 vitest at once | 0 failed; slowest test 1597 ms | 0 failed; slowest test 37 ms |
| store file, 30 runs, 3 vitest at once | 0 failed; slowest test 373 ms | 0 failed; slowest test 27 ms |
| full suite, 30 runs, 3 at once: latency | 0 failed; slowest 3470 ms, 2 runs over 2 s | 0 failed; slowest 136 ms |
| full suite, 30 runs, 3 at once: store | 1 failed ('closed' vs 'listening') | 0 failed; slowest 104 ms |
| full suite, 10 runs in a row | not measured | 10 of 10 green, 2088 tests |

The card's load (three processes on one file) does not reproduce either flake: a single
file is one worker, which is no load. The failures need the whole suite three times over.

Mutations of the subjects, in a scratch copy of `apps/web` (the subjects are outside the
area; the real tree's sha256 is unchanged):
- L1 `timing.ts`, the probe sleeps on the real clock: latency 5 RED of 16.
- L2 `controller.ts`, `basis` always 0: latency 2 RED.
- S1 `controller.ts`, `connect()` refuses a closed controller: store 2 RED.
- S2 `controller.ts`, the next session keeps the ended reporter: store 2 RED (`expected 1
  to be 2`), by the new assertions only.
- S3 `store.ts`, `connect()` loses its live/busy guard: NOT RED, 12 of 12 pass. The
  controller's own guard makes the same refusal, so the store's is unobserved.

## For the lead (outside this card's area; nothing was changed there)

1. **`FakeCloudCore.closed` outlives the session** (`app/lib/voice/fake.ts`). The fix
   belongs there (a create clears it, or mints an id); the wrapper in `store.test.tsx` can
   then go. `session-storm.test.ts` sets `core.closed` by hand and must be read first.
2. **The same `setTimeout(0)` tick is in 20 more test files** (18 under `tests/voice`,
   plus `eye/browser-frame-source`, `research/poll`). `voice/session-storm.test.ts`
   ("under a rate limiter...") took 2.3-3.0 s in 9 of 10 solo runs and hit the 5 s limit
   once under load. It is the next one.
3. **Other files still fail under three suites at once**, all on the 5 s runner limit, none
   investigated: `preview/core-preview.test.tsx` (4 of 30 before, 3 of 30 after; up to
   17.9 s), `cockpit/creative-panel.test.tsx` (2 of 30), `uistate/accessibility.test.ts`
   (1 of 30), `pages/discoverability.test.tsx` (2 of 18). The suite is NOT flake-free
   under that load after this card: 6 red runs of 30 before, 5 of 30 after.
4. **A rig has no scheduler port** (`VoiceRigParts`), so every store test leaves real
   250 ms timers behind. With one, the store tests could run on the `FakeScheduler`.
5. Mutation S3 above: the store's connect guard has no test of its own.
