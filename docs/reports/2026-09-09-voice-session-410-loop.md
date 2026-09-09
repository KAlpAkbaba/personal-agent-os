# Production incident, 2026-09-09 — a closed voice session, a 410 loop, then 429

**Status:** fixed, qualified by automated tests. Runtime verification pending release.
**Severity:** the voice surface was unusable for the owner (`/core` showed `connect_failed`).
**Affected:** `apps/web` — the browser voice client. No server change was needed and none
was made: the Cloud Core behaved correctly throughout.

---

## 1. What the owner saw

`/core` said `connect_failed`. Behind it, the browser POSTed to
`/v1/voice/realtime/sessions/<id>/events` over and over, receiving `410 Gone` each time,
until the calls endpoint began answering `429 Too Many Requests`.

## 2. Root cause

Traced to source and reproduced deterministically. It is not a race — it is a cycle, and
every step of it was in this repository.

1. `EventReporter.flush()` POSTs a batch and it fails. The `.then` that drains the queue
   runs only on SUCCESS, so the batch stays queued. This is deliberate and correct: a
   `network_lost` event must survive the network loss that caused it.
2. The failure sinks fire → `VoiceController.onReportFailure` → `error.gone` → `fail(...)`.
3. `fail()` ended with `this.reporter?.report({kind: "error", ...})` — it reported the
   failure **through the very reporter whose POST had just failed**, pushing a new event
   onto the queue that had failed to drain.
4. `flush()`'s `.finally` saw a non-empty queue and armed the flush timer again.

One request per flush interval (250 ms), for ever, against a session the server had
already declared gone. The `429` was not a separate fault: it was the rate limiter doing
its job against a client that would not stop.

Nothing in the client treated `410` as different from any other failure, so there was no
point at which the cycle could end on its own.

### Three defects fall out of the same trace

* **Session ownership.** `EventReporter`'s poster was a closure over the controller
  (`(events) => this.deps.api.events(this.sessionId, events)`), resolving `this.sessionId`
  at SEND time. Creating a new session overwrote `this.reporter` without ending the old
  one. A surviving stale reporter would therefore have posted one conversation's events
  into the next conversation's session.
* **Clean close.** `dispose()` cleared the flush timer but left the queue and the ability
  to send. A callback landing after a successful close still queued, and an explicit flush
  still sent, to a session that was over.
* **Reconnect fan-out.** `reattachLoop()` had three entry points and `reattach()` a fourth;
  nothing coordinated them. `clearReattachTimer()` cancels a *pending* retry, not one
  already awaiting `POST .../attach`. A flapping network started one reconnect series per
  `online` edge, each with its own attach in flight — a reconnect storm assembled out of
  individually reasonable decisions, and the second half of how this reached a 429.

## 3. What was changed

Ownership and terminality, not backoff. None of the forbidden shortcuts were taken: `410`
is not ignored, not converted, rate limits were not raised, the retry loop was not merely
slowed, and event reporting was not disabled.

### `apps/web/app/lib/voice/events.ts`

| Change | Why |
| --- | --- |
| `EventsPoster` now takes `(sessionId, events)` | The reporter hands over its OWN id. A reporter can only ever post to the session it was built for — structurally, not by convention. |
| `EventReporter(sessionId, …)` | The session is the reporter's identity for its whole life. |
| `end(reason)`, `ReporterEndReason` | Terminal: timer cleared, queue **dropped**, further `report()` and `flush()` refused. Dropping is deliberate — every reason means the events cannot reach where they belong, and the only thing worse than losing telemetry is filing it under the wrong conversation. |
| `410` → `end("gone")`, decided **before** the failure sinks run | Whatever a sink does — including reporting the failure — it cannot revive an ended reporter. |
| `notifyingFailure` reentrancy guard | While sinks run, a sink's own `report()` cannot arm the timer. Without it, a sink's report arms at the flush interval and the backoff the failure branch went on to compute is silently discarded. |
| `429` → bounded backoff, `Retry-After` honoured | 1 s / 4 s / 15 s, then `end("exhausted")`. A brake, not the fix. |
| Transient failures bounded by `MAX_TRANSIENT_ATTEMPTS = 5` | 5xx and network failures retried a bounded number of times rather than for ever. |
| `dispose()` → `end("closed")` | A clean close is now terminal. `VoiceController.close` already flushes first, so no final batch is lost. |
| `statusOf()`, `retryAfterMs()` | Duck-typed rather than importing `VoiceApiError` (that would be a cycle). `Retry-After` accepts seconds and HTTP-date, and is capped at 60 s so a header cannot park the client for an hour. |

