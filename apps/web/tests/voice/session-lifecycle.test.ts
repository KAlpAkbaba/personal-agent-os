/**
 * The production incident of 2026-09-09: a closed voice session, a 410 loop, then 429.
 *
 * A real owner `/core` run repeatedly POSTed to
 * `/v1/voice/realtime/sessions/<id>/events` and got `410 Gone` back, over and over, until
 * the calls endpoint answered `429`. `/core` showed `connect_failed`.
 *
 * Traced to source rather than assumed, the loop is exactly four steps and every one of
 * them is in this repository:
 *
 *   1. `EventReporter.flush()` POSTs a batch and it fails. The `.then` that drains the
 *      queue only runs on SUCCESS, so the batch stays queued.
 *   2. The failure sinks fire -> `VoiceController.onReportFailure` -> `error.gone` ->
 *      `this.fail(...)`.
 *   3. `fail()` ends with `this.reporter?.report({kind:"error", ...})` - it reports the
 *      failure THROUGH THE VERY REPORTER whose failure it is handling. A new event is
 *      pushed onto the same queue.
 *   4. `flush()`'s `.finally` sees a non-empty queue and schedules another flush.
 *
 * Round and round, one POST per flush interval, for ever. Nothing about it is a race: it
 * is deterministic, and these tests drive it with the suite's own fake scheduler so it
 * reproduces in milliseconds.
 *
 * Three further defects fall out of the same trace and are covered here too:
 *
 *   * the poster closure resolved `this.sessionId` at SEND time, and creating a new
 *     session overwrote `this.reporter` without ending the old one - so a surviving stale
 *     reporter posted its old events into the NEW conversation;
 *   * `dispose()` cleared the timer but left the queue and the ability to send, so a
 *     callback that landed after a clean close still posted to a closed session;
 *   * nothing anywhere handled `429`, so the storm had no brake of its own.
 */

import { describe, expect, it } from "vitest";

import type { ClientEvent, EventsResponse } from "../../app/lib/voice/contract";
import { EventReporter, MAX_TRANSIENT_ATTEMPTS } from "../../app/lib/voice/events";
import { VoiceApiError } from "../../app/lib/voice/api";
import { FakeScheduler, settle } from "../../app/lib/voice/fake";

const accepted = (events: ClientEvent[]): EventsResponse => ({
  accepted: events.length,
  resolved_intents: [],
  pending_sideband: [],
  state: {} as EventsResponse["state"],
});

function apiError(status: number, retryAfter: string | null = null): VoiceApiError {
  return new VoiceApiError(
    status,
    "/v1/voice/realtime/sessions/sess-A/events",
    { detail: "gone" },
    retryAfter,
  );
}

/** Move time on and let the flush promises settle, the way real time would. */
async function tick(scheduler: FakeScheduler, ms: number, times = 1): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    scheduler.advance(ms);
    await settle();
  }
}

/** A reporter whose every POST fails with `status`, and a record of what it was sent. */
function failingReporter(status: number, retryAfter: string | null = null) {
  const scheduler = new FakeScheduler();
  const posts: Array<{ session: string; count: number }> = [];
  const reporter = new EventReporter(
    "sess-A",
    (session, events) => {
      posts.push({ session, count: events.length });
      return Promise.reject(apiError(status, retryAfter));
    },
    () => scheduler.now(),
    { flushIntervalMs: 250, scheduler },
  );
  return { scheduler, posts, reporter };
}

describe("a 410 from the events endpoint is terminal for that session", () => {
  it("does not retry the same batch against the same dead session for ever", async () => {
    const { scheduler, posts, reporter } = failingReporter(410);

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    // Ten flush intervals. Against the unfixed reporter this produced ten POSTs.
    await tick(scheduler, 250, 10);

    expect(posts.length).toBe(1);
    expect(reporter.ended).toBe(true);
    expect(reporter.endReason).toBe("gone");
    expect(scheduler.pendingTimers).toBe(0);
  });

  it("drops the queue and refuses new events once it is gone", async () => {
    const { scheduler, posts, reporter } = failingReporter(410);

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250);
    expect(posts.length).toBe(1);

    // Anything reported afterwards - including the controller's own error event - must
    // not resurrect the loop. `report()` still returns a shaped event, because callers
    // read the return value; it simply goes nowhere.
    const orphan = reporter.report({
      kind: "error",
      payload: { error_class: "client_error", message: "x" },
    });
    expect(orphan.kind).toBe("error");
    await tick(scheduler, 250, 4);

    expect(posts.length).toBe(1);
    expect(reporter.pending).toBe(0);
    expect(scheduler.pendingTimers).toBe(0);
  });

  it("reporting a failure through the failing reporter cannot re-arm it", async () => {
    // The production loop's step 3, isolated: the failure sink does exactly what
    // `VoiceController.fail()` used to do, and the reporter must survive it regardless.
    const { scheduler, posts, reporter } = failingReporter(410);
    let sinkCalls = 0;
    reporter.onFailure(() => {
      sinkCalls += 1;
      reporter.report({ kind: "error", payload: { error_class: "client_error", message: "x" } });
    });

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250, 10);

    expect(posts.length).toBe(1);
    expect(sinkCalls).toBe(1);
    expect(scheduler.pendingTimers).toBe(0);
  });

  it("holds the reentrancy guard even on a session that is still alive", async () => {
    // The recursion is broken structurally, not only by 410 being terminal: a sink that
    // reports during a TRANSIENT failure must not arm a second timer either. Otherwise
    // the same loop returns the moment a 503 stands in for a 410.
    const { scheduler, posts, reporter } = failingReporter(503);
    reporter.onFailure(() => {
      reporter.report({ kind: "error", payload: { error_class: "client_error", message: "x" } });
    });

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250, 20);

    // Bounded: it retries a handful of times and then gives up, rather than once per
    // interval for as long as the tab is open.
    expect(posts.length).toBe(MAX_TRANSIENT_ATTEMPTS);
    expect(reporter.endReason).toBe("exhausted");
    expect(scheduler.pendingTimers).toBe(0);
  });
});

