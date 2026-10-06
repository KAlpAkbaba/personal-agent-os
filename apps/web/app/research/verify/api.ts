/**
 * Card verify-mode: what the owner asked to verify, read from
 * `GET /v1/research/verifications` (newest first; `q` = words, `since`/`until` = time).
 *
 * The server owns every decision - the verdict, which sources were kept, the
 * counter-argument, the sentence spoken. This module only carries them; a field
 * the server did not send stays missing rather than invented.
 */

import { apiFetch } from "../../lib/session";

export type VerdictCode = "dogru" | "yanlis" | "kismen" | "belirsiz";

export interface VerifySource {
  url: string;
  title: string;
  published_at: string | null;
  quote: string;
  stance: "supports" | "refutes" | "neutral";
}

export interface Verification {
  verification_id: string;
  created_at: string | null;
  said: string;
  claim: string;
  status: "pending" | "settled";
  verdict: VerdictCode | null;
  verdict_label: string | null;
  confidence: number | null;
  sources: VerifySource[];
  counter_argument: VerifySource | null;
  spoken: string | null;
}

export interface VerificationQuery {
  q?: string;
  since?: string;
  until?: string;
}

export function verificationsPath(query: VerificationQuery = {}): string {
  const params = new URLSearchParams();
  const words = (query.q ?? "").trim();
  if (words) params.set("q", words);
  if (query.since) params.set("since", query.since);
  if (query.until) params.set("until", query.until);
  const tail = params.toString();
  return `/v1/research/verifications${tail ? `?${tail}` : ""}`;
}

export async function listVerifications(query: VerificationQuery = {}): Promise<Verification[]> {
  const response = await apiFetch(verificationsPath(query));
  if (!response.ok) {
    throw new Error(`Doğrulamalar okunamadı (HTTP ${response.status}).`);
  }
  const data = (await response.json()) as { items?: Verification[] };
  return Array.isArray(data.items) ? data.items : [];
}

/** "Bu hafta" / "Son 30 gün" / everything: the window start as an ISO instant, or none. */
export function sinceForDays(days: number | null, now: number = Date.now()): string | undefined {
  if (days === null) return undefined;
  return new Date(now - days * 24 * 60 * 60 * 1000).toISOString();
}
