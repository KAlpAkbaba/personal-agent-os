/**
 * Deterministic synthetic signals for the gate / calibration tests. No real
 * audio anywhere: every sample is computed from a seed, so the numbers the
 * tests pin are reproducible on any machine.
 */

import { type FrameFeatures, analyseFrame, dbToAmplitude, rms } from "../../app/lib/voice/dsp";

export const SR = 48_000;
/** The browser detector samples 1024-sample analyser frames every 20 ms. */
export const FRAME = 1024;
export const HOP_MS = 20;
export const HOP = (SR * HOP_MS) / 1000;

export function ms(n: number): number {
  return Math.round((SR * n) / 1000);
}

/** Seeded LCG in [-1, 1). */
export function noiseGen(seed = 1): () => number {
  let state = seed >>> 0 || 1;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return (state / 0x100000000) * 2 - 1;
  };
}

export function silence(samples: number): Float32Array {
  return new Float32Array(samples);
}

/** White noise scaled to `db` dBFS RMS. */
export function whiteNoise(samples: number, db: number, seed = 7): Float32Array {
  const gen = noiseGen(seed);
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i += 1) out[i] = gen();
  return scaleToDb(out, db);
}

/** Mains hum: 50 Hz with two harmonics, scaled to `db` dBFS RMS. */
export function hum(samples: number, db: number): Float32Array {
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i += 1) {
    const t = i / SR;
    out[i] =
      Math.sin(2 * Math.PI * 50 * t) + 0.5 * Math.sin(2 * Math.PI * 100 * t) + 0.25 * Math.sin(2 * Math.PI * 150 * t);
  }
  return scaleToDb(out, db);
}

/**
 * Speech-like: harmonics of a 120 Hz voice up to 3.4 kHz with a formant-ish
 * 1/k roll-off, amplitude-modulated at a 4 Hz syllable rate (never fully
 * silent between syllables), scaled to `db` dBFS RMS.
 */
export function speechLike(samples: number, db: number, options: { f0?: number; syllableHz?: number } = {}): Float32Array {
  const f0 = options.f0 ?? 120;
  const syllableHz = options.syllableHz ?? 4;
  const out = new Float32Array(samples);
  const harmonics: number[] = [];
  const breath = noiseGen(23);
  for (let k = 1; k * f0 <= 3400; k += 1) harmonics.push(k);
  for (let i = 0; i < samples; i += 1) {
    const t = i / SR;
    let v = 0;
    for (const k of harmonics) {
      // emphasise the first-formant region (300–3000 Hz), like real voices do
      const hz = k * f0;
      const weight = hz < 300 ? 0.35 : hz > 3000 ? 0.4 : 1;
      v += (weight / k) * Math.sin(2 * Math.PI * hz * t + k * 0.7);
    }
    const envelope = 0.55 + 0.45 * Math.sin(2 * Math.PI * syllableHz * t - Math.PI / 2);
    // a little aspiration noise (≈ −24 dB relative) so it is not a pure harmonic comb
    out[i] = (v + 0.06 * breath()) * envelope;
  }
  return scaleToDb(out, db);
}

/** A broadband decaying transient (`widthSamples` long) at `db` dBFS peak-ish RMS. */
export function click(widthSamples: number, db: number, seed = 11): Float32Array {
  const gen = noiseGen(seed);
  const out = new Float32Array(widthSamples);
  for (let i = 0; i < widthSamples; i += 1) out[i] = gen() * Math.exp((-4 * i) / widthSamples);
  return scaleToDb(out, db);
}

export function scaleToDb(signal: Float32Array, db: number): Float32Array {
  const level = rms(signal);
  if (level === 0) return signal;
  const gain = dbToAmplitude(db) / level;
  const out = new Float32Array(signal.length);
  for (let i = 0; i < signal.length; i += 1) out[i] = signal[i] * gain;
  return out;
}

/** Add `part` into `base` at `atSamples` (clipped to the base length). */
export function overlay(base: Float32Array, part: Float32Array, atSamples: number): Float32Array {
  const out = Float32Array.from(base);
  for (let i = 0; i < part.length && atSamples + i < out.length; i += 1) out[atSamples + i] += part[i];
  return out;
}

export function concat(...parts: Float32Array[]): Float32Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Float32Array(total);
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.length;
  }
  return out;
}

export type TimedFrame = { features: FrameFeatures; now: number };

/** Sliding 1024-sample analyser frames every 20 ms; `now` is the frame end in ms. */
export function frames(signal: Float32Array, hop = HOP, frame = FRAME): TimedFrame[] {
  const out: TimedFrame[] = [];
  for (let end = frame; end <= signal.length; end += hop) {
    const slice = signal.subarray(end - frame, end);
    out.push({ features: analyseFrame(slice, SR), now: Math.round((end / SR) * 1000) });
  }
  return out;
}
