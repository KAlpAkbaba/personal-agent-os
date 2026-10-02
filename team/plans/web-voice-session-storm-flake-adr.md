# ADR (unnumbered): `voice/session-storm` waited on the wall clock for the server's answer

Status: proposed by worker `web-voice-session-storm-flake`; the lead numbers it at merge.

## Context

The web suite is a gate step (ADR-0237). The inspector of `web-voice-test-flakes` saw
`apps/web/tests/voice/session-storm.test.ts` ("under a rate limiter, a dead session costs
the client nothing at all") fail 2 of 60 and 7 of 60 full-suite runs under three suites at
once, 0 of 10 alone. The card asks which it is: (a) the test waits on time for something
that has an event, or (b) the product has a race.

## Finding: (a). The test waited on a timer; what failed was the runner's limit.

Reproduced on the unchanged file, 30 full-suite runs, three vitest processes at once:
1 red on this test, at **5011 ms** - the runner's 5 s timeout (`STACK_TRACE_ERROR` from
`@vitest/runner` `chunk-artifact.js:1784`, the timeout's stack), not an assertion. The
same test over those 30 runs: min 2271 ms, median 2365 ms. Alone: 2347 ms.

The lines that show it (base file):

- `:35-37` `settleMacrotasks` is `await new Promise(r => setTimeout(r, 0))` per round.
- `:125-132` `run()` ends every call with `settleMacrotasks(3)`.
- `:280-287` the test calls `run(t.scheduler, 250, 4)` fifty times, after a setup (`:93`)
  that waits six more.

That is 156 real timers (counted: `expected "setTimeout" to be called +0 times, but got
156 times`). `setTimeout(0)` is a timer period, not a turn - 20 of them measured 295 ms
on this machine, 14.8 ms each - so the test spends 2.3 s of its 5 s idle before any load.
The other six tests in the file arm 13 to 31 timers each (read off the same helpers) and
stayed under 720 ms in those runs.

Nothing was waiting for time. The controller's only clock is the injected `FakeScheduler`
(the new pin: 0 real timers over the whole incident). What the waits were FOR is the
server's answer and the controller's reaction to it, and that is a promise chain: the
fake's `Response`, the body read, `VoiceApiError`, `onReportFailure`. The old comment on
`run()` said "a `Response` body is read on a macrotask"; measured on Node 24.15 it
resolves in 3 microtask turns and needs no macrotask at all.

**Not a product race.** With the timers out, no step of the file depends on real time or
on load. The server's request log and event log of all seven original tests, recorded
under the old waits and under the new ones, are identical request for request (paths in
order, events with payloads, legs, credentials minted).

## Decisions

1. Every wait in the file is `answered()`: one event-loop turn (`setImmediate`), repeated
   while a request the client put on the wire is still unanswered (`wire.open`, counted
   beside the fake's answer so the client receives the very promise the server returned).
   No timer anywhere; no timeout added or lengthened; the runner's 5 s stays the hang
   guard it is.
2. When the waits happen did not change (`run()` still gives a few microtask turns per
   step and the full wait every twentieth step and at the end), so what the controller is
   put through is the same.
3. Two tests pin the cause: "the full incident arms no real timer" (`setTimeout` and
   `setInterval` called 0 times over the incident; RED before, 156) and "a wait ends when
   the answer has been acted on" (one request open after the flush fires; after one
   `answered()` none open, one POST, state `closed`).
4. No assertion of the seven original tests was changed.

## Evidence (PROVEN_AUTOMATED; this machine, other workers running on it)

Command, from `apps/web`, three at once, ten rounds:
`node node_modules/vitest/vitest.mjs run --reporter=json --outputFile=<run>.json`

| measurement | BEFORE (`982dc4fb`) | AFTER |
| --- | --- | --- |
| 30 loaded full-suite runs: `session-storm` red | 1 (timeout at 5011 ms) | 0 |
| the rate-limiter test, 30 loaded runs | min 2271, median 2365, max 5011 ms | min 3, median 4, max 95 ms |
| slowest test of the file, 30 loaded runs | 5011 ms | 122 ms |
| the file alone | 7 tests, 4.20 s | 9 tests, 42 ms |
| full suite, 10 runs in a row | not measured | 10 of 10 green, 2106 tests |

1 of 30 is a small count (the inspector's were 2 and 7 of 60); the durations are the
stronger evidence: before, the median run of this test used 47 % of the limit; after, the
slowest test of the file used under 3 %.

Other reds in the same loaded runs, in files outside this card's area: BEFORE
`cockpit/quiet-families` 3 (7.1-8.7 s, all in the first, cold round), `preview/core-preview`
1 (8.7 s), `voice/latency` 1 (5019 ms; ADR-0251 cures it, not yet on this branch's base);
AFTER `voice/store` 1 (`'closed'` vs `'listening'`; ADR-0251 again). The first two are
timeouts too and have no card that I know of.

Mutations of the subject, in a scratch copy of `app/lib` plus the cured test (the subject
is outside the area; the real files' sha256 is unchanged before and after):

- M1 `events.ts`, a 410 no longer ends the reporter (the 2026-09-09 incident): 4 RED,
  among them the rate-limiter test (`expected 8 to be +0`: the limiter is reached) and
  `expected 200 to be 1`.
- M3 `controller.ts`, `reattachLoop` loses its join guard: RED, `expected 21 to be 2`.
- M4 `controller.ts`, `reattach` loses its single flight: RED, `expected 2 to be 1`.
- M5 `events.ts`, the reporter flushes on the real clock: 4 RED, among them both pins.
- M2 `controller.ts`, the 410 is announced through the reporter (`viaReporter: true`):
  NOT RED, 9 of 9. The reporter has ended itself by then; this is the "second wall" the
  comment on `fail()` describes, and no test in this file observes it alone.
- M6 `controller.ts`, `disconnect` flushes but does not dispose the reporter: NOT RED.
- The cure itself, `answered()` back on `setTimeout(0)`: the pin goes RED (51 calls).

## Finding for the lead: a product defect this file does not assert (NOT the flake)

It is deterministic, it is in `app/lib/voice/controller.ts` (outside the area), and
nothing here papers over it or asserts it.

After the server has answered 410, a network flap takes the controller back out of
`closed`, and every flap costs one `POST .../attach` to the session the server declared
gone. Probe (scratch test, removed):

```
after the 410 on /events         state=closed        requests=3
network offline                  state=reconnecting  requests=3
still offline, one minute later  state=reconnecting  requests=3
network back                     state=closed        requests=4   (POST attach -> 410)
second flap                      state=closed        requests=5   (POST attach -> 410)
```

- `controller.ts:2853-2869` (`onReportFailure`, the `gone` branch) patches `state:
  "closed"` but does not set `closing`; neither does `:2788-2791` (`runReattachLoop`).
- `:2739-2748` `onNetworkChange` and `:2750-2762` `onNetworkLost` guard on `closing` and
  on `state === "reconnecting"` only, so a `closed` controller enters `reconnecting`
  (`:2760`) and `reattachLoop` runs when the network returns.

Two consequences: the owner is shown "reconnecting" for a dead session for as long as the
network is down; and the file header's "retries against a session the server declared
gone: 0" holds for `/events` only. In the rate-limiter test the server's log is `contract,
sessions, attach, events, attach, attach, attach, attach`: four attaches after the 410,
one per flap. The test counts `/events` posts and the limiter, so it is green, before and
after this change alike. Bounded by the number of flaps, so not a storm. A card would make
`gone` terminal for the network handlers and add the assertion (attaches after the 410:
0) to this file.

## Known limits

- `answered()` counts a request as answered when the fake's promise settles; the body read
  and the controller's reaction are covered by the event-loop turn, not counted. If a
  later Node reads a body across several turns, the second pin fails rather than flakes.
- `wire` is one counter for the file; tests in a file run one at a time and `beforeEach`
  resets it.
