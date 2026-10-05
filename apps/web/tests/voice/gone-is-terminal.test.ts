/**
 * "Gone" is the server's last word (ADR-0251 addendum 1, the finding for the lead).
 *
 * A 410 used to move the controller to `closed` without making it terminal for the network
 * handlers: a network flap took it back to `reconnecting` - the owner was shown
 * "yeniden bağlanıyor" for a dead session - and every return of the network cost one
 * `POST .../attach` to the session the server had declared gone. The probe's counts on the
 * unchanged controller: 3 requests after the 410, 4 after the first flap, 5 after the second.
 *
 * Two entry points reach the 410 (the reporter's `/events` and the reconnect series'
 * `/attach`), so two cases; then the two things the cure must not break: a NEW session after
 * the gone one, and a controller that is merely disconnected.
 *
 * Every wait is for an answer, never for the wall clock (ADR-0251): the controller's only
 * clock is the injected `FakeScheduler`.
 */

import { createHash } from "node:crypto";

import { beforeEach, describe, expect, it } from "vitest";

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

/** Requests on the wire the server has not answered yet. */
const wire = { open: 0 };

beforeEach(() => {
  wire.open = 0;
});

/** Wait until every request on the wire has been answered and the answer acted on. */
async function answered(): Promise<void> {
  do {
    await new Promise<void>((resolve) => setImmediate(resolve));
  } while (wire.open > 0);
}

async function setup() {
  const core = new FakeCloudCore({ transport: "webrtc" });
  // While `held` is set, an attach reaches the server (recorded, answered) but its answer
  // waits for `release()`; it does not count as open, so `answered()` does not wait for it.
  let held: Array<() => void> | null = null;
  const fetcher: Fetcher = (path, init = {}) => {
    if (held && path.endsWith("/attach")) {
      const answer = core.fetcher(path, init);
      const queue = held;
      return new Promise<void>((resolve) => queue.push(resolve)).then(() => answer);
    }
    wire.open += 1;
    const response = core.fetcher(path, init);
    const done = (): void => {
      wire.open -= 1;
    };
    void response.then(done, done);
    return response;
  };
  const scheduler = new FakeScheduler();
  const network = new FakeNetwork();
  const transports: FakeTransport[] = [];
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback: new FakePlayback(scheduler.now),
    network,
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
  });
  await controller.connect();
  await answered();
  return {
    core,
    controller,
    scheduler,
    network,
    state: () => controller.getSnapshot().state,
    requests: () => core.requests.length,
    log: () => core.requests.map((r) => `${r.method} ${r.path.split("/").slice(-1)[0]}`),
    attaches: () => core.requests.filter((r) => r.path.endsWith("/attach")).length,
    holdAttaches(): void {
      held = [];
    },
    async releaseAttaches(): Promise<void> {
      const queue = held ?? [];
      held = null;
      for (const resolve of queue) resolve();
      await settle();
      await answered();
    },
    speak(at: number): void {
      transports[transports.length - 1].emit({ type: "response_done", at });
    },
  };
}

/** Advance the fake clock past every flush and backoff the controller could arm. */
async function drain(scheduler: FakeScheduler): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    scheduler.advance(250);
    await settle();
    await answered();
  }
}

type Setup = Awaited<ReturnType<typeof setup>>;

/** One network flap, every step observed: offline, a minute offline, back. */
async function flap(t: Setup): Promise<Array<[string, number]>> {
  const seen: Array<[string, number]> = [];
  t.network.set(false);
  await answered();
  seen.push([t.state(), t.requests()]);
  t.scheduler.advance(60_000);
  await settle();
  await answered();
  seen.push([t.state(), t.requests()]);
  t.network.set(true);
  await answered();
  await drain(t.scheduler);
  seen.push([t.state(), t.requests()]);
  return seen;
}

