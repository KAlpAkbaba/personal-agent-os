import { afterEach, describe, expect, it, vi } from "vitest";

import type { SessionCredential } from "../../app/lib/voice/contract";
import { dialectFor, knownDialects } from "../../app/lib/voice/dialects";
import { OpenAIRealtimeDialect } from "../../app/lib/voice/dialects/openaiRealtime";
import {
  TransportConfigError,
  resolveTransportDescriptor,
} from "../../app/lib/voice/transport";
import { WebRtcTransport } from "../../app/lib/voice/webrtc";

const credential: SessionCredential = {
  provider: "some-provider",
  secret: "ephemeral-secret-1",
  expires_at: "2026-09-02T00:10:00Z",
  transport: "webrtc",
  session_ref: "ref-1",
};

describe("transport descriptor", () => {
  it("is built from the server payload; the transport string is authoritative", () => {
    expect(resolveTransportDescriptor({ transport: "simulated", credential })).toEqual({
      kind: "simulated",
    });
    const descriptor = resolveTransportDescriptor({
      transport: "webrtc",
      credential,
      transport_descriptor: {
        kind: "something-else",
        sdp_exchange_url: "https://provider.example/calls",
        data_channel: "events",
        dialect: "openai-realtime",
      },
    });
    expect(descriptor.kind).toBe("webrtc");
    expect(descriptor.sdp_exchange_url).toBe("https://provider.example/calls");
    expect(descriptor.data_channel).toBe("events");
  });

  it("names dialects explicitly; there is no default vendor", () => {
    expect(knownDialects()).toEqual(["openai-realtime"]);
    expect(() => dialectFor(undefined)).toThrow(TransportConfigError);
    expect(() => dialectFor("mystery")).toThrow(/unknown event dialect/);
    expect(dialectFor("openai-realtime").name).toBe("openai-realtime");
  });
});

describe("openai-realtime dialect", () => {
  const dialect = new OpenAIRealtimeDialect();

  it("maps VAD, transcript and response events (GA and beta names)", () => {
    expect(dialect.parseServerEvent({ type: "input_audio_buffer.speech_started" }, 5)).toEqual([
      { type: "speech_started", at: 5 },
    ]);
    expect(dialect.parseServerEvent({ type: "input_audio_buffer.speech_stopped" }, 6)).toEqual([
      { type: "speech_stopped", at: 6 },
    ]);
    expect(
      dialect.parseServerEvent(
        { type: "conversation.item.input_audio_transcription.completed", transcript: "dur" },
        7,
      ),
    ).toEqual([{ type: "owner_transcript", at: 7, text: "dur", final: true }]);
    expect(dialect.parseServerEvent({ type: "response.created", response: { id: "r1" } }, 8)).toEqual([
      { type: "response_started", at: 8, responseId: "r1" },
    ]);
    for (const name of ["response.output_audio_transcript.delta", "response.audio_transcript.delta"]) {
      expect(dialect.parseServerEvent({ type: name, delta: "Ba" }, 9)).toEqual([
        { type: "response_text", at: 9, text: "Ba", final: false },
      ]);
    }
    expect(dialect.parseServerEvent({ type: "output_audio_buffer.started", response_id: "r1" }, 10)).toEqual([
      { type: "audio_started", at: 10, responseId: "r1" },
    ]);
    expect(dialect.parseServerEvent({ type: "unknown.thing" }, 11)).toEqual([]);
    expect(dialect.parseServerEvent("garbage", 12)).toEqual([]);
  });

  it("extracts tool calls from arguments.done and from response.done", () => {
    expect(
      dialect.parseServerEvent(
        {
          type: "response.function_call_arguments.done",
          call_id: "c1",
          name: "research.start",
          arguments: '{"topic":"yapay zekâ"}',
        },
        1,
      ),
    ).toEqual([
      { type: "tool_call", at: 1, callId: "c1", name: "research.start", arguments: { topic: "yapay zekâ" } },
    ]);
    const done = dialect.parseServerEvent(
      {
        type: "response.done",
        response: {
          id: "r2",
          status: "completed",
          output: [
            { type: "message" },
            { type: "function_call", call_id: "c1", name: "research.start", arguments: "not json" },
          ],
        },
      },
      2,
    );
    expect(done).toEqual([
      { type: "tool_call", at: 2, callId: "c1", name: "research.start", arguments: {} },
      { type: "response_done", at: 2, responseId: "r2" },
    ]);
    expect(
      dialect.parseServerEvent({ type: "response.done", response: { id: "r3", status: "cancelled" } }, 3),
    ).toEqual([{ type: "response_cancelled", at: 3, responseId: "r3" }]);
  });

  it("builds cancel / tool-result / completion / say messages", () => {
    expect(dialect.cancelResponse().map((m) => (m as { type: string }).type)).toEqual([
      "response.cancel",
      "output_audio_buffer.clear",
    ]);
    const submit = dialect.submitToolResult("c1", { now: "x" }) as Array<Record<string, unknown>>;
    expect(submit[0]).toEqual({
      type: "conversation.item.create",
      item: { type: "function_call_output", call_id: "c1", output: '{"now":"x"}' },
    });
    expect(submit[1]).toEqual({ type: "response.create" });
    const completed = dialect.notifyToolCompleted("c1", "research.start", { ok: true }) as Array<
      Record<string, unknown>
    >;
    expect(JSON.stringify(completed[0])).toContain("araç tamamlandı");
    expect(completed[1]).toEqual({ type: "response.create" });
    expect(JSON.stringify(dialect.say("Bakıyorum."))).toContain("Bakıyorum.");
  });
});

