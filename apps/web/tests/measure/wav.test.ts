/**
 * The measurement page's WAV writer: what `app.voice.measurement` accepts and
 * `app.voice.stt_compare` reads - PCM 16-bit, mono, 16 000 Hz, a 44-byte header.
 */

import { describe, expect, it } from "vitest";

import {
  TARGET_RATE,
  WAV_HEADER_BYTES,
  bytesToBase64,
  encodeWav,
  resampleTo16k,
} from "../../app/lib/voice/measure/wav";

function sine(rate: number, seconds: number, hz = 440): Float32Array {
  const out = new Float32Array(Math.round(rate * seconds));
  for (let i = 0; i < out.length; i += 1) out[i] = 0.5 * Math.sin((2 * Math.PI * hz * i) / rate);
  return out;
}

function ascii(bytes: Uint8Array, at: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(at, at + length));
}

describe("resampling", () => {
  it("turns one second at 48 000 Hz into 16 000 samples that still follow the sine", () => {
    const out = resampleTo16k(sine(48_000, 1), 48_000);
    expect(TARGET_RATE).toBe(16_000);
    expect(out.length).toBe(16_000);
    const expected = sine(16_000, 1);
    let worst = 0;
    for (let i = 10; i < out.length - 10; i += 1) worst = Math.max(worst, Math.abs(out[i] - expected[i]));
    expect(worst).toBeLessThan(0.05);
  });

  it("passes a 16 000 Hz input through sample for sample", () => {
    const input = sine(16_000, 0.25, 300);
    const out = resampleTo16k(input, 16_000);
    expect(out.length).toBe(input.length);
    expect(Array.from(out)).toEqual(Array.from(input));
  });
});

describe("the WAV header and body", () => {
  it("reads RIFF / WAVE / fmt, PCM, one channel, 16 000 Hz, 16 bit, byte rate 32 000, data = samples x 2", () => {
    const samples = resampleTo16k(sine(48_000, 1), 48_000);
    const bytes = encodeWav(samples);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    expect(WAV_HEADER_BYTES).toBe(44);
    expect(bytes.length).toBe(44 + samples.length * 2);
    expect(ascii(bytes, 0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(bytes.length - 8);
    expect(ascii(bytes, 8, 4)).toBe("WAVE");
    expect(ascii(bytes, 12, 4)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint32(28, true)).toBe(32_000);
    expect(view.getUint16(32, true)).toBe(2);
    expect(view.getUint16(34, true)).toBe(16);
    expect(ascii(bytes, 36, 4)).toBe("data");
    expect(view.getUint32(40, true)).toBe(samples.length * 2);
  });

  it("clamps out-of-range samples instead of wrapping them", () => {
    const bytes = encodeWav(Float32Array.from([1.5, -1.5, 1, -1, 0]));
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    expect(view.getInt16(44, true)).toBe(32767);
    expect(view.getInt16(46, true)).toBe(-32768);
    expect(view.getInt16(48, true)).toBe(32767);
    expect(view.getInt16(50, true)).toBe(-32768);
    expect(view.getInt16(52, true)).toBe(0);
  });
});

describe("base64", () => {
  it("decodes back to the same bytes", () => {
    const bytes = encodeWav(resampleTo16k(sine(44_100, 0.3), 44_100));
    const decoded = Uint8Array.from(Buffer.from(bytesToBase64(bytes), "base64"));
    expect(decoded.length).toBe(bytes.length);
    expect(Buffer.compare(Buffer.from(decoded), Buffer.from(bytes))).toBe(0);
  });
});
