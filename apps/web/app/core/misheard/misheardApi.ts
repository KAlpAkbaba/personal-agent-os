/**
 * The client for `/v1/voice/misheard` (ADR-0254): the notebook of sentences the system did
 * not understand.
 *
 * It sends what the owner pressed and reports what the Cloud Core answered; every rule (the
 * meaning's length, a row that is gone, the 30-day expiry) is the server's, and a refusal is
 * surfaced with the server's own sentence, never turned into a success.
 */

import { apiFetch } from "../../lib/session";

export const MISHEARD_PATH = "/v1/voice/misheard";

/** Exactly the columns of `misheard_utterances`; contract.test.ts holds this to models.py. */
export type MisheardItem = {
  id: string;
  heard_at: string;
  sentence: string;
  mode: string;
  engine: string | null;
  device_id: string | null;
  band: string | null;
  confidence: number | null;
  reason: string;
  resolved_intent: string | null;
  tool: string | null;
  session_id: string;
  meant: string | null;
  answered_at: string | null;
  expires_at: string;
};

export type MisheardList = {
  items: MisheardItem[];
  /** How many have no meaning yet. */
  open: number;
  retention_days: number;
};

export type Refusal = { ok: false; code: string; message: string };

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

function itemPath(id: string): string {
  return `${MISHEARD_PATH}/${encodeURIComponent(id)}`;
}

export async function fetchNotebook(): Promise<{ ok: true; list: MisheardList } | Refusal> {
  const response = await apiFetch(MISHEARD_PATH);
  if (!response.ok) return refusal(response);
  return { ok: true, list: (await response.json()) as MisheardList };
}

export async function answerMeaning(
  id: string,
  meant: string,
): Promise<{ ok: true; item: MisheardItem } | Refusal> {
  const response = await apiFetch(`${itemPath(id)}/meaning`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ meant }),
  });
  if (!response.ok) return refusal(response);
  return { ok: true, item: (await response.json()) as MisheardItem };
}

export async function forgetOne(id: string): Promise<{ ok: true; deleted: number } | Refusal> {
  const response = await apiFetch(itemPath(id), { method: "DELETE" });
  if (!response.ok) return refusal(response);
  return { ok: true, deleted: ((await response.json()) as { deleted: number }).deleted };
}

/** "Defteri unut": ONE request, every row gone. */
export async function forgetAll(): Promise<{ ok: true; deleted: number } | Refusal> {
  const response = await apiFetch(MISHEARD_PATH, { method: "DELETE" });
  if (!response.ok) return refusal(response);
  return { ok: true, deleted: ((await response.json()) as { deleted: number }).deleted };
}
