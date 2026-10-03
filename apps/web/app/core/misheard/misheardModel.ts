/**
 * What one row of the misheard notebook shows - pure, so the page has nothing to decide.
 *
 * The row shows the one sentence as the recogniser wrote it and the few facts around it, in
 * Turkish. A fact the server did not know (no engine, no machine, no band) is left out, never
 * printed as "null"; a reason this page does not know is shown as itself, never hidden.
 */

import type { MisheardItem } from "./misheardApi";

/** The CONTRACT's width for the owner's answer. */
export const MEANT_MAX = 2000;

const REASON_SENTENCE: Record<string, string> = {
  no_intent: "Bu cümleden bir istek çıkaramadım.",
  asked_question: "Ne demek istediğini sana sormak zorunda kaldım.",
  objected: "Yaptığım şeye itiraz ettin.",
  tool_failed: "İsteğini yapmaya çalıştım ama kullandığım araç başarısız oldu.",
};

export const REASONS = Object.keys(REASON_SENTENCE);

const MODE_LABEL: Record<string, string> = { paid: "Ücretli", local: "Yerel" };

const BAND_LABEL: Record<string, string> = {
  high: "yüksek güven",
  medium: "orta güven",
  low: "düşük güven",
};

export function reasonSentence(reason: string): string {
  return REASON_SENTENCE[reason] ?? reason;
}

export function modeLabel(mode: string): string {
  return MODE_LABEL[mode] ?? mode;
}

export type RowView = {
  id: string;
  sentence: string;
  /** The owner's local time. */
  when: string;
  mode: string;
  machine: string | null;
  engine: string | null;
  band: string | null;
  reason: string;
  tool: string | null;
  meant: string | null;
};

/** `timeZone` is for tests; the page leaves it out and gets the browser's own zone. */
export function rowView(item: MisheardItem, timeZone?: string): RowView {
  const heard = new Date(item.heard_at);
  return {
    id: item.id,
    sentence: item.sentence,
    when: Number.isNaN(heard.getTime())
      ? item.heard_at
      : heard.toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short", timeZone }),
    mode: modeLabel(item.mode),
    machine: item.device_id ? `cihaz ${item.device_id.slice(0, 8)}` : null,
    engine: item.engine ? `tanıyan: ${item.engine}` : null,
    band: item.band ? (BAND_LABEL[item.band] ?? item.band) : null,
    reason: reasonSentence(item.reason),
    tool: item.tool ? `araç: ${item.tool}` : null,
    meant: item.meant,
  };
}

/** 'Kaydet' is allowed for 1..2000 characters (after the spaces the server strips too). */
export function canSave(draft: string): boolean {
  const length = draft.trim().length;
  return length >= 1 && length <= MEANT_MAX;
}

export function retentionSentence(days: number): string {
  return `Bu cümleler yalnızca yazı olarak ${days} gün saklanır, sonra kendiliğinden silinir; hiçbir ses saklanmaz.`;
}

export function openSentence(open: number): string {
  return open === 0 ? "Cevap bekleyen cümle yok." : `${open} cümle cevabını bekliyor.`;
}

export const EMPTY_SENTENCE = "Defter boş: yanlış anlaşılan bir cümle yok.";

export function forgottenSentence(deleted: number): string {
  return `Defter unutuldu: ${deleted} cümle silindi.`;
}
