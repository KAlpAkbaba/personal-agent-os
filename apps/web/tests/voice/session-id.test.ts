/**
 * ADR-0045 addendum: the owner copies the CANONICAL full session UUID; the
 * page never asks them to transcribe it. The id survives disconnect.
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
import { canonicalSessionId, copySessionId, copyText, shortSessionId } from "../../app/lib/voice/session-id";

const FULL = "11111111-2222-4333-8444-555555555555"; // what FakeCloudCore mints

function rig() {
  const scheduler = new FakeScheduler();
  const core = new FakeCloudCore({ transport: "webrtc" });
  const controller = new VoiceSessionController({
    api: new VoiceSessionApi(core.fetcher),
    transportFactory: () => new FakeTransport({ now: scheduler.now }),
    playback: new FakePlayback(scheduler.now),
    network: new FakeNetwork(),
    microphone: new FakeMicrophone(),
    localSpeech: new FakeSpeechDetector(),
    now: scheduler.now,
    scheduler,
  });
  return { controller, core };
}

/** A clipboard that records what it was handed. */
function recorder(options: { reject?: boolean } = {}) {
  const written: string[] = [];
  return {
    written,
    clipboard: {
      writeText: async (text: string) => {
        if (options.reject) throw new DOMException("denied", "NotAllowedError");
        written.push(text);
      },
    },
  };
}

describe("session id after disconnect", () => {
  it("is the full canonical UUID while live and stays available after disconnect", async () => {
    const t = rig();
    await t.controller.connect();
    expect(t.controller.getSnapshot().sessionId).toBe(FULL);
    await t.controller.disconnect();
    const closed = t.controller.getSnapshot();
    expect(closed.state).toBe("closed");
    expect(closed.sessionId).toBe(FULL);
    expect(t.core.closed).toBe("client_closed");
  });

  it("survives a failed create and is only replaced when a new session starts", async () => {
    const t = rig();
    await t.controller.connect();
    await t.controller.disconnect();
    t.core.failNext("/sessions", 503);
    await t.controller.connect();
    expect(t.controller.getSnapshot().state).toBe("error");
    expect(t.controller.getSnapshot().sessionId).toBe(FULL);
  });

  it("the copy handler receives the full UUID, not the 8-character short form", async () => {
    const t = rig();
    await t.controller.connect();
    await t.controller.disconnect();
    const snapshot = t.controller.getSnapshot();
    expect(shortSessionId(snapshot.sessionId as string)).toBe("11111111…");
    const rec = recorder();
    const result = await copySessionId(snapshot.sessionId, { clipboard: rec.clipboard });
    expect(result).toEqual({ outcome: "clipboard", text: FULL });
    expect(rec.written).toEqual([FULL]);
    expect(rec.written[0]).toHaveLength(36);
  });
});

describe("copySessionId / copyText", () => {
  it("falls back to the select-and-copy path with the same full UUID when the clipboard API refuses", async () => {
    const rec = recorder({ reject: true });
    const selected: string[] = [];
    const result = await copySessionId(FULL, {
      clipboard: rec.clipboard,
      fallback: (text) => {
        selected.push(text);
        return true;
      },
    });
    expect(result).toEqual({ outcome: "fallback", text: FULL });
    expect(rec.written).toEqual([]);
    expect(selected).toEqual([FULL]);
  });

  it("reports failure without throwing when neither path works, and never copies a non-UUID", async () => {
    expect(await copyText("x", {})).toBe("failed");
    expect(await copyText("x", { fallback: () => false })).toBe("failed");
    expect(
      await copyText("x", {
        fallback: () => {
          throw new Error("no document");
        },
      }),
    ).toBe("failed");
    const rec = recorder();
    expect(await copySessionId(null, { clipboard: rec.clipboard })).toEqual({ outcome: "failed", text: null });
    expect(await copySessionId("11111111…", { clipboard: rec.clipboard })).toEqual({ outcome: "failed", text: null });
    expect(rec.written).toEqual([]);
  });

  it("canonical form is lower-case and trimmed; the short form keeps 8 characters", () => {
    expect(canonicalSessionId(` ${FULL.toUpperCase()} `)).toBe(FULL);
    expect(canonicalSessionId("not-a-uuid")).toBeNull();
    expect(canonicalSessionId(undefined)).toBeNull();
    expect(shortSessionId(FULL)).toBe("11111111…");
    expect(shortSessionId("abc")).toBe("abc");
  });
});
