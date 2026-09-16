"use client";

/**
 * B11 req 372: this browser's half of Web Push.
 *
 * Two concerns kept apart on purpose, the same split `app.webpush` draws on the server:
 *
 * - **the server client** (`fetchVapidPublicKey`, `postSubscription`, `listSubscriptions`,
 *   `deleteSubscription`) — plain `apiFetch` calls, owner-session-gated like every other
 *   client in this shell, fully testable against a mocked `apiFetch`;
 * - **the browser Push API** (`registerAndSubscribe`, `unsubscribeBrowser`,
 *   `currentBrowserSubscription`, `pushSupport`, `notificationPermission`) — the parts that
 *   touch `navigator`/`Notification`/`ServiceWorkerRegistration`, guarded so this module
 *   never throws just from being imported in an environment without them (SSR, or a test
 *   that has not stubbed them).
 *
 * **Permission is requested from exactly one place: inside `registerAndSubscribe`, which
 * this module never calls on its own.** The settings UI calls it from an `onClick` handler
 * and nowhere else — `Notification.requestPermission()` on page load is a dark pattern
 * every major browser now penalises (auto-blocking the origin), and CLAUDE.md's "the owner
 * should not become the software's operator" cuts the other way here too: a permission
 * prompt the owner did not ask for is friction, not help.
 */

import { apiFetch } from "../session";

export const VAPID_PUBLIC_KEY_PATH = "/v1/webpush/public-key";
export const SUBSCRIPTIONS_PATH = "/v1/webpush/subscriptions";
export const SERVICE_WORKER_URL = "/sw.js";

export class WebPushApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "WebPushApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* no JSON body, or not the shape we expect */
  }
  return `API hatası (HTTP ${response.status})`;
}

// ------------------------------------------------------------------- the server client

export type VapidKeyState =
  | { supported: true; publicKey: string }
  | { supported: false; publicKey: null; reason: string };

/** GET /v1/webpush/public-key. Never throws for "not configured" — that is an honest
 * `{ supported: false, reason }`, not an HTTP error (app.webpush.routes.get_public_key). */
export async function fetchVapidPublicKey(): Promise<VapidKeyState> {
  const response = await apiFetch(VAPID_PUBLIC_KEY_PATH);
  if (!response.ok) throw new WebPushApiError(response.status, await readDetail(response));
  const body = (await response.json()) as { supported?: unknown; public_key?: unknown; reason?: unknown };
  if (body.supported === true && typeof body.public_key === "string") {
    return { supported: true, publicKey: body.public_key };
  }
  return { supported: false, publicKey: null, reason: typeof body.reason === "string" ? body.reason : "unknown" };
}

export type StoredSubscription = {
  id: string;
  endpointHost: string;
  createdAt: string | null;
  lastSuccessAt: string | null;
  lastErrorReason: string | null;
  failureCount: number;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function parseStoredSubscription(raw: unknown): StoredSubscription | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const id = str(o.id);
  if (id === null) return null;
  return {
    id,
    endpointHost: str(o.endpoint_host) ?? "",
    createdAt: str(o.created_at),
    lastSuccessAt: str(o.last_success_at),
    lastErrorReason: str(o.last_error_reason),
    failureCount: typeof o.failure_count === "number" ? o.failure_count : 0,
  };
}

/** POST the browser's `PushSubscription.toJSON()` shape. Returns the stored row. */
export async function postSubscription(
  subscription: PushSubscriptionJSON,
  userAgent: string,
): Promise<StoredSubscription> {
  const response = await apiFetch(SUBSCRIPTIONS_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      endpoint: subscription.endpoint,
      keys: { p256dh: subscription.keys?.p256dh ?? "", auth: subscription.keys?.auth ?? "" },
      user_agent: userAgent.slice(0, 256),
    }),
  });
  if (!response.ok) throw new WebPushApiError(response.status, await readDetail(response));
  const parsed = parseStoredSubscription(await response.json());
  if (!parsed) throw new WebPushApiError(response.status, "sunucu beklenmeyen bir yanıt verdi");
  return parsed;
}

