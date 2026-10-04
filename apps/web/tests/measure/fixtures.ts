/**
 * Fixtures and fakes for the measurement page. The sentences are made up on purpose: the
 * page shows what GET returned, so a fixture sentence that exists nowhere else proves it.
 */

import { vi } from "vitest";

import type { Measurement, Place, RecordingItem } from "../../app/lib/voice/measure/api";
import type {
  Clock,
  MeasureMicrophone,
  PcmCapture,
  RecognizerLike,
} from "../../app/lib/voice/measure/recorder";
import type { AppliedInputSettings } from "../../app/lib/voice/ports";

export const MADE_UP = "Mor zürafa çarşamba günü balkonda keman çaldı.";

export function sentences(count = 20): Measurement["sentences"] {
  return Array.from({ length: count }, (_, i) => ({
    index: i + 1,
    text: i === 0 ? MADE_UP : `Uydurma deneme cümlesi numara ${i + 1}.`,
  }));
}

export function item(place: Place, index: number, over: Partial<RecordingItem> = {}): RecordingItem {
  const nn = String(index).padStart(2, "0");
  return {
    place,
    index,
    file: `${place}-${nn}.wav`,
    reference: `Uydurma deneme cümlesi numara ${index}.`,
    recorded_at: "2026-10-03T10:00:00Z",
    expires_at: "2026-11-02T10:00:00Z",
    audio_ms: 2400,
    bytes: 76_844,
    sha256: "a".repeat(64),
    browser_transcript: "uydurma deneme",
    browser_engine: "bilinmiyor",
    capture: { echoCancellation: true },
    ...over,
  };
}

export function measurement(over: Partial<Measurement> = {}): Measurement {
  return {
    sentences: sentences(),
    places: ["ev", "ofis"],
    retention_days: 30,
    max_seconds: 30,
    recordings: [],
    ...over,
  };
}

/** Three of twenty done for 'ev' (1..3), one for 'ofis' (1). */
export function threeDone(over: Partial<Measurement> = {}): Measurement {
  return measurement({
    recordings: [
      item("ev", 1),
      item("ev", 2, { browser_transcript: null, browser_engine: null }),
      item("ev", 3, { browser_transcript: "" }),
      item("ofis", 1),
    ],
    ...over,
  });
}

// ------------------------------------------------------------------ fakes

export type FakeTrack = MediaStreamTrack & { streamId: string; clonedFrom: FakeTrack | null; stop: ReturnType<typeof vi.fn> };

export function fakeStream(id = "stream-1"): MediaStream & { track: FakeTrack } {
  const make = (clonedFrom: FakeTrack | null): FakeTrack => {
    const track = {
      kind: "audio",
      id: `${id}-track${clonedFrom ? "-clone" : ""}`,
      streamId: id,
      clonedFrom,
      stop: vi.fn(),
      clone: () => make(track as unknown as FakeTrack),
    };
    return track as unknown as FakeTrack;
  };
  const track = make(null);
  return {
    id,
    track,
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream & { track: FakeTrack };
}

export const APPLIED: AppliedInputSettings = {
  label: "Masa mikrofonu (USB)",
  deviceId: "dev-1",
  groupId: null,
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: false,
  voiceIsolation: null,
  suppressLocalAudioPlayback: null,
  channelCount: 1,
  sampleRate: 48_000,
  inputLatencyMs: null,
  notHonoured: [],
  requested: { echoCancellation: true, noiseSuppression: true, autoGainControl: false, channelCount: 1 },
  settings: {},
  capabilities: null,
  supportedConstraints: {},
};

export class FakeMicrophone implements MeasureMicrophone {
  applied: AppliedInputSettings | null = null;
  closes = 0;
  opens: Array<{ deviceId?: string; constraints?: unknown }> = [];
  readonly stream = fakeStream();

  async open(deviceId?: string, constraints?: unknown): Promise<MediaStream> {
    this.opens.push({ deviceId, constraints });
    this.applied = APPLIED;
    return this.stream;
  }

  close(): void {
    this.closes += 1;
    this.applied = null;
  }
}

/** A capture that delivers `seconds` of a quiet tone at `rate`. */
export function fakeCapture(rate = 48_000, seconds = 1) {
  const streams: MediaStream[] = [];
  const factory = (stream: MediaStream): PcmCapture => {
    streams.push(stream);
    return {
      sampleRate: rate,
      stop: async () => {
        const out = new Float32Array(Math.round(rate * seconds));
        for (let i = 0; i < out.length; i += 1) out[i] = 0.2 * Math.sin((2 * Math.PI * 220 * i) / rate);
        return out;
      },
    };
  };
  return { factory, streams };
}

export type RecognizerScript = "result" | "throw" | "error" | "silent-end" | "never-ends";

export class FakeRecognizer implements RecognizerLike {
  lang = "";
  continuous = false;
  interimResults = true;
  onresult: RecognizerLike["onresult"] = null;
  onend: RecognizerLike["onend"] = null;
  onerror: RecognizerLike["onerror"] = null;
  starts: unknown[][] = [];

  constructor(
    private readonly script: RecognizerScript,
    private readonly transcript = "ofisü bilgisayarında",
  ) {}

  start(...args: unknown[]): void {
    this.starts.push(args);
    if (this.script === "throw") throw new DOMException("track not accepted", "InvalidStateError");
    if (this.script === "error") queueMicrotask(() => this.onerror?.({ error: "audio-capture" }));
  }

  stop(): void {
    if (this.script === "result") {
      this.onresult?.({
        resultIndex: 0,
        results: [{ isFinal: true, 0: { transcript: this.transcript } }],
      });
      this.onend?.();
    } else if (this.script === "silent-end" || this.script === "error") {
      this.onend?.();
    }
  }

  abort(): void {}
}

/** A clock the test moves by hand. */
export class FakeClock implements Clock {
  now = 0;
  private timers: Array<{ due: number; fn: () => void; live: boolean }> = [];

  setTimeout(fn: () => void, ms: number): () => void {
    const timer = { due: this.now + ms, fn, live: true };
    this.timers.push(timer);
    return () => {
      timer.live = false;
    };
  }

  advance(ms: number): void {
    this.now += ms;
    for (const timer of this.timers) {
      if (timer.live && timer.due <= this.now) {
        timer.live = false;
        timer.fn();
      }
    }
  }

  pending(): number {
    return this.timers.filter((timer) => timer.live).length;
  }
}

/** Let every queued promise continuation run. */
export async function settle(rounds = 20): Promise<void> {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
}
