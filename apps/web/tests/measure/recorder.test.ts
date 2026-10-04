/**
 * One take with every browser object faked: microphone, PCM capture, recogniser, clock.
 */

import { describe, expect, it, vi } from "vitest";

import type { PutBody, PutResult } from "../../app/lib/voice/measure/api";
import {
  ENGINE_UNKNOWN,
  RECOGNIZER_GUARD_MS,
  startTake,
  type RecorderDeps,
  type TakeOptions,
} from "../../app/lib/voice/measure/recorder";
import {
  FakeClock,
  FakeMicrophone,
  FakeRecognizer,
  type RecognizerScript,
  fakeCapture,
  item,
  settle,
} from "./fixtures";

type Upload = { place: string; index: number; body: PutBody };

function rig(over: Partial<RecorderDeps> = {}, answer: PutResult = { ok: true, item: item("ev", 4) }) {
  const microphone = new FakeMicrophone();
  const capture = fakeCapture();
  const clock = new FakeClock();
  const uploads: Upload[] = [];
  const deps: RecorderDeps = {
    microphone,
    capture: capture.factory,
    recognizer: null,
    clock,
    upload: async (place, index, body) => {
      uploads.push({ place, index, body });
      return answer;
    },
    ...over,
  };
  return { deps, microphone, capture, clock, uploads };
}

const OPTIONS: TakeOptions = { place: "ev", index: 4, maxSeconds: 30 };

function wavHeader(body: PutBody) {
  const bytes = Buffer.from(body.audio_wav_base64, "base64");
  return {
    riff: bytes.subarray(0, 4).toString("ascii"),
    channels: bytes.readUInt16LE(22),
    rate: bytes.readUInt32LE(24),
    bits: bytes.readUInt16LE(34),
    dataBytes: bytes.readUInt32LE(40),
  };
}

async function take(r: ReturnType<typeof rig>, options = OPTIONS) {
  const running = startTake(r.deps, options);
  await settle();
  running.stop();
  return running.done;
}

describe("a take", () => {
  it("uploads a 16 000 Hz mono 16-bit WAV with the microphone's applied settings as capture", async () => {
    const r = rig();
    const outcome = await take(r);
    expect(outcome.ok).toBe(true);
    expect(r.uploads).toHaveLength(1);
    const [upload] = r.uploads;
    expect(upload.place).toBe("ev");
    expect(upload.index).toBe(4);
    expect(wavHeader(upload.body)).toEqual({ riff: "RIFF", channels: 1, rate: 16_000, bits: 16, dataBytes: 32_000 });
    expect(upload.body.capture).toEqual({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: false,
      sampleRate: 48_000,
      label: "Masa mikrofonu (USB)",
    });
    expect(Object.keys(upload.body).toSorted()).toEqual(["audio_wav_base64", "browser_engine", "browser_transcript", "capture"]);
  });

  it("passes the device and constraints it was given to the microphone", async () => {
    const r = rig();
    await take(r, { ...OPTIONS, deviceId: "dev-9", constraints: { noiseSuppression: false } });
    expect(r.microphone.opens).toEqual([{ deviceId: "dev-9", constraints: { noiseSuppression: false } }]);
  });

  it("stops by itself at max_seconds", async () => {
    const r = rig();
    const running = startTake(r.deps, { ...OPTIONS, maxSeconds: 5 });
    await settle();
    expect(r.uploads).toHaveLength(0);
    r.clock.advance(4_999);
    await settle();
    expect(r.uploads).toHaveLength(0);
    r.clock.advance(1);
    const outcome = await running.done;
    expect(outcome.ok).toBe(true);
    expect(r.uploads).toHaveLength(1);
  });

  it("never sends more than max_seconds of audio", async () => {
    const capture = fakeCapture(48_000, 3);
    const r = rig({ capture: capture.factory });
    await take(r, { ...OPTIONS, maxSeconds: 2 });
    expect(wavHeader(r.uploads[0].body).dataBytes).toBe(2 * 16_000 * 2);
  });
});