### `apps/web/app/lib/voice/controller.ts`

| Change | Why |
| --- | --- |
| `this.reporter?.end("superseded")` before rebinding | Atomic rebind: no timer, no in-flight retry and no queued event from the old conversation outlives it. |
| Poster is `(sessionId, events) => api.events(sessionId, events)` | The id comes from the reporter, never from `this`. |
| `fail(msg, lines, { viaReporter })` | A failure in the telemetry channel is not announced through that same channel. Every other `fail()` call site is a genuine controller failure and still reports. |
| `429` branch in `onReportFailure` | Records it for the owner and does **not** reconnect. |
| `reattachLoop()` coalesces | A trigger arriving while the series runs joins it instead of starting a second one with its own backoff schedule and attempt count. |
| `reattach()` is single-flight | A tool relay reclaiming a stale leg and a reconnect series share the one attach on the wire, rather than minting two credentials and moving the leg twice. |

### `apps/web/app/lib/voice/api.ts`

`VoiceApiError` carries `retryAfter`, captured where the error is built — the only place
the `Response` still exists. Without it the reporter's `Retry-After` handling was
satisfiable only by the local schedule; the header the server sent was read by nobody.

## 4. Evidence

### Reproduction against the unfixed code

`tests/voice/session-lifecycle.test.ts` drives the reporter with the suite's own
`FakeScheduler`. Against the code as it shipped, three of its four original assertions
failed, the first with **10 posts for 10 flush intervals** (`expected 10 to be less than
or equal to 1`) — the production cadence, exactly.

### Suites

* `apps/web/tests/voice/session-lifecycle.test.ts` — 12 tests, the reporter in isolation.
* `apps/web/tests/voice/session-storm.test.ts` — 7 tests, the **real `VoiceSessionController`**
  against an in-memory Cloud Core that answers 410 once closed, as `service.py` does. The
  acceptance numbers are counted from the server's request log, not the client's opinion
  of itself: 200 flush intervals after a 410 produce exactly **one** POST; a rate limiter
  set to 30 requests is **never reached**; timers armed when the dust settles: **0**.

### Mutation check — the tests bite

Each guard was reverted to its old behaviour, one at a time, and the suites re-run.
**11 mutations, 11 caught**, each by a named test:

| Mutation (the old behaviour restored) | Caught by |
| --- | --- |
| 410 is not terminal | does not retry the same batch against the same dead session for ever (+5 more) |
| an ended reporter queues and schedules again | drops the queue and refuses new events once it is gone |
| no reentrancy guard | a failure sink's own report cannot shorten the backoff it is reporting |
| `end()` keeps the queue | a superseded reporter never posts again, however much is queued |
| `dispose()` clears the timer only | a cleanly closed session cannot be posted to by a late callback |
| `Retry-After` ignored | waits the server's Retry-After rather than its own schedule |
| 429 unbounded | backs off on a bounded schedule and then stops |
| transient retry unbounded | holds the reentrancy guard even on a session that is still alive |
| reporter posts under another session's id | posts only ever to the session it was built for |
| reconnect not single-flight | one reconnect settles the session once, not once per trigger |
| attach not single-flight | a tool relay and a reconnect share one attach |

Two assertions were set from **measurement**, not expectation: one reconnect settles the
session twice (the new leg's `connected` frame, and the series declaring itself done)
against **21** without the coalescing guard; and the racing attach count is **1** against
**2**.

### Gates

* `tsc --noEmit`: clean.
* `oxlint` on every changed file: clean.
* `vitest run` (whole web app): **79 files, 1509 tests, all passing.**

## 5. What is NOT claimed

The controller's `viaReporter: false` is defence in depth, not the load-bearing fix — the
reporter refuses to be revived on its own account, so reverting that one line alone does
not reproduce the loop. It stays because a failure in the telemetry channel must not be
announced through that channel, and saying so where someone would otherwise reintroduce it
is the point.

## 6. Proof marks

| Claim | Mark |
| --- | --- |
| Root cause identified from source | `PROVEN_REAL` — reproduced deterministically before the fix |
| 410 terminal; no loop; no recursion; no runaway timer | `PROVEN_AUTOMATED` |
| Bounded 429 backoff honouring `Retry-After` | `PROVEN_AUTOMATED` |
| Atomic rebind; queue owned by session | `PROVEN_AUTOMATED` |
| Clean close prevents later sends | `PROVEN_AUTOMATED` |
| Single-flight reconnect and attach | `PROVEN_AUTOMATED` |
| Behaviour on the deployed Cloud Core | `NOT_YET_PROVEN` — pending release and runtime check |
