/**
 * Wire dialect "openai-live": the JSON events of OpenAI's GPT-Live sessions
 * (developers.openai.com/api/docs/guides/live, live-conversations and
 * live-delegation, read 2026-10-04; team/plans/gpt-live-web-bridge-adr.md).
 *
 * MEASUREMENT ONLY: chosen per session with `?ses=live`; the default provider and the
 * `openai-realtime` dialect are untouched.
 *
 * GPT-Live talks by itself and hands work back to the client as a *delegation*. The
 * vendor's delegation carries metadata, not the task text ("collect transcripts and keep
 * the current task state yourself"), so the `delegation` event carries only the id; the
 * delegation bridge takes the words from the input transcript it keeps. Only what the
 * guides document is mapped: there is no speech start/stop, audio start/stop, response
 * lifecycle, transcript-done or delegation-cancelled event, and none is invented here.
 */

import type { Dialect, TransportEvent } from "../transport";

export const OPENAI_LIVE_DIALECT = "openai-live";

/**
 * The guides cap each append at 500 tokens. There is no tokenizer in the browser (and no
 * new dependency for one): 1200 characters is the deterministic stand-in - Turkish runs at
 * roughly 3-4 characters per token, so 1200 stays well inside 500 tokens.
 */
export const APPEND_MAX_CHARS = 1200;

const ELLIPSIS = "…";

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/** Cut `text` to APPEND_MAX_CHARS on the last word boundary, ending in "…"; shorter text is untouched. */
export function truncateForAppend(text: string): string {
  if (text.length <= APPEND_MAX_CHARS) return text;
  const room = text.slice(0, APPEND_MAX_CHARS - ELLIPSIS.length);
  const space = room.search(/\s\S*$/u);
  const cut = space > 0 ? room.slice(0, space) : room;
  return `${cut.trimEnd()}${ELLIPSIS}`;
}

export class OpenAILiveDialect implements Dialect {
  readonly name = OPENAI_LIVE_DIALECT;

  parseServerEvent(message: unknown, at: number): TransportEvent[] {
    if (!isObj(message)) return [];
    switch (str(message.type)) {
      case "session.input_transcript.delta":
        return [{ type: "owner_transcript", at, text: str(message.delta) ?? "", final: false }];
      case "session.output_transcript.delta":
        return [{ type: "response_text", at, text: str(message.delta) ?? "", final: false }];
      case "session.delegation.created": {
        const delegation = isObj(message.delegation) ? message.delegation : undefined;
        const delegationId = str(delegation?.id);
        if (!delegationId) return [];
        const offsetMs = typeof message.offset_ms === "number" ? message.offset_ms : undefined;
        return [{ type: "delegation", at, delegationId, ...(offsetMs !== undefined ? { offsetMs } : {}) }];
      }
      case "session.closed":
        return [{ type: "disconnected", at, reason: `session_closed:${str(message.reason) ?? "unknown"}` }];
      case "error": {
        const error = isObj(message.error) ? message.error : message;
        return [
          {
            type: "error",
            at,
            message: str(error.message) ?? "provider error",
            code: str(error.code) ?? str(error.type),
          },
        ];
      }
      default:
        return [];
    }
  }

  /** The spoken result of one delegation. Never without its id. */
  commentaryAppend(delegationId: string, text: string): unknown[] {
    if (!delegationId) return [];
    return [{ type: "session.commentary.append", delegation_id: delegationId, content: truncateForAppend(text) }];
  }

  /** Context the model uses but does not say; `null` = the whole session. */
  thinkingAppend(delegationId: string | null, text: string): unknown[] {
    return [{ type: "session.thinking.append", delegation_id: delegationId, content: truncateForAppend(text) }];
  }

  // GPT-Live has no client tool calls and no documented "stop talking" command
  // (UNVERIFIED in the ADR); a barge-in is the controller's local gate. A relay `say`
  // has no delegation id, and a result without one is never sent - so it is empty too.
  cancelResponse(): unknown[] {
    return [];
  }

  submitToolResult(_callId: string, _result: unknown): unknown[] {
    return [];
  }

  notifyToolCompleted(_callId: string, _name: string, _result: unknown): unknown[] {
    return [];
  }

  say(_text: string): unknown[] {
    return [];
  }
}
