/**
 * The 2026-09-09 incident driven through the real `VoiceSessionController`, and then
 * driven hard.
 *
 * `session-lifecycle.test.ts` isolates the reporter. This file asserts the same thing
 * where the owner met it: a controller talking to a Cloud Core that has closed the
 * session underneath it. The unit under test is the controller; the only fakes are the
 * ones it already takes (network, mic, playback, transport, scheduler) and an in-memory
 * Cloud Core that answers 410 once closed, exactly as `service.py` does.
 *
 * The acceptance numbers §9 and §10 of the report names are counted here, from the
 * server's own request log rather than from the client's opinion of itself:
 *
 *   * retries against a session the server declared gone: 0
 *   * reporter failures produced by reporting a reporter failure: 0
 *   * 429s provoked by the client's own reconnecting: 0
 *   * timers still armed when the dust settles: 0
 */

import { describe, expect, it } from "vitest";

import { VoiceSessionApi, type Fetcher } from "../../app/lib/voice/api";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
  settle,
} from "../../app/lib/voice/fake";

const settleMacrotasks = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/**
 * A Cloud Core behind a rate limiter, so a client that storms is punished the way the real
 * one was rather than being quietly tolerated by the fake.
 */
function limitedCore(limit: number) {
  const core = new FakeCloudCore({ transport: "webrtc" });
  const seen: string[] = [];
  let rateLimited = 0;
  const fetcher: Fetcher = (path, init = {}) => {
    seen.push(path);
    if (seen.length > limit) {
      rateLimited += 1;
      return Promise.resolve(
        new Response(JSON.stringify({ detail: "rate limited" }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "30" },
        }),
      );
    }
    return core.fetcher(path, init);
  };
  return {
    core,
    fetcher,
    requests: seen,
    get rateLimited() {
      return rateLimited;
    },
  };
}

async function setup(fetcher: Fetcher) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const network = new FakeNetwork();
  const transports: FakeTransport[] = [];
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback: new FakePlayback(scheduler.now, (op) => log.push(op)),
    network,
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
    log: (op) => log.push(op),
  });
  await controller.connect();
  await settleMacrotasks();
  return {
    controller,
    scheduler,
    network,
    log,
    /** One event the controller genuinely reports, from the provider's own wire. */
    speak(at: number): void {
      transports[transports.length - 1].emit({ type: "response_done", at });
    },
    /** A provider function call, which the controller relays to the Cloud Core. */
    toolCall(callId: string): void {
      transports[transports.length - 1].emit({
        type: "tool_call",
        at: 10,
        callId,
        name: "clock.now",
        arguments: {},
      });
    },
  };
}

/**
 * Advance the fake clock and let every promise it started settle.
 *
 * Microtasks between rounds and a real macrotask only every so often: a `Response` body is
 * read on a macrotask, so a POST needs one to resolve, but paying Windows' ~16ms timer
 * granularity two hundred times over would make the stress rounds cost seconds of real
 * time for nothing. The fake clock does not advance on a real wait, so nothing about the
 * ordering changes - only how long the test takes to say so.
 */
async function run(scheduler: FakeScheduler, ms: number, times: number): Promise<void> {
  for (let i = 0; i < times; i += 1) {
    scheduler.advance(ms);
    await settle();
    if (i % 20 === 19) await settleMacrotasks(2);
  }
  await settleMacrotasks(3);
}

const eventPosts = (paths: readonly string[]): number =>
  paths.filter((path) => path.endsWith("/events")).length;

