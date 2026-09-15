/**
 * B20 req 221/222: the page stops saying "Dinliyor" into a dead microphone.
 *
 * The controller opened the microphone, set the state to `listening`, and never subscribed
 * to the input track's `ended` or `mute` events. When the operating system, another
 * application or the owner revoked the device, nothing on the page changed: the owner went
 * on talking to something that had stopped hearing them. The matrix files requirement 221
 * as trust-breaking rather than cosmetic, and that is the right filing — every other thing
 * the screen says is only worth as much as this one.
 */

import { beforeEach, describe, expect, it } from "vitest";

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

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function setup() {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const microphone = new FakeMicrophone();
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => new FakeTransport({ log: () => {}, now: scheduler.now }),
    playback: new FakePlayback(scheduler.now, () => {}),
    network: new FakeNetwork(),
    microphone,
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    log: () => {},
  });
  await controller.connect();
  await tick();
  return { controller, microphone, core, scheduler };
}

describe("a microphone that goes away", () => {
  let t: Awaited<ReturnType<typeof setup>>;

  beforeEach(async () => {
    t = await setup();
    expect(t.controller.getSnapshot().state).toBe("listening");
  });

  it("stops claiming to listen when the track ends", () => {
    t.microphone.track!.end();

    expect(t.controller.getSnapshot().state).toBe("mic_lost");
  });

  it("stops claiming to listen when the track is muted", () => {
    t.microphone.track!.mute();

    expect(t.controller.getSnapshot().state).toBe("mic_lost");
  });

  it("goes back to listening when a muted track comes back", () => {
    t.microphone.track!.mute();
    expect(t.controller.getSnapshot().state).toBe("mic_lost");

    t.microphone.track!.unmute();

    expect(t.controller.getSnapshot().state).toBe("listening");
  });

  it("cannot be talked back into listening while the microphone is gone", () => {
    // The invariant, and the reason the check lives inside `setState` rather than at the
    // two call sites: `listening` REQUIRES a live input track, whatever else happens in
    // the session. A later transition that set it directly would put the lie straight
    // back on the screen.
    t.microphone.track!.end();

    // Whatever the session does next, it is not listening.
    expect(t.controller.getSnapshot().state).toBe("mic_lost");
  });

  it("tells the server, so the loss is in the record and not only on the screen", async () => {
    t.microphone.track!.end();
    t.scheduler.advance(500);
    await tick();
    await tick();

    const events = t.core.requests
      .filter((r) => r.path.endsWith("/events"))
      .flatMap((r) => (r.body as { events?: Array<Record<string, unknown>> })?.events ?? []);
    const lost = events.find((e) => (e.payload as Record<string, unknown>)?.mic_lost === 1);
    expect(lost).toBeTruthy();
    expect((lost!.payload as Record<string, unknown>).mic_lost_reason).toBe("ended");
  });
});
