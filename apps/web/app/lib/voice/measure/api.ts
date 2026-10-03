/**
 * The client for `/v1/voice/measurement` (services/api/app/voice/measurement/routes.py):
 * the owner's twenty measurement readings.
 *
 * It sends what the owner pressed and reports what the Cloud Core answered. Every rule (the
 * sentence list, the places, the WAV format, the 30 seconds, the retention) is the server's;
 * a refusal comes back as `{ok: false, code, message}` with the server's own sentence and is
 * never turned into a success. tests/measure/contract.test.ts holds the routes to routes.py.
 */

import { UnauthorizedError, apiFetch } from "../../session";

export const MEASUREMENT_PATH = "/v1/voice/measurement";
/** Route templates exactly as routes.py declares them under the prefix. */
export const RECORDING_ROUTE = "/recordings/{place}/{index}";
export const RECORDINGS_ROUTE = "/recordings";

export type Place = "ev" | "ofis";

export type Sentence = { index: number; text: string };

export type RecordingItem = {
  place: Place;
  index: number;
  file: string;
  reference: string;
  recorded_at: string;
  expires_at: string;
  audio_ms: number;
  bytes: number;
  sha256: string;
  browser_transcript: string | null;
  browser_engine: string | null;
  capture: Record<string, boolean | number | string> | null;
};

export type Measurement = {
  sentences: Sentence[];
  places: Place[];
  retention_days: number;
  max_seconds: number;
  recordings: RecordingItem[];
};

/** Exactly the four CONTRACT keys of the PUT body. */
export type PutBody = {
  audio_wav_base64: string;
  /** null = Chrome's recogniser did not run; "" = it ran and wrote nothing. */
  browser_transcript: string | null;
  browser_engine: string | null;
  capture: Record<string, boolean | number | string> | null;
};

export type Refusal = { ok: false; code: string; message: string };
export type PutResult = { ok: true; item: RecordingItem } | Refusal;
export type DeleteResult = { ok: true; deleted: number } | Refusal;

const UNREACHABLE_TR = "Cloud Core'a ulaşılamadı; bağlantıyı kontrol edip yeniden dene.";

export function recordingPath(place: Place, index: number): string {
  return (
    MEASUREMENT_PATH +
    RECORDING_ROUTE.replace("{place}", encodeURIComponent(place)).replace("{index}", String(index))
  );
}

async function refusal(response: Response): Promise<Refusal> {
  try {
    const body = (await response.json()) as { detail?: { code?: string; message?: string } };
    return {
      ok: false,
      code: body.detail?.code ?? `http_${response.status}`,
      message: body.detail?.message ?? `HTTP ${response.status}`,
    };
  } catch {
    return { ok: false, code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

/** A call that could not reach the server is a refusal too; a lost session still signs out. */
async function call<T>(path: string, init: RequestInit, read: (response: Response) => Promise<T>): Promise<T | Refusal> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch (error) {
    if (error instanceof UnauthorizedError) throw error;
    return { ok: false, code: "unreachable", message: UNREACHABLE_TR };
  }
  if (!response.ok) return refusal(response);
  return read(response);
}

export function fetchMeasurement(): Promise<{ ok: true; measurement: Measurement } | Refusal> {
  return call(MEASUREMENT_PATH, {}, async (response) => ({
    ok: true as const,
    measurement: (await response.json()) as Measurement,
  }));
}

export function putRecording(place: Place, index: number, body: PutBody): Promise<PutResult> {
  const sent: PutBody = {
    audio_wav_base64: body.audio_wav_base64,
    browser_transcript: body.browser_transcript,
    browser_engine: body.browser_engine,
    capture: body.capture,
  };
  return call(
    recordingPath(place, index),
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sent) },
    async (response) => ({ ok: true as const, item: (await response.json()) as RecordingItem }),
  );
}

async function deleted(response: Response): Promise<DeleteResult> {
  const body = (await response.json()) as { deleted?: number };
  return { ok: true, deleted: typeof body.deleted === "number" ? body.deleted : 0 };
}

export function deleteRecording(place: Place, index: number): Promise<DeleteResult> {
  return call(recordingPath(place, index), { method: "DELETE" }, deleted);
}

export function deleteAllRecordings(): Promise<DeleteResult> {
  return call(MEASUREMENT_PATH + RECORDINGS_ROUTE, { method: "DELETE" }, deleted);
}
