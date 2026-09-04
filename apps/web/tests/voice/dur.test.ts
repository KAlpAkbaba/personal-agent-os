/**
 * "Dur" after the assistant has already finished (owner-observed defect,
 * 2026-09-04, real realtime session over WebRTC): the provider's response had
 * completed while its audio was still draining locally; the owner's "dur"
 * stopped the audio but also sent a `response.cancel`, which the provider
 * answered with "Cancellation failed: no active response found" — surfaced to
 * the owner as an error.
 *
 * Each test fails on the defect it names:
 *  a. a barge-in over an ACTIVE response must keep today's fixed order and
 *     cancel the provider (`provider_cancel: 1`, ADR-0040 §3);
 *  b. a barge-in over DRAINING audio (response already done) must stop local
 *     playback and report, but send NO cancel (`provider_cancel: 0`);
 *  c. owner speech when nothing is active and nothing is playing is no
 *     barge-in at all; the utterance still flows to Cloud Core;
 *  d. a provider "no active response" cancel error is benign: no `lastError`,
 *     counted, reported as `error_class: cancel_noop`; other errors unchanged;
 *  e. after (a) the next `response_started` re-arms the barge-in, so a second
 *     "dur" cancels again (double-Dur regression).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { isForbiddenKey } from "../../app/lib/voice/contract";
import { VoiceSessionController, classifyProviderError } from "../../app/lib/voice/controller";
import { EventReporter, type ReportInput } from "../../app/lib/voice/events";
import {
  FakeCloudCore,
  FakeMicrophone,
  FakeNetwork,
  FakePlayback,
  FakeScheduler,
  FakeSpeechDetector,
  FakeTransport,
} from "../../app/lib/voice/fake";

/**
 * The critical barge-in sequence, without the decision lane's own bookkeeping.
 * The two-lane policy logs WHY it interrupted (`barge_in.accepted@…`,
 * `barge_in.dropped`, `barge_in.explicit_stop`) before it acts; the ORDER that
 * must never change is the acting part (ADR-0040 §3), so the assertions below
 * read that and check the decision separately.
 */
const acted = (log: readonly string[]): string[] => log.filter((line) => !line.startsWith("barge_in."));


