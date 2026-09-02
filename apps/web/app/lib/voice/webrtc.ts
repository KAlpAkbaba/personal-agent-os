/**
 * WebRTC media leg driven entirely by the transport descriptor Cloud Core
 * returned (spec §1 "direct media path"): the SDP offer is POSTed to the
 * descriptor's exchange endpoint with the per-session credential as bearer,
 * the answer becomes the remote description, inbound audio arrives on a
 * track, and the provider's JSON events flow on the named data channel,
 * interpreted by the dialect the descriptor names. No vendor URL, channel
 * name or model appears in this file.
 */

import { dialectFor } from "./dialects";
import type { SessionCredential } from "./contract";
import {
  type AudioInput,
  type AudioOutput,
  type ConnectOptions,
  type Dialect,
  type RealtimeTransport,
  type TransportDescriptor,
  type TransportEvent,
  type Unsubscribe,
  TransportConfigError,
} from "./transport";

export type WebRtcTransportOptions = {
  /** Injected for tests; defaults to the browser's RTCPeerConnection. */
  peerConnectionFactory?: (config: RTCConfiguration) => RTCPeerConnection;
  fetchImpl?: typeof fetch;
  rtcConfiguration?: RTCConfiguration;
  /** Milliseconds to wait for the data channel to open. */
  openTimeoutMs?: number;
};

export class WebRtcTransport implements RealtimeTransport {
  readonly kind = "webrtc";
  private pc: RTCPeerConnection | null = null;
  private channel: RTCDataChannel | null = null;
  private sender: RTCRtpSender | null = null;
  private dialect: Dialect | null = null;
  private eventSinks = new Set<(event: TransportEvent) => void>();
  private audioSinks = new Set<(output: AudioOutput) => void>();
  private now: () => number = () => performance.now();
  private closed = false;
  private outbox: unknown[] = [];

  constructor(private readonly options: WebRtcTransportOptions = {}) {}

  async connect(
    descriptor: TransportDescriptor,
    credential: SessionCredential,
    options: ConnectOptions = {},
  ): Promise<void> {
    if (descriptor.kind !== "webrtc") {
      throw new TransportConfigError(`WebRTC transport cannot open a ${descriptor.kind} leg`);
    }
    if (!descriptor.sdp_exchange_url) {
      throw new TransportConfigError(
        "transport descriptor has no sdp_exchange_url; the provider adapter must return one",
      );
    }
    if (!descriptor.data_channel) {
      throw new TransportConfigError(
        "transport descriptor has no data_channel name; the provider adapter must return one",
      );
    }
    this.dialect = dialectFor(descriptor.dialect);
    if (options.now) this.now = options.now;
    this.closed = false;

    const factory =
      this.options.peerConnectionFactory ?? ((config: RTCConfiguration) => new RTCPeerConnection(config));
    const pc = factory(this.options.rtcConfiguration ?? {});
    this.pc = pc;

    pc.ontrack = (event) => {
      const stream = event.streams[0] ?? new MediaStream([event.track]);
      for (const sink of this.audioSinks) sink({ kind: "stream", stream });
    };
    pc.onconnectionstatechange = () => {
      if (this.closed) return;
      if (pc.connectionState === "failed" || pc.connectionState === "closed") {
        this.emit({ type: "disconnected", at: this.now(), reason: `peer_${pc.connectionState}` });
      }
    };

    const mic = options.microphone?.getAudioTracks()[0];
    if (mic) {
      this.sender = pc.addTrack(mic, options.microphone as MediaStream);
    } else {
      // Receive-only until a microphone is attached via sendAudio().
      pc.addTransceiver("audio", { direction: "recvonly" });
    }

    const channel = pc.createDataChannel(descriptor.data_channel);
    this.channel = channel;
    channel.onmessage = (message) => this.onChannelMessage(message.data);
    channel.onclose = () => {
      if (!this.closed) this.emit({ type: "disconnected", at: this.now(), reason: "data_channel_closed" });
    };
    const opened = new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error("data channel did not open in time")),
        this.options.openTimeoutMs ?? 15_000,
      );
      channel.onopen = () => {
        clearTimeout(timeout);
        resolve();
      };
      channel.onerror = () => {
        clearTimeout(timeout);
        reject(new Error("data channel error"));
      };
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const answer = await this.exchangeSdp(descriptor, credential, offer.sdp ?? "");
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
    await opened;
    for (const message of this.outbox.splice(0)) this.send(message);
    this.emit({ type: "connected", at: this.now() });
  }

  private async exchangeSdp(
    descriptor: TransportDescriptor,
    credential: SessionCredential,
    offerSdp: string,
  ): Promise<string> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const headers = new Headers(descriptor.headers ?? {});
    // The per-session credential is the only secret the browser holds; it
    // travels as a bearer to the exchange endpoint the server named.
    headers.set("Authorization", `Bearer ${credential.secret}`);
    let body: BodyInit;
    if (descriptor.sdp_content_type === "multipart/form-data") {
      const form = new FormData();
      form.set("sdp", offerSdp);
      if (descriptor.session_config) form.set("session", JSON.stringify(descriptor.session_config));
      body = form;
    } else {
      headers.set("Content-Type", "application/sdp");
      body = offerSdp;
    }
    const response = await fetchImpl(descriptor.sdp_exchange_url as string, {
      method: "POST",
      headers,
      body,
    });
    if (!response.ok) {
      throw new Error(`SDP exchange failed: HTTP ${response.status}`);
    }
    return await response.text();
  }

  private onChannelMessage(data: unknown): void {
    if (!this.dialect) return;
    let parsed: unknown;
    try {
      parsed = typeof data === "string" ? JSON.parse(data) : data;
    } catch {
      return;
    }
    for (const event of this.dialect.parseServerEvent(parsed, this.now())) this.emit(event);
  }

  private emit(event: TransportEvent): void {
    for (const sink of this.eventSinks) sink(event);
  }

  private send(message: unknown): void {
    if (this.channel && this.channel.readyState === "open") {
      this.channel.send(JSON.stringify(message));
    } else {
      this.outbox.push(message);
    }
  }

  sendAudio(input: AudioInput): void {
    if (input.kind !== "track") {
      throw new TransportConfigError("WebRTC carries the microphone as a track, not PCM frames");
    }
    if (!this.pc) return;
    if (this.sender) {
      void this.sender.replaceTrack(input.track);
    } else {
      this.sender = this.pc.addTrack(input.track);
    }
  }

  onAudio(sink: (output: AudioOutput) => void): Unsubscribe {
    this.audioSinks.add(sink);
    return () => this.audioSinks.delete(sink);
  }

  onEvent(sink: (event: TransportEvent) => void): Unsubscribe {
    this.eventSinks.add(sink);
    return () => this.eventSinks.delete(sink);
  }

  cancelResponse(): void {
    for (const message of this.dialect?.cancelResponse() ?? []) this.send(message);
  }

  submitToolResult(callId: string, result: unknown): void {
    for (const message of this.dialect?.submitToolResult(callId, result) ?? []) this.send(message);
  }

  notifyToolCompleted(callId: string, name: string, result: unknown): void {
    for (const message of this.dialect?.notifyToolCompleted(callId, name, result) ?? []) {
      this.send(message);
    }
  }

  say(text: string): void {
    for (const message of this.dialect?.say(text) ?? []) this.send(message);
  }

  close(): void {
    this.closed = true;
    this.outbox = [];
    try {
      this.channel?.close();
    } catch {
      /* already closed */
    }
    try {
      this.pc?.close();
    } catch {
      /* already closed */
    }
    this.channel = null;
    this.pc = null;
    this.sender = null;
  }
}
