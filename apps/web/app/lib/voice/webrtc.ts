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
  type OutboundAudioStats,
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
  /** req 218: the peer reported `disconnected` and has not come back yet. */
  private impaired = false;
  /** Settles the in-flight open promise exactly once; null when none is armed. */
  private settleOpen: ((error?: Error) => void) | null = null;
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
        return;
      }
      // B20 req 218: `disconnected` is the warning that comes BEFORE `failed`, and until
      // now it was dropped on the floor. Media has stopped arriving; the browser will
      // either recover the candidate pair or give up, and how long it takes to decide is
      // its own business - seconds of silence with the page still claiming to listen.
      if (pc.connectionState === "disconnected") {
        this.impaired = true;
        this.emit({ type: "impaired", at: this.now(), reason: "peer_disconnected" });
        return;
      }
      if (pc.connectionState === "connected" && this.impaired) {
        this.impaired = false;
        this.emit({ type: "recovered", at: this.now() });
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
    // The open promise is armed HERE because `onopen` can fire before the SDP exchange
    // returns, and a handler installed afterwards would miss it. What that used to cost is
    // the reason for everything below: the promise was created with a fifteen-second timer
    // and only awaited on the LAST line, so every other way out of this method - an SDP
    // exchange that fails, a `close()` while the handshake is in flight - abandoned it with
    // the timer still running. Fifteen seconds later the timer rejected a promise nobody was
    // holding, and an unhandled rejection became the Next runtime overlay on whatever page
    // the owner had reached by then. On 2026-09-09 that page was /core, which had not asked
    // for a connection at all.
    //
    // So the settle path is owned by the instance and is idempotent: whoever gets there
    // first - the channel, an error, the timeout, or close() - clears the timer and settles
    // the promise once. `connect()` cannot return, in either direction, leaving a timer.
    let settle!: (error?: Error) => void;
    const opened = new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(
        () => settle(new Error("data channel did not open in time")),
        this.options.openTimeoutMs ?? 15_000,
      );
      let done = false;
      settle = (error?: Error) => {
        if (done) return;
        done = true;
        clearTimeout(timeout);
        this.settleOpen = null;
        if (error) reject(error);
        else resolve();
      };
      channel.onopen = () => settle();
      channel.onerror = () => settle(new Error("data channel error"));
      // If it is somehow already open, no `onopen` is ever coming and the handler would wait
      // for an event that has been and gone. Assigning the handler before the SDP exchange
      // makes this unreachable today; asking the channel what it IS costs one comparison and
      // does not depend on that ordering staying true.
      if (channel.readyState === "open") settle();
    });
    // A rejection this promise can reach is ALWAYS observed, whether or not the code below
    // ever got as far as awaiting it. One no-op handler costs nothing and is the difference
    // between a transport error and a runtime overlay on a page that asked for nothing; the
    // real `await` still sees the rejection, because attaching a handler does not consume it.
    void opened.catch(() => undefined);
    // close() reaches it through here, so a session torn down mid-handshake ends its caller's
    // wait immediately instead of leaving it to the timeout.
    this.settleOpen = settle;

    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const answer = await this.exchangeSdp(descriptor, credential, offer.sdp ?? "");
      await pc.setRemoteDescription({ type: "answer", sdp: answer });
      await opened;
    } catch (error) {
      // Cancel, do not reject. The caller learns what happened from the `throw` below; if the
      // handshake failed before `await opened` was reached there is nobody on that promise,
      // and rejecting it would invent exactly the unobserved rejection this is about. All
      // that is wanted here is that no timer outlives the call.
      settle();
      throw error;
    }
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
    // travels as a bearer to the exchange endpoint the server named. A credential
    // without one (ADR-0173's text transport) can never open a media leg: say so
    // rather than send "Bearer undefined".
    if (!credential.secret) throw new Error(`provider ${credential.provider} minted no media credential`);
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
      // The STATUS alone is not actionable. A 429 from a realtime provider is either
      // "slow down" or "you have no quota left", and those ask opposite things of the owner:
      // wait, or buy credits. The provider says which in the body, so a bounded excerpt of it
      // travels with the status - the owner's screen read "SDP exchange failed: HTTP 429" for
      // an entire day without ever saying which one it was.
      //
      // Bounded and inert: the body of an error response from an SDP endpoint is the
      // provider's own diagnostic JSON. Nothing this client holds is echoed back into it, and
      // the credential travels in a header, never in a body the provider returns.
      let detail = "";
      try {
        detail = (await response.text()).replace(/\s+/g, " ").trim().slice(0, 300);
      } catch {
        /* a body we cannot read is not worth failing differently for */
      }
      throw new Error(
        `SDP exchange failed: HTTP ${response.status}${detail ? ` - ${detail}` : ""}`,
      );
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

  /**
   * The uplink's outbound-rtp counters from the sender's own stats report
   * (ADR-0047 §1). Null before a microphone track is attached, when the
   * platform has no `getStats`, or when no outbound-rtp entry exists yet.
   */
  async outboundAudioStats(): Promise<OutboundAudioStats | null> {
    const sender = this.sender;
    if (!sender || typeof sender.getStats !== "function") return null;
    let report: RTCStatsReport;
    try {
      report = await sender.getStats();
    } catch {
      return null;
    }
    let found: OutboundAudioStats | null = null;
    report.forEach((entry: unknown) => {
      if (found || !entry || typeof entry !== "object") return;
      const stat = entry as Record<string, unknown>;
      if (stat.type !== "outbound-rtp") return;
      if (stat.kind !== undefined && stat.kind !== "audio") return;
      const packets = typeof stat.packetsSent === "number" ? stat.packetsSent : null;
      if (packets === null) return;
      found = {
        packetsSent: packets,
        bytesSent: typeof stat.bytesSent === "number" ? stat.bytesSent : 0,
        timestamp: typeof stat.timestamp === "number" ? stat.timestamp : 0,
      };
    });
    return found;
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
    // End any handshake still waiting, NOW. Without this the caller's promise had only the
    // fifteen-second timeout left, and nobody was holding it by then.
    this.settleOpen?.(new Error("transport closed before the data channel opened"));
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
