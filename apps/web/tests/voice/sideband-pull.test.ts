/**
 * The shell pulls its sideband on a timer (GET .../sessions/{id}/sideband).
 *
 * A web session has no push channel. Before the pull the shell took queued frames only from
 * the answer to its own `/events` POST or an attach, so a briefing queued for an open, silent
 * tab waited for the owner's next sentence. What is asserted: one GET per `SIDEBAND_PULL_MS`
 * while the leg is live and none outside it, a pulled `say` reaches the same sink an
 * `/events` frame does, a 410 ends the pull without a reconnect, any other failure costs one
 * tick, at most one pull is ever on the wire, and a tick yields to a reporter that has events
 * queued (their answer carries the frames anyway).
 *
 * The only clock is the injected `FakeScheduler`; every wait is for an answer.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { VoiceSessionApi, type Fetcher } from "../../app/lib/voice/api";
import { SIDEBAND_PULL_MS } from "../../app/lib/voice/contract";
import { VoiceSessionController } from "../../app/lib/voice/controller";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeSpeechRecognition,
  FakeSpeechSynthesis,
  FakeTransport,
  fakeUtterance,
  settle,
} from "../../app/lib/voice/fake";
import { LOCAL_TRANSPORT, LocalVoiceMode } from "../../app/lib/voice/localMode";

const wire = { open: 0 };

beforeEach(() => {
  wire.open = 0;
});

async function answered(): Promise<void> {
  do {
    await new Promise<void>((resolve) => setImmediate(resolve));
  } while (wire.open > 0);
}

function build() {
  const core = new FakeCloudCore({ transport: "webrtc" });
  // A held path reaches the server (recorded) but its answer waits for `release(path)`.
  const held = new Map<string, Array<() => void>>();
  const fetcher: Fetcher = (path, init = {}) => {
    for (const [suffix, queue] of held) {
      if (path.endsWith(suffix)) {
        const answer = core.fetcher(path, init);
        return new Promise<void>((resolve) => queue.push(resolve)).then(() => answer);
      }
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
  return {
    core,
    controller,
    scheduler,
    network,
    transports,
    pulls: () => core.requests.filter((r) => r.method === "GET" && r.path.endsWith("/sideband")).length,
    attaches: () => core.requests.filter((r) => r.path.endsWith("/attach")).length,
    said: () => transports.flatMap((t) => t.sent.filter((s) => s.startsWith("say:"))),
    hold(suffix: string): void {
      held.set(suffix, []);
    },
    async release(suffix: string): Promise<void> {
      const queue = held.get(suffix) ?? [];
      held.delete(suffix);
      for (const resolve of queue) resolve();
      await settle();
      await answered();
    },
    async tick(ms = SIDEBAND_PULL_MS): Promise<void> {
      scheduler.advance(ms);
      await settle();
      await answered();
    },
  };
}

async function connected() {
  const t = build();
  await t.controller.connect();
  await answered();
  // the connect's own events are posted first: a tick yields to a queued reporter
  await t.controller.flushEvents();
  await answered();
  return t;
}

describe("sideband pull", () => {
  it("is 15 s, one GET per tick, and none before the session exists", async () => {
    expect(SIDEBAND_PULL_MS).toBe(15_000);
    const t = build();
    await t.tick(60_000);
    expect(t.core.requests.length).toBe(0);

    await t.controller.connect();
    await answered();
    expect(t.pulls()).toBe(0);
    await t.tick(SIDEBAND_PULL_MS - 1);
    expect(t.pulls()).toBe(0);
    await t.tick(1);
    expect(t.pulls()).toBe(1);
    await t.tick();
    expect(t.pulls()).toBe(2);
  });

  it("speaks a pulled `say` through the same sink an /events frame uses", async () => {
    const t = await connected();
    // the /events path, as it is today
    t.core.queueSideband("say", { text: "Olay yolundan." });
    t.transports[0].emit({ type: "response_done", at: 1 });
    await t.controller.flushEvents();
    await answered();
    expect(t.said()).toEqual(["say:Olay yolundan."]);

    // the pull path, with the owner silent
    t.core.queueSideband("say", { text: "Efendim, araştırma tamamlandı." });
    await t.tick();
    expect(t.pulls()).toBe(1);
    expect(t.said()).toEqual(["say:Olay yolundan.", "say:Efendim, araştırma tamamlandı."]);
    expect(t.core.pendingSideband).toEqual([]);
    expect(t.controller.getSnapshot().sidebandLog.at(-1)).toBe("söyle: Efendim, araştırma tamamlandı.");

    // drained once: the next pull is empty and nothing is said twice
    await t.tick();
    expect(t.pulls()).toBe(2);
    expect(t.said()).toHaveLength(2);
  });

  it("stops at close and leaks no timer", async () => {
    const t = await connected();
    await t.tick();
    expect(t.pulls()).toBe(1);
    await t.controller.disconnect();
    await answered();
    await t.tick(10 * SIDEBAND_PULL_MS);
    expect(t.pulls()).toBe(1);
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("ends on 410 and never reconnects", async () => {
    const t = await connected();
    t.core.failNext("/sideband", 410, { detail: { error_class: "validation_error", details: { state: "closed" } } });
    await t.tick();
    expect(t.pulls()).toBe(1);
    expect(t.controller.getSnapshot().state).toBe("closed");
    t.network.set(false);
    t.network.set(true);
    await t.tick(10 * SIDEBAND_PULL_MS);
    expect(t.pulls()).toBe(1);
    expect(t.attaches()).toBe(0);
  });

  it("skips one tick on a 500 or a network failure and carries on", async () => {
    const t = await connected();
    t.core.failNext("/sideband", 500);
    await t.tick();
    expect(t.pulls()).toBe(1);
    expect(t.controller.getSnapshot().state).toBe("listening");
    t.core.queueSideband("say", { text: "Sonraki tikte." });
    await t.tick();
    expect(t.pulls()).toBe(2);
    expect(t.said()).toEqual(["say:Sonraki tikte."]);
    expect(t.attaches()).toBe(0);
  });

  it("keeps at most one pull on the wire", async () => {
    const t = await connected();
    t.hold("/sideband");
    await t.tick();
    expect(t.pulls()).toBe(1);
    await t.tick();
    await t.tick();
    expect(t.pulls()).toBe(1);
    await t.release("/sideband");
    await t.tick();
    expect(t.pulls()).toBe(2);
  });

  it("yields a tick to a reporter that has events queued", async () => {
    const t = await connected();
    t.hold("/events");
    t.transports[0].emit({ type: "response_done", at: 1 });
    await t.tick(250);
    await t.tick(SIDEBAND_PULL_MS);
    expect(t.pulls()).toBe(0);
    await t.release("/events");
    await t.tick();
    expect(t.pulls()).toBe(1);
  });

  it("re-arms on a re-attach with one timer, not two", async () => {
    const t = await connected();
    t.network.set(false);
    await answered();
    t.network.set(true);
    await t.tick(1_000);
    expect(t.attaches()).toBe(1);
    const before = t.pulls();
    // The first leg's timer (armed at 0) would fire at 15 000; the new leg opened at 1 000.
    // Two steps, so the re-attach's own late events are answered before the pull's tick.
    await t.tick(SIDEBAND_PULL_MS - 1_000);
    expect(t.pulls()).toBe(before);
    await t.tick(1_000);
    expect(t.pulls()).toBe(before + 1);
    expect(t.controller.getSnapshot().state).toBe("listening");
    await t.tick();
    expect(t.pulls()).toBe(before + 2);
  });
});

function local() {
  const core = new FakeCloudCore({ provider: "local-router", transport: LOCAL_TRANSPORT });
  const scheduler = new FakeScheduler();
  const synthesis = new FakeSpeechSynthesis();
  const recognition = new FakeSpeechRecognition();
  let ids = 0;
  const mode = new LocalVoiceMode({
    api: new VoiceSessionApi(core.fetcher),
    recognition: () => recognition,
    synthesis: () => synthesis,
    utterance: fakeUtterance,
    now: () => 1000 + ids,
    newId: () => `id${(ids += 1)}`,
    setTimer: () => null,
    clearTimer: () => {},
    sidebandScheduler: scheduler,
  });
  const pulls = () => core.requests.filter((r) => r.method === "GET" && r.path.endsWith("/sideband")).length;
  const tick = async (ms = SIDEBAND_PULL_MS) => {
    scheduler.advance(ms);
    await settle();
    await new Promise<void>((resolve) => setImmediate(resolve));
    await settle();
  };
  return { core, scheduler, synthesis, mode, pulls, tick };
}

describe("sideband pull in the local mode (its own loop, the same rule)", () => {
  it("speaks a pulled `say` with the owner silent, and stops with the mode", async () => {
    const t = local();
    await t.tick(60_000);
    expect(t.pulls()).toBe(0);
    await t.mode.start();
    t.core.queueSideband("say", { text: "Efendim, araştırma tamamlandı." });
    await t.tick();
    expect(t.pulls()).toBe(1);
    expect(t.synthesis.spoken.map((u) => u.text)).toEqual(["Efendim, araştırma tamamlandı."]);
    t.synthesis.finish();
    await t.tick();
    expect(t.pulls()).toBe(2);
    expect(t.synthesis.spoken).toHaveLength(1);
    await t.mode.stop();
    await t.tick(10 * SIDEBAND_PULL_MS);
    expect(t.pulls()).toBe(2);
    expect(t.scheduler.pendingTimers).toBe(0);
  });

  it("ends on 410 and carries on past a 500", async () => {
    const t = local();
    await t.mode.start();
    t.core.failNext("/sideband", 500);
    await t.tick();
    await t.tick();
    expect(t.pulls()).toBe(2);
    expect(t.mode.getSnapshot().state).not.toBe("error");
    t.core.failNext("/sideband", 410);
    await t.tick();
    expect(t.mode.getSnapshot().state).toBe("error");
    await t.tick(10 * SIDEBAND_PULL_MS);
    expect(t.pulls()).toBe(3);
  });
});
