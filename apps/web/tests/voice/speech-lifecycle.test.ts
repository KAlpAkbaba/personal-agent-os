/**
 * ADR-0066 — speaking is a lifecycle; energy is a measurement (M18.2 DEFECT 1).
 *
 * Owner-observed on a real WebRTC session: the Core left SPEAKING mid-sentence.
 * `response.done` marks the end of GENERATION; the media track still holds
 * whatever was generated but not yet played, and the provider only sends
 * `output_audio_buffer.stopped` when playback actually ends. The controller
 * used to leave `speaking` on `response_done`.
 *
 * The model pinned here: `speaking` runs from the first audible playback until
 * the final audio belonging to THAT response has actually completed — the
 * provider's `audio_stopped` for the response, else analyser silence for
 * `PLAYBACK_RELEASE_MS` after generation, bounded by `PLAYBACK_DRAIN_MAX_MS`.
 * A barge-in, "dur" or a cancel ends it at once. The analyser's RMS only sets
 * the pulse: a pause inside an answer is a calmer Core, not a state change.
 * Every audible response ends in exactly one `audio_done`, whose `basis` says
 * how the end was judged — never silent loss, never invented speech.
 */
import { describe, expect, it } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { isForbiddenKey } from "../../app/lib/voice/contract";
import {
  PLAYBACK_DRAIN_MAX_MS,
  PLAYBACK_POLL_MS,
  PLAYBACK_RELEASE_MS,
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

async function setup(options: FakeCloudCoreOptions = {}) {
  const log: string[] = [];
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc", ...options });
  const transports: FakeTransport[] = [];
  const playback = new FakePlayback(scheduler.now, (op) => log.push(op));
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => {
      const transport = new FakeTransport({ log: (op) => log.push(op), now: scheduler.now });
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
    reattach: { maxAttempts: 3, baseDelayMs: 100 },
    log: (op) => log.push(op),
  });
  await controller.connect();
  await tick();
  const t = {
    controller,
    core,
    scheduler,
    playback,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
    snap: () => controller.getSnapshot(),
    events: (kind: string) => core.events.filter((e) => e.kind === kind),
    audioDone: async () => {
      await controller.flushEvents();
      return core.events.filter((e) => e.kind === "audio_done");
    },
  };
  return t;
}

type T = Awaited<ReturnType<typeof setup>>;

/**
 * A response `r1` that starts at 1000, whose provider buffer starts at 1300
 * and which is locally audible from 1300: generation running, audio playing.
 */
function audibleResponse(t: T, responseId = "r1", at = 1000): void {
  t.scheduler.advance(at - t.scheduler.now());
  t.transport.emit({ type: "response_started", at, responseId });
  t.transport.emit({ type: "response_text", at: at + 100, text: "İkinci madde: dağıtım gecikti.", final: false });
  t.scheduler.advance(300);
  t.transport.emit({ type: "audio_started", at: at + 300, responseId });
  t.playback.level = 0.5;
  t.playback.activity(at + 300);
}

