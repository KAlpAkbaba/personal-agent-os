/**
 * One measurement take: open the microphone, collect PCM, stop, encode, upload.
 *
 * Every browser object is a seam (`RecorderDeps`), so vitest drives a take with fakes and
 * the page hands in the real ones (`browser.ts`). Three rules live here:
 *
 * - The microphone is closed in a `finally`: after a good take, a refused or failed upload
 *   and a capture that threw alike. The page never keeps a second microphone open.
 * - Chrome's recogniser hears the SAME audio: `start(track)` with a clone of the take's own
 *   audio track (Chrome 135+). It is never started without a track - that would be the
 *   default microphone, a second capture of a different sound.
 * - `browser_transcript` null means Chrome's row was not measured for this sentence (no
 *   recogniser, a start that threw, an error event, or neither an end nor a result within
 *   RECOGNIZER_GUARD_MS after the take); "" means it ran and wrote nothing.
 */

import type { AppliedInputSettings, MicrophoneConstraints } from "../ports";
import type { SttEngine } from "../localMode";
import type { Place, PutBody, PutResult, RecordingItem } from "./api";
import { bytesToBase64, encodeWav, resampleTo16k } from "./wav";

/** How long the recogniser may take to hand over its last result after the take stops. */
export const RECOGNIZER_GUARD_MS = 1_500;
/** This page does not set `processLocally`, so it cannot know which leg Chrome used. */
export const ENGINE_UNKNOWN: SttEngine = "bilinmiyor";
export const RECOGNIZER_LANG = "tr-TR";
/** The server refuses longer capture strings; a device label is cut to this. */
const MAX_CAPTURE_STRING = 120;

export const MICROPHONE_FAILED_TR = "Mikrofon açılamadı; tarayıcının mikrofon iznini kontrol et.";
export const CAPTURE_FAILED_TR = "Ses alınamadı; kayıt yüklenmedi, cümleyi yeniden oku.";
export const UPLOAD_FAILED_TR = "Kayıt Cloud Core'a yüklenemedi; cümleyi yeniden oku.";
export const CANCELLED_TR = "Kayıt yarıda bırakıldı; yüklenmedi.";

/** The subset of BrowserMicrophone a take uses. */
export interface MeasureMicrophone {
  open(deviceId?: string, constraints?: Partial<MicrophoneConstraints>): Promise<MediaStream>;
  readonly applied: AppliedInputSettings | null;
  close(): void;
}

/** PCM collection from a stream; `stop` hands back every sample collected, mono. */
export type PcmCapture = {
  readonly sampleRate: number;
  stop(): Promise<Float32Array>;
};

export type RecognizerLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult:
    | ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void)
    | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  start(track: MediaStreamTrack): void;
  stop(): void;
  abort(): void;
};

export type Clock = { setTimeout(fn: () => void, ms: number): () => void };

export type RecorderDeps = {
  microphone: MeasureMicrophone;
  /** May throw (no AudioContext, a refused node): the take then fails and the microphone closes. */
  capture: (stream: MediaStream) => PcmCapture;
  /** null when the browser has no usable SpeechRecognition with `start(track)`. */
  recognizer: (() => RecognizerLike) | null;
  clock: Clock;
  upload: (place: Place, index: number, body: PutBody) => Promise<PutResult>;
};

export type TakeOptions = {
  place: Place;
  index: number;
  maxSeconds: number;
  deviceId?: string;
  constraints?: Partial<MicrophoneConstraints>;
};

export type TakeOutcome =
  | { ok: true; item: RecordingItem; chromeRan: boolean }
  | { ok: false; code: string; message: string };

export type Take = {
  /** The owner's 'Bitir': stop and upload. */
  stop(): void;
  /** The page went away: stop and do NOT upload. */
  cancel(): void;
  readonly done: Promise<TakeOutcome>;
};

/** The applied settings the CONTRACT's `capture` carries; unknown values are left out. */
export function captureSettings(applied: AppliedInputSettings | null): PutBody["capture"] {
  if (!applied) return null;
  const out: Record<string, boolean | number | string> = {};
  const pick = {
    echoCancellation: applied.echoCancellation,
    noiseSuppression: applied.noiseSuppression,
    autoGainControl: applied.autoGainControl,
    voiceIsolation: applied.voiceIsolation,
    sampleRate: applied.sampleRate,
    label: applied.label ? applied.label.slice(0, MAX_CAPTURE_STRING) : null,
  };
  for (const [key, value] of Object.entries(pick)) {
    if (typeof value === "boolean" || typeof value === "string") out[key] = value;
    else if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
  }
  return out;
}