const tick = async (rounds = 4): Promise<void> => {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

const NO_ACTIVE_RESPONSE = "Cancellation failed: no active response found";

/** The playback fake logs `playback.stop`; every reporter call is logged as `report.<kind>` next to it. */
async function setup() {
  const log: string[] = [];
  const original = EventReporter.prototype.report;
  vi.spyOn(EventReporter.prototype, "report").mockImplementation(function (this: EventReporter, input: ReportInput) {
    log.push(`report.${input.kind}`);
    return original.call(this, input);
  });
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
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
  return {
    controller,
    core,
    scheduler,
    playback,
    log,
    get transport() {
      return transports[transports.length - 1];
    },
    events: (kind: string) => core.events.filter((e) => e.kind === kind),
  };
}

/** A response that is audible at `at + 300` and still speaking when the script continues. */
function speakingResponse(t: Awaited<ReturnType<typeof setup>>, at: number, text = "İkinci madde: dağıtım gecikti."): void {
  t.scheduler.advance(at - t.scheduler.now());
  t.transport.emit({ type: "response_started", at });
  t.transport.emit({ type: "response_text", at: at + 100, text, final: false });
  t.scheduler.advance(300);
  t.transport.emit({ type: "audio_started", at: at + 300 });
  t.playback.activity(at + 300);
  expect(t.playback.playing).toBe(true);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("dur while the assistant speaks (unchanged, ADR-0040 §3)", () => {
  it("a. playback.stop → cancel → spoken → barge_in_start → playback_stopped, provider_cancel 1", async () => {
    const t = await setup();
    speakingResponse(t, 1000);
    t.scheduler.advance(200); // now = 1500
    t.transport.emit({ type: "speech_started", at: 1480 });
    // The client owns interruption: the onset mutes reversibly; the control
    // phrase in the first transcript delta is what stops and cancels.
    expect(t.transport.sent).toEqual([]);
    t.log.length = 0;
    t.transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });

    expect(acted(t.log).slice(0, 7)).toEqual([
      "playback.stop",
      "fake.cancelResponse",
      "transport.cancel",
      "report.spoken",
      "report.barge_in_start",
      "report.playback_stopped",
      "report.barge_in",
    ]);
    expect(t.playback.playing).toBe(false);
    expect(t.transport.sent).toEqual(["cancel"]);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    expect(t.controller.getSnapshot().lastError).toBeNull();

    await t.controller.flushEvents();
    const kinds = t.core.kinds();
    expect(kinds.indexOf("spoken")).toBeLessThan(kinds.indexOf("barge_in_start"));
    expect(kinds.indexOf("barge_in_start")).toBeLessThan(kinds.indexOf("playback_stopped"));
    const [barge] = t.events("barge_in_start");
    expect(barge).toMatchObject({ t_ms: 1480, turn: 1, payload: { provider_cancel: 1, playback_stopped_ms: 20, source: 2, lane: 1 } });
    expect(barge.payload).toHaveProperty("stop_command_ms");
    expect(t.controller.getSnapshot().micMetrics.explicit_stop_command).toBe(1);
    expect(t.events("spoken")).toHaveLength(1);
    expect(t.events("spoken")[0]).toMatchObject({ text: "İkinci madde: dağıtım gecikti.", payload: { final: 0, response_seq: 1 } });
    expect(t.events("playback_stopped")).toHaveLength(1);
    expect(t.events("error")).toHaveLength(0);
    expect(t.controller.getSnapshot().latencyDetail.barge_in?.provider_cancel).toBe(1);
  });

  it("e. after a true interruption the next response_started re-arms the barge-in: a second dur cancels again", async () => {
    const t = await setup();
    // First dur, over an active response.
    speakingResponse(t, 1000);
    t.scheduler.advance(200);
    t.transport.emit({ type: "speech_started", at: 1480 });
    t.transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });
    expect(t.transport.sent).toEqual(["cancel"]);
    t.transport.emit({ type: "response_cancelled", at: 1600 });
    expect(t.controller.getSnapshot().state).toBe("listening");
    // The final transcript of the same turn repeats the phrase: nothing to stop twice.
    t.transport.emit({ type: "owner_transcript", at: 1700, text: "dur", final: true });
    expect(t.transport.sent).toEqual(["cancel"]);
    t.scheduler.advance(300);
    t.transport.emit({ type: "speech_stopped", at: 1800 });
    t.scheduler.advance(300); // past the end-of-turn hold

    // "Devam et" is answered by Cloud Core (server-side); on the client the
    // provider simply starts a new response, which must be interruptible.
    speakingResponse(t, 2500, "Üçüncü madde: ");
    t.scheduler.advance(200);
    t.transport.emit({ type: "speech_started", at: 2990 });
    t.log.length = 0;
    t.transport.emit({ type: "owner_transcript", at: 3020, text: "Dur.", final: false });

    expect(acted(t.log).slice(0, 3)).toEqual(["playback.stop", "fake.cancelResponse", "transport.cancel"]);
    expect(t.transport.sent).toEqual(["cancel", "cancel"]);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    await t.controller.flushEvents();
    const barges = t.events("barge_in_start");
    expect(barges).toHaveLength(2);
    expect(barges[0]).toMatchObject({ turn: 1, payload: { provider_cancel: 1 } });
    expect(barges[1]).toMatchObject({ turn: 2, payload: { provider_cancel: 1 } });
    expect(t.events("spoken").map((e) => e.payload?.response_seq)).toEqual([1, 2]);
    expect(t.events("error")).toHaveLength(0);
    expect(t.controller.getSnapshot().lastError).toBeNull();
    expect(t.controller.getSnapshot().micMetrics.explicit_stop_command).toBe(2);
  });
});

