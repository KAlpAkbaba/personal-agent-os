import { describe, expect, it } from "vitest";

import {
  CLIENT_EVENT_KINDS,
  MAX_EVENTS_PER_REQUEST,
  type ClientEvent,
  type EventsResponse,
  type SidebandFrame,
} from "../../app/lib/voice/contract";
import {
  EventReporter,
  buildClientEvent,
  isForbiddenPayloadKey,
  scrubPayload,
} from "../../app/lib/voice/events";
import { FakeScheduler, settle } from "../../app/lib/voice/fake";

const okResponse = (events: ClientEvent[], pending: SidebandFrame[] = []): EventsResponse => ({
  accepted: events.length,
  resolved_intents: [],
  pending_sideband: pending,
  state: {} as EventsResponse["state"],
});

describe("event reporting shape", () => {
  it("only knows the server's event kinds", () => {
    expect(CLIENT_EVENT_KINDS).toContain("barge_in_start");
    expect(CLIENT_EVENT_KINDS).toContain("playback_stopped");
    expect(CLIENT_EVENT_KINDS).toContain("network_lost");
    expect(CLIENT_EVENT_KINDS).toContain("summary");
    expect(CLIENT_EVENT_KINDS).toContain("spoken"); // M16 §3.2 state kind
    expect(() =>
      buildClientEvent({ kind: "telemetry" as unknown as "state" }, 0),
    ).toThrow(/unknown client event kind/);
  });

  it("produces integer, non-negative, monotonic-clock timestamps and a turn", () => {
    const event = buildClientEvent({ kind: "first_audio", t_ms: 1234.6, turn: 2.2 }, 0);
    expect(event).toEqual({ kind: "first_audio", t_ms: 1235, turn: 2 });
    expect(buildClientEvent({ kind: "end_of_turn", t_ms: -5 }, 0).t_ms).toBe(0);
    expect(buildClientEvent({ kind: "end_of_turn" }, 77.4).t_ms).toBe(77);
  });

  it("never lets audio or a credential into a payload", () => {
    expect(isForbiddenPayloadKey("audio_frame")).toBe(true);
    expect(isForbiddenPayloadKey("clientSecret")).toBe(true);
    expect(isForbiddenPayloadKey("api_key")).toBe(true);
    expect(isForbiddenPayloadKey("playback_stopped_ms")).toBe(false);
    // service.py normalizes before matching: any spelling of the same key is refused.
    for (const spelling of ["apiKey", "api-key", "API_KEY", "Api Key", "access_token", "Password2", "transcriptLen", "context"]) {
      expect(isForbiddenPayloadKey(spelling)).toBe(true);
    }
    for (const safe of ["rms_db", "noise_floor_db", "speech_prob", "gate_opens", "false_starts", "hold_ms", "hesitation"]) {
      expect(isForbiddenPayloadKey(safe)).toBe(false);
    }
    const scrubbed = scrubPayload({
      playback_stopped_ms: 70,
      audio: new ArrayBuffer(8),
      credential: "ek_x",
      nested: { pcm16: new Uint8Array(4), ok: true },
      list: [new ArrayBuffer(2), { secret: 1, keep: 2 }],
    });
    expect(scrubbed).toEqual({ playback_stopped_ms: 70, nested: { ok: true }, list: [{ keep: 2 }] });
  });

  it("bounds text and oversized payloads", () => {
    const long = "a".repeat(5000);
    expect(buildClientEvent({ kind: "summary", text: long }, 0).text?.length).toBe(4000);
    const big = buildClientEvent({ kind: "state", payload: { blob: "x".repeat(5000) } }, 0);
    expect(big.payload).toEqual({ truncated: true, keys: ["blob"] });
  });

  it("batches, flushes on the timer, and keeps a failed batch", async () => {
    const scheduler = new FakeScheduler();
    const posted: ClientEvent[][] = [];
    let fail = true;
    const reporter = new EventReporter(
      "sess-A",
      async (_sessionId, events) => {
        if (fail) throw new Error("network");
        posted.push(events);
        return okResponse(events);
      },
      scheduler.now,
      { flushIntervalMs: 250, scheduler },
    );
    reporter.report({ kind: "mic_speech_start", turn: 1 });
    scheduler.advance(100);
    reporter.report({ kind: "end_of_turn", turn: 1 });
    expect(reporter.pending).toBe(2);
    scheduler.advance(250);
    await settle();
    expect(posted).toHaveLength(0);
    expect(reporter.pending).toBe(2); // failed batch stays for the retry
    fail = false;
    await reporter.flush();
    expect(posted).toHaveLength(1);
    expect(posted[0].map((e) => e.kind)).toEqual(["mic_speech_start", "end_of_turn"]);
    expect(posted[0][1].t_ms).toBe(100);
    expect(reporter.pending).toBe(0);
    expect(reporter.accepted).toBe(2);
  });

  it("never sends more than the server accepts per request", async () => {
    const scheduler = new FakeScheduler();
    const sizes: number[] = [];
    const reporter = new EventReporter(
      "sess-A",
      async (_sessionId, events) => {
        sizes.push(events.length);
        return okResponse(events);
      },
      scheduler.now,
      { flushIntervalMs: 10, scheduler },
    );
    for (let i = 0; i < MAX_EVENTS_PER_REQUEST + 5; i += 1) {
      reporter.report({ kind: "audio_frame", payload: { bytes: 640 } });
    }
    await reporter.flush();
    expect(sizes[0]).toBe(MAX_EVENTS_PER_REQUEST);
    expect(reporter.pending).toBe(5);
  });

  it("hands replayed sideband frames to the subscriber", async () => {
    const scheduler = new FakeScheduler();
    const frame: SidebandFrame = {
      type: "voice_sideband",
      session_id: "s",
      event: "say",
      payload: { text: "Bakıyorum." },
      at: "2026-09-02T00:00:00Z",
    };
    const reporter = new EventReporter(
      "sess-A",
      async (_sessionId, events) => okResponse(events, [frame]),
      scheduler.now,
      { flushIntervalMs: 10, scheduler },
    );
    const seen: SidebandFrame[] = [];
    reporter.onSideband((f) => seen.push(f));
    reporter.report({ kind: "state", payload: { state: "LISTENING" } });
    await reporter.flush();
    expect(seen).toEqual([frame]);
  });
});