describe("the controller against a session the server has closed", () => {
  it("stops after one 410 and never posts to that session again", async () => {
    const limited = limitedCore(10_000);
    const t = await setup(limited.fetcher);

    // The server closes the session underneath us - an expiry, a restart, another leg's
    // close. From here every call to it answers 410, which is what `service.py` does.
    limited.core.closed = "expired";
    const before = eventPosts(limited.requests);

    t.speak(1_000);
    // Two hundred flush intervals. The production loop managed one POST per interval for
    // as long as the tab stayed open.
    await run(t.scheduler, 250, 200);

    expect(eventPosts(limited.requests) - before).toBe(1);
    expect(limited.rateLimited).toBe(0);
    expect(t.controller.getSnapshot().state).toBe("closed");
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("does not answer a 410 by reporting the 410", async () => {
    const limited = limitedCore(10_000);
    const t = await setup(limited.fetcher);
    limited.core.closed = "expired";
    const before = eventPosts(limited.requests);

    t.speak(1_000);
    await run(t.scheduler, 250, 20);

    // The controller's own error event is the one that used to re-arm the loop. It must
    // never reach the wire, and an explicit flush afterwards must find nothing to send.
    expect(await t.controller.flushEvents()).toBeNull();
    await run(t.scheduler, 250, 20);

    expect(eventPosts(limited.requests) - before).toBe(1);
    // The owner is still told what happened - through the UI state, not the dead channel.
    expect(t.controller.getSnapshot().lastError).toBe("Oturum sunucuda kapanmış.");
  });

  it("a flapping network cannot assemble a reconnect storm", async () => {
    // Requirement §6. The flaps are synchronous ON PURPOSE - that is what a real one
    // looks like, and it is the only shape that produces concurrency: each `online` edge
    // used to start its own reconnect series while the previous one was still awaiting
    // its attach.
    const limited = limitedCore(60);
    const t = await setup(limited.fetcher);

    for (let i = 0; i < 20; i += 1) {
      t.network.set(false);
      t.network.set(true);
    }
    await run(t.scheduler, 100, 40);

    expect(limited.rateLimited).toBe(0);
    const attaches = limited.requests.filter((path) => path.endsWith("/attach")).length;
    // One reconnect, not twenty: the later edges join the series already running.
    expect(attaches).toBe(1);
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("one reconnect settles the session once, not once per trigger", async () => {
    // The series itself coalesces, not only the request underneath it. Without that, each
    // of the twenty triggers rides the shared attach and then declares LISTENING on its
    // own - twenty state events for one reconnect, and twenty resets of the retry budget.
    const limited = limitedCore(200);
    const t = await setup(limited.fetcher);
    const before = limited.core.events.filter((e) => e.kind === "state").length;

    for (let i = 0; i < 20; i += 1) {
      t.network.set(false);
      t.network.set(true);
    }
    await run(t.scheduler, 100, 40);
    await t.controller.flushEvents();
    await settleMacrotasks();

    const listening = limited.core.events
      .slice(before)
      .filter((e) => e.kind === "state" && (e.payload as { state?: string })?.state === "LISTENING");
    // Two, measured, not guessed: the new leg's `connected` frame settles the state
    // once, and the reconnect series settles it again when it declares itself done.
    // Without the coalescing guard the same twenty flaps produce twenty-one.
    expect(listening.length).toBe(2);
  });

  it("a tool relay and a reconnect share one attach", async () => {
    // Requirement §6 at the request level. `relayWithReattach` reclaims a stale leg by
    // calling `reattach()` directly, so the reconnect series' own guard does not cover
    // it: two entirely reasonable callers, one credential, one leg move.
    const limited = limitedCore(200);
    // Assigned synchronously by the Promise executor on the next line.
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let gate = true;
    // Counted at the moment the request is STARTED, not when it completes: the whole
    // question is how many attaches were begun while one was already on the wire.
    const started: string[] = [];
    const gated: Fetcher = async (path, init = {}) => {
      started.push(path);
      if (gate && path.endsWith("/attach")) {
        gate = false;
        await held;
      }
      return limited.fetcher(path, init);
    };
    const attachesStarted = (): number => started.filter((p) => p.endsWith("/attach")).length;
    const t = await setup(gated);

    // Another leg takes the session; a tool call on the LIVE leg finds out the hard way
    // and starts reclaiming it. That attach parks on the wire.
    limited.core.stealLeg();
    t.toolCall("call-race");
    await settleMacrotasks(10);
    expect(attachesStarted()).toBe(1);

    // While it is parked, the network drops and comes back - so the reconnect series
    // decides it needs an attach too. There is one already in flight; it must join it.
    t.network.set(false);
    t.network.set(true);
    await settleMacrotasks(10);

    // The second caller must not have started one of its own.
    expect(attachesStarted()).toBe(1);

    release();
    await run(t.scheduler, 100, 20);

    expect(limited.requests.filter((path) => path.endsWith("/attach")).length).toBe(1);
    expect(limited.rateLimited).toBe(0);
  });

  it("under a rate limiter, a dead session costs the client nothing at all", async () => {
    // The full incident: a session the server has closed, a client that keeps talking,
    // and a limiter with very little patience. The acceptance number is that the limiter
    // is never reached, because the client stops on its own.
    const limited = limitedCore(30);
    const t = await setup(limited.fetcher);
    limited.core.closed = "expired";
    const before = eventPosts(limited.requests);

    for (let round = 0; round < 50; round += 1) {
      t.speak(1_000 + round);
      if (round % 10 === 0) {
        t.network.set(false);
        t.network.set(true);
      }
      await run(t.scheduler, 250, 4);
    }

    expect(limited.rateLimited).toBe(0);
    expect(eventPosts(limited.requests) - before).toBe(1);
    expect(t.scheduler.pendingTimers).toBe(0);
  });
});

describe("closing a session for real", () => {
  it("leaves nothing armed and nothing sendable", async () => {
    const limited = limitedCore(10_000);
    const t = await setup(limited.fetcher);

    await t.controller.disconnect("client_closed");
    await settleMacrotasks();
    const afterClose = limited.requests.length;

    // Anything the page does after the close - a late playback callback, a stray report -
    // must not reach a session that is over.
    t.speak(9_000);
    expect(await t.controller.flushEvents()).toBeNull();
    await run(t.scheduler, 250, 20);

    expect(limited.requests.length).toBe(afterClose);
    expect(t.scheduler.pendingTimers).toBe(0);
  });
});
