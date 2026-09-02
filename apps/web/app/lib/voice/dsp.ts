/**
 * Frame-level audio features for the local speech gate and the noise-floor
 * calibration (ADR-0044, layers 2–3). Pure functions over a Float32Array of
 * samples in [-1, 1]; the same code runs on AnalyserNode frames in the browser
 * and on synthetic signals in the tests, so what the tests prove is what the
 * page does. Nothing here keeps audio: a frame in, a handful of numbers out.
 */

/** Lowest level we ever report; digital silence clamps here. */
export const DB_FLOOR = -100;

export type FrameFeatures = {
  /** RMS level in dBFS (≤ 0, ≥ DB_FLOOR). */
  rmsDb: number;
  /** Largest |sample| in the frame. */
  peak: number;
  /** Fraction of samples at or beyond the clipping threshold (0.985). */
  clipRatio: number;
  /** Zero crossings per sample (0..1): speech ≈ 0.02–0.2, broadband noise/clicks ≫ 0.25, hum ≪ 0.01. */
  zcr: number;
  /** Share of energy in the speech band 200–3400 Hz relative to everything above 60 Hz (0..1). */
  speechBandRatio: number;
  /** Share of energy below 150 Hz (mains hum, fan rumble, desk knocks) (0..1). */
  lowBandRatio: number;
  /** Spectral flatness over 100–6000 Hz: harmonic speech ≈ 0.05–0.3, noise ≈ 0.6–1. */
  flatness: number;
  /** Frame duration in ms at the given sample rate. */
  durationMs: number;
};

export function amplitudeToDb(amplitude: number): number {
  if (!(amplitude > 0)) return DB_FLOOR;
  return Math.max(DB_FLOOR, 20 * Math.log10(amplitude));
}

export function dbToAmplitude(db: number): number {
  return 10 ** (db / 20);
}

export function rms(frame: ArrayLike<number>): number {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i += 1) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}

export function zeroCrossingRate(frame: ArrayLike<number>): number {
  if (frame.length < 2) return 0;
  let crossings = 0;
  let previous = frame[0] >= 0;
  for (let i = 1; i < frame.length; i += 1) {
    const current = frame[i] >= 0;
    if (current !== previous) crossings += 1;
    previous = current;
  }
  return crossings / (frame.length - 1);
}

function nextPowerOfTwo(n: number): number {
  let p = 1;
  while (p < n) p <<= 1;
  return p;
}

/** In-place iterative radix-2 FFT (Cooley–Tukey); `re`/`im` lengths must be a power of two. */
export function fft(re: Float64Array, im: Float64Array): void {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i += 1) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      const tr = re[i];
      re[i] = re[j];
      re[j] = tr;
      const ti = im[i];
      im[i] = im[j];
      im[j] = ti;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = (-2 * Math.PI) / len;
    const wRe = Math.cos(angle);
    const wIm = Math.sin(angle);
    const half = len >> 1;
    for (let i = 0; i < n; i += len) {
      let curRe = 1;
      let curIm = 0;
      for (let j = 0; j < half; j += 1) {
        const a = i + j;
        const b = a + half;
        const bRe = re[b] * curRe - im[b] * curIm;
        const bIm = re[b] * curIm + im[b] * curRe;
        re[b] = re[a] - bRe;
        im[b] = im[a] - bIm;
        re[a] += bRe;
        im[a] += bIm;
        const nextRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nextRe;
      }
    }
  }
}

/**
 * Hann-windowed power spectrum, bins 0..N/2 where N is the next power of two
 * ≥ frame.length (zero-padded). Bin k is centred at k·sampleRate/N.
 */
export function powerSpectrum(frame: ArrayLike<number>): { power: Float64Array; size: number } {
  const n = nextPowerOfTwo(Math.max(2, frame.length));
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  const m = frame.length;
  for (let i = 0; i < m; i += 1) {
    const w = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / Math.max(1, m - 1));
    re[i] = frame[i] * w;
  }
  fft(re, im);
  const bins = n / 2 + 1;
  const power = new Float64Array(bins);
  for (let k = 0; k < bins; k += 1) power[k] = re[k] * re[k] + im[k] * im[k];
  return { power, size: n };
}

const CLIP_THRESHOLD = 0.985;
/** 200 Hz keeps a low male f0 inside the band; 3400 Hz is the telephony top. */
const SPEECH_BAND_HZ: [number, number] = [200, 3400];
const LOW_BAND_HZ = 150;
const FLATNESS_BAND_HZ: [number, number] = [100, 6000];
const DC_CUTOFF_HZ = 60;
const EPS = 1e-12;

export function analyseFrame(frame: Float32Array | ArrayLike<number>, sampleRate: number): FrameFeatures {
  const n = frame.length;
  let peak = 0;
  let clipped = 0;
  for (let i = 0; i < n; i += 1) {
    const a = Math.abs(frame[i]);
    if (a > peak) peak = a;
    if (a >= CLIP_THRESHOLD) clipped += 1;
  }
  const level = rms(frame);
  const { power, size } = powerSpectrum(frame);
  const hzPerBin = sampleRate / size;
  let total = 0;
  let speech = 0;
  let low = 0;
  let logSum = 0;
  let linSum = 0;
  let flatBins = 0;
  for (let k = 1; k < power.length; k += 1) {
    const hz = k * hzPerBin;
    const p = power[k];
    if (hz < LOW_BAND_HZ) low += p;
    if (hz >= DC_CUTOFF_HZ) total += p;
    if (hz >= SPEECH_BAND_HZ[0] && hz <= SPEECH_BAND_HZ[1]) speech += p;
    if (hz >= FLATNESS_BAND_HZ[0] && hz <= FLATNESS_BAND_HZ[1]) {
      logSum += Math.log(p + EPS);
      linSum += p;
      flatBins += 1;
    }
  }
  const lowTotal = total + low;
  const flatness =
    flatBins > 0 ? Math.min(1, Math.exp(logSum / flatBins) / (linSum / flatBins + EPS)) : 1;
  return {
    rmsDb: amplitudeToDb(level),
    peak,
    clipRatio: n > 0 ? clipped / n : 0,
    zcr: zeroCrossingRate(frame),
    speechBandRatio: total > EPS ? Math.min(1, speech / total) : 0,
    lowBandRatio: lowTotal > EPS ? Math.min(1, low / lowTotal) : 0,
    flatness: level > 0 ? flatness : 1,
    durationMs: sampleRate > 0 ? (n / sampleRate) * 1000 : 0,
  };
}

/** Sorted-copy percentile (0..1) of a numeric list; NaN for an empty list. */
export function percentile(values: ArrayLike<number>, q: number): number {
  const n = values.length;
  if (n === 0) return Number.NaN;
  const sorted = Array.from(values as ArrayLike<number>).toSorted((a, b) => a - b);
  const pos = Math.min(n - 1, Math.max(0, (n - 1) * q));
  const lower = Math.floor(pos);
  const upper = Math.ceil(pos);
  if (lower === upper) return sorted[lower];
  const t = pos - lower;
  return sorted[lower] * (1 - t) + sorted[upper] * t;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function sigmoid(x: number): number {
  return 1 / (1 + Math.exp(-x));
}

/** Round to one decimal so reported numbers stay small and stable. */
export function round1(value: number): number {
  return Math.round(value * 10) / 10;
}
