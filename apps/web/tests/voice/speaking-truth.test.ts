/**
 * B20 req 231: "Konuşuyor" is a claim about sound.
 *
 * ADR-0066 split GENERATION from playback and the Core's pulse has been honest about it
 * ever since - the pulse is the output analyser's envelope, so a response that has produced
 * no audio yet draws nothing. The words never caught up. The state token turns `speaking`
 * at `response_started`, both readouts print `VOICE_STATE_LABEL[state]` from that instant,
 * and on a slow first token that is a second or more of the page telling the owner the
 * assistant is talking to them in an empty room. The caption was worse: "Araştırma
 * sonuçlarını anlatıyorum…" before a single sample had played.
 *
 * The state machine is deliberately unchanged - the turn IS the assistant's from
 * `response_started`, and the barge-in path needs to know that. What changes is that the
 * text a person reads is derived from the measured phase.
 *
 * Driven through the real controller: the phase these labels read is produced by the same
 * transport events a provider sends, not by a hand-written snapshot.
 */
import { describe, expect, it } from "vitest";

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
import {
  SPEECH_PREPARING_LABEL,
  VOICE_STATE_LABEL,
  speechCaption,
  voiceStateLabel,
} from "../../app/lib/voice/labels";
import { voiceOverlayFrom } from "../../app/lib/uistate/voice-overlay";

const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

async function setup() {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const playback = new FakePlayback(scheduler.now, () => {});
  const transports: FakeTransport[] = [];
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: () => {}, now: scheduler.now });
      transports.push(transport);
      return transport;
    },
    playback,
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
    flushIntervalMs: 250,
    log: () => {},
  });
  await controller.connect();
  await tick();
  return {
    controller,
    scheduler,
    playback,
    get transport() {
      return transports[transports.length - 1];
    },
    snap: () => controller.getSnapshot(),
  };
}

describe("what the page says while the assistant answers", () => {
  it("says it is preparing an answer until audio actually plays", async () => {
    const t = await setup();

    t.transport.emit({ type: "response_started", at: 1000, responseId: "r1" });

    // The turn is the assistant's - that part was never in doubt.
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech.phase).toBe("generating");
    // But nothing is being said yet, and the label no longer says otherwise.
    expect(voiceStateLabel(t.snap())).toBe(SPEECH_PREPARING_LABEL);
    expect(voiceStateLabel(t.snap())).not.toBe(VOICE_STATE_LABEL.speaking);

    t.scheduler.advance(300);
    t.transport.emit({ type: "audio_started", at: 1300, responseId: "r1" });
    t.playback.level = 0.5;
    t.playback.activity(1300);
    await tick();

    expect(t.snap().speech.phase).toBe("audible");
    expect(voiceStateLabel(t.snap())).toBe("Konuşuyor");
  });

  it("still says Konuşuyor while the last of the audio drains after generation ends", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 1000, responseId: "r1" });
    t.scheduler.advance(300);
    t.transport.emit({ type: "audio_started", at: 1300, responseId: "r1" });
    t.playback.level = 0.5;
    t.playback.activity(1300);
    await tick();
    t.scheduler.advance(200);
    t.transport.emit({ type: "response_done", at: 1500, responseId: "r1" });

    expect(t.snap().speech.phase).toBe("draining");
    expect(voiceStateLabel(t.snap())).toBe("Konuşuyor");
  });

  it("holds the Core's caption back until there is speech to caption", async () => {
    // The caption is a sentence in the first person about what is being SAID. The phase it
    // gates on is the one the controller test above produces from real transport events;
    // here the two phases are held next to each other on the same snapshot.
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 1000, responseId: "r1" });
    const generating = { ...t.snap(), toolsRunning: ["research.start"] };

    expect(generating.state).toBe("speaking");
    expect(speechCaption(generating)).toBeNull();
    expect(voiceOverlayFrom(generating, { micLevel: null, outputLevel: null }).caption).toBeNull();

    t.scheduler.advance(300);
    t.transport.emit({ type: "audio_started", at: 1300, responseId: "r1" });
    t.playback.level = 0.5;
    t.playback.activity(1300);
    await tick();
    const audible = { ...t.snap(), toolsRunning: ["research.start"] };

    expect(audible.speech.phase).toBe("audible");
    expect(speechCaption(audible)).toBe("Araştırma sonuçlarını anlatıyorum…");
    expect(voiceOverlayFrom(audible, { micLevel: null, outputLevel: null }).caption).toBe(
      "Araştırma sonuçlarını anlatıyorum…",
    );
  });

  it("never says Konuşuyor for a response that produced no audio at all", async () => {
    // The text-only answer: generation ran and ended, and at no point was there sound.
    const t = await setup();
    const said: string[] = [];
    t.transport.emit({ type: "response_started", at: 100, responseId: "r1" });
    said.push(voiceStateLabel(t.snap()));
    t.transport.emit({ type: "response_done", at: 200, responseId: "r1" });
    said.push(voiceStateLabel(t.snap()));

    expect(said).toEqual([SPEECH_PREPARING_LABEL, VOICE_STATE_LABEL.listening]);
  });

  it("every other state keeps its own label", async () => {
    const t = await setup();
    expect(voiceStateLabel(t.snap())).toBe(VOICE_STATE_LABEL.listening);
    await t.controller.disconnect();
    expect(voiceStateLabel(t.controller.getSnapshot())).toBe(VOICE_STATE_LABEL.closed);
  });
});
