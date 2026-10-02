## Şu an üzerinde çalışılan
web-voice-test-flakes · area: `apps/web/tests/voice/latency.test.ts`, `store.test.tsx`, `team/plans/web-voice-test-flakes-adr.md` · machine: owner's dev PC, worktree `worker-web-voice-test-flakes` (other workers were running on it)

**sha** `1a87ba5aa66e2d8d4aecc7344658248dd7c9fd7d`, pushed, worktree clean. 3 files changed, all inside the area.

**Verdict: both are (a), the test waits on the wall clock; neither is a product race.**
- **Shared cause:** `tick` was `setTimeout(0)`, which costs a timer period (15.6 ms on Windows) per round instead of one turn of the loop.
- **latency:** the fallback test armed 104 real timers, so it took 1.6 s idle and 3.47 s under load against the 5 s limit. The controller itself was already on the fake scheduler.
- **store:** the rig's controller flushes on a real 250 ms timer, and `FakeCloudCore.closed` survives a second create. The "new" session's first report got 410, so the controller said `closed` whenever the timer beat the assertion. The real server mints a new id per create (`RealtimeSessionRow.id default=uuid4`).

**Fix:** `tick` is now `setImmediate` in both files, and store's test rig starts a new session on `POST /sessions`. No timeout was added or lengthened.

**Counts (PROVEN_AUTOMATED)**

| measurement | BEFORE | AFTER |
| --- | --- | --- |
| latency file, 30 runs, 3 at once | 0 failed, slowest test 1597 ms | 0 failed, 37 ms |
| store file, 30 runs, 3 at once | 0 failed, slowest test 373 ms | 0 failed, 27 ms |
| full suite, 30 runs, 3 at once: latency | 0 failed, slowest 3470 ms | 0 failed, 136 ms |
| full suite, 30 runs, 3 at once: store | 1 failed (`closed` vs `listening`, the reconnect test) | 0 failed, 104 ms |
| full suite, 10 runs in a row | not measured | 10 of 10 green, 2088 tests |

- The card's load (three processes on one file) reproduces neither flake; it took the whole suite three times over.
- **I never reproduced the latency timeout itself:** 0 failures in 108 runs of that file before the change. Its cause rests on the measured duration and the timer count, not on an observed failure.

**RED → GREEN**
- latency, new test "a pumped session arms no real timer": RED `expected 104 to be +0`, then GREEN.
- store, both reconnect tests now flush the new session's first report and assert it was accepted and the controller still listens: RED with no load (`expected { state: 'closed' } to match { state: 'listening' }`), then GREEN. Both files: 28 of 28.

**Mutations** (subjects are outside the area, so they were broken in a scratch copy of `apps/web`; the real tree's sha256 is identical before and after)
- L1 `timing.ts`, probe sleeps on the real clock: latency 5 RED of 16.
- L2 `controller.ts`, `basis` always 0: latency 2 RED.
- S1 `controller.ts`, `connect()` refuses a closed controller: store 2 RED.
- S2 `controller.ts`, next session keeps the ended reporter: store 2 RED, caught only by the new assertions.
- S3 `store.ts`, `connect()` loses its live/busy guard: **not RED**, 12 of 12 pass; the controller's own guard hides it.
- The scratch copy sits one directory deeper, so its one import of the contract JSON got one more `../`.

**Fast checks:** tsc exit 0. oxlint shows one warning in `store.test.tsx` (`closes` scoping) that is also on HEAD.

**For the lead, outside the area and untouched** (details in the ADR text)
1. **The web suite is still not flake-free under three suites at once:** 6 red runs of 30 before, 5 of 30 after, all on the 5 s limit, none investigated.
   - `preview/core-preview`: 4 before, 3 after, up to 17.9 s.
   - `cockpit/creative-panel`: 2 of 30.
   - `uistate/accessibility`: 1 of 30.
   - `pages/discoverability`: 2 of 18.
2. The same `setTimeout(0)` tick is in 20 more test files. `voice/session-storm` took 2.3–3.0 s in 9 of 10 solo runs and timed out once under load; it is the next one.
3. `FakeCloudCore.closed` should be cleared in `fake.ts` itself; the wrapper in `store.test.tsx` can then go.
4. `VoiceRigParts` has no scheduler port, so every store test leaves real 250 ms timers behind.

**NOT_RUN:** a solo 10-in-a-row before the change, and the PowerShell gate script itself (I ran the vitest, tsc and oxlint steps directly).

**Open risk:** the store wrapper models "new session" by clearing `closed` and keeps the fake's single session id.
