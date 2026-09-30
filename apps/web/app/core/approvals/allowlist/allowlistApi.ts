/**
 * The client for `/v1/team/allowlist` (ADR-0218): the sites a cloud browser job may ACT on.
 *
 * It sends what the owner typed and reports what the Cloud Core answered; every rule (a
 * registrable domain, not a bare suffix, not deny-listed) is the server's, and a refusal is
 * surfaced with the server's own sentence, never turned into a success.
 */

import { apiFetch } from "../../../lib/session";

export const ALLOWLIST_PATH = "/v1/team/allowlist";

export type AllowlistSite = {
  site: string;
  /** "seed" comes from the shared file and cannot be removed here. */
  source: "seed" | "owner";
  added_at: string;
  added_by: string;
};

export type ChangeResult =
  | { ok: true; already_listed?: boolean }
  | { ok: false; code: string; message: string };

async function refusal(response: Response): Promise<{ code: string; message: string }> {
  try {
    const body = (await response.json()) as { detail?: { code?: string; message?: string } };
    return {
      code: body.detail?.code ?? `http_${response.status}`,
      message: body.detail?.message ?? `HTTP ${response.status}`,
    };
  } catch {
    return { code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

export async function fetchSites(): Promise<AllowlistSite[]> {
  const response = await apiFetch(ALLOWLIST_PATH);
  if (!response.ok) throw new Error((await refusal(response)).message);
  return ((await response.json()) as { sites: AllowlistSite[] }).sites;
}

export async function addSite(site: string): Promise<ChangeResult> {
  const response = await apiFetch(ALLOWLIST_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site }),
  });
  if (!response.ok) return { ok: false, ...(await refusal(response)) };
  const body = (await response.json()) as { already_listed?: boolean };
  return { ok: true, already_listed: body.already_listed };
}

export async function removeSite(site: string): Promise<ChangeResult> {
  const response = await apiFetch(`${ALLOWLIST_PATH}/${encodeURIComponent(site)}`, {
    method: "DELETE",
  });
  if (!response.ok) return { ok: false, ...(await refusal(response)) };
  return { ok: true };
}
