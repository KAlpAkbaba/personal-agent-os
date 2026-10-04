/**
 * What the measurement page shows, derived from the server's answer: which sentence is
 * next for a place, which are done, and the Turkish lines. No sentence text lives here -
 * the sentences are the ones GET returned (stt_compare.OWNER_SENTENCES on the server).
 */

import type { Measurement, Place, RecordingItem } from "./api";

export const PLACE_LABEL: Record<Place, string> = { ev: "Ev", ofis: "Ofis" };

export const LIVE_REASON_TR =
  "Sesli oturum açıkken kayıt yapılamaz: bu sekmede aynı anda tek mikrofon açılır. Önce oturumu kapat.";
export const CHROME_NOT_RUN_TR = "Chrome'un tanıyıcısı bu cümlede çalışmadı; Chrome satırı bu cümle için ölçülmeyecek.";
export const SAVED_TR = "Kaydedildi.";
export const RECORDING_TR = "Kayıt sürüyor; cümleyi oku, bitince 'Bitir'e bas.";
export const UPLOADING_TR = "Kayıt yükleniyor…";

export function recordingFor(data: Measurement, place: Place, index: number): RecordingItem | null {
  return data.recordings.find((item) => item.place === place && item.index === index) ?? null;
}

export function doneIndices(data: Measurement, place: Place): Set<number> {
  return new Set(data.recordings.filter((item) => item.place === place).map((item) => item.index));
}

/** The first sentence not yet recorded for this place; null when all are done. */
export function nextIndex(data: Measurement, place: Place): number | null {
  const done = doneIndices(data, place);
  return data.sentences.find((sentence) => !done.has(sentence.index))?.index ?? null;
}

export function counterText(index: number, total: number): string {
  return `${index} / ${total}`;
}

/** 2400 ms -> "2,4 sn" (Turkish decimal comma). */
export function secondsText(audioMs: number): string {
  return `${(audioMs / 1000).toFixed(1).replace(".", ",")} sn`;
}

export function chromeText(item: RecordingItem): string {
  if (item.browser_transcript === null) return "Chrome çalışmadı; bu cümlede Chrome ölçülmeyecek.";
  if (item.browser_transcript === "") return "Chrome hiçbir şey yazmadı.";
  return `Chrome: “${item.browser_transcript}”`;
}

export function allDoneText(total: number, place: Place): string {
  return `${PLACE_LABEL[place]} için ${total} cümlenin hepsi kaydedildi.`;
}

export function deletedText(count: number): string {
  return `${count} ölçüm kaydı silindi.`;
}

/** The page head, built from the server's numbers. */
export function headLines(data: Measurement): string[] {
  return [
    `Yalnız bu ${data.sentences.length} cümle kaydedilir; her kayıt en çok ${data.max_seconds} saniye sürer.`,
    `Ses kendi sunucunda ${data.retention_days} gün durur, sonra kendiliğinden silinir.`,
    "Kayıt sürerken aynı ses Chrome'un tanıyıcısına da verilir; Chrome onu Google'a gönderebilir.",
    "Odada başka biri konuşurken kaydetme.",
  ];
}