describe("the speech lifecycle (ADR-0066)", () => {
  it("(a) response_started is generating; the first audible playback is what makes it audible and speaking", async () => {
    const t = await setup();
    t.scheduler.advance(1000);
    t.transport.emit({ type: "response_started", at: 1000, responseId: "r1" });
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech).toEqual({
      responseId: "r1",
      phase: "generating",
      firstAudioAt: null,
      generationDoneAt: null,
      playbackDoneAt: null,
      basis: null,
    });
    t.scheduler.advance(300);
    t.transport.emit({ type: "audio_started", at: 1300, responseId: "r1" });
    t.playback.activity(1320);
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech).toMatchObject({ responseId: "r1", phase: "audible", firstAudioAt: 1320 });
  });

  it("(b) response_done while the provider's buffer is still playing keeps speaking, phase draining", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(500);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech).toMatchObject({ phase: "draining", firstAudioAt: 1300, generationDoneAt: 1800, playbackDoneAt: null });
    expect(t.playback.playing).toBe(true);
    // Generation done is reported as before; nothing has claimed the audio ended.
    await t.controller.flushEvents();
    expect(t.events("response_done")).toHaveLength(1);
    expect(t.events("audio_done")).toHaveLength(0);
    // The state events say ASSISTANT_SPEAKING once and never LISTENING for this response yet.
    const states = t.events("state").map((e) => e.payload?.state).filter(Boolean);
    expect(states.at(-1)).toBe("ASSISTANT_SPEAKING");
  });

  it("(c) the provider's audio_stopped for that response ends it: listening, one audio_done with basis provider", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(500);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    t.scheduler.advance(250); // the buffer keeps playing; silence release is still 150 ms away
    expect(t.snap().state).toBe("speaking");
    t.transport.emit({ type: "audio_stopped", at: 2050, responseId: "r1" });
    expect(t.snap().state).toBe("listening");
    expect(t.snap().speech).toMatchObject({ phase: "done", playbackDoneAt: 2050, basis: "provider" });
    const done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(done[0]).toMatchObject({
      t_ms: 2050,
      turn: 0,
      payload: { response_id: "r1", basis: "provider", drain_ms: 250, audible_ms: 750 },
    });
    // The state transition is reported after audio_done, once.
    const kinds = t.core.kinds();
    expect(kinds.indexOf("audio_done")).toBeGreaterThan(kinds.indexOf("response_done"));
    expect(t.core.events.slice(kinds.indexOf("audio_done") + 1)[0]).toMatchObject({ kind: "state", payload: { state: "LISTENING" } });
    // No further timer is waiting to end a response that already ended.
    t.scheduler.advance(PLAYBACK_DRAIN_MAX_MS);
    expect(await t.audioDone()).toHaveLength(1);
    expect(t.snap().state).toBe("listening");
  });

  it("(d) no audio_stopped ever: analyser silence for PLAYBACK_RELEASE_MS after response_done ends it with basis silence", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(500);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    t.playback.level = 0; // the track has gone quiet
    t.scheduler.advance(PLAYBACK_RELEASE_MS - PLAYBACK_POLL_MS);
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech.phase).toBe("draining");
    t.scheduler.advance(PLAYBACK_POLL_MS);
    expect(t.snap().state).toBe("listening");
    expect(t.snap().speech).toMatchObject({ phase: "done", playbackDoneAt: 1800 + PLAYBACK_RELEASE_MS, basis: "silence" });
    const done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(done[0]).toMatchObject({ payload: { response_id: "r1", basis: "silence", drain_ms: PLAYBACK_RELEASE_MS } });
  });

  it("(d') energy still measured after response_done keeps speaking; silence is counted from the last energy", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(500);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    t.playback.level = 0.4; // the buffered tail is still playing
    t.scheduler.advance(1000);
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech.phase).toBe("draining");
    t.playback.level = 0;
    t.scheduler.advance(PLAYBACK_RELEASE_MS - PLAYBACK_POLL_MS);
    expect(t.snap().state).toBe("speaking");
    t.scheduler.advance(PLAYBACK_POLL_MS);
    expect(t.snap().state).toBe("listening");
    expect((await t.audioDone())[0].payload).toMatchObject({ basis: "silence", drain_ms: 1000 + PLAYBACK_RELEASE_MS });
  });

  it("(e) the drain cap: a track that never goes quiet and a provider that never says stop end at PLAYBACK_DRAIN_MAX_MS with basis cap", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(500);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    t.playback.level = 0.6;
    t.scheduler.advance(PLAYBACK_DRAIN_MAX_MS - 1);
    expect(t.snap().state).toBe("speaking");
    t.scheduler.advance(1);
    expect(t.snap().state).toBe("listening");
    expect(t.snap().speech).toMatchObject({ phase: "done", playbackDoneAt: 1800 + PLAYBACK_DRAIN_MAX_MS, basis: "cap" });
    const done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(done[0].payload).toMatchObject({ basis: "cap", drain_ms: PLAYBACK_DRAIN_MAX_MS });
  });

  it("(f1) barge-in through 'dur' ends the lifecycle at the stop: interrupted at once, one audio_done with basis interrupted, no later end", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(200); // now = 1500
    t.transport.emit({ type: "speech_started", at: 1480 });
    expect(t.snap().state).toBe("speaking"); // a potential barge-in only mutes
    t.transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });
    expect(t.snap().state).toBe("interrupted");
    expect(t.playback.playing).toBe(false);
    expect(t.snap().speech).toMatchObject({ phase: "done", basis: "interrupted", generationDoneAt: null });
    let done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(done[0].payload).toMatchObject({ response_id: "r1", basis: "interrupted" });
    expect(done[0].payload).not.toHaveProperty("drain_ms");
    // The provider's late acknowledgements for the cut response change nothing.
    t.transport.emit({ type: "response_cancelled", at: 1600, responseId: "r1" });
    t.transport.emit({ type: "audio_stopped", at: 1650, responseId: "r1" });
    t.transport.emit({ type: "response_done", at: 1700, responseId: "r1" });
    t.scheduler.advance(PLAYBACK_DRAIN_MAX_MS);
    done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(t.snap().speech.phase).toBe("done");
  });

  it("(f2) 'dur' over draining audio (response_done already seen) ends it at once too, with nothing cancelled", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(100);
    t.transport.emit({ type: "response_done", at: 1400, responseId: "r1" });
    expect(t.snap().speech.phase).toBe("draining");
    t.scheduler.advance(100); // now = 1500
    t.transport.emit({ type: "speech_started", at: 1480 });
    t.transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });
    expect(t.snap().state).toBe("interrupted");
    expect(t.transport.sent).toEqual([]);
    expect(t.snap().speech).toMatchObject({ phase: "done", basis: "interrupted" });
    const done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(done[0].payload).toMatchObject({ basis: "interrupted", drain_ms: 100 });
    // The drain timers were dropped with the interruption: no cap, no release, no second end.
    t.scheduler.advance(PLAYBACK_DRAIN_MAX_MS);
    expect(await t.audioDone()).toHaveLength(1);
    expect(t.snap().state).toBe("interrupted");
  });

  it("(f3) a response_cancelled from the provider ends speaking immediately", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(200);
    t.transport.emit({ type: "response_cancelled", at: 1500, responseId: "r1" });
    expect(t.snap().state).toBe("listening");
    expect(t.snap().speech).toMatchObject({ phase: "done", playbackDoneAt: 1500, basis: "interrupted" });
    const done = await t.audioDone();
    expect(done).toHaveLength(1);
    expect(done[0].payload).toMatchObject({ response_id: "r1", basis: "interrupted" });
  });

  it("(g) a natural pause inside the answer never leaves speaking — energy sets the pulse, not the state", async () => {
    const t = await setup();
    audibleResponse(t);
    // Generation still running: the analyser goes quiet for 300 ms, then speech resumes.
    t.playback.level = 0;
    t.scheduler.advance(300);
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech.phase).toBe("audible");
    expect(t.playback.outputLevel()).toBe(0); // the Core is calm, not listening
    t.playback.level = 0.5;
    t.scheduler.advance(400);
    expect(t.snap().state).toBe("speaking");
    // Even a pause LONGER than the release window is not an end while generation runs.
    t.playback.level = 0;
    t.scheduler.advance(PLAYBACK_RELEASE_MS * 3);
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech.phase).toBe("audible");
    // A pause after generation shorter than the release window is a pause too.
    t.playback.level = 0.5;
    t.scheduler.advance(100);
    t.transport.emit({ type: "response_done", at: t.scheduler.now(), responseId: "r1" });
    t.playback.level = 0;
    t.scheduler.advance(300);
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech.phase).toBe("draining");
    t.playback.level = 0.5;
    t.scheduler.advance(300);
    expect(t.snap().state).toBe("speaking");
    expect(await t.audioDone()).toHaveLength(0);
    // Only the provider's stop (or a real silence) ends it.
    t.transport.emit({ type: "audio_stopped", at: t.scheduler.now(), responseId: "r1" });
    expect(t.snap().state).toBe("listening");
    expect((await t.audioDone())[0].payload).toMatchObject({ basis: "provider" });
  });

  it("(h) an audio_stopped for a PREVIOUS response id does not end the current one", async () => {
    const t = await setup();
    audibleResponse(t, "r1", 1000);
    t.scheduler.advance(200);
    t.transport.emit({ type: "response_done", at: 1500, responseId: "r1" });
    t.playback.level = 0.5;
    // A second response takes over while the first still drains: the first
    // ends as superseded (its real end is unknown from here), the second speaks.
    t.scheduler.advance(100);
    t.transport.emit({ type: "response_started", at: 1600, responseId: "r2" });
    expect(t.snap().speech).toMatchObject({ responseId: "r2", phase: "generating" });
    t.scheduler.advance(200);
    t.transport.emit({ type: "audio_started", at: 1800, responseId: "r2" });
    t.playback.activity(1800);
    t.scheduler.advance(300);
    t.transport.emit({ type: "response_done", at: 2100, responseId: "r2" });
    expect(t.snap().speech).toMatchObject({ responseId: "r2", phase: "draining" });
    // The late stop for r1 is not r2's end.
    t.transport.emit({ type: "audio_stopped", at: 2150, responseId: "r1" });
    expect(t.snap().state).toBe("speaking");
    expect(t.snap().speech).toMatchObject({ responseId: "r2", phase: "draining", playbackDoneAt: null });
    expect(t.log).toContain("audio.stopped.stale");
    // r2's own stop is.
    t.transport.emit({ type: "audio_stopped", at: 2300, responseId: "r2" });
    expect(t.snap().state).toBe("listening");
    const done = await t.audioDone();
    expect(done.map((e) => e.payload)).toEqual([
      expect.objectContaining({ response_id: "r1", basis: "superseded", drain_ms: 100 }),
      expect.objectContaining({ response_id: "r2", basis: "provider", drain_ms: 200 }),
    ]);
  });

  it("an audio_stopped that precedes response_done is remembered: the response ends at response_done, basis provider, at the stop's time", async () => {
    const t = await setup();
    audibleResponse(t);
    t.scheduler.advance(400);
    t.transport.emit({ type: "audio_stopped", at: 1700, responseId: "r1" });
    expect(t.snap().state).toBe("speaking"); // generation may still add audio
    expect(t.snap().speech.phase).toBe("audible");
    t.scheduler.advance(100);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    expect(t.snap().state).toBe("listening");
    expect(t.snap().speech).toMatchObject({ phase: "done", generationDoneAt: 1800, playbackDoneAt: 1700, basis: "provider" });
    expect((await t.audioDone())[0]).toMatchObject({ t_ms: 1700, payload: { basis: "provider" } });
  });

  it("a response that never became audible ends at response_done with no audio_done — no speech is invented", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 100, responseId: "r1" });
    t.transport.emit({ type: "response_done", at: 200, responseId: "r1" });
    expect(t.snap().state).toBe("listening");
    expect(t.snap().speech).toMatchObject({ phase: "done", firstAudioAt: null, playbackDoneAt: 200, basis: null });
    expect(await t.audioDone()).toHaveLength(0);
    t.scheduler.advance(PLAYBACK_DRAIN_MAX_MS);
    expect(await t.audioDone()).toHaveLength(0);
  });

  it("a tool_running continuation keeps its behaviour: the state goes to tool_running at response_done, the audio still ends truthfully", async () => {
    const t = await setup({
      toolResponses: {
        "research.start": { status: "running", long_running: true, preamble: "Bakıyorum.", result: { plan_id: "p1" } },
      },
    });
    // A long-running tool call: the preamble response is audible while the tool runs.
    t.transport.emit({ type: "tool_call", at: 500, callId: "call-1", name: "research.start", arguments: { q: "x" } });
    await tick(8);
    expect(t.snap().state).toBe("tool_running");
    t.transport.emit({ type: "response_started", at: 700, responseId: "r1" });
    expect(t.snap().state).toBe("tool_running"); // unchanged: a tool is running
    t.scheduler.advance(700);
    t.transport.emit({ type: "audio_started", at: 900, responseId: "r1" });
    t.playback.activity(900);
    t.transport.emit({ type: "response_done", at: 1400, responseId: "r1" });
    expect(t.snap().state).toBe("tool_running");
    expect(t.snap().speech.phase).toBe("draining");
    t.transport.emit({ type: "audio_stopped", at: 1600, responseId: "r1" });
    expect(t.snap().state).toBe("tool_running");
    expect(t.snap().speech.phase).toBe("done");
    expect((await t.audioDone())[0].payload).toMatchObject({ response_id: "r1", basis: "provider" });
  });

  it("the audio_done payload passes the server's key rule; identifiers are strings, everything else a number", async () => {
    const t = await setup();
    audibleResponse(t);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    t.transport.emit({ type: "audio_stopped", at: 2000, responseId: "r1" });
    const [done] = await t.audioDone();
    expect(done.kind).toBe("audio_done");
    for (const [key, value] of Object.entries(done.payload ?? {})) {
      expect(isForbiddenKey(key), key).toBe(false);
      if (key === "response_id" || key === "basis") expect(typeof value).toBe("string");
      else expect(typeof value, key).toBe("number");
    }
  });

  it("disconnect while draining cuts the lifecycle and sends its audio_done before CLOSED", async () => {
    const t = await setup();
    audibleResponse(t);
    t.transport.emit({ type: "response_done", at: 1800, responseId: "r1" });
    expect(t.snap().speech.phase).toBe("draining");
    await t.controller.disconnect();
    const kinds = t.core.kinds();
    const closed = t.core.events.findIndex((e) => e.kind === "state" && e.payload?.state === "CLOSED");
    expect(kinds.indexOf("audio_done")).toBeGreaterThan(-1);
    expect(kinds.indexOf("audio_done")).toBeLessThan(closed);
    expect(t.events("audio_done")[0].payload).toMatchObject({ basis: "interrupted" });
  });
});