describe("dur after the response already completed", () => {
  it("b. audio still draining: playback stopped and reported, NO cancel sent, provider_cancel 0", async () => {
    const t = await setup();
    speakingResponse(t, 1000);
    // The provider is done generating while the local audio is still playing.
    t.scheduler.advance(100);
    t.transport.emit({ type: "response_done", at: 1400 });
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.playback.playing).toBe(true); // draining

    t.scheduler.advance(100); // now = 1500
    t.transport.emit({ type: "speech_started", at: 1480 });
    expect(t.playback.muted).toBe(true); // reversible, over the draining audio too
    t.log.length = 0;
    t.transport.emit({ type: "owner_transcript", at: 1520, text: "dur", final: false });

    // Local playback is silenced first, exactly as for an active response;
    // the provider cancel is skipped because `responseActive` is false.
    expect(acted(t.log).slice(0, 5)).toEqual([
      "playback.stop",
      "transport.cancel_skipped",
      "report.barge_in_start",
      "report.playback_stopped",
      "report.barge_in",
    ]);
    expect(t.log).not.toContain("fake.cancelResponse");
    expect(t.log).not.toContain("transport.cancel");
    expect(t.transport.sent).toEqual([]);
    expect(t.playback.playing).toBe(false);
    expect(t.controller.getSnapshot().state).toBe("interrupted");
    expect(t.controller.getSnapshot().lastError).toBeNull();

    await t.controller.flushEvents();
    const [barge] = t.events("barge_in_start");
    expect(barge).toMatchObject({ t_ms: 1480, turn: 1, payload: { provider_cancel: 0, playback_stopped_ms: 20 } });
    expect(barge.payload).not.toHaveProperty("stop_command_ms");
    expect(t.events("playback_stopped")).toHaveLength(1);
    // The completed response already reported its whole transcript (M16 §3.2:
    // one `spoken` per response); the cut over draining audio adds none.
    expect(t.events("spoken")).toHaveLength(1);
    expect(t.events("spoken")[0]).toMatchObject({ t_ms: 1400, payload: { final: 1, response_seq: 1 } });
    expect(t.events("error")).toHaveLength(0);
    expect(t.controller.getSnapshot().latencyDetail.barge_in?.provider_cancel).toBe(0);
    for (const [key, value] of Object.entries(barge.payload ?? {})) {
      expect(isForbiddenKey(key), key).toBe(false);
      expect(typeof value, key).toBe("number");
    }
  });

  it("b'. the hesitation-resume path over draining audio also skips the cancel", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 100 });
    t.transport.emit({ type: "owner_transcript", at: 400, text: "raporun ikinci maddesini şey", final: true });
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 500 }); // filler tail: 900 ms hold
    t.scheduler.advance(300);
    // A premature response that completes (generation done) while still draining.
    t.transport.emit({ type: "response_started", at: 800 });
    t.transport.emit({ type: "response_done", at: 850 });
    expect(t.playback.playing).toBe(true);
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_started", at: 900 });
    expect(t.log).toContain("playback.stop");
    expect(t.transport.sent).toEqual([]);
    await t.controller.flushEvents();
    expect(t.events("barge_in_start")[0].payload).toMatchObject({ hesitation_resume: 1, provider_cancel: 0 });
    expect(t.events("error")).toHaveLength(0);
  });

  it("c. nothing active, nothing playing: no barge-in, no cancel, no error; the utterance still reports", async () => {
    const t = await setup();
    speakingResponse(t, 1000);
    t.scheduler.advance(100);
    t.transport.emit({ type: "response_done", at: 1400 });
    t.scheduler.advance(600);
    t.transport.emit({ type: "audio_stopped", at: 2000 });
    t.playback.playing = false; // the local sink drained
    expect(t.controller.getSnapshot().state).toBe("listening");

    t.log.length = 0;
    t.scheduler.advance(500);
    t.transport.emit({ type: "speech_started", at: 2480 });
    t.transport.emit({ type: "owner_transcript", at: 2700, text: "dur", final: true });
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 2900 });
    t.scheduler.advance(300);

    expect(t.log).not.toContain("playback.stop");
    expect(t.log).not.toContain("transport.cancel");
    expect(t.log).not.toContain("transport.cancel_skipped");
    expect(t.transport.sent).toEqual([]);
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.controller.getSnapshot().lastError).toBeNull();
    await t.controller.flushEvents();
    expect(t.events("barge_in_start")).toHaveLength(0);
    expect(t.events("playback_stopped")).toHaveLength(0);
    expect(t.events("error")).toHaveLength(0);
    expect(t.events("utterance")).toEqual([expect.objectContaining({ turn: 1, text: "dur" })]);
    expect(t.events("end_of_turn")).toHaveLength(1);
  });
});