describe("the microphone is closed", () => {
  it("after a successful take", async () => {
    const r = rig();
    await take(r);
    expect(r.microphone.closes).toBe(1);
  });

  it("after a refused upload, and the refusal keeps the server's sentence", async () => {
    const r = rig({}, { ok: false, code: "wav_empty", message: "Kayıt boş; cümleyi yeniden oku." });
    const outcome = await take(r);
    expect(outcome).toMatchObject({ ok: false, code: "wav_empty", message: "Kayıt boş; cümleyi yeniden oku." });
    expect(r.microphone.closes).toBe(1);
  });

  it("after an upload that throws", async () => {
    const r = rig({
      upload: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const outcome = await take(r);
    expect(outcome.ok).toBe(false);
    expect(r.microphone.closes).toBe(1);
  });

  it("after a capture that throws", async () => {
    const r = rig({
      capture: () => {
        throw new Error("AudioContext refused");
      },
    });
    const outcome = await take(r);
    expect(outcome.ok).toBe(false);
    expect(r.microphone.closes).toBe(1);
  });
});

describe("Chrome's recogniser on the same audio", () => {
  function withRecognizer(script: RecognizerScript) {
    const recognizer = new FakeRecognizer(script);
    const factory = vi.fn(() => recognizer);
    const r = rig({ recognizer: factory });
    return { r, recognizer, factory };
  }

  it("a final transcript travels in the PUT with browser_engine 'bilinmiyor'", async () => {
    const { r, recognizer } = withRecognizer("result");
    const outcome = await take(r);
    expect(ENGINE_UNKNOWN).toBe("bilinmiyor");
    expect(r.uploads[0].body.browser_transcript).toBe("ofisü bilgisayarında");
    expect(r.uploads[0].body.browser_engine).toBe("bilinmiyor");
    expect(recognizer.lang).toBe("tr-TR");
    expect(outcome).toMatchObject({ ok: true, chromeRan: true });
  });

  it("its start received a clone of the SAME stream's audio track, and never ran without one", async () => {
    const { r, recognizer } = withRecognizer("result");
    await take(r);
    expect(recognizer.starts).toHaveLength(1);
    const [track] = recognizer.starts[0] as [{ streamId: string; clonedFrom: unknown }];
    expect(track).toBeDefined();
    expect(track.clonedFrom).toBe(r.microphone.stream.track);
    expect(r.capture.streams).toEqual([r.microphone.stream]);
    expect(track.streamId).toBe(r.capture.streams[0].id);
    for (const args of recognizer.starts) expect(args[0]).toBeTruthy();
  });

  it("with NO recogniser: browser_transcript null and browser_engine null", async () => {
    const r = rig({ recognizer: null });
    const outcome = await take(r);
    expect([r.uploads[0].body.browser_transcript, r.uploads[0].body.browser_engine]).toEqual([null, null]);
    expect(outcome).toMatchObject({ ok: true, chromeRan: false });
  });

  it("with a start that throws: null and null, and the take is still uploaded", async () => {
    const { r } = withRecognizer("throw");
    await take(r);
    expect(r.uploads).toHaveLength(1);
    expect([r.uploads[0].body.browser_transcript, r.uploads[0].body.browser_engine]).toEqual([null, null]);
  });

  it("with an error event: null and null", async () => {
    const { r } = withRecognizer("error");
    await take(r);
    expect([r.uploads[0].body.browser_transcript, r.uploads[0].body.browser_engine]).toEqual([null, null]);
  });

  it("ending without a result: '' (it ran and wrote nothing)", async () => {
    const { r } = withRecognizer("silent-end");
    await take(r);
    expect(r.uploads[0].body.browser_transcript).toBe("");
    expect(r.uploads[0].body.browser_engine).toBe("bilinmiyor");
  });

  it("no end and no result before the guard: null, and the take is still uploaded", async () => {
    const { r } = withRecognizer("never-ends");
    const running = startTake(r.deps, OPTIONS);
    await settle();
    running.stop();
    await settle();
    expect(r.uploads).toHaveLength(0);
    r.clock.advance(RECOGNIZER_GUARD_MS);
    await running.done;
    expect(r.uploads[0].body.browser_transcript).toBeNull();
    expect(r.uploads[0].body.browser_engine).toBeNull();
    expect(r.microphone.closes).toBe(1);
  });

  it("the cloned track is stopped after the take", async () => {
    const { r, recognizer } = withRecognizer("result");
    await take(r);
    const [track] = recognizer.starts[0] as [{ stop: ReturnType<typeof vi.fn> }];
    expect(track.stop).toHaveBeenCalled();
  });
});
