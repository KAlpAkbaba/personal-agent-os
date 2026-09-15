/**
 * Provider-agnostic realtime media transport (M12 spec §1, track D).
 *
 * The browser never knows a vendor or a model. It knows:
 *
 * - a `TransportDescriptor` — WHERE to exchange SDP, WHAT the data channel is
 *   called, WHICH wire dialect the JSON events on it follow, and the audio
 *   formats — all returned by Cloud Core with the session;
 * - a `SessionCredential` — the short-lived per-session secret Cloud Core
 *   minted through the provider adapter.
 *
 * Every provider-specific detail lives in a *dialect* (dialects/*.ts), a pure
 * mapping between the provider's JSON events and the normalized
 * `TransportEvent`s below. The controller only ever sees normalized events.
 */

import type { SessionCredential, SessionLegPayload } from "./contract";

/**
 * How to open the media leg. `kind` is the server's `transport` string. The
 * remaining fields are read from `transport_descriptor` on the session payload
 * (or on the credential); a WebRTC leg refuses to connect without an SDP
 * exchange endpoint, a data-channel name and a dialect — the browser never
 * fills those in from a built-in vendor table.
 */
export type TransportDescriptor = {
  kind: "webrtc" | "websocket" | "simulated" | (string & {});
  /** WebRTC: where the SDP offer is POSTed with the credential as bearer. */
  sdp_exchange_url?: string;
  /**
   * WebRTC: how the offer is sent. `application/sdp` (default) posts the raw
   * offer; `multipart/form-data` posts `sdp` plus a `session` JSON field
   * carrying `session_config` (some providers expect this shape).
   */
  sdp_content_type?: "application/sdp" | "multipart/form-data";
  /** WebRTC: name of the events data channel (e.g. provided by the adapter). */
  data_channel?: string;
  /** Wire dialect for the JSON events on the data channel. */
  dialect?: string;
  /** Opaque provider session config the adapter wants sent with the offer. */
  session_config?: Record<string, unknown>;
  /** Extra headers for the SDP exchange (never the vendor key). */
  headers?: Record<string, string>;
  audio?: {
    input_format?: string;
    output_format?: string;
    sample_rate_hz?: number;
  };
  /** Anything else the adapter added; kept opaque. */
  [extra: string]: unknown;
};

export class TransportConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TransportConfigError";
  }
}

/**
 * Build the descriptor from a session/attach payload. The server's `transport`
 * string is authoritative for `kind`; any descriptor object is merged over it.
 */
export function resolveTransportDescriptor(
  payload: Pick<SessionLegPayload, "transport" | "credential"> & {
    transport_descriptor?: Record<string, unknown>;
  },
): TransportDescriptor {
  const raw =
    payload.transport_descriptor ??
    payload.credential?.transport_descriptor ??
    undefined;
  const base: TransportDescriptor = { kind: payload.transport };
  if (raw && typeof raw === "object") {
    return { ...(raw as Record<string, unknown>), ...base, kind: payload.transport };
  }
  return base;
}

/** Audio the client pushes toward the provider. */
export type AudioInput =
  | { kind: "track"; track: MediaStreamTrack }
  | { kind: "pcm16"; data: ArrayBuffer; filler?: boolean };

/** Audio the provider sends back. */
export type AudioOutput =
  | { kind: "stream"; stream: MediaStream }
  | { kind: "pcm16"; data: ArrayBuffer; responseId?: string };

/**
 * Normalized provider events. `at` is the transport's own monotonic
 * timestamp (ms) for the moment the event was observed on the client.
 */
export type TransportEvent =
  | { type: "connected"; at: number }
  | { type: "speech_started"; at: number }
  | { type: "speech_stopped"; at: number }
  | { type: "owner_transcript"; at: number; text: string; final: boolean }
  | { type: "response_started"; at: number; responseId?: string }
  | { type: "response_text"; at: number; text: string; final: boolean }
  | { type: "audio_started"; at: number; responseId?: string }
  | { type: "audio_stopped"; at: number; responseId?: string }
  | { type: "response_done"; at: number; responseId?: string }
  | { type: "response_cancelled"; at: number; responseId?: string }
  | {
      type: "tool_call";
      at: number;
      callId: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | { type: "error"; at: number; message: string; code?: string }
  /**
   * B20 req 218: the link is in trouble but not gone.
   *
   * WebRTC says `disconnected` before it says `failed`, and the gap between them is the
   * browser's own patience - seconds in which no media flows, nothing is delivered, and
   * the page went on saying "Dinliyor" because the only thing the transport reported was
   * `failed`. The connection can recover on its own, so this is a WARNING and not a
   * teardown: the controller gives it a short grace and re-attaches if it does not clear.
   */
  | { type: "impaired"; at: number; reason: string }
  /** The impaired link came back by itself; whatever the warning started is called off. */
  | { type: "recovered"; at: number }
  | { type: "disconnected"; at: number; reason: string };

export type TransportEventType = TransportEvent["type"];

export type ConnectOptions = {
  /** Microphone stream to attach before the offer (WebRTC). */
  microphone?: MediaStream;
  /** Monotonic clock; defaults to performance.now(). */
  now?: () => number;
};

export type Unsubscribe = () => void;

/**
 * Outbound audio counters as the transport's own stack reports them
 * (WebRTC: `RTCRtpSender.getStats()` outbound-rtp). ADR-0047 §1 measures the
 * real first uplink packet after a speech onset from the first INCREASE of
 * `packetsSent`, never from a provider event.
 */
export type OutboundAudioStats = {
  packetsSent: number;
  bytesSent: number;
  /** The stats object's own timestamp (ms; clock as the platform defines it). */
  timestamp: number;
};

/**
 * The interface every media transport implements. The controller depends on
 * nothing else; the WebRTC implementation and the deterministic fake are
 * interchangeable.
 */
export interface RealtimeTransport {
  readonly kind: string;
  connect(
    descriptor: TransportDescriptor,
    credential: SessionCredential,
    options?: ConnectOptions,
  ): Promise<void>;
  /** Push audio toward the provider (a track swap on WebRTC, frames elsewhere). */
  sendAudio(input: AudioInput): void;
  /** Optional: the uplink's outbound audio counters right now; null when there is no sender/stats. */
  outboundAudioStats?(): Promise<OutboundAudioStats | null>;
  onAudio(sink: (output: AudioOutput) => void): Unsubscribe;
  onEvent(sink: (event: TransportEvent) => void): Unsubscribe;
  /** Cancel the provider's in-flight response (barge-in step 2). */
  cancelResponse(): void;
  /** Hand a tool result back for `callId` and let the provider continue. */
  submitToolResult(callId: string, result: unknown): void;
  /**
   * A long-running tool finished after its `running` result was already
   * submitted: give the provider the final outcome so it can resume speech.
   */
  notifyToolCompleted(callId: string, name: string, result: unknown): void;
  /** Ask the provider to speak a short phrase now (sideband `say`). */
  say(text: string): void;
  close(reason?: string): void;
}

/** A pure mapping between a provider's JSON events and TransportEvents. */
export interface Dialect {
  readonly name: string;
  /** Provider JSON → normalized event(s). Unknown events yield []. */
  parseServerEvent(message: unknown, at: number): TransportEvent[];
  cancelResponse(): unknown[];
  submitToolResult(callId: string, result: unknown): unknown[];
  notifyToolCompleted(callId: string, name: string, result: unknown): unknown[];
  say(text: string): unknown[];
}
