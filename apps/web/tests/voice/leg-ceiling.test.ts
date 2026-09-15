/**
 * B20 req 223: the client beats the provider's media-leg ceiling.
 *
 * ADR-0105 fixed half of this. The client used to count down the session's `expires_at`
 * and close a healthy conversation on it; the session does NOT expire, and that countdown
 * was removed. The other half was left standing: a provider ends a single realtime LEG on
 * its own ceiling — OpenAI Realtime at sixty minutes — whatever the session says, and
 * nothing on the client knew the ceiling existed. A long conversation was cut mid-sentence
 * and the reconnect series picked up the pieces afterwards: a break the owner hears, for
 * something the client could have been told about an hour in advance.
 *
 * The server now publishes `leg_max_seconds` with every leg it opens. What is pinned here:
 * the client re-opens before the ceiling, waits for a gap in the conversation while there
 * is time to wait, stops waiting when there is not, re-arms from the payload it was just
 * handed rather than from the first one it saw — and invents no ceiling for a provider
 * that declares none.
 */
import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import {
  LEG_RENEW_DEFER_MS,
  LEG_RENEW_HARD_MS,
  LEG_RENEW_MARGIN_MS,
  LEG_RENEW_RETRY_MS,
  VoiceSessionController,
} from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  type FakeCloudCoreOptions,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";

const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

/** Five minutes, so the whole ceiling fits in a test that advances a fake clock. */
const CEILING_S = 300;
const CEILING_MS = CEILING_S * 1000;

async function setup(options: FakeCloudCoreOptions = {}) {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", legMaxSeconds: CEILING_S, ...options });
  const transports: FakeTransport[] = [];
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: () => {}, now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback: new FakePlayback(scheduler.now, () => {}),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
    log: () => {},
  });
  await controller.connect();
  await tick();
  return {
    controller,
    core,
    scheduler,
    get transport() {
      return transports[transports.length - 1];
    },
    legs: () => transports.length,
    attaches: () => core.requests.filter((r) => r.path.endsWith("/attach")).length,
    /** Move the fake clock and let the attach that a fired timer starts actually finish. */
    async run(ms: number) {
      scheduler.advance(ms);
      await tick(8);
    },
  };
}

