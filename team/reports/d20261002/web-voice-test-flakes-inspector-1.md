**Inspector report — web-voice-test-flakes @ `1a87ba5a` (cycle d20261002)**

The worker's diagnosis holds for both files: (a) the test waited on the wall clock, no product race. The commit touches only the three files in the area; nothing in it touches a table, migration, script or container.

**Pass 1 — run from a clean tree**
- The two files: 28 of 28. `tsc` exit 0; oxlint exit 0, one `closes` scoping warning in `store.test.tsx` that is on a line the diff does not touch.
- Full suite: 122 files / 2088 tests green; 10 of 10 in a row.

| measurement (3 vitest at once) | BEFORE (base test files, scratch copy) | AFTER |
| --- | --- | --- |
| latency file, 30 runs | 0 failed, slowest 1595 ms | 0 failed, 45 ms |
| store file, 30 runs | 0 failed, slowest 371 ms | 0 failed, 33 ms |
| full suite, 60 runs: latency | **1 failed at 5024 ms** (the fallback test); 4676 ms in the other batch | 0 failed, slowest 105 ms |
| full suite, 60 runs: store | 0 failed, slowest 464 ms | 0 failed, slowest 127 ms |
| full suite, 60 runs: `session-storm` | 2 failed | 7 failed |

- **I reproduced the latency timeout the worker never saw**, on the exact test their ADR names. Their cause is now observed, not inferred.
- I did not reproduce the store flake under load, but it is deterministic once the wrapper is removed (below).

**RED-first, redone in a scratch copy**
- Latency `tick` back to `setTimeout(0)`: the new test goes RED, `expected 104 to be +0`.
- Store wrapper no longer clears `closed`: both reconnect tests RED, `state: 'closed'` vs `'listening'`.
- Store `tick` back to `setTimeout(0)` with the wrapper kept: 28 of 28 pass. Nothing pins the store file's tick; it is speed only.

**My mutations of the subjects** (scratch copy, different from the worker's)
- `events.ts`, reporter ignores the injected scheduler: latency RED (`expected 1 to be +0`).
- `timing.ts`, probe bound three times too long: latency 2 RED.
- `store.ts`, `reconnect()` skips the disconnect: store RED (`expected 1 to be 2`).
- `controller.ts`, `disconnect()` never closes the server session: store RED.
- `controller.ts`, `connect()` does not reset `closing`: **not RED** in these two files.
- `controller.ts`, `connect()` does not reset `closedByUs`: **not RED** in these two files.
- The last two are not behaviours the changed tests name; I did not run the rest of `tests/voice` against them.

**Pass 2 — tried to break it**
- **Does the wrapper hide a product race?** No. Each `EventReporter` is bound to its own session id, and `connect()` ends the previous one before building the next. The server mints an id per create (`RealtimeSessionRow.id default=uuid.uuid4`). The fake reusing one id and one `closed` marker was the only path to the 410.
- **Timeouts:** none added or lengthened; the runner's 5 s is untouched. The new store assertions are stronger than the old ones.
- **Secrets, paths, contract drift:** none; test files only.
- **Open risk, as the worker said:** the wrapper lives in the test, not in `fake.ts`, so other files using `FakeCloudCore` still have the stale `closed`.

**For the lead**
- `voice/session-storm` ("under a rate limiter…") is the remaining red under three suites at once: 7 of 60 after, 2 of 60 before, 0 of 10 solo. The file is unchanged, and 7 against 2 is not enough to call a real increase. It needs its own card.
- I saw no failures in `preview/core-preview`, `cockpit/creative-panel`, `uistate/accessibility` or `pages/discoverability` in 120 loaded full-suite runs; the worker's counts for those are unconfirmed.

**Evidence classes**
- Before/after counts, RED-first, mutations: PROVEN_AUTOMATED.
- `quality-gate.ps1` itself: NOT_RUN. The branch is a worker branch; I ran its three web steps (oxlint, vitest, tsc) directly.
- The real tree's sha256 is identical before and after, the scratch copy is removed, `git status` is clean, and nothing is left running.

APPROVE
