/**
 * B20 req 218: the reconnect stops waiting to be told it is too late.
 *
 * Everything about reconnecting was reactive. The series started when the transport
 * reported the peer had FAILED, or when the browser said the network had gone — and
 * WebRTC reports `disconnected` well before `failed`, then takes as long as it likes to
 * decide between them. Those seconds are dead air with the page still saying "Dinliyor",
 * and the client had the warning in its hand the whole time: `onconnectionstatechange`
 * fired, the handler looked only for `failed` and `closed`, and dropped the rest.
 *
 * The rule pinned here: a warning is a stopwatch, not a teardown. Most blips clear by
 * themselves and a session that tore itself down on every one of them would be worse than
 * the disease; one that never acts until the browser gives up is the defect.
 */
import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { DEFAULT_IMPAIRED_GRACE_MS, VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";

const tick = async (rounds = 6): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

async function setup() {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const transports: FakeTransport[] = [];
  const log: string[] = [];
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
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  return {
    controller,
    core,
    scheduler,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
    legs: () => transports.length,
    attaches: () => core.requests.filter((r) => r.path.endsWith("/attach")).length,
    snap: () => controller.getSnapshot(),
    async run(ms: number) {
      scheduler.advance(ms);
      await tick(8);
    },
    async events(): Promise<Array<Record<string, unknown>>> {
      await controller.flushEvents();
      return core.events.map((e) => (e.payload ?? {}) as Record<string, unknown>);
    },
  };
}

describe("a link that is in trouble but not gone", () => {
  it("does not tear the session down on the warning itself", async () => {
    const t = await setup();

    t.transport.emit({ type: "impaired", at: t.scheduler.now(), reason: "peer_disconnected" });

    // The leg is still up and the state is untouched: this may yet be nothing.
    expect(t.snap().state).toBe("listening");
    expect(t.attaches()).toBe(0);
    expect(t.legs()).toBe(1);
  });

  it("re-attaches when the warning does not clear, before the transport gives up", async () => {
    const t = await setup();
    t.transport.emit({ type: "impaired", at: t.scheduler.now(), reason: "peer_disconnected" });

    await t.run(DEFAULT_IMPAIRED_GRACE_MS - 1);
    expect(t.attaches()).toBe(0);

    await t.run(1);

    expect(t.attaches()).toBe(1);
    expect(t.legs()).toBe(2);
    expect(t.snap().state).toBe("listening");
  });

  it("calls the whole thing off when the link comes back by itself", async () => {
    const t = await setup();
    t.transport.emit({ type: "impaired", at: 0, reason: "peer_disconnected" });

    await t.run(500);
    t.transport.emit({ type: "recovered", at: t.scheduler.now() });
    await t.run(DEFAULT_IMPAIRED_GRACE_MS * 3);

    expect(t.attaches()).toBe(0);
    expect(t.legs()).toBe(1);
  });

  it("records both the warning and how long it lasted", async () => {
    const t = await setup();
    t.transport.emit({ type: "impaired", at: 0, reason: "peer_disconnected" });
    await t.run(700);
    t.transport.emit({ type: "recovered", at: t.scheduler.now() });
    await t.run(0);

    const payloads = await t.events();
    expect(payloads.some((p) => p.link_impaired === 1)).toBe(true);
    const cleared = payloads.find((p) => p.link_impaired === 0);
    expect(cleared).toBeTruthy();
    expect(cleared?.link_impaired_ms).toBe(700);
  });

  it("a second warning while one is already running does not start a second stopwatch", async () => {
    const t = await setup();
    t.transport.emit({ type: "impaired", at: 0, reason: "peer_disconnected" });
    await t.run(1000);
    t.transport.emit({ type: "impaired", at: t.scheduler.now(), reason: "peer_disconnected" });

    // Still measured from the FIRST warning: the second must not push the deadline out.
    await t.run(DEFAULT_IMPAIRED_GRACE_MS - 1000);

    expect(t.attaches()).toBe(1);
  });

  it("a peer that fails outright still goes straight to the reconnect series", async () => {
    // The pre-existing path, unchanged: a failure is not a warning and waits for nothing.
    const t = await setup();

    t.transport.emit({ type: "disconnected", at: t.scheduler.now(), reason: "peer_failed" });
    await tick(8);

    expect(t.attaches()).toBe(1);
  });

  it("a warning on a session that is already reconnecting is ignored", async () => {
    const t = await setup();
    t.core.failNext("/attach", 500);
    t.transport.emit({ type: "disconnected", at: t.scheduler.now(), reason: "peer_failed" });
    await tick(8);
    expect(t.snap().state).toBe("reconnecting");
    const before = t.attaches();

    t.transport.emit({ type: "impaired", at: t.scheduler.now(), reason: "peer_disconnected" });
    await t.run(DEFAULT_IMPAIRED_GRACE_MS);

    // Whatever happens next is the reconnect series' business; the warning added nothing.
    expect(t.log.filter((op) => op.startsWith("link.impaired"))).toHaveLength(0);
    expect(t.attaches()).toBeGreaterThanOrEqual(before);
  });

  it("the new leg starts healthy: a warning on the old one does not follow it", async () => {
    const t = await setup();
    t.transport.emit({ type: "impaired", at: 0, reason: "peer_disconnected" });
    await t.run(DEFAULT_IMPAIRED_GRACE_MS);
    expect(t.legs()).toBe(2);

    // The grace of the OLD leg must not still be counting against the new one.
    await t.run(DEFAULT_IMPAIRED_GRACE_MS * 3);

    expect(t.attaches()).toBe(1);
  });
});