describe("the provider's media-leg ceiling", () => {
  it("re-opens the leg before the ceiling, with the whole margin still to spare", async () => {
    const t = await setup();

    await t.run(CEILING_MS - LEG_RENEW_MARGIN_MS - 1000);
    expect(t.attaches()).toBe(0);

    await t.run(1000);

    expect(t.attaches()).toBe(1);
    // A second media leg, not merely a request: the point of the renewal is fresh media.
    expect(t.legs()).toBe(2);
    // And it happened with the margin intact, not scraping the ceiling.
    expect(t.scheduler.now()).toBeLessThanOrEqual(CEILING_MS - LEG_RENEW_MARGIN_MS);
    expect(t.controller.getSnapshot().state).toBe("listening");
  });

  it("records the renewal, so a leg that keeps being re-opened is visible in the session", async () => {
    const t = await setup();
    await t.run(CEILING_MS - LEG_RENEW_MARGIN_MS);
    await t.controller.flushEvents();

    const renewed = t.core.events.filter(
      (e) => (e.payload as Record<string, unknown> | undefined)?.leg_renewed === 1,
    );
    expect(renewed).toHaveLength(1);
    expect((renewed[0].payload as Record<string, unknown>).leg_age_s).toBe(
      (CEILING_MS - LEG_RENEW_MARGIN_MS) / 1000,
    );
  });

  it("gives a provider that declares no ceiling none", async () => {
    // The honest default. A client that invented a ceiling for a provider that publishes
    // no `leg_max_seconds` would be re-opening healthy legs on a schedule of its own.
    const t = await setup({ legMaxSeconds: 0 });

    await t.run(2 * 60 * 60 * 1000);

    expect(t.attaches()).toBe(0);
    expect(t.legs()).toBe(1);
  });

  it("shortens the lead when the ceiling is shorter than the margin", async () => {
    // A one-minute ceiling with a two-minute margin would schedule the renewal in the past
    // and re-open the leg immediately, for ever. Half the ceiling is still ahead of it.
    const t = await setup({ legMaxSeconds: 60 });

    await t.run(29_000);
    expect(t.attaches()).toBe(0);

    await t.run(1_000);
    expect(t.attaches()).toBe(1);
  });

  it("waits for a gap rather than cutting into the assistant's answer", async () => {
    const t = await setup();
    await t.run(CEILING_MS - LEG_RENEW_MARGIN_MS - 1000);
    t.transport.emit({ type: "response_started", at: t.scheduler.now(), responseId: "r1" });
    expect(t.controller.getSnapshot().state).toBe("speaking");

    await t.run(1000);
    expect(t.attaches()).toBe(0);

    // Still nothing while the answer runs on.
    await t.run(LEG_RENEW_DEFER_MS * 3);
    expect(t.attaches()).toBe(0);

    t.transport.emit({ type: "response_done", at: t.scheduler.now(), responseId: "r1" });
    expect(t.controller.getSnapshot().state).toBe("listening");

    await t.run(LEG_RENEW_DEFER_MS);
    expect(t.attaches()).toBe(1);
  });

  it("stops waiting when there is no time left to wait", async () => {
    // Politeness has a floor. An answer that runs all the way to the ceiling gets the
    // re-open anyway: a renewal the owner notices beats the provider cutting the leg.
    const t = await setup();
    await t.run(CEILING_MS - LEG_RENEW_MARGIN_MS - 1000);
    t.transport.emit({ type: "response_started", at: t.scheduler.now(), responseId: "r1" });

    // Up to the hard point, deferring in `LEG_RENEW_DEFER_MS` steps the whole way.
    await t.run(CEILING_MS - LEG_RENEW_HARD_MS - t.scheduler.now());

    expect(t.attaches()).toBe(1);
    expect(t.scheduler.now()).toBeLessThan(CEILING_MS);
  });

  it("arms the next renewal from the leg it was just handed, not from the first one", async () => {
    const t = await setup();
    // The server's answer changes between legs - a different provider, a different plan.
    t.core.legMaxSeconds = 260;

    await t.run(CEILING_MS - LEG_RENEW_MARGIN_MS);
    expect(t.attaches()).toBe(1);

    // The NEW ceiling is 260s, so the next renewal is due 140s later, not 180s later.
    await t.run(139_000);
    expect(t.attaches()).toBe(1);

    await t.run(1_000);
    expect(t.attaches()).toBe(2);
    expect(t.legs()).toBe(3);
  });

  it("leaves the working leg alone when the renewal fails, and tries again", async () => {
    const t = await setup();
    t.core.failNext("/attach", 500);

    await t.run(CEILING_MS - LEG_RENEW_MARGIN_MS);

    // The attach failed; the leg it would have replaced is still the live one, and the
    // session is not in reconnect - nothing was taken away from it.
    expect(t.attaches()).toBe(1);
    expect(t.legs()).toBe(1);
    expect(t.controller.getSnapshot().state).toBe("listening");

    await t.run(LEG_RENEW_RETRY_MS);

    expect(t.attaches()).toBe(2);
    expect(t.legs()).toBe(2);
  });

  it("waits, even for a nonsense ceiling", async () => {
    // A renewal scheduled for "now" does not wait, it spins. Removing the hard floor while
    // red-proving this batch turned the deferral into a zero-delay loop that hung the suite
    // synchronously; a ceiling of a few milliseconds from a confused server is the same
    // shape arriving over the wire. Bounded by `LEG_RENEW_MIN_WAIT_MS`, never unbounded.
    const t = await setup({ legMaxSeconds: 0.002 });

    await t.run(10_000);

    expect(t.attaches()).toBeGreaterThan(0);
    expect(t.attaches()).toBeLessThanOrEqual(10_000 / 250);
  });

  it("stops renewing once the session is closed", async () => {
    const t = await setup();
    await t.controller.disconnect();

    await t.run(CEILING_MS * 2);

    expect(t.attaches()).toBe(0);
  });
});
