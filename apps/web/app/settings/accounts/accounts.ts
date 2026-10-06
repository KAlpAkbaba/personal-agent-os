/**
 * Ayarlar > Hesaplar's client (card mail-accounts-connect, the owner 2026-10-05: "Gmail ve
 * M365 bağlayayım, 3 hesap, isimlendirmek istiyorum").
 *
 * Four calls, each the Cloud Core's own owner-gated route. Connecting is a redirect: the
 * Cloud Core hands back the provider's authorization URL (PKCE + a single-use state), the
 * browser goes there, the owner signs in at Google/Microsoft, and the provider sends the
 * browser to the Cloud Core's callback - never to this page with a code. No password and no
 * token ever passes through the web shell.
 */

import { apiFetch } from "../../lib/session";

export const ACCOUNTS_PATH = "/v1/accounts";
export const ACCOUNTS_CONNECT_PATH = "/v1/accounts/connect";

export type AccountProvider = "gmail" | "microsoft";

export type Account = {
  id: string;
  name: string;
  provider: AccountProvider;
  address: string;
  scopes: string[];
  state: "connected" | "error" | string;
  connected_at: string | null;
  last_sync_at: string | null;
  last_error: string | null;
};

export type ProviderSetup = { configured: boolean; console_url: string; steps: string[] };

export type AccountsSetup = {
  redirect_uri: string;
  public_base_configured: boolean;
  google: ProviderSetup;
  microsoft: ProviderSetup;
};

export type AccountsData = { accounts: Account[]; setup: AccountsSetup };

export type Outcome = { ok: boolean; speech: string };

export const PROVIDER_LABEL: Record<AccountProvider, string> = {
  gmail: "Gmail",
  microsoft: "Microsoft 365",
};

export function accountPath(id: string): string {
  return `${ACCOUNTS_PATH}/${encodeURIComponent(id)}`;
}

export function stateLabel(account: Account): string {
  if (account.state === "connected") return "Bağlı";
  if (account.state === "error") return `Sorun var${account.last_error ? ` (${account.last_error})` : ""}`;
  return account.state;
}

/** "son eşitleme 5 Eki 14:02" or "henüz eşitlenmedi". */
export function syncLabel(account: Account): string {
  if (!account.last_sync_at) return "henüz eşitlenmedi";
  const at = new Date(account.last_sync_at);
  if (Number.isNaN(at.getTime())) return "henüz eşitlenmedi";
  return `son eşitleme ${at.toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" })}`;
}

async function speechOf(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { speech?: unknown };
    return typeof body.speech === "string" ? body.speech : fallback;
  } catch {
    return fallback;
  }
}

export async function fetchAccounts(): Promise<AccountsData> {
  const response = await apiFetch(ACCOUNTS_PATH);
  if (!response.ok) throw new Error(`Hesaplar alınamadı (HTTP ${response.status}).`);
  return (await response.json()) as AccountsData;
}

/** The provider's authorization URL to send the browser to, or the Cloud Core's refusal. */
export async function connectAccount(
  provider: AccountProvider,
  name: string,
): Promise<{ ok: true; url: string } | { ok: false; speech: string }> {
  const trimmed = name.trim();
  if (!trimmed) return { ok: false, speech: "Önce hesaba bir ad verin (ör. İş, Kişisel)." };
  try {
    const response = await apiFetch(ACCOUNTS_CONNECT_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, name: trimmed }),
    });
    if (!response.ok) return { ok: false, speech: await speechOf(response, `Bağlanamadı (HTTP ${response.status}).`) };
    const body = (await response.json()) as { authorize_url?: unknown };
    const url = typeof body.authorize_url === "string" ? body.authorize_url : "";
    // Only ever a provider's own sign-in page - never an arbitrary URL from a response.
    if (!url.startsWith("https://accounts.google.com/") && !url.startsWith("https://login.microsoftonline.com/")) {
      return { ok: false, speech: "Beklenmeyen bir bağlantı adresi geldi; gidilmedi." };
    }
    return { ok: true, url };
  } catch (err) {
    return { ok: false, speech: err instanceof Error ? err.message : String(err) };
  }
}

export async function renameAccount(id: string, name: string): Promise<Outcome> {
  try {
    const response = await apiFetch(accountPath(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    if (!response.ok) return { ok: false, speech: await speechOf(response, `Ad değişmedi (HTTP ${response.status}).`) };
    return { ok: true, speech: "Hesabın adı değişti." };
  } catch (err) {
    return { ok: false, speech: err instanceof Error ? err.message : String(err) };
  }
}

export async function disconnectAccount(id: string): Promise<Outcome> {
  try {
    const response = await apiFetch(accountPath(id), { method: "DELETE" });
    const speech = await speechOf(response, response.ok ? "Bağlantı kesildi." : `Kesilemedi (HTTP ${response.status}).`);
    return { ok: response.ok, speech };
  } catch (err) {
    return { ok: false, speech: err instanceof Error ? err.message : String(err) };
  }
}