export async function listSubscriptions(): Promise<StoredSubscription[]> {
  const response = await apiFetch(SUBSCRIPTIONS_PATH);
  if (!response.ok) throw new WebPushApiError(response.status, await readDetail(response));
  const body = (await response.json()) as { subscriptions?: unknown };
  const rows = Array.isArray(body.subscriptions) ? body.subscriptions : [];
  return rows.map(parseStoredSubscription).filter((row): row is StoredSubscription => row !== null);
}

export async function deleteSubscription(id: string): Promise<void> {
  const response = await apiFetch(`${SUBSCRIPTIONS_PATH}/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!response.ok && response.status !== 404) throw new WebPushApiError(response.status, await readDetail(response));
}

// ------------------------------------------------------------------- pure helpers

/**
 * The standard "VAPID public key -> `applicationServerKey`" conversion the Push API
 * requires: base64url text to a `Uint8Array` of raw bytes. Pure and DOM-free, so it is
 * tested directly rather than only through a browser-backed integration test.
 */
export function urlBase64ToUint8Array(base64url: string): Uint8Array {
  const padding = "=".repeat((4 - (base64url.length % 4)) % 4);
  const base64 = (base64url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

// ------------------------------------------------------------------- browser feature state

export type PushSupport = "supported" | "unsupported";

/** Whether THIS browser can do Web Push at all - never whether it currently does. */
export function pushSupport(): PushSupport {
  if (typeof navigator === "undefined" || typeof window === "undefined") return "unsupported";
  if (!("serviceWorker" in navigator)) return "unsupported";
  if (!("PushManager" in window)) return "unsupported";
  return "supported";
}

export type NotificationPermissionState = NotificationPermission | "unsupported";

export function notificationPermission(): NotificationPermissionState {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}

/**
 * The browser-side subscription, if one already exists - a READ, never a permission
 * prompt, so callers may use this on mount to render an honest "subscribed" state
 * without asking for anything.
 */
export async function currentBrowserSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() === "unsupported") return null;
  const registration = await navigator.serviceWorker.getRegistration(SERVICE_WORKER_URL);
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

/**
 * Registers the service worker (idempotent - a second call reuses the existing
 * registration), asks for notification permission, and subscribes. The ONLY function in
 * this module that requests permission; callers invoke it from a click handler only
 * (module docstring).
 *
 * Throws `Error("permission_denied")` rather than proceeding when the owner declines -
 * a caller must not interpret "no subscription" from a denied prompt as "try again
 * later", because the browser will not ask again without the owner changing the site
 * permission themselves.
 */
export async function registerAndSubscribe(vapidPublicKey: string): Promise<PushSubscription> {
  if (pushSupport() === "unsupported") throw new Error("push_unsupported");
  const registration = await navigator.serviceWorker.register(SERVICE_WORKER_URL);
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("permission_denied");
  const existing = await registration.pushManager.getSubscription();
  if (existing) return existing;
  return registration.pushManager.subscribe({
    userVisibleOnly: true,
    // TS's DOM lib types `PushSubscriptionOptionsInit.applicationServerKey` as a
    // `BufferSource` backed specifically by `ArrayBuffer` (never `SharedArrayBuffer`);
    // `new Uint8Array(n)` is always backed by a plain `ArrayBuffer`, so this is a real
    // `BufferSource` at runtime — the cast only works around the lib types being
    // stricter than `Uint8Array`'s own generic signature here.
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as BufferSource,
  });
}

/** Unsubscribes THIS browser locally. The caller is responsible for also calling
 * `deleteSubscription` on the server row (the settings UI does both together). */
export async function unsubscribeBrowser(): Promise<void> {
  const subscription = await currentBrowserSubscription();
  if (subscription) await subscription.unsubscribe();
}