type Recognition = { finish(): Promise<string | null>; dispose(): void };

const NOT_RUN: Recognition = { finish: async () => null, dispose: () => {} };

function startRecognition(deps: RecorderDeps, stream: MediaStream): Recognition {
  if (!deps.recognizer) return NOT_RUN;
  const source = stream.getAudioTracks()[0];
  if (!source) return NOT_RUN;
  let track: MediaStreamTrack;
  let recognizer: RecognizerLike;
  try {
    track = source.clone();
  } catch {
    return NOT_RUN;
  }
  try {
    recognizer = deps.recognizer();
  } catch {
    track.stop();
    return NOT_RUN;
  }
  const finals: string[] = [];
  /** The error Chrome named (e.g. "audio-capture"); any error means its row is not measured. */
  let failure: string | null = null;
  let ended = false;
  let onEnded: (() => void) | null = null;
  Object.assign(recognizer, {
    lang: RECOGNIZER_LANG,
    continuous: true,
    interimResults: false,
    onresult: (event: Parameters<NonNullable<RecognizerLike["onresult"]>>[0]) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) finals.push(result[0].transcript.trim());
      }
    },
    onerror: (event: { error?: string }) => {
      failure = event.error ?? "error";
    },
    onend: () => {
      ended = true;
      onEnded?.();
    },
  } satisfies Partial<RecognizerLike>);
  try {
    recognizer.start(track);
  } catch {
    track.stop();
    return NOT_RUN;
  }
  const transcript = () => finals.filter(Boolean).join(" ");
  return {
    async finish() {
      if (failure === null && !ended) {
        try {
          recognizer.stop();
        } catch {
          /* the wait below decides */
        }
        if (!ended) {
          await new Promise<void>((resolve) => {
            const cancel = deps.clock.setTimeout(resolve, RECOGNIZER_GUARD_MS);
            onEnded = () => {
              cancel();
              resolve();
            };
          });
        }
      }
      track.stop();
      if (failure !== null) return null;
      if (ended) return transcript();
      return finals.length > 0 ? transcript() : null;
    },
    dispose() {
      onEnded = null;
      if (!ended) {
        try {
          recognizer.abort();
        } catch {
          /* already gone */
        }
      }
      track.stop();
    },
  };
}

export function startTake(deps: RecorderDeps, options: TakeOptions): Take {
  let release: (() => void) | null = null;
  let cancelled = false;
  const stopped = new Promise<void>((resolve) => {
    release = resolve;
  });

  async function run(): Promise<TakeOutcome> {
    let recognition: Recognition = NOT_RUN;
    try {
      let stream: MediaStream;
      try {
        stream = await deps.microphone.open(options.deviceId, options.constraints);
      } catch {
        return { ok: false, code: "microphone_failed", message: MICROPHONE_FAILED_TR };
      }
      const capture = captureSettings(deps.microphone.applied);
      let pcm: PcmCapture;
      try {
        pcm = deps.capture(stream);
      } catch {
        return { ok: false, code: "capture_failed", message: CAPTURE_FAILED_TR };
      }
      recognition = startRecognition(deps, stream);
      const cancelLimit = deps.clock.setTimeout(() => release?.(), options.maxSeconds * 1000);
      await stopped;
      cancelLimit();
      let samples: Float32Array;
      try {
        samples = await pcm.stop();
      } catch {
        return { ok: false, code: "capture_failed", message: CAPTURE_FAILED_TR };
      }
      const transcript = await recognition.finish();
      if (cancelled) return { ok: false, code: "cancelled", message: CANCELLED_TR };
      const limit = Math.floor(options.maxSeconds * pcm.sampleRate);
      const wav = encodeWav(resampleTo16k(samples.subarray(0, limit), pcm.sampleRate));
      const body: PutBody = {
        audio_wav_base64: bytesToBase64(wav),
        browser_transcript: transcript,
        browser_engine: transcript === null ? null : ENGINE_UNKNOWN,
        capture,
      };
      let result: PutResult;
      try {
        result = await deps.upload(options.place, options.index, body);
      } catch {
        return { ok: false, code: "upload_failed", message: UPLOAD_FAILED_TR };
      }
      if (!result.ok) return result;
      return { ok: true, item: result.item, chromeRan: transcript !== null };
    } finally {
      recognition.dispose();
      deps.microphone.close();
    }
  }

  return {
    stop: () => release?.(),
    cancel: () => {
      cancelled = true;
      release?.();
    },
    done: run(),
  };
}
