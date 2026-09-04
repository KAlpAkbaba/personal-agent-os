/**
 * M16 §3.2 — the `spoken` state event: the assistant transcript spoken so far,
 * reported when the owner cuts the assistant off (before `barge_in_start`)
 * and when a response completes, so Cloud Core can place the narration
 * cursor at the sentence after the last one fully spoken.
 *
 * Each test fails on the defect it names: a `spoken` queued before the
 * playback stop (latency), one missing or doubled at completion, a head
 * instead of a tail on truncation, or the owner's words inside the
 * assistant's transcript.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceSessionApi } from "../../app/lib/voice/api";
import { isForbiddenKey, MAX_EVENT_TEXT_CHARS } from "../../app/lib/voice/contract";
import { VoiceSessionController, describeNarrationCursor } from "../../app/lib/voice/controller";
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

const OWNER_WORDS = "raporun ikinci maddesini oku";

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
    spoken: () => core.events.filter((e) => e.kind === "spoken"),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("spoken (M16 §3.2)", () => {
  it("barge-in: playback.stop → spoken (final 0, the buffered transcript) → barge_in_start → playback_stopped", async () => {
    const t = await setup();
    t.transport.emit({ type: "owner_transcript", at: 500, text: OWNER_WORDS, final: true });
    t.scheduler.advance(1000);
    t.transport.emit({ type: "response_started", at: 1000 });
    t.transport.emit({ type: "response_text", at: 1100, text: "İkinci madde: ", final: false });
    t.transport.emit({ type: "response_text", at: 1200, text: "dağıtım gecikti. Üçüncü", final: false });
    t.scheduler.advance(300);
    t.playback.activity(1300);
    expect(t.playback.playing).toBe(true);

    t.scheduler.advance(200);
    t.transport.emit({ type: "speech_started", at: 1480 });
    t.log.length = 0;
    t.transport.emit({ type: "owner_transcript", at: 1500, text: "dur", final: false });

    // The fixed barge-in order is untouched; `spoken` is queued after the
    // stop and the cancel, immediately before barge_in_start.
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

    // The provider's cancel acknowledgement (or a racing completion) never
    // produces a second `spoken` for the response that was cut.
    t.transport.emit({ type: "response_cancelled", at: 1600 });
    t.transport.emit({ type: "response_done", at: 1650 });
    await t.controller.flushEvents();

    const kinds = t.core.kinds();
    expect(kinds.indexOf("spoken")).toBeLessThan(kinds.indexOf("barge_in_start"));
    expect(kinds.indexOf("barge_in_start")).toBeLessThan(kinds.indexOf("playback_stopped"));
    const spoken = t.spoken();
    expect(spoken).toHaveLength(1);
    expect(spoken[0]).toEqual({
      kind: "spoken",
      t_ms: 1480,
      turn: 0,
      text: "İkinci madde: dağıtım gecikti. Üçüncü",
      payload: { final: 0, response_seq: 1, chars: "İkinci madde: dağıtım gecikti. Üçüncü".length },
    });
    expect(spoken[0].payload).not.toHaveProperty("truncated");
  });

  it("barge-in before any transcript reports an empty spoken (chars 0), still before barge_in_start", async () => {
    const t = await setup();
    t.transport.emit({ type: "response_started", at: 0 });
    t.scheduler.advance(300);
    t.playback.activity(300);
    t.scheduler.advance(200);
    t.transport.emit({ type: "speech_started", at: 500 });
    t.transport.emit({ type: "owner_transcript", at: 520, text: "bekle", final: false });
    await t.controller.flushEvents();
    const spoken = t.spoken();
    expect(spoken).toHaveLength(1);
    expect(spoken[0]).toMatchObject({ text: "", payload: { final: 0, response_seq: 1, chars: 0 } });
    expect(t.core.kinds().indexOf("spoken")).toBeLessThan(t.core.kinds().indexOf("barge_in_start"));
  });

  it("completion: one spoken final:1 per response with a transcript, none for a silent response", async () => {
    const t = await setup();
    // Response 1: deltas, completed.
    t.transport.emit({ type: "response_started", at: 100 });
    t.transport.emit({ type: "response_text", at: 200, text: "Saat ", final: false });
    t.transport.emit({ type: "response_text", at: 300, text: "on.", final: false });
    t.transport.emit({ type: "response_done", at: 400 });
    // Response 2: nothing said (a tool-only response), completed.
    t.transport.emit({ type: "response_started", at: 500 });
    t.transport.emit({ type: "response_done", at: 600 });
    // Response 3: the provider's final transcript replaces the deltas.
    t.transport.emit({ type: "response_started", at: 700 });
    t.transport.emit({ type: "response_text", at: 750, text: "Bit", final: false });
    t.transport.emit({ type: "response_text", at: 800, text: "Bitti.", final: true });
    t.transport.emit({ type: "response_done", at: 900 });
    await t.controller.flushEvents();

    const spoken = t.spoken();
    expect(spoken).toHaveLength(2);
    expect(spoken[0]).toMatchObject({ t_ms: 400, text: "Saat on.", payload: { final: 1, response_seq: 1, chars: 8 } });
    expect(spoken[1]).toMatchObject({ t_ms: 900, text: "Bitti.", payload: { final: 1, response_seq: 3, chars: 6 } });
    // The transcript precedes the response's completion timing event.
    const kinds = t.core.kinds();
    const firstSpoken = kinds.indexOf("spoken");
    expect(kinds[firstSpoken + 1]).toBe("response_done");
    expect(kinds.filter((k) => k === "response_done")).toHaveLength(3);
  });

  it("a transcript longer than the server limit keeps the TAIL and says so", async () => {
    const t = await setup();
    const tail = "Son cümle burada biter.";
    const head = "Uzun anlatım. ".repeat(400); // 5600 chars of earlier sentences
    t.transport.emit({ type: "response_started", at: 0 });
    t.transport.emit({ type: "response_text", at: 10, text: head, final: false });
    t.transport.emit({ type: "response_text", at: 20, text: tail, final: false });
    t.transport.emit({ type: "response_done", at: 30 });
    await t.controller.flushEvents();

    const [spoken] = t.spoken();
    expect(spoken.text).toHaveLength(MAX_EVENT_TEXT_CHARS);
    expect(spoken.text?.endsWith(tail)).toBe(true);
    expect(spoken.text?.startsWith("Uzun anlatım. Son")).toBe(false);
    expect(spoken.payload).toEqual({ final: 1, response_seq: 1, chars: head.length + tail.length, truncated: 1 });
  });

  it("never carries the owner's transcript, and its payload is numbers under accepted keys", async () => {
    const t = await setup();
    t.transport.emit({ type: "speech_started", at: 100 });
    t.transport.emit({ type: "owner_transcript", at: 300, text: OWNER_WORDS, final: true });
    t.scheduler.advance(400);
    t.transport.emit({ type: "speech_stopped", at: 400 });
    t.scheduler.advance(300);
    // Response 1 completes.
    t.transport.emit({ type: "response_started", at: 700 });
    t.transport.emit({ type: "response_text", at: 800, text: "İkinci madde şu.", final: true });
    t.transport.emit({ type: "response_done", at: 900 });
    // Response 2 is cut while the owner talks again.
    t.scheduler.advance(300);
    t.transport.emit({ type: "response_started", at: 1000 });
    t.transport.emit({ type: "response_text", at: 1100, text: "Üçüncü madde", final: false });
    t.scheduler.advance(200);
    t.playback.activity(1200);
    t.scheduler.advance(100);
    t.transport.emit({ type: "owner_transcript", at: 1250, text: "dur", final: false });
    t.transport.emit({ type: "speech_started", at: 1300 });
    await t.controller.flushEvents();

    const spoken = t.spoken();
    expect(spoken).toHaveLength(2);
    for (const event of spoken) {
      expect(event.text).not.toContain(OWNER_WORDS);
      expect(event.text).not.toContain("dur");
      for (const [key, value] of Object.entries(event.payload ?? {})) {
        expect(isForbiddenKey(key), key).toBe(false);
        expect(typeof value, key).toBe("number");
      }
    }
    expect(spoken[0].text).toBe("İkinci madde şu.");
    expect(spoken[1]).toMatchObject({ text: "Üçüncü madde", payload: { final: 0, response_seq: 2 } });
    // The owner's words still reach Cloud Core, but only as `utterance`.
    expect(t.core.events.filter((e) => e.kind === "utterance").map((e) => e.text)).toEqual([OWNER_WORDS]);
    // No /events batch was refused.
    expect(t.core.events.filter((e) => e.kind === "spoken")).toHaveLength(2);
  });
});

describe("narration cursor sideband (M16 §3.2, /voice)", () => {
  it("keeps the last cursor's state, action and position for the page", async () => {
    const t = await setup();
    t.core.queueSideband("narration_cursor", {
      narration_session_id: "n-1",
      state: "PAUSED",
      action: "dur",
      speed: 1.0,
      cursor: { section_id: "s2", paragraph_id: "p3", sentence_index: 2, char_offset: 0 },
    });
    await t.controller.flushEvents();
    const cursor = t.controller.getSnapshot().narrationCursor;
    expect(cursor).toEqual({ state: "PAUSED", action: "dur", sectionId: "s2", paragraphId: "p3", sentenceIndex: 2 });
    expect(describeNarrationCursor(cursor!)).toBe("s2 ¶p3 3. cümle");
    const line = t.controller.getSnapshot().sidebandLog.at(-1);
    expect(line).toBe("anlatım imleci: PAUSED (dur) — s2 ¶p3 3. cümle");

    // A cursor-less frame (narration finished) still shows the state.
    t.core.queueSideband("narration_cursor", { state: "IDLE", action: null, cursor: null });
    t.transport.emit({ type: "response_started", at: 50 });
    await t.controller.flushEvents();
    expect(t.controller.getSnapshot().narrationCursor).toEqual({
      state: "IDLE",
      action: null,
      sectionId: null,
      paragraphId: null,
      sentenceIndex: null,
    });
    expect(t.controller.getSnapshot().sidebandLog.at(-1)).toBe("anlatım imleci: IDLE — konum yok");
  });
});
