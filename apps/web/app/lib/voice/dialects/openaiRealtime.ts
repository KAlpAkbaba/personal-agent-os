/**
 * Wire dialect "openai-realtime": the JSON event family used on the
 * `oai-events`-style data channel (docs/research/realtime-providers-2026-09.md §1).
 *
 * This is a protocol mapping, not a provider choice: it is selected only when
 * the descriptor Cloud Core returns names it, and it carries no model, voice
 * or endpoint. Both the GA event names and the earlier beta names are
 * accepted on the way in, because the API renamed several during its
 * beta → GA migration and the client must not break on either.
 */

import type { Dialect, TransportEvent } from "../transport";

export const OPENAI_REALTIME_DIALECT = "openai-realtime";

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function parseArguments(raw: unknown): Record<string, unknown> {
  if (isObj(raw)) return raw;
  if (typeof raw !== "string" || !raw.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    return isObj(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function functionCallsIn(response: unknown): Array<{
  callId: string;
  name: string;
  arguments: Record<string, unknown>;
}> {
  if (!isObj(response) || !Array.isArray(response.output)) return [];
  const out: Array<{ callId: string; name: string; arguments: Record<string, unknown> }> = [];
  for (const item of response.output) {
    if (!isObj(item) || item.type !== "function_call") continue;
    const callId = str(item.call_id);
    const name = str(item.name);
    if (!callId || !name) continue;
    out.push({ callId, name, arguments: parseArguments(item.arguments) });
  }
  return out;
}

export class OpenAIRealtimeDialect implements Dialect {
  readonly name = OPENAI_REALTIME_DIALECT;

  parseServerEvent(message: unknown, at: number): TransportEvent[] {
    if (!isObj(message)) return [];
    const type = str(message.type);
    if (!type) return [];
    switch (type) {
      case "input_audio_buffer.speech_started":
        return [{ type: "speech_started", at }];
      case "input_audio_buffer.speech_stopped":
        return [{ type: "speech_stopped", at }];
      case "conversation.item.input_audio_transcription.delta":
        return [{ type: "owner_transcript", at, text: str(message.delta) ?? "", final: false }];
      case "conversation.item.input_audio_transcription.completed":
        return [
          { type: "owner_transcript", at, text: str(message.transcript) ?? "", final: true },
        ];
      case "response.created": {
        const response = isObj(message.response) ? message.response : undefined;
        return [{ type: "response_started", at, responseId: str(response?.id) }];
      }
      case "response.output_audio_transcript.delta":
      case "response.audio_transcript.delta":
      case "response.output_text.delta":
      case "response.text.delta":
        return [{ type: "response_text", at, text: str(message.delta) ?? "", final: false }];
      case "response.output_audio_transcript.done":
      case "response.audio_transcript.done":
        return [{ type: "response_text", at, text: str(message.transcript) ?? "", final: true }];
      case "response.output_text.done":
      case "response.text.done":
        return [{ type: "response_text", at, text: str(message.text) ?? "", final: true }];
      case "output_audio_buffer.started":
        return [{ type: "audio_started", at, responseId: str(message.response_id) }];
      case "output_audio_buffer.stopped":
        return [{ type: "audio_stopped", at, responseId: str(message.response_id) }];
      case "response.function_call_arguments.done": {
        const callId = str(message.call_id);
        const name = str(message.name);
        if (!callId || !name) return [];
        return [{ type: "tool_call", at, callId, name, arguments: parseArguments(message.arguments) }];
      }
      case "response.done": {
        const response = isObj(message.response) ? message.response : undefined;
        const responseId = str(response?.id);
        const events: TransportEvent[] = functionCallsIn(response).map((call) => ({
          type: "tool_call" as const,
          at,
          ...call,
        }));
        if (response?.status === "cancelled") {
          events.push({ type: "response_cancelled", at, responseId });
        } else {
          events.push({ type: "response_done", at, responseId });
        }
        return events;
      }
      case "response.cancelled":
        return [{ type: "response_cancelled", at }];
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

  cancelResponse(): unknown[] {
    // The server truncates unplayed audio on cancel; clearing the WebRTC
    // output buffer as well makes the interruption immediate on the wire.
    return [{ type: "response.cancel" }, { type: "output_audio_buffer.clear" }];
  }

  submitToolResult(callId: string, result: unknown): unknown[] {
    return [
      {
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: callId,
          output: JSON.stringify(result ?? {}),
        },
      },
      { type: "response.create" },
    ];
  }

  notifyToolCompleted(callId: string, name: string, result: unknown): unknown[] {
    // The provider already received the `running` output for this call_id;
    // the final outcome arrives as a system message so the model resumes
    // with the real result (spec §6). Provider-side interleaving of tool
    // latency and speech is unverified, so this path never depends on it.
    const text =
      `[araç tamamlandı] ${name} (${callId}): ` + JSON.stringify(result ?? {});
    return [
      {
        type: "conversation.item.create",
        item: { type: "message", role: "system", content: [{ type: "input_text", text }] },
      },
      { type: "response.create" },
    ];
  }

  say(text: string): unknown[] {
    return [
      {
        type: "response.create",
        response: { instructions: `Şu cümleyi kısaca ve aynen söyle: "${text}"` },
      },
    ];
  }
}
