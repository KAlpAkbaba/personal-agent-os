/**
 * The client for `/v1/urgent-alert` (urgent-alert-wire): is the phone alarm connected, what
 * became of the last alarm, and 'önemli deneme bildirimi gönder'.
 *
 * Every rule (three tests an hour, "bağlı değil") is the Cloud Core's; a refusal is surfaced
 * with the server's own sentence, never turned into a success. No key ever comes back here.
 */

import { apiFetch } from "../../lib/session";

export const URGENT_ALERT_STATUS_PATH = "/v1/urgent-alert/status";
export const URGENT_ALERT_TEST_PATH = "/v1/urgent-alert/test";

/** Exactly what `GET /v1/urgent-alert/status` returns (app/urgent_alert/routes.py). */
export type UrgentAlertStatus = {
  configured: boolean;
  open_receipts: number;
  /** When the owner last saw an alarm (the phone's "Gördüm" or read in the inbox). */
  last_seen_at: string | null;
  last_outcome: "seen" | "unseen" | "cancelled" | null;
};

export type Refusal = { ok: false; code: string; message: string };

async function refusal(response: Response): Promise<Refusal> {
  try {
    const body = (await response.json()) as { detail?: string | { message?: string } };
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    return { ok: false, code: `http_${response.status}`, message: message ?? `HTTP ${response.status}` };
  } catch {
    return { ok: false, code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

export async function fetchStatus(): Promise<{ ok: true; status: UrgentAlertStatus } | Refusal> {
  const response = await apiFetch(URGENT_ALERT_STATUS_PATH);
  if (!response.ok) return refusal(response);
  return { ok: true, status: (await response.json()) as UrgentAlertStatus };
}

export async function sendTest(): Promise<{ ok: true; id: string } | Refusal> {
  const response = await apiFetch(URGENT_ALERT_TEST_PATH, { method: "POST" });
  if (!response.ok) return refusal(response);
  return { ok: true, id: ((await response.json()) as { id: string }).id };
}

/** "görüldü HH:MM" / "görülmedi" / nothing yet - the line the Kokpit shows. */
export function lastSentence(status: UrgentAlertStatus, timeZone?: string): string | null {
  if (status.last_outcome === "unseen") return "görülmedi";
  if (status.last_seen_at) {
    const at = new Date(status.last_seen_at);
    if (Number.isNaN(at.getTime())) return null;
    const hhmm = at.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone });
    return `görüldü ${hhmm}`;
  }
  return null;
}
