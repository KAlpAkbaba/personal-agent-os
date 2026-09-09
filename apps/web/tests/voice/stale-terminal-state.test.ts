/**
 * The screen that said two opposite things at once (owner report, 2026-09-09).
 *
 * `/voice` showed `Dinliyor` — connected, microphone live, turn counter advancing — and, at
 * the same time, the red terminal line `Oturum sunucuda kapanmış.` One of those was false and
 * the owner had no way to tell which.
 *
 * Both are written by the same controller, and only `connect()` ever cleared `lastError`. So a
 * marker written at ANY other moment — the superseded session's reporter answering 410 just
 * after the new session started, the rate limiter, a transient failure during a reconnect —
 * outlived the condition that produced it and sat next to `Dinliyor` for the rest of the
 * session's life.
 *
 * The invariant asserted here is the owner's: a session that is actually listening carries no
 * stale marker. Not "connect clears it" — that was already true and was not enough — but
 * "arriving at listening clears it", by whichever path we arrive.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { VoiceSessionController } from "../../app/lib/voice/controller";
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

function setup() {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const transports: FakeTransport[] = [];
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback: new FakePlayback(scheduler.now, (op) => log.push(op)),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
    log: (op) => log.push(op),
  });
  return {
    controller,
    core,
    scheduler,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
  };
}

/** Make the reporter's next flush fail, and let it. */
async function reportFailure(t: ReturnType<typeof setup>, status: number, detail: string): Promise<void> {
  t.core.failNext("/events", status, { detail });
  t.scheduler.advance(300); // past flushIntervalMs
  await tick(10);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a listening session carries no stale terminal marker", () => {
  it("a marker written during a session is gone once the session is listening again", async () => {
    const t = setup();
    await t.controller.connect();
    await tick();
    expect(t.controller.getSnapshot().state).toBe("listening");

    // The rate limiter writes a marker on a live session without ending it - the simplest
    // real way to put one there, and the same field the 410 line uses.
    await reportFailure(t, 429, "slow down");
    expect(t.controller.getSnapshot().lastError).not.toBeNull();

    // The session loses its transport and recovers, which is how a session arrives at
    // listening WITHOUT going through connect() - the one path that used to clear the marker.
    t.transport.emit({ type: "disconnected", at: t.scheduler.now(), reason: "peer_failed" });
    await tick(10);
    t.scheduler.advance(500);
    await tick(10);

    const snapshot = t.controller.getSnapshot();
    expect(snapshot.state).toBe("listening");
    // The whole point: the owner cannot be shown "Dinliyor" and a stale failure at once.
    expect(snapshot.lastError).toBeNull();
    expect(snapshot.lastErrorLines).toEqual([]);
  });

  it("a media leg that never opens does NOT become LISTENING", async () => {
    // Owner question B. The claim being tested is not "the timeout works" but "the word the
    // owner reads means the transport the owner needs is usable": a transport whose connect
    // never succeeds must leave a controlled failure state, not `Dinliyor`.
    const log: string[] = [];
    const scheduler = new FakeScheduler();
    const core = new FakeCloudCore({ transport: "webrtc" });
    const controller = new VoiceSessionController({
      api: new VoiceSessionApi(core.fetcher),
      transportFactory: () => {
        const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
        // The media leg opens no data channel: exactly the shape of the owner's 15 s failure.
        transport.connect = async () => {
          throw new Error("data channel did not open in time");
        };
        return transport;
      },
      playback: new FakePlayback(scheduler.now, (op) => log.push(op)),
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
    await tick(10);

    const snapshot = controller.getSnapshot();
    expect(snapshot.state).not.toBe("listening");
    // ...and the owner is told, in a state the UI can offer a retry from - not a crash.
    expect(snapshot.lastError).toMatch(/Medya bağlantısı kurulamadı/);
    expect(snapshot.lastError).toContain("data channel did not open in time");
  });

  it("keeps the REASON a media leg failed, not the consequence of our own close", async () => {
    // The owner's real state on 2026-09-09: ten consecutive sessions died 1.3-2.3 s after
    // creation, in IDLE, with no utterance - the media leg never opened. The controller says
    // so ("Medya bağlantısı kurulamadı: …", carrying the provider's own sentence), then closes
    // the server session. The reporter's trailing POST answers 410, and the gone-branch used to
    // replace that reason with "Oturum sunucuda kapanmış." - true, useless, and caused by us.
    // The owner spent the day being told the consequence.
    const log: string[] = [];
    const scheduler = new FakeScheduler();
    const core = new FakeCloudCore({ transport: "webrtc" });
    const controller = new VoiceSessionController({
      api: new VoiceSessionApi(core.fetcher),
      transportFactory: () => {
        const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
        transport.connect = async () => {
          throw new Error("SDP exchange failed: HTTP 401");
        };
        return transport;
      },
      playback: new FakePlayback(scheduler.now, (op) => log.push(op)),
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
    // Everything the reporter still had in flight lands against a session we just closed.
    core.failNext("/events", 410, { detail: "session gone" });
    scheduler.advance(300);
    await tick(10);

    const snapshot = controller.getSnapshot();
    expect(snapshot.lastError).toMatch(/Medya bağlantısı kurulamadı/);
    expect(snapshot.lastError).toContain("HTTP 401");
    // The consequence must not have replaced the cause.
    expect(snapshot.lastError).not.toContain("Oturum sunucuda kapanmış");
  });

  it("still shows the failure when the session is genuinely broken", async () => {
    // The fix must not become "never show errors". A session that could NOT recover keeps its
    // marker, because there the marker is the truth.
    const t = setup();
    await t.controller.connect();
    await tick();

    for (let i = 0; i < 5; i += 1) t.core.failNext("/attach", 503);
    t.transport.emit({ type: "disconnected", at: t.scheduler.now(), reason: "peer_failed" });
    for (let i = 0; i < 6; i += 1) {
      await tick(6);
      t.scheduler.advance(1_000);
    }
    await tick(10);

    const broken = t.controller.getSnapshot();
    expect(broken.state).not.toBe("listening");
    expect(broken.lastError).not.toBeNull();
  });
});