describe("a session is the unit a reporter belongs to", () => {
  it("posts only ever to the session it was built for", async () => {
    const scheduler = new FakeScheduler();
    const sessions: string[] = [];
    const reporter = new EventReporter(
      "sess-A",
      (session, events) => {
        sessions.push(session);
        return Promise.resolve(accepted(events));
      },
      () => scheduler.now(),
      { flushIntervalMs: 250, scheduler },
    );

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250);

    expect(sessions).toEqual(["sess-A"]);
    expect(reporter.session).toBe("sess-A");
  });

  it("a superseded reporter never posts again, however much is queued", async () => {
    // Rebinding used to overwrite `this.reporter` and leave the old one's timer running.
    // Its poster read the CONTROLLER's current session id at send time, so the old
    // conversation's events would have landed in the new one.
    const scheduler = new FakeScheduler();
    let posts = 0;
    const old = new EventReporter(
      "sess-A",
      (_session, events) => {
        posts += 1;
        return Promise.resolve(accepted(events));
      },
      () => scheduler.now(),
      { flushIntervalMs: 250, scheduler },
    );

    old.report({ kind: "state", payload: { state: "LISTENING" } });
    old.end("superseded");
    old.report({ kind: "state", payload: { state: "THINKING" } });
    await tick(scheduler, 250, 4);

    expect(posts).toBe(0);
    expect(old.pending).toBe(0);
    expect(scheduler.pendingTimers).toBe(0);
  });

  it("a cleanly closed session cannot be posted to by a late callback", async () => {
    // Requirement §7. `dispose()` used to clear the timer only; a callback that fired
    // after the close still queued an event and an explicit flush still sent it.
    const scheduler = new FakeScheduler();
    let posts = 0;
    const reporter = new EventReporter(
      "sess-A",
      (_session, events) => {
        posts += 1;
        return Promise.resolve(accepted(events));
      },
      () => scheduler.now(),
      { flushIntervalMs: 250, scheduler },
    );

    reporter.dispose();
    reporter.report({ kind: "playback_stopped", payload: {} });
    expect(await reporter.flush()).toBeNull();
    await tick(scheduler, 250, 4);

    expect(posts).toBe(0);
    expect(reporter.endReason).toBe("closed");
    expect(scheduler.pendingTimers).toBe(0);
  });
});

describe("429 has a brake of its own", () => {
  it("backs off on a bounded schedule and then stops", async () => {
    const { scheduler, posts, reporter } = failingReporter(429);

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    // Well past the longest backoff step, many times over.
    await tick(scheduler, 1_000, 60);

    // Three backoff steps means at most four attempts, not one per flush interval.
    expect(posts.length).toBeLessThanOrEqual(4);
    expect(reporter.ended).toBe(true);
    expect(scheduler.pendingTimers).toBe(0);
  });

  it("a failure sink's own report cannot shorten the backoff it is reporting", async () => {
    // What the reentrancy guard is actually FOR. `VoiceController` reports on failure, so
    // a sink runs inside the catch. Without the guard that sink's `report()` arms the
    // timer first, at the flush interval - and the backoff the failure branch went on to
    // compute is then silently discarded, because `arm()` will not replace a live timer.
    // The client keeps its 250ms cadence into a server that just said 429.
    const { scheduler, posts, reporter } = failingReporter(429);
    reporter.onFailure(() => {
      reporter.report({ kind: "error", payload: { error_class: "client_error", message: "x" } });
    });

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250);
    expect(posts.length).toBe(1);

    // Three flush intervals inside the first backoff step (1s) must buy nothing at all.
    await tick(scheduler, 250, 3);
    expect(posts.length).toBe(1);

    await tick(scheduler, 250);
    expect(posts.length).toBe(2);
  });

  it("waits the server's Retry-After rather than its own schedule", async () => {
    // The first local step is 1s. With `Retry-After: 30`, 5s of time must buy nothing.
    const { scheduler, posts, reporter } = failingReporter(429, "30");

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250);
    expect(posts.length).toBe(1);

    await tick(scheduler, 1_000, 5);
    expect(posts.length).toBe(1);

    await tick(scheduler, 30_000);
    expect(posts.length).toBe(2);
  });
});

describe("a session that is still alive is unaffected", () => {
  it("keeps batching and retrying an ordinary transient failure", async () => {
    const scheduler = new FakeScheduler();
    let posts = 0;
    const reporter = new EventReporter(
      "sess-A",
      (_session, events) => {
        posts += 1;
        if (posts === 1) return Promise.reject(apiError(503));
        return Promise.resolve(accepted(events));
      },
      () => scheduler.now(),
      { flushIntervalMs: 250, scheduler },
    );

    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await tick(scheduler, 250, 2);

    expect(posts).toBe(2);
    expect(reporter.pending).toBe(0);
    expect(reporter.ended).toBe(false);
  });
});