describe("a session the server has declared gone stays gone", () => {
  it("after the 410 on /events, a network flap neither reconnects nor sends anything", async () => {
    const t = await setup();
    t.core.closed = "expired";
    t.speak(1_000);
    await drain(t.scheduler);
    expect([t.state(), t.requests()]).toEqual(["closed", 3]);

    // Two flaps, one assertion, so a failure shows both (the probe: 4, then 5).
    expect([...(await flap(t)), ...(await flap(t))]).toEqual([
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
    ]);
    expect(t.attaches()).toBe(0);
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("after the 410 on the reconnect series' attach, the same", async () => {
    const t = await setup();
    t.core.closed = "expired";
    // The first flap is the one that finds out: its attach answers 410.
    t.network.set(false);
    await answered();
    expect(t.state()).toBe("reconnecting");
    t.network.set(true);
    await answered();
    await drain(t.scheduler);
    // contract, sessions, attach - and nothing after the 410, not even the queued telemetry.
    expect([t.state(), t.requests()]).toEqual(["closed", 3]);

    expect([...(await flap(t)), ...(await flap(t))]).toEqual([
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
      ["closed", 3],
    ]);
    expect(t.attaches()).toBe(1);
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("a 410 on /events while an attach is in flight: the attach's 503 schedules no second attach", async () => {
    // The third way in: the reporter hears the 410 first, the reconnect series' attach on
    // the wire then fails with something else, and its retry timer must not reach the
    // session the server has declared gone (the `gone` guard in `reattachLoop()`).
    const t = await setup();
    t.core.failNext("/attach", 503);
    t.holdAttaches();
    t.network.set(false);
    await answered();
    expect(t.state()).toBe("reconnecting");
    t.network.set(true);
    await answered();
    expect(t.attaches()).toBe(1); // on the wire; its 503 is held

    t.core.closed = "expired";
    t.scheduler.advance(250); // the flush timer: /events answers 410
    await settle();
    await answered();
    expect(t.state()).toBe("closed");

    await t.releaseAttaches(); // the attach fails with 503: a retry timer is armed
    await drain(t.scheduler); // ... and fires
    expect([t.state(), t.attaches()]).toEqual(["closed", 1]);
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("a new session after the gone one connects, and a flap reconnects it as before", async () => {
    const t = await setup();
    t.core.closed = "expired";
    t.speak(1_000);
    await drain(t.scheduler);
    expect(t.state()).toBe("closed");

    // The owner starts again; the server takes a new session.
    t.core.closed = null;
    await t.controller.connect();
    await answered();
    expect(t.state()).toBe("listening");
    const before = t.attaches();

    t.network.set(false);
    await answered();
    expect(t.state()).toBe("reconnecting");
    t.network.set(true);
    await answered();
    await drain(t.scheduler);
    expect(t.state()).toBe("listening");
    expect(t.attaches() - before).toBe(1);
  });

  it("a controller that is merely disconnected reconnects exactly as before", async () => {
    // No 410 anywhere. The expected log was captured from the unchanged controller.
    const t = await setup();
    expect([...(await flap(t)), ...(await flap(t))]).toEqual([
      ["reconnecting", 2],
      ["reconnecting", 3],
      ["listening", 6],
      ["reconnecting", 6],
      ["reconnecting", 7],
      ["listening", 10],
    ]);
    expect(t.log()).toEqual([
      "GET contract",
      "POST sessions",
      "POST events",
      "POST attach",
      "POST events",
      "POST events",
      "POST events",
      "POST attach",
      "POST events",
      "POST events",
    ]);
    // Byte-equal, bodies included: the sha256 of the whole log as the unchanged controller
    // produced it.
    expect(createHash("sha256").update(JSON.stringify(t.core.requests)).digest("hex")).toBe(
      "cde26c1df03819f9f3f8eb27e47dcf1ffddfdc9128ecc070f8d4d34e7eeea624",
    );
    expect(t.scheduler.pendingTimers).toBe(0);
  });
});
