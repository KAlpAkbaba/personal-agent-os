/**
 * What the page does when the owner presses a button: one call each, and one sentence back.
 *
 * "Defteri unut" is ONE request and no second question (owner rule 2026-09-18: the first word
 * applies). A refusal comes back with the server's own sentence and `ok: false`.
 */

import { answerMeaning, forgetAll, forgetOne } from "./misheardApi";
import { forgottenSentence } from "./misheardModel";

export type Notice = { ok: boolean; message: string };

export async function saveMeaning(id: string, meant: string): Promise<Notice> {
  const result = await answerMeaning(id, meant);
  return result.ok ? { ok: true, message: "Kaydedildi." } : { ok: false, message: result.message };
}

export async function forgetRow(id: string): Promise<Notice> {
  const result = await forgetOne(id);
  return result.ok ? { ok: true, message: "Silindi." } : { ok: false, message: result.message };
}

export async function forgetNotebook(): Promise<Notice> {
  const result = await forgetAll();
  return result.ok
    ? { ok: true, message: forgottenSentence(result.deleted) }
    : { ok: false, message: result.message };
}