describe("provider 'no active response' cancel error (benign)", () => {
  it("classifies on the code or the message, nothing else", () => {
    expect(classifyProviderError("response_cancel_not_active", "anything")).toBe("cancel_noop");
    expect(classifyProviderError("invalid_request_error", NO_ACTIVE_RESPONSE)).toBe("cancel_noop");
    expect(classifyProviderError(undefined, "No Active Response for this session")).toBe("cancel_noop");
    expect(classifyProviderError("server_error", "internal")).toBeNull();
    expect(classifyProviderError(undefined, "rate limit reached")).toBeNull();
  });

  it("d. no lastError, no error state, counted, reported as error_class cancel_noop; other errors unchanged", async () => {
    const t = await setup();
    // The owner-observed sequence: dur over draining audio, then the late
    // provider answer to a cancel (as an older client would have sent one).
    speakingResponse(t, 1000);
    t.transport.emit({ type: "response_done", at: 1400 });
    t.scheduler.advance(100);
    t.transport.emit({ type: "speech_started", at: 1480 });
    t.transport.emit({ type: "owner_transcript", at: 1500, text: "dur", final: false });
    t.log.length = 0;
    t.transport.emit({ type: "error", at: 1520, code: "response_cancel_not_active", message: NO_ACTIVE_RESPONSE });

    expect(t.controller.getSnapshot().lastError).toBeNull();
    expect(t.controller.getSnapshot().state).toBe("interrupted"); // untouched by the benign error
    expect(t.controller.getSnapshot().micMetrics.cancel_noop_errors).toBe(1);
    expect(t.log).toContain("provider.cancel_noop");

    // The message-only shape (no code) is the same benign case.
    t.transport.emit({ type: "error", at: 1530, code: "invalid_request_error", message: NO_ACTIVE_RESPONSE });
    expect(t.controller.getSnapshot().lastError).toBeNull();
    expect(t.controller.getSnapshot().micMetrics.cancel_noop_errors).toBe(2);

    // Any other provider error keeps today's handling.
    t.transport.emit({ type: "error", at: 1600, code: "server_error", message: "The server had an error" });
    expect(t.controller.getSnapshot().lastError).toBe("The server had an error");
    expect(t.controller.getSnapshot().micMetrics.cancel_noop_errors).toBe(2);

    await t.controller.flushEvents();
    const errors = t.events("error");
    expect(errors).toHaveLength(3);
    expect(errors[0]).toMatchObject({ t_ms: 1520, turn: 1, payload: { error_class: "cancel_noop", cancel_noop_errors: 1 } });
    expect(errors[1]).toMatchObject({ payload: { error_class: "cancel_noop", cancel_noop_errors: 2 } });
    expect(errors[2]).toMatchObject({ payload: { error_class: "server_error" } });
    for (const event of errors) {
      expect(event.text).toBeUndefined();
      expect(JSON.stringify(event)).not.toContain("no active response");
      for (const [key, value] of Object.entries(event.payload ?? {})) {
        expect(isForbiddenKey(key), key).toBe(false);
        expect(typeof value === "number" || (key === "error_class" && typeof value === "string" && value.length <= 32), key).toBe(true);
      }
    }
    // The session's metrics carry the count too (numbers only), so the
    // benchmark can tell a benign cancel echo from a real provider fault.
    await t.controller.disconnect();
    const closing = t.events("state").filter((e) => e.payload?.mic_metrics === 1 && e.payload?.session_end === 1);
    expect(closing).toHaveLength(1);
    expect(closing[0].payload).toMatchObject({ cancel_noop_errors: 2 });
  });

  it("a benign cancel error before any barge-in is still benign", async () => {
    const t = await setup();
    t.transport.emit({ type: "error", at: 50, code: "response_cancel_not_active", message: NO_ACTIVE_RESPONSE });
    expect(t.controller.getSnapshot().lastError).toBeNull();
    expect(t.controller.getSnapshot().state).toBe("listening");
    expect(t.controller.getSnapshot().micMetrics.cancel_noop_errors).toBe(1);
  });
});