// ------------------------------------------------------------ WebRTC leg

class FakeDataChannel {
  readyState: RTCDataChannelState = "connecting";
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly label: string) {}
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = "closed";
  }
  open(): void {
    this.readyState = "open";
    this.onopen?.();
  }
}

class FakePeerConnection {
  channels: FakeDataChannel[] = [];
  tracks: unknown[] = [];
  transceivers: string[] = [];
  local: RTCSessionDescriptionInit | null = null;
  remote: RTCSessionDescriptionInit | null = null;
  connectionState = "new";
  ontrack: ((ev: unknown) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  closed = false;
  createDataChannel(label: string): FakeDataChannel {
    const channel = new FakeDataChannel(label);
    this.channels.push(channel);
    queueMicrotask(() => channel.open());
    return channel;
  }
  addTrack(track: unknown): unknown {
    this.tracks.push(track);
    return { replaceTrack: async () => undefined };
  }
  addTransceiver(kind: string): void {
    this.transceivers.push(kind);
  }
  async createOffer(): Promise<RTCSessionDescriptionInit> {
    return { type: "offer", sdp: "v=0 offer" };
  }
  async setLocalDescription(desc: RTCSessionDescriptionInit): Promise<void> {
    this.local = desc;
  }
  async setRemoteDescription(desc: RTCSessionDescriptionInit): Promise<void> {
    this.remote = desc;
  }
  close(): void {
    this.closed = true;
  }
}

describe("WebRTC transport", () => {
  const descriptor = {
    kind: "webrtc" as const,
    sdp_exchange_url: "https://provider.example/v1/realtime/calls",
    data_channel: "provider-events",
    dialect: "openai-realtime",
  };

  it("refuses a descriptor without an endpoint, channel or dialect", async () => {
    const transport = new WebRtcTransport({
      peerConnectionFactory: () => new FakePeerConnection() as unknown as RTCPeerConnection,
    });
    await expect(transport.connect({ kind: "webrtc" }, credential)).rejects.toThrow(TransportConfigError);
    await expect(
      transport.connect({ ...descriptor, data_channel: undefined }, credential),
    ).rejects.toThrow(/data_channel/);
    await expect(transport.connect({ ...descriptor, dialect: undefined }, credential)).rejects.toThrow(
      /dialect/,
    );
    await expect(transport.connect({ kind: "simulated" }, credential)).rejects.toThrow(/simulated/);
  });

  it("exchanges SDP at the descriptor's endpoint with the session credential only", async () => {
    const pc = new FakePeerConnection();
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const transport = new WebRtcTransport({
      peerConnectionFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: (async (url: string, init: RequestInit) => {
        requests.push({ url, init });
        return new Response("v=0 answer", { status: 200 });
      }) as unknown as typeof fetch,
    });
    const events: string[] = [];
    transport.onEvent((e) => events.push(e.type));
    const mic = { getAudioTracks: () => [{ id: "mic-track" }] } as unknown as MediaStream;
    await transport.connect(descriptor, credential, { microphone: mic, now: () => 42 });

    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe(descriptor.sdp_exchange_url);
    const headers = new Headers(requests[0].init.headers);
    expect(headers.get("authorization")).toBe(`Bearer ${credential.secret}`);
    expect(headers.get("content-type")).toBe("application/sdp");
    expect(requests[0].init.body).toBe("v=0 offer");
    expect(pc.remote).toEqual({ type: "answer", sdp: "v=0 answer" });
    expect(pc.channels.map((c) => c.label)).toEqual(["provider-events"]);
    expect(pc.tracks).toHaveLength(1);
    expect(events).toEqual(["connected"]);

    // Provider events on the channel are interpreted by the dialect...
    pc.channels[0].onmessage?.({ data: JSON.stringify({ type: "input_audio_buffer.speech_started" }) });
    expect(events).toEqual(["connected", "speech_started"]);
    // ...and client commands are serialized through it.
    transport.cancelResponse();
    transport.submitToolResult("c1", { ok: 1 });
    expect(pc.channels[0].sent.map((s) => (JSON.parse(s) as { type: string }).type)).toEqual([
      "response.cancel",
      "output_audio_buffer.clear",
      "conversation.item.create",
      "response.create",
    ]);
    transport.close();
    expect(pc.closed).toBe(true);
  });

  // --------------------------------------------------- the timer that outlived its promise
  //
  // The owner's 2026-09-09 report: /voice was connected and LISTENING, and opening /core threw
  // "data channel did not open in time" into the Next runtime overlay - a page that had not
  // asked for a connection at all. The overlay is what an UNHANDLED rejection looks like.
  //
  // `connect()` arms a 15 s timer and hands its promise to `await opened` at the very END of
  // the method. Every path that leaves before that line - an SDP exchange that fails, a
  // `close()` while the handshake is in flight - abandons the promise while its timer is still
  // running. Fifteen seconds later the timer rejects something nobody is awaiting, on
  // whichever page happens to be mounted by then.
  //
  // So the invariant is stated as one a test can see: when connect() has returned, in EITHER
  // direction, this transport owns no pending timer.

  afterEach(() => {
    vi.useRealTimers();
  });

  it("leaves no timer behind when the SDP exchange fails", async () => {
    vi.useFakeTimers();
    const pc = new FakePeerConnection();
    // The channel never opens - the exchange fails first, which is the real ordering.
    pc.createDataChannel = (label: string) => {
      const channel = new FakeDataChannel(label);
      pc.channels.push(channel);
      return channel;
    };
    const transport = new WebRtcTransport({
      peerConnectionFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: (async () => {
        throw new Error("network down");
      }) as unknown as typeof fetch,
    });

    await expect(transport.connect(descriptor, credential)).rejects.toThrow(/network down/);

    expect(vi.getTimerCount()).toBe(0);
  });

  it("leaves no timer behind when the session is closed mid-handshake", async () => {
    vi.useFakeTimers();
    const pc = new FakePeerConnection();
    pc.createDataChannel = (label: string) => {
      const channel = new FakeDataChannel(label);
      pc.channels.push(channel);
      return channel;
    };
    // A box, not a bare `let`: TypeScript narrows a `let` initialised to null and never sees
    // the assignment that happens inside the fetch closure, so `release?.()` below becomes a
    // call on `never`.
    const gate: { release: (() => void) | null } = { release: null };
    const transport = new WebRtcTransport({
      peerConnectionFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: (async () =>
        new Promise((resolve) => {
          gate.release = () => resolve(new Response("v=0 answer", { status: 200 }));
        })) as unknown as typeof fetch,
    });

    let outcome: string | null = null;
    void transport.connect(descriptor, credential).then(
      () => {
        outcome = "resolved";
      },
      () => {
        outcome = "rejected";
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    transport.close();
    gate.release?.();
    // A SMALL advance on purpose: closing must settle the connect now, not fifteen seconds
    // from now. Before the fix the caller is still waiting on a promise whose only remaining
    // path is the timeout, which is precisely the abandoned promise this is about.
    await vi.advanceTimersByTimeAsync(50);

    expect(outcome).toBe("rejected");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("a channel that never opens still fails the connect, bounded", async () => {
    // The timeout itself is not the defect and is not weakened: a channel that never opens
    // must still end the connect, once, through the promise the caller is holding.
    vi.useFakeTimers();
    const pc = new FakePeerConnection();
    pc.createDataChannel = (label: string) => {
      const channel = new FakeDataChannel(label);
      pc.channels.push(channel);
      return channel;
    };
    const transport = new WebRtcTransport({
      peerConnectionFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: (async () => new Response("v=0 answer", { status: 200 })) as unknown as typeof fetch,
      openTimeoutMs: 1_000,
    });

    const settled = transport.connect(descriptor, credential).then(
      () => "resolved",
      (error: unknown) => (error as Error).message,
    );
    await vi.advanceTimersByTimeAsync(1_200);

    expect(await settled).toMatch(/did not open in time/);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("uses multipart with the server-provided session config when asked", async () => {
    const pc = new FakePeerConnection();
    let body: FormData | null = null;
    const transport = new WebRtcTransport({
      peerConnectionFactory: () => pc as unknown as RTCPeerConnection,
      fetchImpl: (async (_url: string, init: RequestInit) => {
        body = init.body as FormData;
        return new Response("v=0 answer", { status: 200 });
      }) as unknown as typeof fetch,
    });
    await transport.connect(
      { ...descriptor, sdp_content_type: "multipart/form-data", session_config: { type: "realtime" } },
      credential,
    );
    const form = body as unknown as FormData;
    expect(form.get("sdp")).toBe("v=0 offer");
    expect(form.get("session")).toBe('{"type":"realtime"}');
    expect(pc.transceivers).toEqual(["audio"]); // no microphone yet: receive-only
  });
});
