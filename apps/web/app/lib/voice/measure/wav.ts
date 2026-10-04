/**
 * The measurement recording's file format: PCM 16-bit, mono, 16 000 Hz, a 44-byte RIFF
 * header - what `app.voice.measurement` accepts and `app.voice.stt_compare` reads.
 *
 * Pure functions on numbers; nothing here touches a browser object.
 */

export const TARGET_RATE = 16_000;
export const WAV_HEADER_BYTES = 44;

/**
 * Mono Float32 samples at `fromRate` -> mono Float32 samples at 16 000 Hz.
 *
 * Downsampling averages the source samples each output sample stands for (a box filter
 * centred on the output instant), which keeps the speech band and removes most of what
 * would alias; upsampling interpolates linearly. 16 000 Hz in is passed through as is.
 */
export function resampleTo16k(samples: Float32Array, fromRate: number): Float32Array {
  if (fromRate === TARGET_RATE) return Float32Array.from(samples);
  if (!(fromRate > 0) || samples.length === 0) return new Float32Array(0);
  const ratio = fromRate / TARGET_RATE;
  const length = Math.round(samples.length / ratio);
  const out = new Float32Array(length);
  const last = samples.length - 1;
  for (let i = 0; i < length; i += 1) {
    const centre = i * ratio;
    if (ratio > 1) {
      const from = Math.max(0, Math.ceil(centre - ratio / 2));
      const to = Math.min(last, Math.ceil(centre + ratio / 2) - 1);
      let sum = 0;
      for (let j = from; j <= to; j += 1) sum += samples[j];
      out[i] = to >= from ? sum / (to - from + 1) : samples[Math.min(last, Math.round(centre))];
    } else {
      const left = Math.min(last, Math.floor(centre));
      const right = Math.min(last, left + 1);
      const t = centre - left;
      out[i] = samples[left] * (1 - t) + samples[right] * t;
    }
  }
  return out;
}

/** 16 000 Hz mono Float32 samples -> a PCM 16-bit WAV file. Out-of-range values are clamped. */
export function encodeWav(samples: Float32Array): Uint8Array {
  const dataBytes = samples.length * 2;
  const bytes = new Uint8Array(WAV_HEADER_BYTES + dataBytes);
  const view = new DataView(bytes.buffer);
  const ascii = (at: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(at + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, TARGET_RATE, true);
  view.setUint32(28, TARGET_RATE * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);
  for (let i = 0; i < samples.length; i += 1) {
    const s = samples[i] > 1 ? 1 : samples[i] < -1 ? -1 : samples[i];
    view.setInt16(WAV_HEADER_BYTES + i * 2, Math.round(s < 0 ? s * 0x8000 : s * 0x7fff), true);
  }
  return bytes;
}

/** Bytes -> base64, in chunks so a 30-second take never builds one huge argument list. */
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
