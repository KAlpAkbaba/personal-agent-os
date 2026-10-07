import { describe, expect, it } from "vitest";

import { dialectFor, knownDialects } from "../../app/lib/voice/dialects";
import {
  APPEND_MAX_CHARS,
  OPENAI_LIVE_DIALECT,
  OpenAILiveDialect,
  truncateForAppend,
} from "../../app/lib/voice/dialects/openaiLive";
import { OpenAIRealtimeDialect } from "../../app/lib/voice/dialects/openaiRealtime";

/**
 * The vendor's documented event shapes (developers.openai.com guides/live-delegation and
 * guides/live-conversations, read 2026-10-04; team/plans/gpt-live-web-bridge-adr.md).
 * Nothing here is invented: an event the guides do not name is not mapped.
 */
const DELEGATION_CREATED = {
  type: "session.delegation.created",
  event_id: "event_delegation",
  offset_ms: 1000,
  delegation: { id: "item_delegation_123", type: "delegation", target: "client" },
};
const INPUT_DELTA = { type: "session.input_transcript.delta", delta: "Yarın hava", start_ms: 600, end_ms: 800 };
const OUTPUT_DELTA = { type: "session.output_transcript.delta", delta: "Bakıyorum", start_ms: 900, end_ms: 1100 };
const CLOSED = { type: "session.closed", reason: "close_requested", usage: { seconds: 128 } };
const ERROR = {
  type: "error",
  error: { type: "invalid_request_error", code: "unknown_delegation", message: "no such delegation", param: "delegation_id" },
};

describe("openai-live dialect: vendor events -> TransportEvents", () => {
  const dialect = new OpenAILiveDialect();

  it("maps a delegation to a 'delegation' event carrying the vendor's id (the task text is ours, not the vendor's)", () => {
    expect(dialect.parseServerEvent(DELEGATION_CREATED, 42)).toEqual([
      { type: "delegation", at: 42, delegationId: "item_delegation_123", offsetMs: 1000 },
    ]);
  });

  it("drops a delegation without an id: nothing could ever be answered for it", () => {
    expect(dialect.parseServerEvent({ type: "session.delegation.created", delegation: {} }, 1)).toEqual([]);
    expect(dialect.parseServerEvent({ type: "session.delegation.created" }, 1)).toEqual([]);
  });

  it("maps the two transcript streams as deltas (the vendor documents no 'done' event)", () => {
    expect(dialect.parseServerEvent(INPUT_DELTA, 5)).toEqual([
      { type: "owner_transcript", at: 5, text: "Yarın hava", final: false },
    ]);
    expect(dialect.parseServerEvent(OUTPUT_DELTA, 6)).toEqual([
      { type: "response_text", at: 6, text: "Bakıyorum", final: false },
    ]);
  });

  it("maps session.closed to disconnected and error to error", () => {
    expect(dialect.parseServerEvent(CLOSED, 7)).toEqual([
      { type: "disconnected", at: 7, reason: "session_closed:close_requested" },
    ]);
    expect(dialect.parseServerEvent(ERROR, 8)).toEqual([
      { type: "error", at: 8, message: "no such delegation", code: "unknown_delegation" },
    ]);
  });

  it("does not invent events the guides do not document", () => {
    for (const type of [
      "session.started",
      "session.updated",
      "session.commentary.appended",
      "input_audio_buffer.speech_started",
      "response.created",
      "something.new",
    ]) {
      expect(dialect.parseServerEvent({ type }, 1)).toEqual([]);
    }
    expect(dialect.parseServerEvent(null, 1)).toEqual([]);
    expect(dialect.parseServerEvent("x", 1)).toEqual([]);
  });

  it("builds commentary.append for one delegation id, and thinking.append with an id or the session (null)", () => {
    expect(dialect.commentaryAppend("item_delegation_123", "Yarın İstanbul'da 18 derece.")).toEqual([
      { type: "session.commentary.append", delegation_id: "item_delegation_123", content: "Yarın İstanbul'da 18 derece." },
    ]);
    expect(dialect.thinkingAppend("item_delegation_123", "bağlam")).toEqual([
      { type: "session.thinking.append", delegation_id: "item_delegation_123", content: "bağlam" },
    ]);
    expect(dialect.thinkingAppend(null, "oturum bağlamı")).toEqual([
      { type: "session.thinking.append", delegation_id: null, content: "oturum bağlamı" },
    ]);
  });

  it("never builds a commentary without a delegation id", () => {
    expect(dialect.commentaryAppend("", "metin")).toEqual([]);
  });

  it("cuts an append at the 500-token stand-in (1200 chars) on a word boundary, with an ellipsis", () => {
    const word = "kelime ";
    const long = word.repeat(400); // 2800 chars
    const [message] = dialect.commentaryAppend("d1", long) as Array<{ content: string }>;
    expect(message.content.length).toBeLessThanOrEqual(APPEND_MAX_CHARS);
    expect(message.content.endsWith("…")).toBe(true);
    expect(message.content.slice(0, -1).endsWith("kelime")).toBe(true);
    const [thinking] = dialect.thinkingAppend(null, long) as Array<{ content: string }>;
    expect(thinking.content).toBe(message.content);
    // Under the cap the text is untouched; one unbroken run is cut hard.
    expect(truncateForAppend("kısa cümle")).toBe("kısa cümle");
    const run = "a".repeat(5000);
    expect(truncateForAppend(run)).toHaveLength(APPEND_MAX_CHARS);
    expect(APPEND_MAX_CHARS).toBe(1200);
  });

  it("speaks only through delegations: tool, cancel and say commands are empty (no documented shape)", () => {
    expect(dialect.cancelResponse()).toEqual([]);
    expect(dialect.submitToolResult("c", {})).toEqual([]);
    expect(dialect.notifyToolCompleted("c", "n", {})).toEqual([]);
    expect(dialect.say("merhaba")).toEqual([]);
  });
});

describe("dialect registry", () => {
  it("resolves 'openai-live' and leaves 'openai-realtime' as it was", () => {
    expect(OPENAI_LIVE_DIALECT).toBe("openai-live");
    expect(dialectFor("openai-live")).toBeInstanceOf(OpenAILiveDialect);
    expect(dialectFor("openai-live").name).toBe("openai-live");
    expect(dialectFor("openai-realtime")).toBeInstanceOf(OpenAIRealtimeDialect);
    expect(knownDialects()).toEqual(["openai-live", "openai-realtime"]);
  });
});
