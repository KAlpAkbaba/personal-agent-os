"use client";

/**
 * Owner sign-in for the web shell (M9 / ADR-0027 follow-up).
 *
 * Every API endpoint except `/v1/system/health` now requires an owner bearer
 * session, so the artifact inbox stopped working the moment the identity layer
 * landed. This module is the whole fix, and it is deliberately small:
 *
 * - there is ONE owner and no accounts, so there is no user management here,
 *   no registration, no profile, no roles — just "paste the owner credential
 *   once, get a session token, send it";
 * - the credential is exchanged for a session and then dropped. It is never
 *   stored, so a browser that is later compromised yields at most one session,
 *   which the owner can revoke from another client;
 * - a 401 from any call clears the stored session and puts the sign-in panel
 *   back. The API deliberately does not say whether a session expired, was
 *   revoked, or never existed, and neither does this.
 *
 * Where the token lives, and the trade-off, stated rather than hidden: the
 * session token is kept in memory and mirrored into `localStorage` so a page
 * reload does not ask the owner to re-authenticate. `localStorage` is readable
 * by any script that gets injected into this origin. The alternative — an
 * httpOnly cookie — would require the API to become a cookie-issuing,
 * CSRF-defending surface, which ADR-0027 deliberately did not make it (it is a
 * bearer-token API for native clients first). Given a single-owner shell served
 * on loopback/Tailscale with no third-party scripts, this is the smaller risk;
 * if the web shell is ever exposed more widely, this is the decision to revisit.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8001";

const STORAGE_KEY = "pagentos.owner_session";

let cachedToken: string | null = null;
let loadedFromStorage = false;

type Listener = (token: string | null) => void;
const listeners = new Set<Listener>();

function notify(): void {
  for (const listener of listeners) listener(cachedToken);
}

/** Subscribe to sign-in / sign-out. Returns an unsubscribe function. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getToken(): string | null {
  if (cachedToken) return cachedToken;
  if (loadedFromStorage || typeof window === "undefined") return cachedToken;
  loadedFromStorage = true;
  try {
    cachedToken = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private mode / storage disabled: stay in memory only.
    cachedToken = null;
  }
  return cachedToken;
}

export function setToken(token: string): void {
  cachedToken = token;
  loadedFromStorage = true;
  try {
    window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    /* in-memory only is still a working session */
  }
  notify();
}

export function clearToken(): void {
  cachedToken = null;
  loadedFromStorage = true;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
  notify();
}

/** Raised when the API refuses the session. The caller shows sign-in again. */
export class UnauthorizedError extends Error {
  constructor(path: string) {
    super(`unauthorized: ${path}`);
    this.name = "UnauthorizedError";
  }
}

/**
 * `fetch` with the owner session attached and 401 handled in exactly one place.
 * Every API call in the shell goes through this; nothing calls `fetch` directly.
 */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (response.status === 401) {
    clearToken();
    throw new UnauthorizedError(path);
  }
  return response;
}

export type OwnerSession = {
  session_id: string;
  client_kind: string;
  client_label: string;
  expires_at: string;
};

/**
 * Exchange the owner credential for a session. The credential is used here and
 * nowhere else — it is not stored, not logged, and not kept in component state
 * after this resolves.
 */
export async function signIn(credential: string): Promise<OwnerSession> {
  const response = await fetch(`${API_BASE}/v1/identity/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      owner_credential: credential,
      client_kind: "web",
      label: "web shell",
    }),
  });
  if (response.status === 401) {
    throw new UnauthorizedError("/v1/identity/sessions");
  }
  if (response.status === 429) {
    throw new Error("Çok fazla deneme. Bir dakika sonra tekrar dene.");
  }
  if (!response.ok) {
    throw new Error(`Giriş başarısız (HTTP ${response.status})`);
  }
  const payload = await response.json();
  setToken(payload.token);
  return payload as OwnerSession;
}

/** Revoke this session server-side, then forget it locally either way. */
export async function signOut(): Promise<void> {
  try {
    await apiFetch("/v1/identity/sessions/current", { method: "DELETE" });
  } catch {
    /* already gone server-side; the local clear below is what matters */
  }
  clearToken();
}

export async function currentSession(): Promise<OwnerSession | null> {
  try {
    const response = await apiFetch("/v1/identity/sessions/current");
    if (!response.ok) return null;
    return (await response.json()) as OwnerSession;
  } catch {
    return null;
  }
}
